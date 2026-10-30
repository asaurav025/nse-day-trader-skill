# Roadmap

Agreed on 2026-10-30, after the static-IP VPS went live. Nothing here is built yet.
The rule: real trading experience decides what comes next. Data quality and safety come before features.

**Design constraint:** the operator is not at a desk during market hours. The day-to-day flow has to work
with about 5 minutes of phone attention per day.

## Order

### 1. Trade what exists first

- Dry run for 2-3 weeks, running the scan by hand whenever possible. Then go live with a tiny max loss per trade.
- The first live step is a 1-share test that confirms the static-IP whitelist end to end.
- Meanwhile, test a **no-target variant** in trader-timemachine: enter, rest the stop, flatten at 15:10.
  If its odds are close to the target-based version, it is the simplest version to automate.

### 2. Semi-automatic flow, approved from the phone (top priority)

| Part | Automated? | How |
|---|---|---|
| Daily Kite login (password + TOTP) | **No** | Stays manual, done on the phone. Never script the credentials. |
| 09:30 scan, news, odds, ticket | Yes | A scheduled job on the VPS calls the skill tools directly. |
| Approving the order | Human, on the phone | A Telegram bot sends the ticket and a YES reply places it. No reply by a cutoff (about 10:30) means no trade. |
| Stop-loss | Already | The SL leg rests at Zerodha. |
| Target, stop hit, infra failures | Alert | Phone alerts on target or stop hits, and on egress-IP mismatch, tunnel drop or kite-mcp down. |
| 15:10 flatten | Yes | Automatic, then a summary message. Flatten only reduces risk, so it is the one accepted exception to "human yes". |

**Where it runs:**
- The unattended parts (scan job, Telegram bot, flatten) are plain Python on the VPS, **without Claude**.
  The bot places orders through the local kite-mcp-server.
- Headless Claude on the VPS was rejected: it costs more, needs an Anthropic login stored on the server,
  and its answers vary from day to day. For orders, fixed Python is more predictable and easier to audit.
- Claude Code on the operator's computer stays for reviews, the journal, the time machine and manual trading.

**Login catch:**
- A kite-mcp-server login belongs to one MCP connection, so the bot needs its own Kite session.
- Each morning the bot sends the Zerodha login link on Telegram, and the operator logs in on the phone.
- The computer's Claude Code session needs a separate login only on days it is also used for trading.

**Not wanted for now: fully automatic placement.**
- The strategy is unproven.
- A bug would place orders instead of proposing them.
- SEBI's retail algo rules apply.

**Never** rest a target LIMIT alongside the SL without a watcher. With MIS, a double fill can open a short.

**Plan:**
- About 3 small PRs: the bot, the scheduled scan, the auto-flatten.
- A security review first, because the VPS would hold the daily Kite session and act on Telegram replies.

### 3. kite-data: Zerodha as the only market-data source

- Replace Yahoo with official Zerodha data. That gives live prices, exact ticks, a circuit-limit check
  in trader-risk, and a fast 09:30-snapshot ORB scan.
- The design is on branch `kite-data-design` (`docs/design/kite-data-service.md`).
- It needs a ₹500/month Kite **Connect** app.
- Phase 0 verifies the five open facts on the live account. Phases 1-5 follow, one PR each.

### 4. kite-mcp-server v0.4, once it is a stable release

- v0.4 can send `market_protection`, which allows MARKET entries and SL-M stops in place of LIMIT/SL.
- Adopting it means reworking the ticket, its tests and `deploy/` (a multiplexed `orders` tool, a new OAuth layer).

## Small chores, any time

- **2027 NSE holiday list:** add it to `trader_common/clock.py` once NSE publishes it (usually December).
  Until then, the tools warn for 2027 dates.
- **CI:** bump `actions/checkout` and `actions/setup-python` in `.github/workflows/tests.yml` before GitHub's
  Node.js 20 deprecation. The Python 3.8 job needs a new runner if ubuntu-22.04 is retired.
- **Cleanup PR:** remove the duplicated helpers the code review flagged:
  - the symbol_arg/symbol_of wrappers
  - DEFAULTS/config_units in risk and budget
  - trader-exit's `tick()` vs `money.tick`
  - scan.py's `gaps()`/`usable()`

## Later, only if wanted

- **Automatic fill recording via Kite postbacks,** as part of kite-data. It needs a checksum-verified endpoint
  and an Access bypass for that one path, so a security review comes first.
- **A trade journal and weekly P&L review:** win rate, average R, time-machine odds vs reality.
- **Trailing stops:** needs `modify_order` re-enabled (it is deliberately excluded now) and a new trader-exit rule.
- **New setups** (VWAP reclaim, gap-and-go) **or short selling.** Short selling is out of scope today,
  and every new setup needs time-machine evidence first.
- **WebSocket live ticks:** only if REST polling proves too slow.
