# Static-IP VPS for kite-mcp-server

Since 1 April 2026, Zerodha rejects Kite Connect **order** requests (place, modify, cancel)
that do not come from a static IP whitelisted on your developer account. Market data,
positions and the order book work from any IP. This folder stands up a small VPS that
holds that IP and runs Zerodha's MCP server, reachable only through Cloudflare.

```
laptop: Claude Code ──HTTPS + CF-Access service token──▶ Cloudflare Access (KITE_HOSTNAME)
                                                              │ Cloudflare Tunnel (outbound from the VPS)
VPS (Vultr Mumbai, Reserved IP = main IP, ufw: no inbound)    ▼
  cloudflared ──▶ 127.0.0.1:MCP_PORT kite-mcp-server ──▶ api.kite.trade (from the Reserved IP)
```

| file | what |
| --- | --- |
| `bootstrap.sh` | run once as root on a fresh Ubuntu LTS VPS; idempotent |
| `.env.deploy.example` | its settings; copy to `.env.deploy` (gitignored) |
| `kite.env.example` | template for `/etc/kite-mcp/kite.env` (the Kite key and secret go there, by hand) |
| `kite-mcp.service` | systemd unit: loopback only, sandboxed, `Restart=always` |
| `check-egress-ip.sh` | daily check that the VPS still leaves from the whitelisted IP |
| `cloudflared-hostname.md` | Cloudflare dashboard steps: route, Access app, service token |
| `claude-mcp-config.example.json` | Claude Code (and read-only Claude Desktop) MCP config |

Nothing here contains a key, a token or your hostname. Keep it that way: `.env.deploy`,
`*.env` and `.mcp.json` are gitignored.

## What the sources say (read before you start)

Checked on 2026-10-30 against kite-mcp-server **v0.3.2** (latest stable release, commit
`5e044dc`), Zerodha's static-IP support article and Kite forum, Cloudflare's Zero Trust
docs and Vultr's docs. Where they differ from the original plan:

- **Server version.** v0.3.2 is pinned. The `v0.4.0-dev*` pre-releases add an OAuth layer
  and different variables; do not mix their docs with this setup.
- **Environment variables** (from `app/app.go`): `KITE_API_KEY`, `KITE_API_SECRET`,
  `APP_MODE` (`http` serves `/mcp`; `sse` serves `/sse` + `/message`; `hybrid` both),
  `APP_HOST` (default `localhost`), `APP_PORT` (default `8080`, not `PORT`),
  `PUBLIC_BASE_URL`, `EXCLUDED_TOOLS`, `LOG_LEVEL`, `ADMIN_ENDPOINT_SECRET_PATH` (leave unset).
- **The server has no authentication.** Anyone who reaches `/mcp` can call its tools.
  Cloudflare Access is the only lock, so the route must never exist without the Access app.
- **Login flow.** The `login` tool returns `PUBLIC_BASE_URL/authorize?session_id=<signed>`.
  That page sends you to Kite; Kite sends your browser back to the app's redirect URL,
  **`/callback`**, with a `request_token`. Signed links expire after **5 minutes**.
  The signing key is random per process and sessions live in memory: a server restart, or
  a new MCP session from your client, means a new Kite login.
- **Reserved IP.** On Vultr, attaching a Reserved IP to a running instance adds an extra
  address; Vultr does not say outbound traffic uses it (on Linux it normally does not).
  So the Reserved IP must be the instance's **main** IP: deploy the instance with it, or
  convert the instance's existing main IP into a Reserved IP. Then the "instance IP" and the
  Reserved IP are the same address, and `check-egress-ip.sh` must confirm it before you
  whitelist anything.
- **IPv6.** `api.kite.trade` has an IPv6 address, and Go tries IPv6 first on a dual-stack
  host. A forum user whitelisted one family and was seen on the other. bootstrap turns IPv6
  off by default; create the instance without IPv6 as well.
- **Tunnel origin.** `http://127.0.0.1:MCP_PORT`, not `localhost` (the server binds IPv4 loopback).
- **Callback behind Access.** The browser hits `/callback` coming back from Kite. Without an
  Access cookie it is sent to the OTP login first, and Cloudflare does not document whether
  the `request_token` query survives that detour. So the daily routine passes the OTP
  **first**. No Bypass policy.
- **Approval cards.** Claude Code's `permissions.ask` rules are documented to prompt on every
  matching call. Claude's docs describe per-tool "Needs approval" for connectors, not for a
  local mcp-remote server in Claude Desktop, so **Claude Code is the client for orders**;
  Desktop is documented for read-only use until you have checked it yourself.
