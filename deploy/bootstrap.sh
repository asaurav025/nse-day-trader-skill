#!/usr/bin/env bash
# bootstrap.sh: stand up the static-IP VPS for zerodha/kite-mcp-server.
#
# Run once as root on a fresh Ubuntu LTS (22.04 or 24.04) instance; safe to re-run.
#     scp -r deploy/ root@<reserved-ip>:/root/deploy     # with deploy/.env.deploy filled in
#     ssh root@<reserved-ip> 'bash /root/deploy/bootstrap.sh'
#
# What it does, in order (each step checks before it changes anything):
#   1. base packages, time zone, unattended security upgrades
#   2. ADMIN_USER with sudo and your SSH key; root and password SSH logins off
#   3. ufw: deny all inbound; SSH only from ADMIN_SSH_FROM_IP, or not at all (tunnel only)
#   4. optional: IPv6 off (api.kite.trade has an AAAA record; the whitelist is IPv4)
#   5. cloudflared from Cloudflare's apt repo, run as a service from CF_TUNNEL_TOKEN
#   6. kite-mcp-server: the pinned release binary (SHA-256 checked), or built from the
#      pinned commit with a pinned Go toolchain when the VPS is not x86_64
#   7. /etc/kite-mcp/kite.env (0600) from kite.env.example, written once, never overwritten
#   8. kite-mcp.service, started only once the API key and secret are filled in
#   9. the daily egress-IP check (cron)
#
# It never prompts for, prints or logs the Kite API key/secret or the tunnel token.

set -euo pipefail
umask 022

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=${ENV_FILE:-$HERE/.env.deploy}

KITE_DIR=/etc/kite-mcp
KITE_ENV=$KITE_DIR/kite.env
KITE_USER=kite-mcp
KITE_LIB=/usr/local/lib/kite-mcp
KITE_BIN=/usr/local/bin/kite-mcp-server
KITE_UNIT=/etc/systemd/system/kite-mcp.service
CF_ENV=/etc/cloudflared/tunnel.env
CF_UNIT=/etc/systemd/system/cloudflared.service
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-kite-hardening.conf

# Go toolchain for the source fallback (kite-mcp-server v0.3.2 needs go >= 1.24.2).
GO_VERSION=go1.24.13
go_sha256() {
    case "$1" in
        amd64) printf '%s' 1fc94b57134d51669c72173ad5d49fd62afb0f1db9bf3f798fd98ee423f8d730 ;;
        arm64) printf '%s' 74d97be1cc3a474129590c67ebf748a96e72d9f3a2b6fef3ed3275de591d49b3 ;;
        *) return 1 ;;
    esac
}

