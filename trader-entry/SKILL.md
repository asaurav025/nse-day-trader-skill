---
name: trader-entry
description: >-
  Entry Timer for NSE stocks. Watches a setup bar by bar and tells you the
  moment to get in. Reads free Yahoo 1-minute or 5-minute bars (SYMBOL.NS),
  builds the opening range (the first 15 minutes from 9:15 IST by default)
  and calls the entry on the first completed bar that CLOSES above the range
  high, or above a level you give it (like a 20-day high from the scanner),
  with an optional volume check. Says waiting and how far, triggered with the
  time and the price, expired, or not open yet, and writes trader/entry.json
  for /trader-exit. Long side only. It never places an order. Use when the
  user says "tell me when to get in", "watch RELIANCE", "has it broken out",
  "did it break the opening range", "entry signal", "time my entry", "check my
  watchlist", or after /trader-scanner and /trader-news, before /trader-exit.
argument-hint: "[NSE symbol] [--setup orb|level] [--interval 5m|1m]"
---

# trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. It
reads bars, 1-minute or 5-minute, and waits for each one to finish. A price
that pokes above the high in the middle of a bar is not an entry. A bar that
CLOSES above it is. Then it says the time and the price, writes them down for
the Exit Planner, and stops. It never buys anything. Intraday trading loses
most people money, and a clean signal does not change that.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE              # 15-minute opening range, 5-minute bars
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --write      # also save trader/entry.json
python3 "${CLAUDE_SKILL_DIR}/entry.py" check RELIANCE --setup level --level 1180
python3 "${CLAUDE_SKILL_DIR}/entry.py" check M&M --interval 1m --min-volume-x 1.5
python3 "${CLAUDE_SKILL_DIR}/entry.py" watch RELIANCE --until 14:45  # one line per closed bar, stops on the trigger
python3 "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in trader/watchlist.json
```

Symbols are bare NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`; a trailing
`.NS` is accepted). Other flags: `--or-minutes N` (default 15), `--json`,
`--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`, or a folder
of `<SYMBOL>.json`), `--now "2026-10-29 10:15"` (IST) to replay a day. `watch`
with `--now` or `--offline` runs on a simulated clock and replays the day bar
by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/watchlist.json` | the scanner's hits (`hits[].symbol`), for `watchlist` |
| `trader/config.json` | `no_entries_before` (09:30) and `no_entries_after` (14:45), IST. Optional |
| Yahoo chart API | today's bars, `RELIANCE.NS`, `range=1d&interval=5m` (or `1m`): free, unofficial, can lag |
| `trader/entry.json` | with `check --write`, or when `watch` triggers: symbol, setup, status, time, bar, price, level, opening range |

## The rule

- **Opening range (orb).** The high and low of the first 15 minutes: the
  9:15, 9:20 and 9:25 five-minute bars, or fifteen 1-minute bars. The level to
  beat is the range high.
- **Level.** A price you give, like a 20-day high from `/trader-scanner`.
- **The trigger** is the first completed bar after the range that CLOSES
  above the level. A wick above is not a trigger. A close exactly at the
  level is not above it.
- **The time** is that bar's close time (the 9:35 bar closes at 9:40 AM). The
  price is its close.
- **Only closed bars count.** A bar is closed when the clock passes its start
  plus 5 (or 1) minutes and Yahoo has caught up to that moment (it waits a
  minute at most). Yahoo's live tick, like 10:49:45, is never a bar.
- **Volume, if you want it.** `--min-volume-x 1.5` also needs the trigger
  bar's volume at 1.5 times the range bars' average. Yahoo sometimes reports
  NSE's 9:15 bar with volume 0 (in multi-day downloads); a range bar with no
  volume is left out of the average rather than counted as 0.
- **Time limits.** No entries before 09:30 or after 14:45 (config): MIS
  positions must be flat by 15:10, and Zerodha squares them off from about
  15:20. NSE has no early closes.
- **What it says.** Waiting (and how far the last close is from the level),
  triggered, expired, or no data. Before 9:15: not open yet. Before 9:30: the
  range is still forming. Weekends and NSE holidays: the market is closed (the
  2026 list is built in; `"holidays"` in trader/config.json adds or removes a
  date).

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `/trader-scanner`. Check `/trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line:
   waiting and how far, triggered at what time and price, or expired.
3. To wait for it, Claude can run `entry.py watch SYMBOL --until 14:45` in
   the background, or `entry.py check SYMBOL --write` on a `/loop` every 5
   minutes. `watch` wakes just after each bar closes, prints one line per
   bar, and exits 0 on the trigger (it writes `trader/entry.json`) or 1 when
   the setup expires. Keep the computer awake while it watches: a sleeping
   laptop pauses it, and it catches up, late, when it wakes.
4. When it triggers, say the time and the price, and that nothing was placed.
   If the trigger is more than a bar old, say that too: a late entry is a
   different trade.
5. Hand off to `/trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Example: the reel

RELIANCE, Thursday 2026-10-29, 5-minute bars (real Yahoo data). The opening
range is 1,156.20 to 1,170.50. At 9:37 the 9:35 bar is still open, so it waits:

```
WAITING  last close ₹1,169.50 (9:30 bar), ₹1.00 under the range high ₹1,170.50 (-0.09%).
The 9:35 bar is still forming (₹1,174.30 so far, closes 9:40 AM); it does not count until it closes.
```

At 9:40 the bar has closed:

```
RELIANCE  Thu 2026-10-29 09:40 IST  opening-range breakout (15-minute range) on 5-minute bars (Yahoo, free, can lag)
Opening range 9:15-9:30: high ₹1,170.50, low ₹1,156.20 (3 of 3 bars).
TRIGGERED 9:40 AM at ₹1,174.30: the 9:35 bar (bar 4) closed above the range high ₹1,170.50 (+0.32%).
This is the moment to get in, if you take the trade. Nothing was placed.
Next: set the stop and target BEFORE you buy: /trader-exit (exit.py plan --from trader/entry.json).
```

With `--min-volume-x 1` the 9:35 bar is too thin (0.74x the range's average
volume of 368,006) and the trigger moves to 10:00 AM at 1,173.80 (the 9:55
bar, 2.26x).

## Rules

- Bar by bar, on free Yahoo 1-minute or 5-minute bars. They are unofficial
  and can lag. Say so when a minute matters.
- A signal is information, not an order, and not a promise. Nothing here
  promises winning trades or money.
- Long side only (intraday shorts are out of scope). If the user wants a
  short, say that.
- One trigger per setup per day: the first close above. Later closes do not
  reset it.
- Never call an entry early because a bar looks like it will close above.

## What it never does

It never places an order, never sizes a position, never sets the stop (that
is `/trader-exit`), never moves the level after the fact, and never counts a
bar that has not closed. It never runs `/trader-broker` from a background
watch: nobody is there to say yes.

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
