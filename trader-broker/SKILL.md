---
name: trader-broker
description: >-
  Order Executor for Zerodha (Kite). Dry run by default (Zerodha has no paper
  account), never without your yes. Shows each order as a plain two-leg
  ticket: a LIMIT buy, then, once it fills, a protective SL stop for exactly
  the shares that filled; the target is an alert, not an order. Prints the
  exact place_order / cancel_order arguments for kite-mcp-server, records your
  yes word for word, then Claude's own approval prompt asks again for each
  call. Builds the exit ticket (cancel the open SL, then a LIMIT sell) by your
  flat-by time, records fills, and logs each trade's max loss so trader-risk
  can hold the daily limit. Covers the static-IP setup for kite-mcp-server on
  the user's VPS (deploy/), the daily Kite login (Step 0), keys only on the
  VPS and never in chat, and keeping order tools on ask. Use when the user
  says "place the order", "buy it", "execute", "send it to my broker", "close
  my position", "flatten", "get me out", "connect Kite", "log in to Kite",
  "set up the VPS", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the market only after three
separate gates, every time, for every ticket:

1. **trader-risk passed this exact order today** (matched by fingerprint: the
   symbol, qty, limit, product, stop, SL limit and target).
2. **The user typed an explicit yes for this one ticket** in the chat.
3. **The user allowed the call once** on Claude's own approval prompt, which
   appears because the order tools are on ask (Claude Code `permissions.ask`;
   "Needs approval" where a client offers it). Every leg is its own call and
   gets its own prompt.

Two yeses from the user, one PASS from the rules. No yes, no order. And while
`"dry_run": true` (the default), Claude calls no order tool at all.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id 251029000123456
python3 "${CLAUDE_SKILL_DIR}/ticket.py" stop --index 1 --filled-qty 49 --filled-price 1176.90
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --leg stop --status placed --broker-id 251029000123789
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --last 1196.40
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 49 --price 1190.45 --closes 251029000123789
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that ticket, prints each leg's exact arguments, builds the
exit ticket, and keeps the log in `trader/state/broker.json`.
`--now "2026-10-29 10:15"` pins the clock (IST).

## The two legs, and why LIMIT and SL

Zerodha has no bracket order. A ticket is:

| leg | call | when |
| --- | --- | --- |
| 1 entry | `place_order` BUY, `order_type` LIMIT, `product` MIS | after your yes |
| 2 stop | `place_order` SELL, `order_type` SL, `trigger_price` = stop, `price` = SL limit, `quantity` = what filled | after the entry fills, covered by the same yes |
| target | none: a trader-alerts alert | when it rings: an exit ticket |
| exit | `cancel_order` the open SL, then `place_order` SELL LIMIT | after trader-risk and a new yes |

Kite rejects API MARKET and SL-M orders without a market-protection value, and
kite-mcp-server v0.3.2 (the stable release `deploy/` installs) cannot send one.
So the entry is a LIMIT `entry_limit_buffer_pct` (0.3) above the plan's entry,
the stop is an SL whose limit is `stop_limit_buffer_pct` (0.5) under its
trigger, and an exit is a LIMIT `exit_limit_buffer_pct` (0.5) under the last
price. That is market protection under your own control. Its cost, said on
every ticket: if the price gaps below the SL's limit, the SL triggers but does
not fill, and the position is unprotected. The max loss trader-risk holds you
to is measured at the SL's limit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed (each leg), failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `stop` (the entry fill) and `fill` |
| `trader/plan.json` | the flat-by time for `flatten` |
| `trader/config.json` | `dry_run` (default true) and the three buffers |

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
   With the server named `kite`, the tools are `mcp__kite__place_order` and
   `mcp__kite__cancel_order` in Claude Code.
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

0. Step 0 above: the user is logged in to Kite today (`get_margins` answers).
1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket, both legs and the max loss, and ask: "Place this order?
   Reply yes or no." One ticket per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   ticket (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. **Dry run** (it prints "DRY RUN ... Do NOT call"): call no order tool. Tell
   the user what would have been sent. To rehearse the rest, run
   `ticket.py stop --index N --filled-qty <qty> --filled-price <limit>` and
   later `flatten`; every step logs as if it happened, ids `DRYRUN-...`.
5. **Live** (only after "Recorded your yes" and with `"dry_run": false`): call
   `place_order` with exactly the arguments the ticket printed. Claude's prompt
   appears: the user allows it once, or denies it. Then
   `ticket.py result --index N --status placed --broker-id <order_id>`, or
   `--status denied`, or `--status failed --note "<Kite's message>"`. A failed
   order is not retried without a new check and a new yes.
6. Watch the entry fill (`get_order_history <order_id>` or `get_orders`, read
   only). A LIMIT that has not filled within a few minutes has missed: ask the
   user whether to cancel it (`cancel_order`, its own prompt). Never raise the
   limit to chase.