log() { printf '\n==> %s\n' "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf 'bootstrap.sh: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- settings

[[ $EUID -eq 0 ]] || die "run as root (sudo bash $0)"
[[ -r "$ENV_FILE" ]] || die "no $ENV_FILE: copy .env.deploy.example to .env.deploy and fill it in"
[[ -r "$HERE/kite.env.example" && -r "$HERE/kite-mcp.service" && -r "$HERE/check-egress-ip.sh" ]] \
    || die "run from a full copy of the deploy/ folder"
# shellcheck source=/dev/null
os_id=$(. /etc/os-release && printf '%s' "${ID:-}")
[[ "$os_id" == "ubuntu" ]] || die "this script targets Ubuntu LTS (found ${os_id:-unknown})"

# Plain shell assignments; not exported, so the tunnel token never reaches child processes.
# shellcheck source=/dev/null
. "$ENV_FILE"

MCP_PORT=${MCP_PORT:-8080}
TIMEZONE=${TIMEZONE:-Asia/Kolkata}
DISABLE_IPV6=${DISABLE_IPV6:-yes}
SSH_VIA_TUNNEL_ONLY=${SSH_VIA_TUNNEL_ONLY:-no}
ADMIN_SSH_FROM_IP=${ADMIN_SSH_FROM_IP:-}
KITE_MCP_VERSION=${KITE_MCP_VERSION:-v0.3.2}
KITE_MCP_SHA256=${KITE_MCP_SHA256:-}
KITE_MCP_COMMIT=${KITE_MCP_COMMIT:-}

ipv4_re='^[0-9]{1,3}(\.[0-9]{1,3}){3}$'
user_re='^[a-z_][a-z0-9_-]{0,30}$'
key_re='^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com) [A-Za-z0-9+/=]+'
host_re='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$'
[[ "${ADMIN_USER:-}" =~ $user_re ]] || die "ADMIN_USER must be a plain lower-case user name"
[[ "$ADMIN_USER" != "root" && "$ADMIN_USER" != "$KITE_USER" ]] || die "ADMIN_USER cannot be root or $KITE_USER"
[[ "${ADMIN_SSH_PUBKEY:-}" =~ $key_re ]] \
    || die "ADMIN_SSH_PUBKEY must be one OpenSSH public key line"
[[ "${KITE_HOSTNAME:-}" =~ $host_re ]] \
    || die "KITE_HOSTNAME must be a lower-case DNS name like kite.example.in"
[[ "$KITE_HOSTNAME" != "kite.example.in" ]] || die "KITE_HOSTNAME is still the placeholder"
[[ -n "${CF_TUNNEL_TOKEN:-}" ]] || die "CF_TUNNEL_TOKEN is empty"
if [[ ! "$MCP_PORT" =~ ^[0-9]+$ ]] || (( MCP_PORT < 1024 || MCP_PORT > 65535 )); then
    die "MCP_PORT must be a port from 1024 to 65535"
fi
[[ "${EXPECTED_STATIC_IP:-}" =~ $ipv4_re ]] || die "EXPECTED_STATIC_IP must be the Reserved IPv4 address"
[[ "$EXPECTED_STATIC_IP" != "203.0.113.10" ]] || die "EXPECTED_STATIC_IP is still the placeholder"
if [[ -n "$ADMIN_SSH_FROM_IP" ]]; then
    [[ "$ADMIN_SSH_FROM_IP" =~ $ipv4_re ]] || die "ADMIN_SSH_FROM_IP must be one IPv4 address (or empty)"
elif [[ "$SSH_VIA_TUNNEL_ONLY" != "yes" ]]; then
    die "ADMIN_SSH_FROM_IP is empty: that closes port 22 to the internet. Set up SSH through the tunnel first
(README.md, 'SSH without an open port'), then set SSH_VIA_TUNNEL_ONLY=yes to confirm."
fi
[[ "$KITE_MCP_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "KITE_MCP_VERSION must be a release tag like v0.3.2"
[[ "$KITE_MCP_SHA256" =~ ^[0-9a-f]{64}$ ]] || die "KITE_MCP_SHA256 must be the release asset's SHA-256"

case "$(uname -m)" in
    x86_64) ARCH=amd64 ;;
    aarch64) ARCH=arm64 ;;
    *) die "unsupported CPU $(uname -m)" ;;
esac

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------- 1. base system

log "Base packages, time zone $TIMEZONE, unattended upgrades"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg ufw unattended-upgrades cron git >/dev/null
timedatectl set-timezone "$TIMEZONE"

cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
# No automatic reboot: a reboot restarts kite-mcp-server and drops the day's Kite login.
cat > /etc/apt/apt.conf.d/52kite-no-auto-reboot <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF
systemctl enable --now unattended-upgrades.service >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 2. admin user and SSH

log "Admin user $ADMIN_USER, SSH key only"
if ! id -u "$ADMIN_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$ADMIN_USER" >/dev/null
    note "created $ADMIN_USER"
fi
usermod -aG sudo "$ADMIN_USER"
# The account has no password (key-only login), so sudo cannot ask for one.
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$ADMIN_USER" > "$TMP/sudoers"
visudo -cqf "$TMP/sudoers" || die "sudoers check failed"
install -m 0440 -o root -g root "$TMP/sudoers" "/etc/sudoers.d/90-$ADMIN_USER"

