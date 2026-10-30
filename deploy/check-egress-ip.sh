#!/usr/bin/env bash
# check-egress-ip.sh: is this VPS still leaving the internet from the IP Zerodha whitelisted?
#
# Installed by bootstrap.sh as /usr/local/sbin/check-egress-ip.sh, run by
# /etc/cron.d/kite-egress-check every morning before the open and at boot.
# Reads EXPECTED_STATIC_IP from /etc/kite-mcp/egress.conf (or the environment).
#
# Exit 0: the outbound IPv4 address is the expected one and there is no IPv6 egress.
# Exit 1: a mismatch, or IPv6 egress (api.kite.trade has an AAAA record, so the server could
#         reach Kite over IPv6, which the IPv4 whitelist does not cover).
# Exit 2: could not find out (no network, both lookup services down, bad config).
# Problems go to syslog at priority crit (tag kite-egress), to stderr and to every
# logged-in terminal, so a changed IP is caught before the market opens: Zerodha allows
# only one whitelist change per week.

set -euo pipefail

CONF=${EGRESS_CONF:-/etc/kite-mcp/egress.conf}
TAG=kite-egress

say() {  # priority message
    logger -t "$TAG" -p "user.$1" -- "$2" || true
    printf '%s %s: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$TAG" "$2" >&2
}

shout() {
    say crit "$1"
    if command -v wall >/dev/null 2>&1; then
        printf '%s: %s\n' "$TAG" "$1" | wall 2>/dev/null || true
    fi
}

if [[ -z "${EXPECTED_STATIC_IP:-}" && -r "$CONF" ]]; then
    EXPECTED_STATIC_IP=$(sed -n 's/^EXPECTED_STATIC_IP=//p' "$CONF" | tail -n 1 | tr -d '"'"'"' \r')
fi
if [[ ! "${EXPECTED_STATIC_IP:-}" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    shout "EXPECTED_STATIC_IP is missing or not an IPv4 address (set it in $CONF)"
    exit 2
fi

lookup() {  # family(4|6) -> the address one of the services reports, or nothing
    local family=$1 url ip
    for url in https://ifconfig.me/ip https://api.ipify.org https://icanhazip.com; do
        ip=$(curl "-$family" -fsS --max-time 10 "$url" 2>/dev/null | tr -d ' \r\n') || ip=""
        if [[ -n "$ip" ]]; then
            printf '%s\n' "$ip"
            return 0
        fi
    done
    return 1
}

if ! actual=$(lookup 4); then
    shout "could not determine the outbound IPv4 address (ifconfig.me, ipify and icanhazip all failed)"
    exit 2
fi

status=0
if [[ "$actual" != "$EXPECTED_STATIC_IP" ]]; then
    shout "EGRESS IP MISMATCH: this VPS reaches the internet from $actual, but Zerodha whitelists $EXPECTED_STATIC_IP. Orders will be rejected. See deploy/README.md, 'If the IP changes'."
    status=1
else
    say info "egress IPv4 $actual matches the whitelisted address"
fi

# Only a real IPv6 answer counts (a NAT64 path can come back with an IPv4 address).
if v6=$(lookup 6) && [[ "$v6" == *:* ]]; then
    shout "IPv6 EGRESS PRESENT ($v6): api.kite.trade has an IPv6 address and the server may use it, which the IPv4 whitelist does not cover. Set DISABLE_IPV6=yes and re-run bootstrap.sh, or disable IPv6 on the instance."
    status=1
fi

exit "$status"
