#!/usr/bin/env python3
"""News monitor for trader-news. Standard library only.

Watches headlines for the NSE stocks you watch or hold and prints only what
is new since the last check, newest first, with how far each stock has moved
today and a flag on anything that names one of your stocks.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --symbols RELIANCE TCS M&M
    python3 news.py --symbols RELIANCE --all --hours 72  # everything in the last 3 days, seen or not
    python3 news.py --no-google                      # Yahoo's feed only
    python3 news.py --no-moves                       # skip the price line per symbol

Your symbols, when --symbols is not given: every hit in trader/watchlist.json
(trader-scanner), the symbol in trader/plan.json (trader-exit) and the
positions in trader/portfolio.json.

Sources, RSS only, one request each per symbol:
  - Yahoo Finance headline RSS, by Yahoo symbol:
    https://feeds.finance.yahoo.com/rss/2.0/headline?s=<SYMBOL>.NS&region=IN&lang=en-IN
    Yahoo carries headlines for few NSE stocks (checked 2026-10-30: 7 of 20
    large caps, none for RELIANCE or HDFCBANK), so it is not enough alone.
  - Google News RSS, searched by the company's name in quotes, India edition:
    https://news.google.com/rss/search?q="Reliance Industries"+when:2d&hl=en-IN&gl=IN&ceid=IN:en
    The name comes from NSE's equity list (trader/universe.txt, written by
    scan.py universe; "Limited" / "Ltd" dropped). A name search also finds
    round-ups and similar names, so every headline is tagged with whether it
    actually names the company or the symbol; one that does not is marked "?",
    and one that shares no word with the name at all (a market wrap, a list
    of "stocks to buy") is left out unless --loose.
    No name for a symbol: Google is skipped for it and the tool says so.
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against yesterday's close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m
NSE's own corporate announcements are not read.

Times print in India Standard Time (IST). State: trader/state/news.json remembers
what you have seen (30 days), so an item shows once. Offline: --offline DIR
reads yahoo-<SYMBOL>.xml, google-<SYMBOL>.xml and chart-<SYMBOL>.json from DIR
(a missing file is skipped). The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader_common import clock, money as rupees, symbols as nse  # noqa: E402

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=IN&lang=en-IN"
GOOGLE = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
BROWSER_UA = "Mozilla/5.0 (compatible; nse-day-trader-skill/1.0; +https://github.com/asaurav025/nse-day-trader-skill)"
DEFAULT_UNIVERSE = "trader/universe.txt"
KEEP_DAYS = 30
MOVE_PCT = 3.0  # a stock this far from yesterday's close is flagged as moving
NAME_SUFFIX = re.compile(r"[\s,]+(?:limited|ltd\.?)\s*$", re.I)

KEYWORDS = [
    ("results", r"\bresults?\b|\bearnings\b|\bQ[1-4]\b|\bnet profit\b|\bprofit (?:rises|falls|jumps|drops)|"
                r"\brevenue\b|\bguidance\b|\bEBITDA\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|price target|\bbuy rating\b|"
                r"\bsell rating\b|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bSEBI order\b|\bshow[- ]cause\b|"
              r"\bNCLT\b|\bcourt\b|\bED raid|\braids?\b|\bfraud\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bstake sale\b|\btakeover\b|\bdeal\b"),
    ("capital", r"\bdividend\b|\bbuyback\b|\bbonus issue\b|\brights issue\b|\bstock split\b|\bQIP\b|"
                r"\bOFS\b|\bblock deal\b|\bbulk deal\b"),
    ("people", r"\bCEO\b|\bMD\b|\bCFO\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\bpledge|\bdefault\b|\bdowngrade\b|\binsolvency\b|\bdelist|\bhack|\bbreach\b|\boutage\b"),
    ("circuit", r"\bupper circuit\b|\blower circuit\b|\bcircuit (?:limit|filter)\b|\btrading (?:halt|suspen)"),
    ("regulatory", r"\bSEBI\b|\bRBI\b|\bCCI\b|\bDGCA\b|\bUSFDA\b|\bapproval\b|\bapproves\b|\blicen[cs]e\b"),
    ("macro", r"\brepo rate\b|\bMPC\b|\bCPI\b|\binflation\b|\bGDP\b|\bUnion Budget\b|\bFIIs?\b|\bFPIs?\b|"
              r"\bDIIs?\b|\bcrude\b|\brupee\b|\btariffs?\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_ist(utc):
    """Aware or naive UTC -> naive IST wall-clock time."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return clock.to_ist(utc)


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def symbol_of(text):
    sym = nse.normalize(text)
    if sym is None:
        raise NewsError("%r is not an NSE symbol (RELIANCE, M&M, BAJAJ-AUTO; .NS is optional)" % text)
    return sym


