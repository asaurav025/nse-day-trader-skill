# Cloudflare: route, Access app, service token

Dashboard steps for your existing tunnel. Menu names are from Cloudflare's docs as of
October 2026 ("public hostname" is now a **Published application** route). Placeholders:
`KITE_HOSTNAME` (e.g. `kite.example.in`), `MCP_PORT` (8080).

Do the Access application (step 2) **before** the route (step 3). Cloudflare's own docs
warn that a published application without one "will be available to anyone on the
Internet", and this one can place orders.

## 1. One-time PIN login method

Zero Trust > **Integrations > Identity providers** > **Add new** > **One-time PIN**.
Cloudflare emails a PIN, valid for 10 minutes, and only to addresses an Allow policy matches.

## 2. Access application on KITE_HOSTNAME

Zero Trust > **Access controls > Applications** > **Create new application** >
**Self-hosted and private** > **Add public hostname** > `KITE_HOSTNAME`, all paths.

- **Session duration:** 24 hours. The daily routine opens `https://KITE_HOSTNAME/` once
  in the morning to get the cookie; the Kite login callback later that morning needs it.
- **Login methods:** One-time PIN only.
- **Policies** (Access is deny by default; Service Auth policies are evaluated before Allow):
  1. `mcp-client`: action **Service Auth**, include **Service Token** = `kite-mcp-client`
     (created in step 4). This is how Claude Code gets in without a browser.
  2. `owner`: action **Allow**, include **Emails** = your address only. This is how your
     browser reaches `/authorize` and `/callback` during the Kite login.
- No Bypass policy. Cloudflare discourages Bypass, and the morning OTP makes it unnecessary.

## 3. Published application route

Zero Trust > **Networking > Tunnels** > your tunnel > **Routes** tab > **Add route** >
**Published application**:

- Subdomain + Domain: `KITE_HOSTNAME`
- **Service URL:** `http://127.0.0.1:MCP_PORT`. Use `127.0.0.1`, not `localhost`:
  kite-mcp-server listens on IPv4 loopback only, and `localhost` can resolve to `::1`.
- Under the route's additional (origin) settings, turn on **Protect with Access** for this
  application, so cloudflared itself checks the Access token before forwarding. The server
  has no authentication of its own.

Leave the hostname's DNS record proxied (orange cloud). The tunnel creates it.

## 4. Service token for the MCP client

Zero Trust > **Access controls > Service credentials > Service Tokens** > **Create**:
name `kite-mcp-client`, duration 1 year (put the expiry in your calendar).

Copy the **Client ID** and **Client Secret** at once; the secret is shown only once. They go
in a file on your laptop that only you can read (see `README.md`, "Claude Code"), never in
this repo or a chat. Requests send them as the headers `CF-Access-Client-Id` and
`CF-Access-Client-Secret`.

## 5. Optional: SSH without an open port

When `ADMIN_SSH_FROM_IP` is empty, port 22 is closed to the internet. Reach SSH through the
same tunnel instead, set up **before** you run bootstrap with `SSH_VIA_TUNNEL_ONLY=yes`:

- **Hostname route:** another Published application route on the tunnel, e.g.
  `ssh.example.in` > Service URL `ssh://127.0.0.1:22`, with its own Access application
  (One-time PIN, Allow your email). On the laptop, in `~/.ssh/config`:

  ```
  Host kite-vps
      HostName ssh.example.in
      User trader
      ProxyCommand cloudflared access ssh --hostname %h
  ```

- **WARP:** add a private network route on the tunnel for the VPS (its IP/32), enrol your
  laptop in the WARP client, and allow it in a Gateway network policy. Then `ssh` to that
  IP over WARP. Test it before closing port 22.

Keep the Vultr web console as the break-glass path either way.

## 6. Streaming caveats

MCP over HTTP answers with `text/event-stream`, which Cloudflare passes through unbuffered.
An idle stream can be closed after Cloudflare's 125-second proxy read timeout (only
Enterprise plans can raise it). Whether a reconnect keeps the same MCP session depends on
the client; a new MCP session has no Kite login. If Claude reports session or login errors,
call the `login` tool again.
