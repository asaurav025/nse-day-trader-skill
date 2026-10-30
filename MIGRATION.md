# Migration: from the US Day Trader skill to NSE / Zerodha

This repo is a port of
[jakeschincariol/day-trader-skill](https://github.com/jakeschincariol/day-trader-skill)
(11 skills, US stocks, Alpaca / IBKR, New York time) to the National Stock
Exchange of India and Zerodha's Kite (9 skills, IST, rupees). This file lists
what changed, what was dropped and why, the decisions taken on the way, what
is still open, and how to run the worked example end to end in dry run.

Everything below was checked against real data between 2026-10-29 and
2026-10-30 (Yahoo `.NS` charts, NSE's equity list and holiday master, Kite
Connect docs, the zerodha/kite-mcp-server source).

## At a glance

| | upstream | this port |
| --- | --- | --- |
| market | NYSE / Nasdaq, 9:30 to 16:00 New York, DST | NSE cash equities, 9:15 to 15:30 IST (UTC+5:30, no DST) |
| universe | Nasdaq Trader symbol directories | NSE `EQUITY_L.csv`, series EQ (2,357 symbols on 2026-10-30) |
| symbols | `TSLA`, `BRK.B` | bare NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`); `SYMBOL.NS` only towards Yahoo |
| money | `$`, cents | `₹` with Indian grouping (₹1,00,000), NSE price-band ticks |
| limits | `*_usd` | `*_inr` (old keys are refused by name) |
| last entry / flat-by | 15:30 / 15:55 | 14:45 / 15:10 (Zerodha squares MIS off from about 15:20) |
| broker | Alpaca MCP (paper) or IBKR connector, bracket orders | Zerodha kite-mcp-server v0.3.2 on your static-IP VPS; two legs: LIMIT entry, then SL stop |
| paper trading | Alpaca paper account | none at Zerodha: `"dry_run": true` (default) logs and sends nothing |
| news | Yahoo RSS + SEC 8-K | Yahoo RSS (`.NS`) + Google News RSS by company name |
| PDT | `pdt_guard` (FINRA) | removed |
| skills | 11 | 9 (analyst and pelosi dropped) |
| tests | 315, US fixtures | 370, real NSE fixtures |

## Decisions taken with the repo owner

Each of these came up because NSE data, Kite or the MCP server did not behave
the way the original plan assumed. They were asked, not guessed.

1. **Yahoo's spark endpoint drops real NSE bars** (14 of 20 large caps lost at
   least one 5-minute bar on 2026-10-29; the v8 chart had them). The scanner's
   stage 1 now lets a chart missing up to 10 % of its slots through to stage 2,
   where the v8 chart must still be complete. Offline replays stay strict.
2. **Yahoo reports NSE's 9:15 bar with volume 0** in multi-day downloads. The
   entry timer's `--min-volume-x` average leaves zero-volume range bars out.
3. **Last new entry 14:45** (25 minutes before the 15:10 flat-by, the same gap
   upstream kept between 15:30 and 15:55).
4. **The 15:20 bar is always null on Yahoo** (15:15 sometimes, 15:25 often), so
   no NSE session was ever "full" to 15:30 and the time machine could not run.
   A session now counts as full to `flat_by` (15:10, 71 bars) and replays end there.
5. **Orders go through a self-hosted kite-mcp-server** on a Vultr VPS in Mumbai
   whose Reserved IP (its main IP, checked by `check-egress-ip.sh`) is
   whitelisted with Zerodha, exposed only through Cloudflare Tunnel + Access.
   Each morning: Access OTP in the browser, then the MCP `login` tool. Claude
   Code is the client for orders; Claude Desktop is read-only until its approval
   card is verified. See `deploy/README.md`.
6. **LIMIT and SL, not MARKET and SL-M.** Kite rejects API MARKET and SL-M orders
   without a `market_protection` value, and kite-mcp-server v0.3.2 (the stable
   release) cannot send one (its `place_order` schema and gokiteconnect v4.3.5
   have no such field; the v0.4.0-dev pre-releases add it). The entry is a LIMIT
   0.3 % above the trigger and the stop an SL whose limit is 0.5 % under its
   trigger: market protection under the user's control. The cost: a gap below
   the SL's limit leaves it triggered but unfilled. The max loss trader-risk
   holds you to is measured at the SL's limit, from the LIMIT entry.
7. **NSE's tick is not a fixed ₹0.05.** Since April 2025 it goes by price band
   (₹0.01 under ₹250, ₹0.05 to ₹1,000, ₹0.10 to ₹5,000, ₹0.50 to ₹10,000, ₹1 to
   ₹20,000, ₹5 above), set monthly from the previous month-end close. The tools
   use that close (Yahoo daily bars), a per-symbol `tick_sizes` override, or the
   coarsest tick within 20 % of the price (always valid: the ticks are nested).
   The fixed ₹0.05 would have made Kite reject the RELIANCE example's orders.
8. **Yahoo's headline RSS has no headlines for most NSE stocks** (7 of 20 large
   caps, none for RELIANCE). trader-news adds a Google News RSS search by the
   company's NSE name and says which headlines actually name the stock.
9. **The yes check missed NSE symbols.** `parse_yes` let "yes HDFCBANK",
   "yes BAJAJ-AUTO" and "yes M&M" through as a yes for a RELIANCE ticket (its
   symbol pattern was built for short US tickers), and read the 20 in
   "yes 20MICRONS" as an amount. Two lines changed (the symbol pattern, and
   the ticket's own symbol is taken out before amounts are read); the rest of
   `parse_yes` is upstream's, line for line.

Two more calls made on the way, both conservative, both easy to change:

- **One yes per ticket covers its legs** (as upstream's one yes covered the
  bracket's three). Each leg is still its own call with its own approval prompt,
  and the SL leg's arguments are fixed when you say yes (only the filled qty is
  filled in).
- **Dry run logs a simulated placement** (`DRYRUN-...` ids), so daily limits,
  the SL leg and flatten rehearse exactly as they would live.

## Fixes from the independent code review of PR #1

A reviewer with no part in the port read the PR and found ten problems; all
were fixed with a regression test each.

1. Re-running `ticket.py stop` after a failed SL booked the entry fill twice
   (96 shares on paper for 48 held, and flatten would then sell 96). The entry
   fill is now recorded once per ticket.
2. A second `flatten` did not cancel an earlier exit sell still resting, so two
   sells could both fill. An exit ticket now cancels every order still resting
   for the symbol (`cancel_order_ids`).
3. When the exit's cancel went through but its sell did not, the cancelled SL
   still looked open. `result` now records which cancels went through
   (`--cancelled`, required when the sell was not placed) and says plainly when
   the position has no stop and no exit.
4. Dry-run fills were written to the real `trader/portfolio.json`. They now go
   to `trader/portfolio.dryrun.json`, read by budget, risk and flatten in dry
   run; live mode never sees a dry-run order.
5. One leftover alert rule from an earlier day (a `before_close` key) stopped
   every alert from ringing. Only today's rules are validated; old ones are
   reported and skipped.
6. A `"holidays"` override in a config given with `--config` was ignored. Every
   calendar-aware tool now reads its own config's override.
7. Weights mode sized at the quote and bought 0.3 % higher with a fixed buffer,
   so an order could break the cap. It sizes at the limit, with the configured
   buffer and tick.
8. The float-noise snap could round a computed stop toward the entry on a
   high-priced stock. Only float noise (2e-7 of the value) snaps now.
9. "yes 20MICRONS" was read as an amount of 20 (see decision 9).
10. trader-exit accepted an off-grid `flat_by` (15:12) that the time machine then
    refused. trader-exit refuses it.

## What changed, skill by skill

**trader_common/ (new).** One copy of what nine scripts duplicated. `clock.py`:
IST, the session, NSE's 2026 CM holidays (from NSE's holiday master, a copy in
`tests/fixtures/trader-common/`), a `"holidays"` override in trader/config.json,
the 15:10 / 14:45 / 15:20 defaults. `symbols.py`: NSE symbols and the `.NS`
mapping (`M&M` -> `M%26M.NS`). `money.py`: rupees, Indian grouping, the tick
ladder, float32-safe rounding. `orders.py`: the order shape, the fingerprint,
the exact kite-mcp-server arguments, the max loss. Every script adds the repo
root to `sys.path`, so `python3 <skill>/<tool>.py` still works from anywhere.

**trader-scanner.** Universe from `EQUITY_L.csv` (series EQ, browser-like headers,
`--offline` kept). Yahoo `.NS`; `--min-price 50`, daily `--min-volume 5,00,000`.
Opening range 9:15 to 9:30, first hit at 9:35. Stage-1 gap tolerance (decision 1).

**trader-news.** SEC 8-K feed removed. Yahoo RSS with `.NS` and `region=IN`,
plus Google News RSS by company name (decision 8), India keywords (results,
SEBI, RBI, circuits, bonus / rights, pledges). `--tickers` became `--symbols`;
`--no-sec`, `--forms`, `--user-agent` removed; `--no-google`, `--names`,
`--loose`, `--universe` added. No NSE corporate announcements.

**trader-entry.** IST, NSE symbols, no early closes, entries 09:30 to 14:45,
the zero-volume 9:15 bar left out of the volume average (decision 2).

**trader-exit.** Stops rounded to the stock's tick away from the entry,
targets to the nearest tick (half up, in decimals), R and reward:risk from the
rounded prices. The plan records `tick_size` and `tick_basis`. `flat_by` after
the ~15:20 MIS square-off warns; at or after 15:30 it is refused.

**trader-timemachine.** Full sessions to `flat_by` (decision 4). Sessions on or
after the plan's date are never used. RELIANCE 2026-10-29 with a 1R target:
57 sessions, target first 23.9 %, stop first 31.1 %, history 27 fired
(13 target, 11 stop, 3 neither).

**trader-budget.** Sizes against the LIMIT entry and the SL's limit
(decision 6); writes the new order shape (`order_type`, `price`, `product`,
`stop`, `stop_limit`, `target`, `tick_size`). `*_inr` limits, whole shares only
(`fractional` removed), `pdt_guard` removed, INR example config with
`dry_run`, `broker: zerodha` and the three buffers. Weights mode writes CNC
LIMIT buys.

**trader-risk.** Only LIMIT orders (MARKET blocked, decision 6), product MIS
or CNC, every price a whole number of the stock's tick, an SL limit under its
trigger, F&O symbols blocked, `pdt_guard` and the PDT counting removed, the
max loss at the SL's limit. No circuit-band check: Yahoo has no band data, and
the plan said not to fake it.

**trader-broker.** Rewritten ticket: `show` / `approve` / `result` (per leg) /
`stop` (records the entry fill, prints the SL for exactly what filled) / `fill
--closes` / `flatten --last` (finds the open SL to cancel, then a LIMIT sell
0.5 % under the last price) / `log`. Exact `place_order` and `cancel_order`
arguments for kite-mcp-server v0.3.2; a Kite tag (`ndt` + fingerprint) on both
legs. Dry run by default. The three gates are unchanged: trader-risk PASS for
this fingerprint today, the user's explicit yes for one ticket, Claude's own
approval prompt on each call. SKILL.md: the static-IP setup, "Step 0, every
trading day: log in to Kite", and SEBI / NSE intraday facts in place of the
FINRA PDT paragraph.

**trader-alerts.** NSE clock, `before_close` refused (NSE has no early
closes), plan imports on the plan's tick, a target alert (the take-profit is
an alert on Zerodha, not a resting order), the "SL triggered but did not fill"
case called out. The Stooq fallback is removed: its endpoint no longer
answers, even for US symbols, and it has no NSE data.

**deploy/ (new).** The static-IP VPS for kite-mcp-server: `bootstrap.sh`
(idempotent, shellcheck-clean), the systemd unit (loopback only, sandboxed),
the egress-IP check, Cloudflare and Claude config, and the runbook.

**Repo.** Plugin and marketplace renamed `nse-day-trader-skill`
(asaurav025/nse-day-trader-skill); README rewritten; a fork copyright line in
LICENSE; `.gitignore` keeps `trader/`, `*.env`, `deploy/.env.deploy` and
`.mcp.json` out of git.

## Dropped, and why

- **trader-analyst** (Earnings Analyst) read company facts from SEC EDGAR's
  XBRL API. India has no free, keyless equivalent with structured results.
- **trader-pelosi** (Pelosi Tracker) read US House periodic transaction reports.
  There is no Indian equivalent.
- **SEC 8-K feed** in trader-news, **Stooq** in trader-alerts, **Alpaca / IBKR**
  setup, **bracket orders**, **pattern-day-trader** checks, **early closes**.

Their folders, tests and fixtures are gone. Out of scope by design: F&O, BSE,
margin beyond MIS, short selling, GTT, a web UI.

## Known limits and open items

- **Circuit limits are not checked.** Yahoo has no price-band data. Kite's
  `get_quotes` has it; wiring it in would need trader-risk to read a saved
  quote. Not done.
- **The tick basis uses Yahoo's daily close**, not NSE's official closing price
  (a weighted average of the last 30 minutes). They differ only near a band
  edge, where the coarse fallback is the safe choice; `tick_sizes` overrides.
- **Holidays cover 2026 only.** The tools say so for other years; add dates with
  `"holidays"` in trader/config.json until the 2027 list is in.
- **Claude Desktop**'s per-tool "Needs approval" is not documented for local
  mcp-remote servers: Claude Code is the client for orders.
- **kite-mcp-server v0.3.2** has no auth of its own (Cloudflare Access is the
  lock) and keeps sessions in memory (a restart means a new Kite login).
- **Scanner**: with `no_entries_after` later than 15:15, the null 15:20 bar
  would make every chart unusable in stage 2. The 14:45 default avoids it.
- **Exits after 14:45** pass trader-risk (as they should) without an
  "after the entry window" note.
- **Python 3.8**: the suite ran on Python 3.14; 3.8 compatibility is checked by
  the repo tests' syntax scan, not by running 3.8.
- **No live order has been placed** through this code. The first one is the
  1-share test order in `deploy/README.md`, step 8.

## The worked example, end to end, in dry run

RELIANCE, Thursday 2026-10-29, real data, offline on the repo's fixtures, so
anyone gets the same numbers. Run from an empty folder (it writes `trader/`
there); `R` is your clone.

```bash
R=~/nse-day-trader-skill; F=$R/tests/fixtures
mkdir -p trader && cp "$R/trader-budget/config.example.json" trader/config.json   # "dry_run": true

python3 $R/trader-scanner/scan.py orb --offline $F/trader-scanner/wall5m-2026-10-29.json.gz --now "2026-10-29 10:15"
python3 $R/trader-news/news.py --symbols RELIANCE --names RELIANCE="Reliance Industries Limited" \
    --offline $F/trader-news --now "2026-10-30T04:45:00Z" --hours 24 --dry-run --no-moves
python3 $R/trader-entry/entry.py check RELIANCE --offline $F/trader-entry/RELIANCE-2026-10-29-5m.json \
    --now "2026-10-29 09:40" --write
python3 $R/trader-exit/exit.py plan --stop or-mid --target 2R --offline $F/trader-exit --now "2026-10-29 09:41" --write
python3 $R/trader-timemachine/timemachine.py run --symbol RELIANCE --date 2026-10-29 --entry 1174.30 \
    --stop 1163.30 --target 1185.30 --entry-time "9:40 AM" \
    --offline $F/trader-timemachine/RELIANCE-60d5m.json.gz --now "2026-10-29 09:41"
python3 $R/trader-budget/size.py --now "2026-10-29 09:42" --write
python3 $R/trader-risk/check.py --now "2026-10-29 09:42"
python3 $R/trader-broker/ticket.py show --now "2026-10-29 09:42"
python3 $R/trader-broker/ticket.py approve --index 1 --reply "yes" --now "2026-10-29 09:42"
python3 $R/trader-broker/ticket.py stop --index 1 --filled-qty 48 --filled-price 1176.90 --now "2026-10-29 09:43"
python3 $R/trader-alerts/alerts.py add --from-plan
python3 $R/trader-broker/ticket.py flatten --symbol RELIANCE --last 1196.40 --now "2026-10-29 11:40"
python3 $R/trader-risk/check.py --now "2026-10-29 11:40"
python3 $R/trader-broker/ticket.py approve --index 1 --reply "yes" --now "2026-10-29 11:41"
python3 $R/trader-broker/ticket.py fill --symbol RELIANCE --side sell --qty 48 --price 1190.45 --now "2026-10-29 11:42"
python3 $R/trader-broker/ticket.py log --now "2026-10-29 12:00"
```

What you should see, step by step:

1. Scan (the 50-chart fixture wall): 38 of 50 broke their range by 10:15,
   RELIANCE at 9:40 AM, ₹1,174.30, range 1,156.20 to 1,170.50. (Over the whole
   universe live that morning: 124 of the 199 most traded checked.)
2. News: the day's RELIANCE stories from Google News (Yahoo's RELIANCE feed
   is empty), each tagged.
3. Entry: TRIGGERED 9:40 AM at ₹1,174.30, the 9:35 bar (bar 4).
4. Exit: stop ₹1,163.30 (the midpoint 1,163.35, down to RELIANCE's ₹0.10 tick,
   banded on its 2026-09-30 close ₹1,187.00), target ₹1,196.30 (2R), risk ₹11.00.
5. Time machine (1R, ₹1,185.30): target first 23.9 %, stop first 31.1 %, over
   57 sessions to 2026-10-28.
6. Budget: LIMIT 1177.90, SL 1163.30 / 1157.40, 48 shares (bound by the ₹1,000
   max loss), ₹56,539.20, max loss ₹984 (₹700.80 at the trigger).
7. Risk: PASS, reward:risk 1.26, cash after ₹43,460.80, "dry run: nothing goes
   to Zerodha". (One share more, 49, is blocked at ₹1,004.50.)
8. Broker: the ticket and the exact `place_order` arguments under "DRY RUN: do
   not call"; "sure" or "yes 63" would not be recorded; "yes" is, as
   `DRYRUN-entry-...`. `stop` prints the SL leg (`order_type` SL, trigger
   1163.3, price 1157.4, quantity 48).
9. Alerts: stop 1163.3, target 1196.3, flat-by 15:10.
10. Flatten: "first cancel the open order for it: DRYRUN-stop-... / SELL 48
    RELIANCE, LIMIT 1,190.40"; trader-risk passes it as an exit; the yes is
    logged; cash ends at ₹1,00,650.40 in `trader/portfolio.dryrun.json`. The
    rehearsal never creates or changes the real `trader/portfolio.json`.

With live data, drop the `--offline` and `--now` flags (and run `scan.py
universe` first). Nothing is ever sent while `"dry_run": true`. Going live is
your own edit of `trader/config.json` after `deploy/README.md` (static IP,
whitelist, daily login), and Claude still asks for your yes and its own
approval on every call.

## Tests

```bash
python3 -m unittest discover -s tests
```

370 tests, offline, on fixtures cut from real NSE data on 2026-10-30.
Per file (upstream in brackets): scanner 44 (36), entry 46 (39), exit 18 (14),
timemachine 48 (47), budget 38 (31), risk 48 (43), broker 27 (18), alerts 35
(30), news 21 (17), repo 15 (11), common 30 (new).