def short_name(name):
    """'Reliance Industries Limited' -> 'Reliance Industries' (how headlines write it)."""
    n = " ".join(str(name or "").split())
    return NAME_SUFFIX.sub("", n).strip() or None


def universe_names(path=DEFAULT_UNIVERSE):
    """{SYMBOL: company name} from trader/universe.txt (SYMBOL|Name, scan.py universe)."""
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                sym, _, name = ln.strip().partition("|")
                s = nse.normalize(sym)
                if s and name.strip():
                    out[s] = name.strip()
    return out


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = nse.normalize(h.get("symbol") or h.get("ticker"))
        if sym:
            out[sym] = h.get("name")
    pl = load_json(plan) or {}
    sym = nse.normalize(pl.get("symbol"))
    if sym:
        out.setdefault(sym, pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = nse.normalize(p.get("ticker") or p.get("symbol"))
        if sym:
            out[sym] = p.get("name") or out.get(sym)
    return out


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url.split("?")[0]))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url.split("?")[0], exc.reason))


def google_url(name, hours):
    days = max(1, min(30, int(math.ceil(hours / 24.0))))
    return GOOGLE % urllib.parse.quote_plus('"%s" when:%dd' % (name, days))


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def parse_rss(data, symbol, source="Yahoo Finance"):
    """An RSS 2.0 feed (Yahoo's headlines or a Google News search) -> items.
    Google News titles end with " - <publisher>"; that goes into "publisher"."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NewsError("%s RSS for %s is not valid XML: %s" % (source, symbol, exc))
    out = []
    prefix = "google:" if source == "Google News" else "rss:"
    for it in root.iter("item"):
        title = _text(it, "title")
        link = _text(it, "link")
        guid = _text(it, "guid") or link
        publisher = _text(it, "source")
        if publisher and title.endswith(" - " + publisher):
            title = title[:-len(" - " + publisher)].rstrip()
        summary = re.sub(r"<[^>]+>", " ", _text(it, "description"))
        out.append({"id": prefix + guid, "source": source, "publisher": publisher or None, "kind": "headline",
                    "symbol": symbol, "title": title, "summary": " ".join(summary.split()),
                    "link": link, "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, symbol):
    """Yahoo chart JSON -> {"symbol", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % symbol)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % symbol)
    when = meta.get("regularMarketTime")
    return {"symbol": symbol, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for s in sorted(watch):
        name = "chart-%s.json" % s
        try:
            try:
                data = fetcher.get(CHART % ("query2", nse.yahoo_quoted(s)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", nse.yahoo_quoted(s)), name)
            out.append(parse_chart(data, s))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (s, exc))
    return out


def mention_patterns(watch):
    """How a headline names a stock: the symbol in brackets or after NSE:, the symbol as a word
    when it is not an ordinary word, or the company's short name."""
    pats = {}
    for s, name in watch.items():
        alts = [r"\(%s\)" % re.escape(s), r"\bNSE\s*:\s*%s\b" % re.escape(s)]
        if not s.isalpha() or len(s) >= 5:     # M&M, BAJAJ-AUTO, RELIANCE; not ITC or LT (ordinary words)
            alts.append(r"(?<![\w&-])%s(?![\w&-])" % re.escape(s))
        short = short_name(name)
        if short and len(short) >= 4:
            alts.append(r"\b%s\b" % re.escape(short).replace(r"\ ", r"[\s-]+"))
        pats[s] = re.compile("|".join(alts), re.I)
    return pats


def tag(items, watch):
    """Which of your stocks each item actually names, and what it is about. A Google News
    match that names none of them gets touches [] (shown with a "?")."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = set()
        if it["source"] != "Google News" and it["symbol"] in watch:
            touches.add(it["symbol"])
        for s, rx in pats.items():
            if rx.search(hay):
                touches.add(s)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def name_words(name):
    """The distinctive words of a company name (4+ letters): 'Mahindra & Mahindra' -> {'mahindra'}."""
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", short_name(name) or "")} - {"india", "indian", "bank",
                                                                                       "company", "industries"}


def drop_strays(items, watch):
    """Google matches that name none of your stocks and share no word with the company's name
    (a market wrap, a list of 'stocks to buy') -> (kept, dropped count)."""
    kept, dropped = [], 0
    for it in items:
        if it["source"] == "Google News" and not it["touches"]:
            words = name_words(watch.get(it["symbol"]))
            title = it["title"].lower()
            if not words or not any(w in title for w in words):
                dropped += 1
                continue
        kept.append(it)
    return kept, dropped


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day (across sources)."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for s in it.get("touches", []):
                if s not in hit["touches"]:
                    hit["touches"].append(s)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, google=True, hours=48, errors=None):
    errors = [] if errors is None else errors
    items = []
    yahoo_empty = []
    for s in sorted(watch):
        try:
            got = parse_rss(fetcher.get(YAHOO % nse.yahoo_quoted(s), "yahoo-%s.xml" % s), s)
            items += got
            if not got:
                yahoo_empty.append(s)
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("Yahoo headlines for %s: %s" % (s, exc))
    if google:
        for s in sorted(watch):
            short = short_name(watch[s])
            if not short:
                errors.append("no company name for %s, so no Google News search: run scan.py universe "
                              "(trader/universe.txt) or pass --names %s=\"Company Name\"" % (s, s))
                continue
            try:
                items += parse_rss(fetcher.get(google_url(short, hours), "google-%s.xml" % s), s, "Google News")
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("Google News for %s: %s" % (s, exc))
    if yahoo_empty and not google:
        errors.append("Yahoo carries no headlines for %s (its NSE coverage is thin); drop --no-google"
                      % ", ".join(yahoo_empty))
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_ist(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-10s %s %+.2f%% vs yesterday's close %s%s  %s" % (
            m["symbol"], rupees.inr(m["price"]), m["pct"], rupees.inr(m["prev_close"]), when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_ist(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"]) if it["touches"] else it["symbol"] + "?"
        src = it["publisher"] or it["source"]
        out.append("  %s  %-11s %-18s %s" % (when, who, src[:18], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    if any(not it["touches"] for it in items):
        out.append("A symbol with \"?\": the search matched, but the headline does not name the company; "
                   "it may be about something else.")
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "Not financial advice." % to_ist(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--symbols", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--names", nargs="*", metavar="SYM=NAME", help="company names for the Google search")
    ap.add_argument("--universe", default=DEFAULT_UNIVERSE, help="SYMBOL|Name list for the company names")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-google", action="store_true", help="Yahoo's feed only")
    ap.add_argument("--loose", action="store_true",
                    help="keep Google matches that do not name the company (market wraps, lists)")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time, for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        known = watch_from_files(args.portfolio, args.watchlist, args.plan)
        if args.symbols:
            watch = {symbol_of(s): None for s in args.symbols}
            watch.update({s: known[s] for s in watch if known.get(s)})
        else:
            watch = known
        names = universe_names(args.universe)
        for pair in args.names or []:
            s, _, n = pair.partition("=")
            names[symbol_of(s)] = n.strip()
        for s in watch:
            watch[s] = watch[s] or names.get(s)
    except NewsError as e:
        print("news: %s" % e, file=sys.stderr)
        return 2
    if not watch:
        print("news: no symbols. Pass --symbols, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, google=not args.no_google, hours=args.hours, errors=errors)
    items = dedupe(tag(items, watch))
    if not args.loose:
        items, strays = drop_strays(items, watch)
        if strays:
            errors.append("%d Google match%s that name none of your stocks left out (market wraps, lists); "
                          "--loose shows them" % (strays, "" if strays == 1 else "es"))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
