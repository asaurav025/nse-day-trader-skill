---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every stock you watch
  and flags what moves it: Yahoo Finance headlines per symbol plus a Google
  News search by the company's name (both RSS), and how far each stock is from
  yesterday's close. Shows only what is new since the last check, newest
  first, in IST, says whether each headline actually names the company, tags
  each item (results, analyst, deal, capital, circuit, regulatory, macro...)
  and never repeats one. Use when the user says "any news on RELIANCE", "why
  is this moving", "what's the news on my watchlist", "morning briefing",
  "pre-market news", "monitor the news", "did anything break", or after
  /trader-scanner finds setups.
argument-hint: "[NSE symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results date, a
circuit or a SEBI order. Before a setup becomes a trade, this skill reads two
news feeds for every stock on the watchlist and tells you only what is new. It
also prints how far each stock has moved today, so a headline and a move sit
side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                          # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols RELIANCE M&M --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --symbols RELIANCE --json
```

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--symbols`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| `trader/universe.txt` | each symbol's company name, from NSE's equity list (`scan.py universe`); `--names RELIANCE="Reliance Industries"` adds one by hand |
| Yahoo Finance headline RSS | `https://feeds.finance.yahoo.com/rss/2.0/headline?s=RELIANCE.NS&region=IN&lang=en-IN` |
| Google News RSS search | `"<company name>" when:2d`, India edition (`hl=en-IN&gl=IN`) |
| Yahoo Finance chart endpoint | the last price against yesterday's close (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

Why two feeds: Yahoo carries headlines for few NSE stocks (on 2026-10-30, 7 of
20 large caps; none for RELIANCE or HDFCBANK). Google News covers them, but a
name search also finds market round-ups and "stocks to buy" lists. So every
headline says which of your stocks it actually names; one that does not is
marked `?`, and one that shares no word with the company's name is left out
(the note counts them; `--loose` shows them). NSE's own corporate
announcements are not read.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the publisher and link it. Never
  summarise a paywalled story you could not read: say so. Google's links go
  through news.google.com first.
- **The exchange beats the press.** A results date, a board meeting, a bonus
  or a split is confirmed on nseindia.com (the company's announcements) or on
  the company's own site. Tell the user to check there before trading on it.
- **Say what it touches.** Every item lists the symbols it names (a `?` means
  the search matched but the headline does not name the company) and its tags:
  results, analyst, legal, deal, capital, people, risk, circuit, regulatory,
  macro.
- **MOVING** marks a stock 3 % or more from yesterday's close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  yesterday's close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter first: anything tagged results, circuit, legal,
   regulatory or risk, and every MOVING symbol.
3. Write a short brief, per symbol: what happened, the publisher and link, the
   move, and one line on why it matters to the setup (for example "results are
   out today: the stock can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: results inside the trade, a circuit, an order from SEBI.
   Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before 9:15.

## Example: the reel

Friday 2026-10-30, RELIANCE reports its Q2 results today. The check at 10:15
IST finds nothing on Yahoo's RELIANCE feed, and Google News leads with the
day's real stories: "Reliance Industries Q2 results: Profit may top Rs 21,000
crore as refining, Jio drive earnings" (Moneycontrol.com, 9:36, results),
"Reliance Industries shares down 26% in 2026: Here's how to trade RIL stocks on
results day" (The Economic Times, 9:38, results). The price line reads
`RELIANCE ₹1,164.90 -0.21% vs yesterday's close ₹1,167.40`. The brief:
"RELIANCE reports today, so the stock can gap when the numbers land; an
intraday trade is out by 15:10 either way." Market wraps that only mention
Mahindra in passing are left out for M&M. When a symbol has no news, say so
rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol, with a pause
  between calls.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
