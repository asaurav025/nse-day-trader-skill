---
name: trader-scanner
description: >-
  Chart Scanner for the NSE. Scans every NSE equity for setups:
  opening-range breakouts, flags, oversold bounces. Builds the universe from
  NSE's own list of equities (EQUITY_L.csv, series EQ), runs a pre-market
  scan of daily closes (20-day closing-high breakout, bull flag, reclaimed the
  20-day average, oversold, oversold bounce) and an intraday opening-range
  breakout scan on 5-minute bars, then writes trader/watchlist.json for
  trader-news and trader-entry. Free Yahoo data (SYMBOL.NS), bar by bar, IST.
  Use when the user says "scan the market", "find me setups", "what is
  breaking out", "opening range breakouts", "scan for bull flags", "oversold
  stocks", "pre-market scan", "build my watchlist", "what should I look at
  today", or at the start of a trading day before /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most individual intraday traders lose
money; the scanner does not change that. It only saves you from flipping
through 2,000 charts by hand.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                          # -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                             # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,M&M  # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                               # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                    # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Flags: `--min-price 50` (rupees) and `--min-volume` (daily 5,00,000 shares,
orb 0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-29 10:15"` (IST) to pin the clock, `--offline PATH` to read
saved files, `--no-write`, `--show N`, `--json`. Exit code 0 when the scan
found something, 1 when it found nothing or could not run (the reason is one
plain line). Symbols are bare NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`);
`.NS` is added only when Yahoo is asked.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily`, `orb` and trader-news |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:45); `"holidays"` adds or removes NSE holidays |

## The universe

`universe` downloads NSE's `EQUITY_L.csv`
(nsearchives.nseindia.com/content/equities/EQUITY_L.csv, with browser-like
headers: NSE turns away anything else) and keeps a row when its series is
`EQ` (normal rolling settlement, intraday allowed) and its symbol is a plain
NSE trading symbol (capitals and digits, `&` and `-` inside). `BE` / `BZ`
(trade-for-trade), SME and every other series are out. On 2026-10-30 the file
had 2,620 rows and this kept 2,357 (230 `BE` and 33 `BZ` dropped).
`--offline FILE` reads a saved copy.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 2,357
symbols take 118 requests. Liquidity comes from the spark meta: last price at
least `--min-price` and last session's volume at least `--min-volume`. Run it
before 9:15 or after 15:30: during the session the volume is the day's so far,
and today's daily bar is left out until the close.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

Hits are listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar to
CLOSE above the range high, at that bar's close time (the first possible one
is the 9:30 bar, at 9:35 AM).

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range (a necessary condition, so no
   breakout is lost). Yahoo's spark answer for NSE symbols drops some real
   bars that the full chart has (14 of 20 large caps lost one or more on
   2026-10-29, with no pattern), so a spark chart missing up to 10 % of its
   slots is passed to stage 2 instead of being called unusable.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each, and here every bar from 9:15 to the cutoff must be there.
   Range high = the highest high of the range bars, range low = the lowest
   low. On a strong morning most charts are candidates, so stage 2 checks at
   most `--max-confirm` (200) of them, the most traded first (price times
   volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

A missing bar in the v8 chart means a halt or a thin stock. Null closes are
dropped, a bar off the 5-minute grid (the live tick, like 10:58:07) is
dropped, and a bar counts only once it is complete (start + 5 minutes). Before
9:35 AM, on a weekend or NSE holiday, after the close, or after
`no_entries_after` (an expired setup) it prints one plain line and writes
nothing.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial. Never round a partial scan up to "the
  whole market".
- NSE's 2026 trading holidays (NSE's holiday master, fetched 2026-10-30) are
  built in; `"holidays"` in trader/config.json adds or removes a date.

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:15 is the classic): `scan.py orb`. Tell the user how
   many usable charts broke their range and when, and that a later hit is
   closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news SYMBOL` (what is moving
   it), then `/trader-entry SYMBOL` (the entry bar by bar). Never pick for them.

## Example: the reel

Thursday 2026-10-29, `scan.py orb --at 10:15` over the whole universe (real
Yahoo data):

```
Opening-range breakout scan, Thu 2026-10-29 10:15 AM IST: 15-minute range (9:15 to 9:30 AM), 5-minute bars, Yahoo.
Fetched 2,357 of 2,357 charts (1 failed or had no data); 1,472 usable (188 under the price / volume floor); 1,074 candidates, 199 confirmed with real highs and lows.
171 of the candidates were sent to stage 2 because Yahoo's spark answer was missing a bar or two (the v8 chart decides).
PARTIAL: 874 more candidates were not checked (only the 200 most traded are; --max-confirm 0 checks all).
124 of the 199 checked charts closed above their opening-range high by 10:15 AM:
   9:35 AM  ABB           ₹6,510.00  range 6,441.50 to 6,493.00  +0.26% over
  ...
   9:35 AM  HDFCBANK        ₹682.50  range 678.30 to 682.45  +0.01% over
  ...
   9:40 AM  RELIANCE      ₹1,174.30  range 1,156.20 to 1,170.50  +0.32% over
```

RELIANCE's range is the 9:15 to 9:25 bars, high 1,170.50, low 1,156.20; the
9:35 bar closed at 1,174.30, 0.32 % over the high, at 9:40 AM. The user picks
RELIANCE and Claude hands it to `/trader-news RELIANCE`, then
`/trader-entry RELIANCE`.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

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
