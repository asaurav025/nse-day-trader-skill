# The Day Trader skill for NSE

9 Claude skills for NSE day trading with Zerodha: scan the NSE for setups, track the news, time every entry and exit, and replay every outcome before you risk a rupee.

An NSE / Zerodha port of
[jakeschincariol/day-trader-skill](https://github.com/jakeschincariol/day-trader-skill)
(MIT). Free, no signup, no data API key. The tools are plain Python that read
free public data: Yahoo Finance charts (`SYMBOL.NS`), NSE's list of equities,
and Yahoo / Google News RSS. Orders go through Zerodha's own Kite MCP server,
which you run on a small static-IP VPS (`deploy/`).

One scans the NSE for setups. Another tracks the news on every stock you
watch. One watches a setup bar by bar and tells you the moment to get in, and
another sets your stop and your target before you do. And the craziest one is
the Time Machine: before you risk a rupee, it replays the rest of the session
10,000 times from the stock's own recent bars, plus every past day the same
setup fired, and tells you the honest odds.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Dry run is the default: Zerodha has no paper account, so
every ticket is rendered and logged and no order tool is called until you
change that yourself.

## Install

Paste this into Claude:

```
https://github.com/asaurav025/nse-day-trader-skill

install skill
```

Or as a plugin, in Claude Code:

```
/plugin marketplace add asaurav025/nse-day-trader-skill
/plugin install nse-day-trader-skill@nse-day-trader-skill
```

Claude Code namespaces plugin skills, so installed as a plugin they show up as
`/nse-day-trader-skill:trader-scanner` and so on. Copy the folders instead if
you want plain `/trader-scanner` (copy `trader_common/` with them: every tool
imports it from the folder above its own):

```bash
git clone https://github.com/asaurav025/nse-day-trader-skill.git
cp -r nse-day-trader-skill/trader-* nse-day-trader-skill/trader_common ~/.claude/skills/
```

The tools need Python 3.8 or newer. Nothing to pip install. Placing orders
needs more: a Kite Connect app, Zerodha's static-IP whitelist, and
kite-mcp-server on a VPS. `deploy/README.md` is the runbook. Everything up to
the order ticket works without it.

## The nine

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans the NSE's ~2,350 equities for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks the news on every stock you watch (Yahoo and Google News RSS) and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, on the stock's NSE tick. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a rupee: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits and stops anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade, in rupees. |
| `/trader-broker` | Order Executor | Turns an approved trade into Zerodha orders (a LIMIT buy, then an SL stop), dry run by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop, the target, the opening bell or flat-by time hits. |

The upstream pack's Earnings Analyst (SEC XBRL) and Pelosi Tracker (US House
filings) are not ported: neither has a free Indian equivalent.

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 15:10                                           (while it is open)
news                                                                           (on a schedule)
```

Times are IST. The session is 9:15 to 15:30; the default last entry is 14:45
and the default flat-by is 15:10, because Zerodha squares MIS positions off
from about 15:20.

An example: RELIANCE, Thursday October 29 2026, real data.

1. **`/trader-scanner`** at 10:15 AM over all 2,357 NSE EQ stocks: 1,472
   usable charts, 1,074 candidates, and of the 199 most traded it checked, 124
   had closed above their 15-minute opening range (a strong morning; the rest
   are reported as unchecked, never as misses). RELIANCE is one: range
   1,156.20 to 1,170.50, the 9:35 candle closed at 1,174.30, 0.32 % over the top.
2. **`/trader-news`**: the day's RELIANCE stories, from Google News (Yahoo's
   RELIANCE feed is empty), each tagged and marked with whether it names the
   company.
3. **`/trader-entry`** watched RELIANCE bar by bar. At 9:37 it said waiting
   (the 9:35 bar was still forming). At 9:40 it said triggered: the 9:35 bar
   closed at 1,174.30, above the 15-minute range high of 1,170.50.
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 1,163.35, down to RELIANCE's ₹0.10 tick: 1,163.30, so 11.00 a
   share at risk; target 2R, 1,196.30; flat by 15:10 either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times, each
   5-minute candle drawn from RELIANCE's own candles at that time of day over
   the previous 57 full sessions (seed 19, so anyone can repeat it). With a 1R
   target (1,185.30): target first 23.9 %, stop first 31.1 %, still open at
   15:10 45.0 %. The same rule on those 57 real sessions fired 27 times: 13
   target, 11 stop, 3 neither. Its verdict: leans toward the stop. That is the
   point of looking before you risk a rupee.
6. **`/trader-budget`** with a ₹1,00,000 account and a ₹1,000 max loss per
   trade: the entry is a LIMIT at 1,177.90 (0.3 % above the trigger) and the
   stop an SL that triggers at 1,163.30 and sells no lower than 1,157.40, so
   1,000 / 20.50 = 48 shares (₹56,539.20). If the SL fills at its limit, the
   loss is ₹984.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window, the tick,
   position size and cash. PASS or BLOCK, with the reason.
8. **`/trader-broker`** shows you the ticket in plain words, both legs and the
   exact `place_order` arguments. You type yes for that ticket. In dry run it
   is logged and nothing is sent. Live, Claude calls the Kite MCP server's
   `place_order`, and Claude's own approval prompt asks you once more; when
   the buy fills, the SL goes in for exactly the shares that filled.
9. **`/trader-alerts`** rings on the stop, the target (the take-profit is an
   alert on Zerodha, not a resting order) and at 15:10 to flatten whatever is
   still open: cancel the SL, then a LIMIT sell.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe                                # ~2,350 NSE EQ equities, EQUITY_L.csv
python3 trader-scanner/scan.py daily                                   # pre-market setups, whole universe
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far
python3 trader-news/news.py --symbols RELIANCE --hours 12              # headlines and today's move
python3 trader-entry/entry.py check RELIANCE --setup orb               # waiting / triggered / expired
python3 trader-entry/entry.py watch RELIANCE --every 30                # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 2R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by 15:10
python3 trader-alerts/alerts.py check                                  # rings what hit, once
```

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real NSE data: RELIANCE's 5-minute
bars for the 60 days to October 29 2026, an opening-range scan snapshot, the
news feeds of October 30 and NSE's 2026 holiday list. They reproduce the
numbers in the example above.

