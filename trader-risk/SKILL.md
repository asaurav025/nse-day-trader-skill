---
name: trader-risk
description: >-
  Risk Manager. Checks every trade against your limits (in rupees) and stops
  anything over them, before it can reach the broker. For an intraday trade:
  a stop is required, the loss at the SL's limit must fit your max loss per trade
  (measured from the higher of the plan's entry and a fresh price), reward:risk
  must meet your minimum, the day's total risk must fit your daily loss limit,
  no more than your trades per day, and no new entries outside your entry
  window (IST) or when the NSE is closed. It also keeps the basics: blocked
  symbols, order size, position cap, cash buffer, margin, short sales, F&O,
  whole shares and the stock's NSE tick, malformed orders, duplicates and a daily
  order limit. Getting out is never blocked by a day limit. Prints PASS or
  BLOCK with the reason and records the verdicts so trader-broker only shows
  an order that passed. Use when the user says "check my order", "is this
  safe", "risk check", "does this break my rules", "can I take this trade",
  "how much have I risked today", or before /trader-broker, always.
argument-hint: "[path to orders.json]"
---

# trader-risk

The Risk Manager is the gate between a proposal and the broker. Every order
written by trader-budget or trader-broker's flatten passes through it. A
BLOCK is final for that order: it does not go to the user for a yes, and it
does not go to the broker. Most people who trade intraday lose money. These
limits keep each loss and each day's losses small. They do not make a trade win.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/check.py"                           # trader/ files; writes trader/state/risk.json
python3 "${CLAUDE_SKILL_DIR}/check.py" --json
python3 "${CLAUDE_SKILL_DIR}/check.py" --no-write                # look without recording
python3 "${CLAUDE_SKILL_DIR}/check.py" --now "2026-10-29 10:15"  # pin the clock (IST)
```

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed orders (trader-budget, or trader-broker's flatten) |
| `trader/config.json` | the limits, in rupees (trader-budget ships `config.example.json`) |
| `trader/portfolio.json` | cash and holdings; if missing, a fresh account funded with the budget |
| `trader/prices.json` | a fresh price raises the entry it measures risk from; also values holdings |
| `trader/state/broker.json` | what was placed today: duplicates, trades per day, daily loss |
| `trader/state/risk.json` | the verdicts it writes, with a fingerprint of each exact order |

## Entries and exits

An **entry** opens or adds to a position: a buy, or a sell that would go
short. An **exit** only reduces what you already hold. Getting out must
always be possible, so an exit is checked for format, asset and short sales
only. Every other rule (day limits, entry window, loss, reward:risk, blocked
list, size, duplicates) turns into a warning for an exit, never a BLOCK.

## The checks

Orders are checked in file order, as if each passed order had filled, so a
batch that is fine order by order but breaks a limit together is caught.

| check | blocks when |
| --- | --- |
| format | no symbol, side not buy or sell, a qty that is not whole shares, an `order_type` other than LIMIT (Kite takes no API MARKET or SL-M order without market protection, which kite-mcp-server v0.3.2 cannot send), no limit `price`, a `tick_size` NSE does not use, a price / `stop` / `stop_limit` / `target` off the stock's tick, a product other than MIS (or CNC) |
| asset | an F&O contract (`NIFTY26OCTFUT`, `...24500CE`): out of scope; anything that is not an NSE equity symbol (`RELIANCE`, `M&M`, `BAJAJ-AUTO`; `.NS` is accepted and dropped) |
| short | a sell of more than you hold (`allow_short` false) |
| blocked | (entries) the symbol is on the `blocked` list |
| size | (entries) under `min_order_inr` or over `max_order_inr` |
| position | (entries) the stock would be over `max_position_pct` of the account |
| cash | (entries) the buy would leave less than the cash buffer, or need margin |
| stop | (entries) no `stop` (the SL leg's trigger) while `require_stop` is true, no `stop_limit` for the SL, a stop on the wrong side of the limit, or an SL limit not under its trigger |
| loss | (entries) qty x (limit price minus SL limit) over `max_loss_per_trade_inr`: the worst entry fill and the worst stop fill |
| reward:risk | (entries) (target minus limit) / (limit minus stop) under `min_reward_risk` |
| daily loss | (entries) the max losses of today's entries (dry-run rehearsals included), plus this one, over `max_daily_loss_inr` |
| trades | (entries) `max_trades_per_day` entries already placed today |
| window | (entries) before `no_entries_before` or after `no_entries_after`, or the NSE is closed |
| duplicate | (entries) the same symbol and side twice in one batch, or already placed today |
| daily limit | (entries) `max_orders_per_day` orders already placed today |

How the numbers work:

- **Rupees.** Every limit key ends in `_inr`. A config that still has a
  `_usd` key is refused, with the new name, rather than read as rupees.
  Old keys that no longer mean anything on the NSE (`pdt_guard`, `fractional`,
  `allow_options`) are ignored with a warning.
- **The tick.** NSE's tick goes by price band (₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to ₹5,000, ₹0.50 to ₹10,000, ₹1 to ₹20,000, ₹5 above).
  Each price in the order (limit, stop, SL limit, target) must be a whole number
  of the order's `tick_size` (a `tick_sizes` entry in trader/config.json wins;
  without either, the coarsest tick near the price); off the tick is a format
  BLOCK, because the exchange would reject it.
- **The entry it measures from**: the limit price for a limit order. For a
  market order, the HIGHER of the plan's entry and a fresh price from
  `trader/prices.json` (under 15 minutes old). A plan written at 9:40 cannot
  understate the risk at 10:55 after the price ran up. An older price is not
  used, and it says so.
- **Daily loss** counts the full max loss of every entry placed today, win or
  lose, as trader-broker logged it. Three trades that each risk ₹1,000 use up
  a ₹3,000 day.
- **The window** is IST (UTC+5:30, no daylight saving), worked out in the
  tool, never from the computer's clock zone. Weekends and NSE trading holidays
  (the 2026 list, plus any `"holidays"` in `trader/config.json`) are closed.
  Set both `no_entries_*` to null to switch the window off.
- **Not checked: circuit limits.** A stock's daily price band is not in
  Yahoo's free data, so this tool cannot see it. A stop or target beyond the
  band may not fill while the stock is locked at its circuit; check the band on
  the broker's quote for anything that has moved a lot today.

Warnings do not block, but say them to the user: a LIVE account, a number
sent as a number instead of a string, a stop that lives only in the plan and
not at the broker, a price too old to use, an ignored config key.

## Steps

1. If the price may have moved since the plan, refresh it first
   (trader-alerts: `alerts.py quote RELIANCE --write`).
2. Run `check.py`. Exit code 0 means every order passed, 1 means at least one
   was blocked.
3. Tell the user the result in plain words, order by order: the max loss at
   the stop, reward:risk, and how much of today's limits it uses. For a
   BLOCK, give the reason and the fix (fewer shares, a closer stop from
   trader-exit, wait for the window, or skip the trade). Never change a
   limit yourself.
4. If the order changes in any way, run the check again. trader-broker matches
   each order to its verdict by fingerprint, so an edited order is not ready
   until it passes again, and a verdict from yesterday does not count today.

## Example: the reel

The RELIANCE order from trader-budget at 10:15 AM on Thursday 2026-10-29, on a
fresh ₹1,00,000 account with the example config (real 2026-10-29 bars; plan
from trader-exit: entry 1,174.30, opening-range-mid stop 1,163.30, 2R target 1,196.30, tick ₹0.10):

```
PASS  #1 buy RELIANCE 48 shares, entry
          limit 1177.90, SL trigger 1163.30 limit 1157.40, target 1196.30: risk 20.50 a share to the SL limit
          max loss ₹984 (limit ₹1,000), reward:risk 1.26 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss

1 passed, 0 blocked at 2026-10-29 10:15 IST. Cash after the passed orders: ₹43,460.80 (dry run: nothing goes to Zerodha).
```

The risk is measured from the LIMIT (the worst entry fill) to the SL's limit
(the worst stop fill): 1,177.90 - 1,157.40 = 20.50 a share. One more share and
it is blocked:

```
BLOCK #1 buy RELIANCE 49 shares, entry
          limit 1177.90, SL trigger 1163.30 limit 1157.40, target 1196.30: risk 20.50 a share to the SL limit
          max loss ₹1,004.50 (limit ₹1,000), reward:risk 1.26 (min 1)
          today before this: 0 of 3 trades, ₹0 of ₹3,000 daily loss
        - loss: 49 shares x 20.50 risk a share = ₹1,004.50 if the SL fills at 1157.40, over your ₹1,000 max loss per trade
```

A MARKET order is blocked by format (Kite would reject it without market
protection). At 9:20 the order is blocked by the window (no new entries before
09:30). The 15:10 exit that closes the position passes, even though it is after
the 14:45 entry cutoff: it is an exit.

## Rules

- A PASS only means the order is allowed to be shown to the user. It is not a yes.
- Every number comes from the config, the order, the portfolio, the prices or the broker log.
- Free Yahoo prices are unofficial and can lag; the broker's price is the one that fills, and a
  stop can fill worse than its price in a fast market.

## What it never does

It never places an order, never edits an order to make it pass, never
loosens a limit, never blocks a sell that only gets the user out, and never
treats a PASS as the user's yes.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Dry run by default (`"dry_run": true`): Zerodha has no paper account, so trader-broker renders and logs
  every ticket and Claude calls no order tool. Going live is the user's own edit of their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No F&O, ever (out of scope). No margin, short selling or leverage unless the config allows it and the
  user confirms.
- Broker keys live on the broker's server (see trader-broker), never in the chat.