home=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
install -d -m 0700 -o "$ADMIN_USER" -g "$ADMIN_USER" "$home/.ssh"
touch "$home/.ssh/authorized_keys"
if ! grep -qxF -- "$ADMIN_SSH_PUBKEY" "$home/.ssh/authorized_keys"; then
    printf '%s\n' "$ADMIN_SSH_PUBKEY" >> "$home/.ssh/authorized_keys"
    note "installed the SSH key"
fi
chown "$ADMIN_USER:$ADMIN_USER" "$home/.ssh/authorized_keys"
chmod 0600 "$home/.ssh/authorized_keys"

# sshd takes the first value it reads, and Ubuntu's cloud images ship
# 50-cloud-init.conf with PasswordAuthentication yes: 10- sorts first.
cat > "$TMP/sshd" <<EOF
# Written by nse-day-trader-skill deploy/bootstrap.sh
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers $ADMIN_USER
EOF
if ! cmp -s "$TMP/sshd" "$SSHD_DROPIN"; then
    install -m 0644 "$TMP/sshd" "$SSHD_DROPIN"
    sshd -t || { rm -f "$SSHD_DROPIN"; die "sshd rejected the new settings; nothing changed"; }
    # Ubuntu 24.04 starts sshd per connection from ssh.socket: when the service is not
    # running there is nothing to reload, and the next connection reads the new settings.
    systemctl try-reload-or-restart ssh.service 2>/dev/null || systemctl try-reload-or-restart sshd.service 2>/dev/null || true
    note "root and password logins are off: test 'ssh $ADMIN_USER@...' in a NEW terminal before closing this one"
fi

# ---------------------------------------------------------------- 3. firewall

log "Firewall: deny all inbound"
# Rebuilt from scratch every run, so a changed ADMIN_SSH_FROM_IP leaves no stale rule.
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
if [[ -n "$ADMIN_SSH_FROM_IP" ]]; then
    ufw allow from "$ADMIN_SSH_FROM_IP" to any port 22 proto tcp comment 'admin ssh' >/dev/null
    note "SSH (22/tcp) open to $ADMIN_SSH_FROM_IP only"
else
    note "no inbound ports at all: SSH only through the Cloudflare Tunnel"
fi
ufw --force enable >/dev/null

# ---------------------------------------------------------------- 4. IPv6

if [[ "$DISABLE_IPV6" == "yes" ]]; then
    log "IPv6 off (the Zerodha whitelist is the IPv4 Reserved IP)"
    cat > /etc/sysctl.d/60-kite-disable-ipv6.conf <<'EOF'
# api.kite.trade has an AAAA record; Go tries IPv6 first on a dual-stack host.
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 0
EOF
    sysctl -q --system
else
    rm -f /etc/sysctl.d/60-kite-disable-ipv6.conf
    note "IPv6 left on: the egress check will warn if it can reach the internet over IPv6"
fi

# ---------------------------------------------------------------- 5. cloudflared

log "cloudflared"
if ! command -v cloudflared >/dev/null 2>&1; then
    install -d -m 0755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
    printf 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main\n' \
        > /etc/apt/sources.list.d/cloudflared.list
    apt-get update -q
    apt-get install -y -q cloudflared >/dev/null
fi
note "$(cloudflared --version 2>&1 | head -n 1)"

# The token goes in a root-only file read by systemd, not on the command line
# (where 'cloudflared service install <token>' would leave it, readable by every user).
install -d -m 0700 /etc/cloudflared
( umask 077; printf 'TUNNEL_TOKEN=%s\n' "$CF_TUNNEL_TOKEN" > "$TMP/tunnel.env" )
cf_changed=no
if ! cmp -s "$TMP/tunnel.env" "$CF_ENV"; then
    install -m 0600 -o root -g root "$TMP/tunnel.env" "$CF_ENV"
    cf_changed=yes
