---
name: trader-alerts
description: >-
  Price Alerts. Rings when a price, a stop, the target or the opening bell
  hits. Turns the trade plan into three alerts in one go (the stop, the
  target and "flat by close" at your flat-by time, 15:10 IST by default),
  plus above / below / stop / target levels, percent moves from the previous
  close, any time of day, and the NSE opening bell, each ringing once so you
  are not spammed. On Zerodha the take-profit is this alert, not a resting
  order. Knows NSE hours (09:15 to 15:30 IST) and the 2026 NSE trading
  holidays, fetches free delayed Yahoo quotes (RELIANCE.NS) one symbol at a
  time, and writes trader/prices.json in rupees for sizing and the risk
  check. Use when the user says "alert me when", "tell me if RELIANCE hits",
  "watch my stop", "watch my target", "watch my trade", "remind me to square
  off by 3:10", "is the market open", "when does the market open", "get me a
  price", or right after /trader-exit writes a plan.
argument-hint: "[symbol] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when an alert rings, nothing
is bought or sold by this skill.

On Zerodha there are no bracket orders. After the entry fills, only the
protective **SL stop** rests at the broker and does the stopping: it triggers at
the stop and sells no lower than its limit, so a gap through the limit leaves it
triggered but unfilled. The **take-profit is an alert, not a resting order**:
when the target alert rings, the user sells through trader-broker (`flatten`:
cancel the open SL, then a LIMIT sell just under the last price, after
trader-risk and the user's yes). The flat-by alert does the same before
Zerodha's MIS auto square-off, which starts around 15:20.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1163.30
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1185.30
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:10 --label "flat by close"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol M&M --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan symbols
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

`--now "2026-10-29 15:10"` pins the clock (IST); `--offline DIR` reads saved
quote files (`DIR/yahoo-<SYMBOL>.json`) instead of the network. Symbols are
bare NSE trading symbols (RELIANCE, M&M, BAJAJ-AUTO); a trailing `.NS` is
accepted and dropped. Prices are rupees.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes in INR, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the symbols held, for `quote --holdings` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:10") on a trading day | once per trading day |
| `opening_bell` | the NSE is open | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today. `--from-plan`
rounds the plan's stop and target to the plan's NSE tick (`tick_size`). NSE has no early
closes, so a rule with the old `"before_close"` key is refused with a message
saying to remove it.

## Quotes

- Yahoo: `query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS`, an
  unofficial free JSON endpoint, one request per symbol, half a second apart
  (`M&M` goes out as `M%26M.NS`). On a 429 it waits and tries once more; on a
  429 or a 5xx after that it tries `query1`. It identifies itself as
  `nse-day-trader-skill/1.0`.
- Yahoo is the only quote source. When it does not answer, `check` prints the
  error and rings nothing for that symbol; it never guesses a price.
- Free quotes are unofficial, delayed and can lag. They are fine for alerts
  and for checking a size, not for timing an order. The broker's price is the
  one that fills. Each price in `trader/prices.json` keeps its quote time, and
  trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from the Kite connector (`get_ltp` or
  `get_quotes`, read only) and write it into `trader/prices.json` as
  `{"as_of": "...Z", "prices": {"RELIANCE": 1167.40}}`.

## The NSE clock

Regular session 09:15 to 15:30 IST (India Standard Time, UTC+5:30, no daylight
saving), closed on weekends and on the NSE capital-market trading holidays for
2026 published by NSE (nseindia.com, Exchange Communication > Holidays). The
list lives in `trader_common/clock.py`. Add or lift a closure in
`trader/config.json` with `"holidays": {"2026-12-31": "ad-hoc closure"}` (a
date mapped to `null` removes one). For any other year it says the holiday
list is unknown. Special weekend sessions (Muhurat trading) are not modelled.

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the symbols in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang, in one line each:
   - **stop**: the SL at Zerodha has probably triggered. Check `get_orders` /
     `get_positions` (read only). Filled: record it (`ticket.py fill ... --closes <SL order id>`).
     Still open with the price under its limit: the stop did NOT fill, and the
     position is unprotected; say so plainly and offer `flatten` at once.
   - **target**: nothing rests at the target. If the user wants to take it:
     trader-broker `flatten` (cancel the SL, then a LIMIT sell), then
     trader-risk, then the user's yes.
   - **flat by close**: close anything still open the same way, before the MIS
     auto square-off.
   Never act for them, and never place an order from a scheduled task.

## Example: the reel

RELIANCE on Thursday 2026-10-29 (real Yahoo 5-minute bars): the 15-minute
opening range is ₹1,156.20 to ₹1,170.50, the 9:35 bar closes above it, entry
₹1,174.30 at 9:40 AM, stop at the range midpoint ₹1,163.30, target 1R
₹1,185.30, flat by 15:10. `add --from-plan` writes `reliance-plan-stop`,
`reliance-plan-target` and `reliance-flat-by` for that day. From 9:40 to 15:10
the bars stay between ₹1,165.00 and ₹1,182.00, so neither price alert rings.
At 15:10:

```
NSE OPEN (2026-10-29 15:10 IST, regular session).
ALERT Flat by close: it is 15:10 IST (set for 15:10), the market closes at 15:30. If RELIANCE is still
open, close it: trader-broker flatten --symbol RELIANCE builds the sell ticket, then trader-risk and your yes.
```

It rings once. A later check that day says
`Nothing hit. 3 rule(s) watched today, RELIANCE ₹1,167.40.` The next morning
the plan's three alerts are no longer watched; with `add --opening-bell`, the
bell rings at 9:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Free quotes are delayed and unofficial.
- Confirm every new rule back to the user: symbol, type, level in rupees, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Dry run by default (`"dry_run": true`): Zerodha has no paper account, so trader-broker renders and logs
  every ticket and Claude calls no order tool. Going live is the user's own edit of their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one ticket in the chat, and the
  user allowed each Kite order call once on Claude's own approval prompt (the order tools stay on ask).
  Never set an order tool to "Always allow", and never place an order from a scheduled task.
- No F&O, no margin beyond Zerodha's intraday (MIS) product, no short selling.
- Kite keys live only in the MCP server's environment on the user's VPS (deploy/), never in the chat.
