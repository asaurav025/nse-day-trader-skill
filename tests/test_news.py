import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, ROOT, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")      # real feeds fetched on Fri 2026-10-30
NOW = "2026-10-30T10:15:00+05:30"           # results day for RELIANCE, 10:15 IST
UTC = datetime.timezone.utc


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


class Feeds(unittest.TestCase):
    def test_yahoo_rss(self):
        items = news.parse_rss(raw("yahoo-TCS.xml"), "TCS")
        self.assertEqual(len(items), 14)
        first = items[0]
        self.assertEqual(first["title"],
                         "TCS and Workato Join Forces to Speed Enterprise Integration Modernization with AI")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 26, 13, 0, tzinfo=UTC))
        self.assertEqual((first["source"], first["publisher"]), ("Yahoo Finance", None))
        self.assertTrue(first["id"].startswith("rss:"))
        self.assertTrue(first["link"].startswith("https://finance.yahoo.com/"))

    def test_google_news_rss_strips_the_publisher(self):
        items = news.parse_rss(raw("google-RELIANCE.xml"), "RELIANCE", "Google News")
        self.assertEqual(len(items), 100)
        first = items[0]
        self.assertEqual(first["title"], "Ambani's Reliance set for profit jump on refining, Jio boost")
        self.assertEqual(first["publisher"], "Reuters")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 30, 6, 35, tzinfo=UTC))
        self.assertTrue(first["id"].startswith("google:"))
        self.assertTrue(first["link"].startswith("https://news.google.com/"))
        self.assertTrue(all(not i["title"].endswith(" - " + (i["publisher"] or "\0")) for i in items))

    def test_yahoos_empty_nse_feed(self):
        self.assertEqual(news.parse_rss(raw("yahoo-RELIANCE.xml"), "RELIANCE"), [])
        errors = []
        f = news.Fetcher(offline=DIR)
        self.assertEqual(news.collect(f, {"RELIANCE": None}, google=False, errors=errors), [])
        self.assertIn("Yahoo carries no headlines for RELIANCE (its NSE coverage is thin)", errors[0])

    def test_times_print_in_ist(self):
        self.assertEqual(news.parse_time("Fri, 30 Oct 2026 04:45:00 GMT").hour, 4)
        self.assertEqual(news.parse_time("2026-10-30T10:15:00+05:30").hour, 4)
        self.assertIsNone(news.parse_time("soon"))
        when = datetime.datetime(2026, 10, 30, 4, 45, tzinfo=UTC)
        self.assertEqual(news.to_ist(when).strftime("%H:%M"), "10:15")
        self.assertEqual(news.to_ist(datetime.datetime(2026, 1, 15, 4, 45, tzinfo=UTC)).strftime("%H:%M"), "10:15")

    def test_feed_urls(self):
        self.assertIn("s=M%26M.NS&region=IN&lang=en-IN", news.YAHOO % news.nse.yahoo_quoted("M&M"))
        self.assertEqual(news.google_url("Reliance Industries", 48),
                         "https://news.google.com/rss/search?q=%22Reliance+Industries%22+when%3A2d"
                         "&hl=en-IN&gl=IN&ceid=IN:en")
        self.assertIn("when%3A5d", news.google_url("Mahindra & Mahindra", 100))
        self.assertIn("%26", news.google_url("Mahindra & Mahindra", 24))
        self.assertIn("when%3A30d", news.google_url("TCS", 10000))

    def test_company_names(self):
        self.assertEqual(news.short_name("Reliance Industries Limited"), "Reliance Industries")
        self.assertEqual(news.short_name("Mahindra & Mahindra Ltd."), "Mahindra & Mahindra")
        self.assertEqual(news.short_name("  Tata  Consultancy Services  Ltd "), "Tata Consultancy Services")
        self.assertIsNone(news.short_name(None))
        with tempfile.TemporaryDirectory() as d:
            u = os.path.join(d, "universe.txt")
            with open(u, "w", encoding="utf-8") as fh:
                fh.write("RELIANCE|Reliance Industries Limited\nM&M|Mahindra & Mahindra Limited\nBRK.B|nope\n")
            self.assertEqual(news.universe_names(u), {"RELIANCE": "Reliance Industries Limited",
                                                      "M&M": "Mahindra & Mahindra Limited"})