- **Zerodha rules** (support article "static IP"): up to two IPs (a primary and an optional
  secondary), **one change per calendar week**, set at developers.kite.trade > Profile >
  **IP Whitelist**, applies to every app on the account, an IP can be linked to one account
  (you and your immediate family). Order endpoints only.

## 1. Vultr instance and Reserved IP

1. Vultr > **Network > Reserved IPs** > add an **IPv4** Reserved IP in **Mumbai** (`bom`).
   If Mumbai does not offer one, stop: the location of the IP and the instance must match.
2. Deploy a Cloud Compute instance in Mumbai, Ubuntu 24.04 LTS, x86_64 (1 vCPU / 1 GB is
   plenty), **with the Reserved IP as its main IP**, **IPv6 off**, your SSH key added.
   (Already have an instance? Network > Reserved IPs > Add > pick the instance's current IP >
   **Convert**. Do not merely attach a second address.)
3. Write the Reserved IP down: it is `EXPECTED_STATIC_IP`, and later the Zerodha whitelist.

## 2. Kite Connect app

At developers.kite.trade:

1. **Create new app**, type Connect. **Redirect URL:** `https://KITE_HOSTNAME/callback`.
   Postback URL: leave empty.
2. Keep the API key and secret somewhere only you can read (a password manager). They will
   be typed into the VPS in step 3.4 and nowhere else. Not in Claude, not in this repo.
3. Do **not** set the IP whitelist yet (step 6): one change per calendar week.

## 3. Bootstrap the VPS