`MIGRATION.md` lists everything that changed from the upstream US pack, and why.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
SEBI's own studies found that most individual intraday traders in equity cash
lose money. You can lose money. Decide for yourself, and talk to a
SEBI-registered adviser if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Keep the Kite order tools on ask in Claude Code
(`permissions.ask`), never "Always allow", so Claude's own approval prompt is
a second check on every call. Never place orders from a scheduled task. No
F&O, no margin beyond MIS, no short selling.

**Dry run first.** `"dry_run": true` is the default in `config.example.json`:
Zerodha has no paper account, so a rehearsal logs every step and sends
nothing. Going live is your own edit of your own config. Kite keys live only
in the MCP server's environment on your VPS, never in the chat.

**Intraday on NSE.** MIS is Zerodha's intraday product. Its leverage is capped
by SEBI's margin rules and varies by stock; this pack sizes on your cash, not
on leverage. Open MIS positions are squared off by Zerodha from about 15:20.
Every stock has a daily price band (circuit limit), and at its circuit a stock
may not trade at all: a stop or a sell can sit unfilled. The tools cannot see
the band. NSE's tick size goes by price band (₹0.01 under ₹250 up to ₹5 above
₹20,000), set monthly; prices are rounded to it.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: results, a circuit, a headline. Treat its odds as a reality
check, never as a promise.

**A stop is a plan, not a guarantee.** The stop is an SL order with a limit
under its trigger (Kite takes no SL-M order over the API without market
protection, which the stable kite-mcp-server cannot send). If the price gaps
below that limit, the SL triggers but does not fill. The max loss shown is the
loss if the SL fills at its limit.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you, and it drops some NSE
bars. The scanner and the entry timer read 1-minute or 5-minute bars, not
every tick. Check anything that matters against Kite before you act on it.

**Taxes.** Intraday equity gains are speculative business income in India.
Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json, targets.example.json
trader-broker/      SKILL.md, ticket.py
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
trader_common/      clock.py (IST, NSE holidays), symbols.py, money.py (INR, ticks), orders.py (Kite legs)
deploy/             static-IP VPS for kite-mcp-server: bootstrap.sh, runbook, Cloudflare and Claude config
tests/              unittest suite + offline fixtures
```

MIT. Upstream by [Jake Schincariol](https://opusjake.ai); NSE / Zerodha port by
[asaurav025](https://github.com/asaurav025).