fi
cat > "$TMP/cloudflared.service" <<EOF
# Written by nse-day-trader-skill deploy/bootstrap.sh
[Unit]
Description=Cloudflare Tunnel (remotely managed) for $KITE_HOSTNAME
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
TimeoutStartSec=0
EnvironmentFile=$CF_ENV
ExecStart=/usr/bin/cloudflared --no-autoupdate tunnel run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
if ! cmp -s "$TMP/cloudflared.service" "$CF_UNIT"; then
    install -m 0644 "$TMP/cloudflared.service" "$CF_UNIT"
    cf_changed=yes
fi
systemctl daemon-reload
systemctl enable cloudflared.service >/dev/null 2>&1
if [[ "$cf_changed" == "yes" ]] || ! systemctl is-active --quiet cloudflared.service; then
    systemctl restart cloudflared.service
fi
note "cloudflared.service: $(systemctl is-active cloudflared.service || true)"

# ---------------------------------------------------------------- 6. kite-mcp-server

log "kite-mcp-server $KITE_MCP_VERSION"
if ! id -u "$KITE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$KITE_USER"
fi
install -d -m 0755 "$KITE_LIB"
target=$KITE_LIB/kite-mcp-server-$KITE_MCP_VERSION

fetch_release() {
    local url="https://github.com/zerodha/kite-mcp-server/releases/download/$KITE_MCP_VERSION/kite-mcp-server-linux-$ARCH"
    note "downloading $url"
    curl -fsSL --retry 3 -o "$TMP/kms" "$url" || return 1
    printf '%s  %s\n' "$KITE_MCP_SHA256" "$TMP/kms" | sha256sum -c --status \
        || die "SHA-256 mismatch for the downloaded kite-mcp-server: refusing to install it"
    install -m 0755 -o root -g root "$TMP/kms" "$target"
}

build_from_source() {
    [[ "$KITE_MCP_COMMIT" =~ ^[0-9a-f]{40}$ ]] || die "the source fallback needs KITE_MCP_COMMIT (40 hex digits)"
    local tarball="$GO_VERSION.linux-$ARCH.tar.gz"
    note "no release binary for linux-$ARCH: building $KITE_MCP_VERSION with $GO_VERSION"
    curl -fsSL --retry 3 -o "$TMP/$tarball" "https://go.dev/dl/$tarball"
    printf '%s  %s\n' "$(go_sha256 "$ARCH")" "$TMP/$tarball" | sha256sum -c --status || die "SHA-256 mismatch for $tarball"
    tar -C "$TMP" -xzf "$TMP/$tarball"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$KITE_MCP_VERSION" \
        https://github.com/zerodha/kite-mcp-server "$TMP/src"
    [[ "$(git -C "$TMP/src" rev-parse HEAD)" == "$KITE_MCP_COMMIT" ]] \
        || die "tag $KITE_MCP_VERSION does not point at the pinned commit $KITE_MCP_COMMIT"
    ( cd "$TMP/src" && GOTOOLCHAIN=local GOPATH="$TMP/gopath" GOCACHE="$TMP/gocache" CGO_ENABLED=0 \
        "$TMP/go/bin/go" build -o "$TMP/kms" \
        -ldflags "-s -w -X 'main.MCP_SERVER_VERSION=$KITE_MCP_VERSION'" main.go )
    install -m 0755 -o root -g root "$TMP/kms" "$target"
}

if [[ ! -x "$target" ]]; then
    if [[ "$ARCH" == "amd64" ]]; then
        fetch_release || die "could not download the release binary"
    else
        build_from_source
    fi
fi
kms_changed=no
if [[ "$(readlink -f "$KITE_BIN" 2>/dev/null || true)" != "$target" ]]; then
    ln -sfn "$target" "$KITE_BIN"
    kms_changed=yes
fi
note "$("$KITE_BIN" --version 2>&1 | head -n 1 || true)"

# ---------------------------------------------------------------- 7. kite.env