7. **As soon as it fills:** `ticket.py stop --index N --filled-qty Q
   --filled-price P` records the fill and prints leg 2 for exactly Q shares.
   Call `place_order` with those arguments (its own prompt), then
   `ticket.py result --index N --leg stop --status placed --broker-id <id>`.
   Until then the position has no stop: say so, and do it first. If `stop`
   says the price is already under the stop, do not place it: flatten instead.

## Steps: while it is open, and out by flat-by

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). The SL at Zerodha does the stopping.
- When the SL fills, record it: `ticket.py fill --symbol RELIANCE --side sell
  --qty 49 --price 1163.10 --closes <SL order id>`. If the stop alert rang but
  the SL is still open with the price under its limit, it did NOT fill: tell
  the user the position is unprotected and offer the exit at once.
- On the target, or by `flat_by` (15:10 IST; Zerodha squares MIS off from
  about 15:20), close anything still open:
  1. `get_positions` and `get_ltp` (read only): the shares held and the last price.
  2. `ticket.py flatten --symbol RELIANCE --last <ltp> [--qty <held>]` writes
     the exit ticket: cancel the open SL (taken from today's log), then a LIMIT
     sell 0.5% under the last price.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one ticket, as above.
  5. Live: `cancel_order` first (its own prompt). If Kite says the SL already
     completed, do NOT sell: record that fill and stop. Then `place_order`
     with the sell, then `result`. A LIMIT under the market fills at the best
     bid or better; if the price fell through it, it rests: check `get_orders`
     and flatten again with a fresh `--last`.

## Example: the reel (dry run)

RELIANCE, Thursday 2026-10-29, real bars, the example config. trader-budget
sized it at 49 shares and trader-risk passed it at 10:15:

```
ORDER 1 of 1, Zerodha (Kite), DRY RUN: nothing is sent
  BUY 49 RELIANCE, LIMIT 1,177.85, MIS, good for today
  then, once it fills: SELL those shares with an SL stop, trigger 1,163.35, limit 1,157.50
  target 1,196.20: an alert, not an order
  max loss about ₹997.15 if the SL fills at its limit 1,157.50 (₹710.50 at the trigger 1,163.35); a gap below 1,157.50 leaves the SL unfilled
  READY: trader-risk PASS, waiting for your yes
  DRY RUN: do not call. In a live account Claude would call:
    1. place_order with exactly:
       {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
        "quantity": 49, "product": "MIS", "order_type": "LIMIT", "price": 1177.85, "validity": "DAY",
        "tag": "ndt578aff16688f"}
  Leg 2, the SL stop, comes after the entry fills: ticket.py stop --index 1 ...
```

"sure" is not recorded; "yes 63" is not recorded (this ticket is 49 shares);
"yes, 49 RELIANCE" is. In dry run that yes is logged as placed
(`DRYRUN-entry-...`), and `stop --filled-qty 49 --filled-price 1176.90` prints
leg 2 (`order_type` SL, `trigger_price` 1163.35, `price` 1157.5, `quantity` 49).
At 11:40 the target alert rings with the last price 1,196.40; `flatten` writes
"first cancel the open SL stop, order DRYRUN-stop-... / SELL 49 RELIANCE,
LIMIT 1,190.40", trader-risk passes it as an exit, and the user's yes logs it.
Live, each of those calls would have shown Claude's approval prompt first.

## What it never does

- Never calls an order tool in dry run, and never sets `"dry_run": false` itself. The user does that.
- Never places an order without all three gates, and never two tickets on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys, tokens, passwords, TOTPs or request tokens.
- Never raises a limit to chase a fill, never moves a stop further away, never re-sends a failed order.
- Never uses GTT, F&O, margin (beyond MIS's own intraday product) or short selling.
- Never promises a fill price: a LIMIT may not fill, and an SL may trigger without filling.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Dry run by default (`"dry_run": true`): Zerodha has no paper account, so this skill renders and logs
  every ticket and Claude calls no order tool. Going live is the user's own edit of their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one ticket in the chat, and the
  user allowed each call once on Claude's own prompt because the order tools are on ask. Never set an
  order tool to "Always allow", and never add it to a permissions `allow` list.
- SEBI / NSE intraday facts, not advice: MIS is Zerodha's intraday product; its leverage is capped by
  SEBI's peak-margin rules and varies by stock (check Kite's margin calculator; this pack sizes on your
  cash, not on leverage). Zerodha squares off open MIS positions from about 15:20, at the market, with a
  charge. Each stock has a daily price band (circuit limit); at its circuit a stock may not trade, so a
  stop or a sell can sit unfilled. This pack cannot see the band (Yahoo has no band data).
- Broker keys live only on the VPS (`/etc/kite-mcp/kite.env`), the Cloudflare service token only in a
  0600 file on the laptop; neither ever goes in the chat.