1. On the laptop: `cp deploy/.env.deploy.example deploy/.env.deploy` and fill it in:
   `ADMIN_USER`, `ADMIN_SSH_PUBKEY`, `KITE_HOSTNAME`, `CF_TUNNEL_TOKEN` (Zero Trust >
   Networking > Tunnels > your tunnel > the install command's token), `MCP_PORT`,
   `EXPECTED_STATIC_IP`, and either `ADMIN_SSH_FROM_IP` or `SSH_VIA_TUNNEL_ONLY=yes`
   (see "SSH" below).
2. Copy and run:
   ```bash
   scp -r deploy root@<reserved-ip>:/root/deploy
   ssh root@<reserved-ip> 'bash /root/deploy/bootstrap.sh'
   ```
3. **Before closing that root session**, open a new terminal and check `ssh ADMIN_USER@...`
   and `sudo -v` work. bootstrap has turned root and password logins off.
4. Fill the Kite credentials on the VPS, by hand:
   ```bash
   sudoedit /etc/kite-mcp/kite.env      # KITE_API_KEY=..., KITE_API_SECRET=...
   sudo systemctl restart kite-mcp
   sudo systemctl status kite-mcp       # active (running)
   sudo ss -ltnp | grep kite            # 127.0.0.1:8080 only
   ```
5. `shred -u /root/deploy/.env.deploy` once you are done (it holds the tunnel token).
   Re-running bootstrap later needs a fresh copy.

Re-running bootstrap is safe: it changes only what differs, never overwrites
`/etc/kite-mcp/kite.env`, and restarts kite-mcp-server only when its binary or unit
changed (a restart drops your Kite login).

**SSH.** With `ADMIN_SSH_FROM_IP=<your IP>`, ufw allows port 22 from that address only.
With it empty, no port is open at all; reach SSH through the tunnel (Access-protected
hostname route, or WARP), see `cloudflared-hostname.md` step 5. Set that up and test it
**before** running bootstrap with `SSH_VIA_TUNNEL_ONLY=yes`; the Vultr web console is the
break-glass path.

## 4. Cloudflare

Follow `cloudflared-hostname.md`: One-time PIN, the Access app with a Service Auth policy
(service token) and an Allow policy (your email), then the Published application route
`KITE_HOSTNAME` > `http://127.0.0.1:MCP_PORT` with **Protect with Access** on, then the
service token.

Check from the laptop: `curl -sI https://KITE_HOSTNAME/` must answer with a redirect to
Cloudflare Access (not 200), and with the two `CF-Access-Client-*` headers it must reach the
server's status page.

## 5. Claude Code on the laptop

1. Put the service token in a file only you can read, and load it in your shell:
   ```bash
   mkdir -p ~/.config/kite-mcp && chmod 700 ~/.config/kite-mcp
   cat > ~/.config/kite-mcp/cf-access.env <<'EOF'
   export CF_ACCESS_CLIENT_ID=...
   export CF_ACCESS_CLIENT_SECRET=...
   EOF
   chmod 600 ~/.config/kite-mcp/cf-access.env
   echo '. ~/.config/kite-mcp/cf-access.env' >> ~/.zshrc
   ```
2. Add the server from the `claude_code` block of `claude-mcp-config.example.json`, either
   as `.mcp.json` in your trading folder (gitignored here) or with
   `claude mcp add-json kite '<the "kite" object>'`. Claude Code expands `${VAR}` from the
   environment.
3. Keep every order tool on ask, in `.claude/settings.json`:
   ```json
   {"permissions": {"ask": ["mcp__kite__place_order", "mcp__kite__cancel_order", "mcp__kite__modify_order"]}}
   ```
   Never list an order tool under `allow`, never answer an order prompt with "don't ask
   again", and do not run this server in a bypass-permissions mode.

Claude Desktop (mcp-remote, the `claude_desktop` block) is for read-only use until you have
checked, on a test call, that `place_order` and `cancel_order` show an approval card every
time. Never click "Always allow" on them.

## 6. Zerodha IP whitelist (last)

Only now, once the egress check passes:

```bash
sudo /usr/local/sbin/check-egress-ip.sh    # "egress IPv4 <Reserved IP> matches", exit 0
```

developers.kite.trade > Profile > **IP Whitelist** > primary = the Reserved IP > Update.
It is one change per calendar week, so check the address twice.

## 7. Read-only smoke test

In Claude Code, in this order:

1. Browser: open `https://KITE_HOSTNAME/`, pass the email OTP (so `/callback` will pass Access).
2. Claude: call the **`login`** tool; open its link within 5 minutes; log in to Kite with your
   password and TOTP; the page says "Login Successful".
3. Claude: **`get_profile`** (your name) and **`get_margins`** (your funds).
4. VPS: `curl -4 -s ifconfig.me` prints the Reserved IP, and `curl -6 -s --max-time 5 ifconfig.me`
   prints nothing.

## 8. One 1-share MIS test order

This proves the whitelist works for orders. Pick a liquid stock you would not mind holding
for minutes (e.g. one share of a low-priced large cap), during market hours, with the funds
for it. Claude Code must show its approval prompt for each call; deny anything you did not
expect.

1. `place_order`: `variety=regular, exchange=NSE, tradingsymbol=<SYMBOL>, transaction_type=BUY,
   quantity=1, product=MIS, order_type=LIMIT, price=<about 1 % under the last price, a multiple
   of the stock's tick: ₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to ₹5,000>`. Expect an order id. An error naming the IP means the whitelist or the egress
   IP is wrong (see "If the IP changes").
2. `get_orders`: the order is OPEN (not REJECTED).
3. `cancel_order`: `variety=regular, order_id=<id>`. `get_orders` shows CANCELLED.

A LIMIT under the market tests placement without a fill. If you want to see a fill too,
a MARKET buy of 1 share followed at once by a MARKET sell costs brokerage and the spread; MIS
positions left open are squared off by Zerodha from about 15:20.

## Daily routine (every trading day)

The Kite access token expires around 6 AM IST every day. The login is never automated: you type
your password and TOTP into Kite's own page in the browser, every day. Before 9:15:

1. Optional: `ssh` in and check the morning egress result: `sudo journalctl -t kite-egress --since today`.
2. Browser: open `https://KITE_HOSTNAME/` and pass the email OTP.
3. Claude Code: `login` tool > open the link within 5 minutes > Kite password + TOTP.
4. Claude Code: `get_margins` (read only) to confirm the session works.

Log in again after any of these: a `kite-mcp` restart, a VPS reboot, restarting Claude Code
or a lost MCP session (tools answer that you are not logged in).

## If the IP changes

Signs: the 08:30 egress check logs `EGRESS IP MISMATCH` (syslog priority crit, tag
`kite-egress`), or Kite rejects orders mentioning the IP.

1. Do not trade from the VPS until fixed. Read-only tools still work from any IP.
2. Find out why: was the Reserved IP detached, the instance re-created, IPv6 turned back on,
   or WARP / a VPN installed on the VPS (it would change the egress)?
3. Preferred fix: re-attach the **same** Reserved IP as the instance's main IP, so the
   whitelist does not change. `sudo check-egress-ip.sh` must pass again.
4. Only if the old IP is gone for good: update `EXPECTED_STATIC_IP` (re-run bootstrap or edit
   `/etc/kite-mcp/egress.conf`), then change the whitelist at developers.kite.trade. That uses
   this calendar week's one change. Zerodha allows a secondary IP; registering a second
   Reserved IP in advance gives you a spare.

## Upgrading kite-mcp-server

Set `KITE_MCP_VERSION`, `KITE_MCP_SHA256` (the release asset's digest on GitHub) and, for
source builds, `KITE_MCP_COMMIT` in `.env.deploy`, re-read the new version's env variables
and routes, then re-run bootstrap after market hours: it restarts the server, which ends the
day's Kite login.
