---
name: trader-broker
description: >-
  Order Executor. Sends approved orders to your broker, paper trading by
  default, never without your yes. Shows each order as a plain ticket (a
  bracket shows its stop-loss, take-profit and the max loss at the stop)
  plus the exact JSON for Alpaca's place_stock_order, records your yes word
  for word, then Claude's own Allow once / Deny card asks again. Builds the
  sell ticket that closes a day trade by your flat-by time, records fills,
  and logs each trade's max loss so trader-risk can hold the daily limit.
  Covers the static-IP setup for Zerodha's kite-mcp-server on the user's VPS
  (deploy/), the daily Kite login (Step 0), keys only on the VPS and never in
  chat, and keeping order tools on ask / "Needs approval". Use when the user
  says "place the order", "buy it", "execute", "send it to my broker",
  "close my position", "flatten", "get me out", "connect Kite", "log in to
  Kite", "set up the VPS", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the market only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the order tool is set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 8 --filled-price 364.20
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol TSLA --side sell --qty 8 --price 370.04
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol TSLA --qty 8
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-10-19 10:15"` pins the clock (New York time).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Setup: Zerodha Kite through your static-IP VPS

Since 1 April 2026 Zerodha rejects Kite Connect order requests that do not come from a
static IP whitelisted on the developer account. Orders therefore go through Zerodha's own
MCP server (github.com/zerodha/kite-mcp-server, v0.3.2) running on the user's VPS, whose
Reserved IP is whitelisted, reachable only through Cloudflare Tunnel + Access. The full
runbook and every file are in `deploy/` at the repo root (`deploy/README.md`); in short:

1. Vultr VPS in Mumbai with a Reserved IPv4 as its **main** IP, IPv6 off.
2. Kite Connect app with redirect URL `https://<your hostname>/callback`.
3. `deploy/bootstrap.sh` on the VPS: ufw with no inbound ports, cloudflared, kite-mcp-server
   on 127.0.0.1 only. The Kite API key and secret are typed by the user into
   `/etc/kite-mcp/kite.env` on the VPS, nowhere else.
4. Cloudflare: Access app (email one-time PIN for the user's browser, a service token for
   Claude Code), then the tunnel route to `http://127.0.0.1:8080`.
5. Claude Code connects to `https://<your hostname>/mcp` with the service-token headers from
   environment variables (`deploy/claude-mcp-config.example.json`), and keeps every order
   tool on ask in `.claude/settings.json`:
   `"permissions": {"ask": ["mcp__kite__place_order", "mcp__kite__cancel_order", "mcp__kite__modify_order"]}`.
   Never put an order tool in `allow`. Claude Desktop (via mcp-remote) is read-only until the
   user has checked that `place_order` shows an approval card on every call.
6. Only after `check-egress-ip.sh` passes: whitelist the Reserved IP at developers.kite.trade >
   Profile > IP Whitelist (one change per calendar week).

The server hides `place_gtt_order`, `modify_gtt_order`, `delete_gtt_order` and `modify_order`
(`EXCLUDED_TOOLS`): this pack never uses them.

**Keys never go in the chat.** Never ask for the Kite API key or secret, the Cloudflare
service token or the tunnel token; never read `kite.env`, `.env.deploy` or
`cf-access.env` into the conversation. If the user pastes one anyway, tell them to delete
that message and rotate it (Kite: developers.kite.trade; Cloudflare: Zero Trust > Service
Tokens).

## Step 0, every trading day: log in to Kite

The Kite session expires around 6 AM IST every day, and the server keeps it only in memory.
Before anything that reads the account or places an order:

1. The user opens `https://<your hostname>/` in their browser and passes the Cloudflare
   email one-time PIN (so Kite's redirect back to `/callback` is let through).
2. Call the MCP **`login`** tool. Show its warning and its link. The user opens the link
   within 5 minutes and logs in on Kite's own page with their password and TOTP.
   Never type, ask for or relay a password, TOTP or `request_token`.
3. Call **`get_margins`** (read only) to confirm the session.

Repeat after a server restart, a VPS reboot, a Claude Code restart, or when a tool answers
that the user is not logged in. The login is never automated and never done from a
scheduled task.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Place this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Only after "Recorded your yes", call `place_stock_order` with exactly the
   JSON the ticket printed. Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <id> --filled-qty Q --filled-price P` (the fill from the
   response or `get_open_position`), or `--status denied`, or `--status failed`
   with `--note`. A failed order is not retried without a new check and a new yes.
   The log keeps the entry's max loss for trader-risk's daily limit.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). The bracket's own orders do the stopping.
- When the stop-loss or the take-profit fills, record it:
  `ticket.py fill --symbol TSLA --side sell --qty 8 --price 358.32`.
- By `flat_by` (15:55 New York), anything still open gets closed:
  1. Ask Alpaca how many shares are held: `get_open_position` (read only).
  2. `ticket.py flatten --symbol TSLA --qty 8` writes a market sell for those
     shares to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. The bracket's stop-loss and take-profit still hold those shares, so
     Alpaca rejects a new sell until they are canceled: `get_orders` (status
     open, nested true) shows them; `cancel_order_by_id` each one (Needs
     approval). Then `place_stock_order` with the ticket, then `result`.

## Example: the reel

```
ORDER 1 of 1, PAPER account, Alpaca
  BUY 8 TSLA, market, good for today
  stop-loss 358.32
  take-profit 370.04
  max loss about $46.88 if the stop fills at 358.32 (it can fill worse in a fast market)
  why: opening-range breakout on TSLA: 8 shares, bound by your $50 max loss per trade (8 x 5.86 = $46.88 at the stop)
  READY: trader-risk PASS, waiting for your yes
  Claude will call place_stock_order with exactly:
    {"symbol": "TSLA", "side": "buy", "qty": "8", "type": "market", "time_in_force": "day",
     "order_class": "bracket", "take_profit_limit_price": "370.04", "stop_loss_stop_price": "358.32"}
```

The user replies "yes". Claude records it, calls `place_stock_order`, and the
card reads "Claude wants to use Place stock order" with that JSON and two
buttons: Allow once, Deny. Allow once sends it to the paper account. The
`why` and the plan stay in the file; they are never sent.

## What it never does

- Never places an order without all three gates, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys or passwords.
- Never switches `ALPACA_PAPER_TRADE` or `"paper"` to live. The user does that themselves.
- Never calls `close_all_positions`, `cancel_all_orders`, option or crypto order tools on its own.
- Never promises a fill price: a market order and a stop fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