log "$KITE_ENV"
install -d -m 0755 -o root -g root "$KITE_DIR"   # egress.conf is public; kite.env is 0600
if [[ ! -e "$KITE_ENV" ]]; then
    sed -e "s/__MCP_PORT__/$MCP_PORT/" -e "s/__KITE_HOSTNAME__/$KITE_HOSTNAME/" \
        "$HERE/kite.env.example" > "$TMP/kite.env"
    install -m 0600 -o root -g root "$TMP/kite.env" "$KITE_ENV"
    note "written from kite.env.example; fill KITE_API_KEY and KITE_API_SECRET by hand"
else
    chown root:root "$KITE_ENV"
    chmod 0600 "$KITE_ENV"
    note "exists: left as it is (re-check APP_PORT=$MCP_PORT and PUBLIC_BASE_URL=https://$KITE_HOSTNAME)"
fi
keys_filled=no
if grep -Eq '^KITE_API_KEY=[^[:space:]]+' "$KITE_ENV" && grep -Eq '^KITE_API_SECRET=[^[:space:]]+' "$KITE_ENV"; then
    keys_filled=yes
fi
printf 'EXPECTED_STATIC_IP=%s\n' "$EXPECTED_STATIC_IP" > "$TMP/egress.conf"
install -m 0644 -o root -g root "$TMP/egress.conf" "$KITE_DIR/egress.conf"

# ---------------------------------------------------------------- 8. kite-mcp.service

log "kite-mcp.service"
if ! cmp -s "$HERE/kite-mcp.service" "$KITE_UNIT"; then
    install -m 0644 "$HERE/kite-mcp.service" "$KITE_UNIT"
    kms_changed=yes
fi
systemctl daemon-reload
systemctl enable kite-mcp.service >/dev/null 2>&1
if [[ "$keys_filled" != "yes" ]]; then
    systemctl stop kite-mcp.service 2>/dev/null || true
    note "NOT started: KITE_API_KEY / KITE_API_SECRET are empty in $KITE_ENV."
    note "Fill them with 'sudoedit $KITE_ENV', then: sudo systemctl restart kite-mcp"
elif [[ "$kms_changed" == "yes" ]] || ! systemctl is-active --quiet kite-mcp.service; then
    # A restart drops the in-memory Kite session: only when something changed or it is down.
    systemctl restart kite-mcp.service
    sleep 2
    note "restarted (log in to Kite again from Claude: MCP tool 'login')"
else
    note "running and unchanged: left alone (your Kite login is kept)"
fi
if systemctl is-active --quiet kite-mcp.service; then
    listeners=$(ss -Hltn "sport = :$MCP_PORT" | awk '{print $4}' | sort -u | tr '\n' ' ')
    case "$listeners" in
        "127.0.0.1:$MCP_PORT ") note "listening on 127.0.0.1:$MCP_PORT only" ;;
        *) systemctl stop kite-mcp.service
           die "kite-mcp-server is listening on '$listeners', not 127.0.0.1:$MCP_PORT only: stopped it" ;;
    esac
fi

# ---------------------------------------------------------------- 9. egress check

log "Daily egress-IP check"
install -m 0755 -o root -g root "$HERE/check-egress-ip.sh" /usr/local/sbin/check-egress-ip.sh
cat > /etc/cron.d/kite-egress-check <<'EOF'
# Written by nse-day-trader-skill deploy/bootstrap.sh. Times are the VPS's (Asia/Kolkata).
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
30 8 * * * root /usr/local/sbin/check-egress-ip.sh
@reboot root sleep 90 && /usr/local/sbin/check-egress-ip.sh
EOF
chmod 0644 /etc/cron.d/kite-egress-check
if /usr/local/sbin/check-egress-ip.sh; then
    note "egress check passed"
else
    note "egress check FAILED (see the message above): fix it before whitelisting or trading"
fi

log "Done"
note "Next (README.md): test SSH as $ADMIN_USER in a new terminal; Cloudflare public hostname"
note "$KITE_HOSTNAME -> http://127.0.0.1:$MCP_PORT and the Access app; then the Zerodha IP whitelist."