class Tagging(unittest.TestCase):
    def test_touches_and_question_marks(self):
        watch = {"M&M": "Mahindra & Mahindra Limited"}
        items = news.tag(news.parse_rss(raw("google-M&M.xml"), "M&M", "Google News"), watch)
        def first(prefix):
            return next(i for i in items if i["title"].startswith(prefix))
        self.assertEqual(first("Mahindra & Mahindra transfers stock options")["touches"], ["M&M"])
        self.assertEqual(first("Mahindra Teases New Thar")["touches"], [])

    def test_symbol_mentions(self):
        pats = news.mention_patterns({"M&M": None, "BAJAJ-AUTO": None, "ITC": None, "RELIANCE": None})
        self.assertTrue(pats["M&M"].search("Buy NSE: M&M on dips"))
        self.assertFalse(pats["M&M"].search("Mahindra Finance (NSEI:M&MFIN) fair value"))
        self.assertTrue(pats["BAJAJ-AUTO"].search("BAJAJ-AUTO hits a record"))
        self.assertTrue(pats["ITC"].search("ITC Ltd (ITC) results"))
        self.assertFalse(pats["ITC"].search("the itc says"))           # a short plain word is not a mention
        self.assertTrue(pats["RELIANCE"].search("Reliance KG basin dispute"))

    def test_strays_are_left_out_unless_loose(self):
        watch = {"M&M": "Mahindra & Mahindra Limited"}
        items = news.tag(news.parse_rss(raw("google-M&M.xml"), "M&M", "Google News"), watch)
        kept, dropped = news.drop_strays(items, watch)
        self.assertEqual(len(kept) + dropped, len(items))
        self.assertGreater(dropped, 0)
        self.assertTrue(all(i["touches"] or "mahindra" in i["title"].lower() for i in kept))
        wraps = [i for i in items if i["title"].startswith("Sensex")]
        self.assertTrue(wraps and not any(w in kept for w in wraps))

    def test_dedupe_merges_the_same_story_across_sources(self):
        a = news.parse_rss(raw("yahoo-TCS.xml"), "TCS")
        b = [dict(i, id="google:" + i["id"], source="Google News", publisher="Somewhere", link="g" + i["link"])
             for i in a]
        items = news.dedupe(news.tag(a + b, {"TCS": "Tata Consultancy Services Limited"}))
        self.assertEqual(len(items), 14)
        self.assertEqual(items[0]["source"], "Yahoo Finance")

    def test_india_day_trading_tags(self):
        items = [{"id": "x", "source": "Yahoo Finance", "publisher": None, "kind": "headline", "symbol": "M&M",
                  "title": "M&M hits upper circuit after Q2 results; bonus issue, SEBI order, RBI repo rate cut",
                  "summary": "", "link": "", "time": None}]
        tags = news.tag(items, {"M&M": None})[0]["tags"]
        for t in ("results", "circuit", "capital", "regulatory", "macro", "legal"):
            self.assertIn(t, tags)


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        none = os.path.join(self.tmp.name, "none.json")
        self.base = ["--offline", DIR, "--state", self.state, "--now", NOW, "--portfolio", none,
                     "--watchlist", none, "--plan", none, "--universe", none]
        self.rel = ["--names", "RELIANCE=Reliance Industries Limited"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        code, out, err = run(["--symbols", "RELIANCE", "--hours", "24", "--json"] + self.rel + self.base)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 33)
        self.assertTrue(all(t <= "2026-10-30T04:45:00+00:00" for t in times))   # nothing after the clock
        self.assertIn("26 Google matches that name none of your stocks left out", data["notes"][0])
        code, out, _ = run(["--symbols", "RELIANCE", "--hours", "24"] + self.rel + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_the_reel_reliance_results_morning(self):
        code, out, err = run(["--symbols", "RELIANCE", "--hours", "24", "--no-moves"] + self.rel + self.base)
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "  2026-10-30 09:59 IST  RELIANCE    The Economic Times Reliance Industries Q2 "
                                   "results today: Can the old economy rescue the new Reliance?")
        self.assertIn("tags: results", lines[1])
        self.assertIn("Moneycontrol.com", out)
        self.assertNotIn("Reuters", out)                       # 12:05 IST, after the pinned clock
        self.assertIn("Checked 2026-10-30 10:15 IST", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(chr(0x2014), out)

    def test_loose_shows_the_market_wraps(self):
        args = ["--symbols", "m&m.ns", "--names", "M&M=Mahindra & Mahindra Limited", "--hours", "48",
                "--now", "2026-10-30T18:00:00+05:30", "--json", "--dry-run"] + self.base[:2]
        strict = json.loads(run(args)[1])
        loose = json.loads(run(args + ["--loose"])[1])
        self.assertEqual((len(strict["items"]), len(loose["items"])), (14, 32))
        self.assertTrue(any(i["touches"] == [] for i in strict["items"]))     # shown with a "?"
        code, out, _ = run([a for a in args if a != "--json"])
        self.assertIn("M&M?", out)
        self.assertIn("does not name the company", out)

    def test_symbols_are_normalised_and_checked(self):
        code, out, err = run(["--symbols", "m&m.ns", "--no-google", "--hours", "2400", "--json", "--dry-run"]
                             + self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual({i["symbol"] for i in json.loads(out)["items"]}, {"M&M"})
        code, _, err = run(["--symbols", "BRK.B"] + self.base)
        self.assertEqual(code, 2)
        self.assertIn("not an NSE symbol", err)

    def test_symbols_and_names_from_the_trader_files(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [{"symbol": "RELIANCE.NS", "setups": ["orb"]}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "m&m"}, fh)
        self.assertEqual(sorted(news.watch_from_files("/nonexistent.json", wl, plan)), ["M&M", "RELIANCE"])
        uni = os.path.join(self.tmp.name, "universe.txt")
        with open(uni, "w") as fh:
            fh.write("RELIANCE|Reliance Industries Limited\n")
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        args[args.index("--universe") + 1] = uni
        code, out, err = run(["--json", "--dry-run", "--hours", "24"] + args)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(any(i["source"] == "Google News" for i in data["items"]))
        self.assertEqual({s for i in data["items"] for s in i["touches"]}, {"RELIANCE"})
        self.assertFalse(os.path.exists(self.state))

    def test_no_symbols_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no symbols", err)

    def test_no_name_means_no_google_search(self):
        code, out, _ = run(["--symbols", "TCS", "--hours", "48", "--no-moves", "--dry-run",
                            "--now", "2026-10-30T18:00:00+05:30"] + self.base[:2])
        self.assertIn("no company name for TCS, so no Google News search", out)
        self.assertIn("Yahoo Finance", out)
        self.assertNotIn("Google", out.split("note:")[0])

    def test_moves_in_rupees_and_ist(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        self.assertEqual((m["price"], m["prev_close"], m["pct"]), (1164.9, 1167.4, -0.21))
        self.assertEqual(news.render_moves([m])[0].rstrip(),
                         "RELIANCE   ₹1,164.90 -0.21% vs yesterday's close ₹1,167.40 at 15:28 IST")
        self.assertIn("MOVING", news.render_moves([dict(m, pct=-3.1)])[0])
        self.assertNotIn("MOVING", news.render_moves([dict(m, pct=-1.2)])[0])
        self.assertIn("MOVING", news.render_moves([dict(m, pct=-1.2)], move_pct=1.0)[0])
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "NOPE")
        # no chart file offline: no move line and no error note for it
        code, out, _ = run(["--symbols", "HDFCBANK", "--no-google", "--hours", "24", "--dry-run"] + self.base)
        self.assertNotIn("vs yesterday's close", out)
        self.assertNotIn("price for", out)

    def test_no_sec_anywhere(self):
        for name in ("parse_sec_atom", "sec_user_agent", "cik_map", "SEC_FEED"):
            self.assertFalse(hasattr(news, name), name)
        with open(os.path.join(ROOT, "trader-news", "news.py"), encoding="utf-8") as fh:
            self.assertNotIn("sec.gov", fh.read())
        with self.assertRaises(SystemExit):
            run(["--symbols", "RELIANCE", "--no-sec"] + self.base)

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-09-01T00:00:00+00:00", "new": "2026-10-29T00:00:00+00:00"}}
        now = news.parse_time(NOW)
        news.remember([], state, now)
        self.assertEqual(sorted(state["seen"]), ["new"])
        self.assertEqual(state["last_check"], now.isoformat())


if __name__ == "__main__":
    unittest.main()
