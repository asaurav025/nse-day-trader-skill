# Design: kite-data, Zerodha as the only market-data source

Status: proposal, for review. Branch `kite-data-design` (off `nse`). Nothing
here is built yet.

## Goal

Replace Yahoo Finance everywhere it is used (charts, quotes, ticks, the move
line in the news) with Zerodha's own market data, through a small read-only
service on the static-IP VPS. Keep the Kite keys and session on the VPS, never
on the laptop. Fix the two gaps Yahoo caused: the exact tick size of each
stock, and the circuit-limit check trader-risk does not have.

Not goals: placing orders from this service (that stays with kite-mcp-server
and its three gates), F&O, live tick streaming in the first version, any change
to the trading rules.

## What is verified, and what is not yet

Checked on 2026-10-30 against Zerodha's documentation:

| fact | source |
| --- | --- |
| Personal plan (free): orders, GTT, portfolio; **no** live or historical data. Connect plan (₹500 per app per month): adds WebSocket live data and historical candles; the separate historical add-on was dropped in Feb 2025 | [Kite API charges](https://support.zerodha.com/category/trading-and-markets/kite-api/articles/what-are-the-charges-for-kite-apis), [data plan](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/historical-data-and-live-market-data-payment-plan) |
| Rate limits: quote 1 request/s, historical candles 3/s, everything else 10/s; a breach is HTTP 429 | [Kite docs: exceptions](https://kite.trade/docs/connect/v3/exceptions/) |
| `GET /quote` up to 500 instruments, `/quote/ohlc` and `/quote/ltp` up to 1,000; the full quote carries `lower_circuit_limit` and `upper_circuit_limit`; instruments are `NSE:RELIANCE` | [Kite docs: market quotes](https://kite.trade/docs/connect/v3/market-quotes/) |
| `GET /instruments/NSE`: a gzipped CSV made once a day, with `instrument_token`, `tradingsymbol`, `name`, `tick_size`, `lot_size` | same |
| Historical: `GET /instruments/historical/:instrument_token/:interval`, intervals `minute`, `3minute`, `5minute`, ..., `day`; candles are `[timestamp, open, high, low, close, volume]` with `+0530` timestamps | [Kite docs: historical](https://kite.trade/docs/connect/v3/historical/) |
| Login: `https://kite.zerodha.com/connect/login?v=3&api_key=...` sends `request_token` to the app's registered redirect URL; `POST /session/token` with `checksum = SHA-256(api_key + request_token + api_secret)`; the token expires at 6 AM; header `Authorization: token api_key:access_token` | [Kite docs: user](https://kite.trade/docs/connect/v3/user/) |

To verify in phase 0, before any code depends on it:

1. The maximum date range per historical request for each interval (forum
   posts say 60 days for `minute` and more for `5minute`; the docs do not say).
2. Whether the historical endpoint returns today's still-forming candle.
3. Whether a Personal app works with kite-mcp-server for orders, and whether one
   account may hold a Personal app and a Connect app at the same time.
4. Whether ₹500 includes GST.
5. How far back 5-minute history goes for NSE equities.

## Architecture

```
laptop                                   VPS (static Reserved IP, ufw: no inbound)
------                                   ----------------------------------------
Claude Code ── MCP over HTTPS ──▶ CF Access ─▶ cloudflared ─▶ kite-mcp-server :8080 ──▶ Kite (orders app, Personal, free)
                                                                     orders only, three gates
tools (scan, entry, exit, ...)
  └─ trader_common/data.py ─ HTTPS ─▶ CF Access ─▶ cloudflared ─▶ kite-data :8090 ──────▶ Kite (data app, Connect, ₹500)
                                                                  read-only, caches on disk
browser (morning) ── login ──▶ both /callback routes
```

### Two Kite apps, not one

| app | plan | used by | redirect URL |
| --- | --- | --- | --- |
| orders | Personal (free) | kite-mcp-server (unchanged) | `https://KITE_HOSTNAME/callback` |
| data | Connect (₹500 / month) | kite-data (new) | `https://KITE_DATA_HOSTNAME/callback` |

Why two:
- A Kite app has exactly one redirect URL, and each login yields one access
  token. Sharing one app would mean routing one `/callback` between two
  services, and relying on two concurrent tokens for the same app, which the
  docs do not promise (open question 3 above).
- Least privilege by construction: the data service holds no key that its code
  uses for orders, and the order server holds no data entitlement it needs.
- Cost stays ₹500 a month: the orders app can be Personal.

The price: two Kite logins each morning instead of one (both in the browser,
both with TOTP, never automated).

## The kite-data service (VPS)

Standard-library Python, like the rest of the pack, one file plus a module:
`deploy/kite-data/kite_data.py`, run by `kite-data.service` (systemd, its own
system user, sandboxed like `kite-mcp.service`), listening on
`127.0.0.1:8090` only, published by the existing Cloudflare Tunnel on its own
hostname `KITE_DATA_HOSTNAME` with its own Access application (email OTP for
the browser login; a service token for the laptop tools).

**Secrets.** `/etc/kite-data/kite-data.env` (root, 0600): `KITE_DATA_API_KEY`,
`KITE_DATA_API_SECRET`, filled by hand on the VPS, as with kite-mcp-server.
The access token lives in memory only: a restart means a new login, like the
order server.

**Never an order.** The service calls only `GET` Kite endpoints under
`/instruments`, `/quote` and `/user/profile`, plus `POST /session/token` to
log in. A test fails if any other Kite path, or any non-GET call besides the
login, appears in its code.

**Login.** `GET /login` redirects to Kite's login for the data app;
`GET /callback?request_token=...` exchanges it (checksum as above) and shows
"Logged in until 06:00 IST". `GET /v1/health` says whether it is logged in.

**Rate limits.** One token bucket per Kite limit (quote 1/s, historical 3/s,
other 10/s), shared by every request the service makes, with one backoff and
retry on a 429.

**Caches** (`/var/lib/kite-data`, the service's own directory):
- the NSE instruments CSV, refreshed each morning (tick sizes, tokens, names);
- daily candles per symbol, appended once a day after the close;
- 5-minute candles per symbol per finished session (they never change);
- the opening-range snapshot of the day (below).

**Scheduled read-only jobs** (systemd timers on the VPS, data only, they never
touch orders, so the "never from a scheduled task" rule is not in play; they
do nothing until the morning login):
- 09:00 IST: refresh the instruments dump.
- 09:30:05 IST: the opening-range snapshot: `/quote/ohlc` for every EQ symbol
  (about 2,350, so 3 requests of up to 1,000). At that moment the day's high
  and low are the 15-minute opening range.
- 15:40 IST: append today's daily candle for every symbol from `/quote/ohlc`
  (3 requests), so the daily scan never needs 2,350 historical calls.

### API (JSON over HTTPS, GET only, behind Access)

| endpoint | returns | Kite calls |
| --- | --- | --- |
| `/v1/health` | logged in or not, until when, cache dates | none |
| `/v1/instruments?series=EQ` | symbol, name, tick_size, instrument_token | none (cache) |
| `/v1/quote?symbols=RELIANCE,TCS` | last price, day OHLC, volume, **lower/upper circuit**, **tick_size**, quote time | `/quote`, 500 a request |
| `/v1/chart/RELIANCE?interval=5m&days=60` | candles in the **same JSON shape the tools already read** (Yahoo's v8 chart layout), plus `meta.tickSize`, `meta.source = "kite"` | historical, cached by finished session |
| `/v1/orb?date=2026-10-29&or_minutes=15` | per symbol: opening-range high/low (from the 09:30 snapshot), day high/low and last price now | `/quote/ohlc` (3 requests) |
| `/v1/daily?symbols=...&days=90` | daily closes | none after the first backfill |

Keeping the chart layout means every tool's parser, every offline fixture and
every test stays as it is; only the fetch layer changes.

## The laptop side

**One client.** `trader_common/data.py` replaces the Yahoo fetch code in
scan.py, entry.py, exit.py, timemachine.py, alerts.py and news.py. It reads
`KITE_DATA_URL` and the Cloudflare service token (`CF_ACCESS_CLIENT_ID`,
`CF_ACCESS_CLIENT_SECRET`) from the environment (the 0600 `cf-access.env` the
runbook already sets up), never from a tracked file. When the service is not
logged in today, every tool says so in one line ("kite-data: not logged in
today: open https://KITE_DATA_HOSTNAME/login") and stops. No Yahoo fallback.

**`--offline` stays.** Tests and replays read saved chart files exactly as now.

**Scanner, opening-range breakouts.** Stage 1 becomes `/v1/orb`: a chart is a
candidate when its day high is above its opening-range high (a necessary
condition for a close above it). Stage 2 stays: the 5-minute chart of the most
traded candidates (`--max-confirm 200`, about 70 seconds at 3 a second), where
every bar must be there. If the 09:30 snapshot is missing (the morning login
came after 09:30), stage 1 falls back to the most traded `--max-confirm`
symbols by today's turnover from `/v1/quote`, and says the scan is partial.

**Scanner, daily.** `/v1/daily` from the cache.

**Exact ticks.** trader-exit takes `tick_size` from `/v1/quote` (Zerodha's
instrument list) instead of inferring it from the month-end close; the plan
records `tick_basis: "kite instruments"`. The `tick_sizes` config override
stays.

**Circuit limits.** A new read-only step writes `trader/prices.json` with last
price, tick size and both circuit limits (from `/v1/quote`). trader-risk, still
offline and pure, reads it and adds a check for entries: BLOCK when the LIMIT
is above the upper circuit or the SL limit is below the lower circuit (the order
would be rejected or could never fill); a warning when the target is beyond the
upper circuit. The price must be under 15 minutes old, as today.

**News.** Google News RSS only; Yahoo's headline feed is removed. The move line
uses `/v1/quote`.

**Removed for good:** every Yahoo URL, `.NS` mapping, the spark gap
tolerance, the float32 noise handling (Kite prices are exact; the snap stays
harmless), the month-end tick inference (kept only for `--offline` fixtures).

## The morning routine, after this change

1. Browser: `https://KITE_HOSTNAME/` and `https://KITE_DATA_HOSTNAME/`, email OTP.
2. Data: `https://KITE_DATA_HOSTNAME/login`, Kite password + TOTP. Before 09:30,
   so the opening-range snapshot runs.
3. Orders: Claude Code, the MCP `login` tool, Kite password + TOTP.
4. Check: `/v1/health` logged in; `get_margins` answers.

## Deploy changes (`deploy/`)

- `bootstrap.sh` installs `kite-data` (the Python file, its systemd unit and
  three timers), its user and `/etc/kite-data/kite-data.env` (written once,
  0600, keys filled by hand), and checks it listens on 127.0.0.1:8090 only.
- Cloudflare: a second Published application route `KITE_DATA_HOSTNAME` ->
  `http://127.0.0.1:8090`, its own Access app (Allow your email, Service Auth
  for a service token), Protect with Access on.
- Kite: a second app, Connect plan, redirect URL
  `https://KITE_DATA_HOSTNAME/callback`. The orders app may move to the free
  Personal plan (open question 3).
- The egress-IP check and the static-IP whitelist do not change (data calls
  need no whitelisted IP; they leave from the same address anyway).

## Testing

- The service: unit tests against an in-process fake of Kite built from
  recorded Kite responses (instruments CSV, quote, OHLC, historical candles,
  a 429), covering the rate limiter, the caches, the snapshot, the chart
  layout and the "GET only, no order path" rule.
- The tools: unchanged fixtures, plus tests for the new client's errors (not
  logged in, Access refused, 429) and for the circuit check.
- New fixtures recorded from kite-data once the VPS runs (a 60-day RELIANCE
  chart, a quote with circuit limits, an opening-range snapshot), replacing the
  Yahoo recordings over time.
- A repo test: no Yahoo host left in product code.

## Phases

| phase | what | done when |
| --- | --- | --- |
| 0 | verify the five open facts above with the real data app | each answered in this file |
| 1 | kite-data service, unit, timers, deploy changes, login | `/v1/health` logged in on the VPS; the fake-Kite tests pass |
| 2 | `trader_common/data.py`; entry, exit, timemachine, alerts, news on kite-data | the RELIANCE dry run reproduces on live Kite data |
| 3 | scanner on `/v1/orb` and `/v1/daily` | a full 10:15 scan in under 2 minutes |
| 4 | exact ticks and the circuit check in budget / risk / broker | a stop below the lower circuit is blocked |
| 5 | remove Yahoo code and docs; re-cut fixtures from kite-data | the "no Yahoo host" repo test passes |
| later | WebSocket ticks for live 5-minute bars (no third-party library: the client and Kite's binary packets by hand) | only if REST polling proves too slow |

## Risks and open questions

- **Two logins a day.** If phase 0 shows one app can serve both services
  safely, a single Connect app with one routed callback could replace this; it
  would still need two logins (one token each), so the gain is small.
- **Login after 09:30** misses the snapshot: the scan falls back to the most
  traded names and says it is partial.
- **The VPS becomes a dependency for research too.** Without it (or before the
  login), nothing but `--offline` runs. That is the cost of leaving Yahoo.
- **Rate limits** shape the scanner: full 5-minute history for 2,350 stocks
  would take about 13 minutes at 3 a second, which is why the design never
  asks for it.
- **Cost:** ₹500 a month plus GST if it applies (open question 4).
