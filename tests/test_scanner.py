"""trader-scanner tests, offline, on real NSE data. Fixtures in tests/fixtures/trader-scanner/:

EQUITY_L.csv                  NSE's list of equities as published on 2026-10-30 (2,620 rows)
wall5m-2026-10-29.json.gz     {SYMBOL: Yahoo v8 chart}, 50 NSE stocks, range=5d interval=5m,
                              fetched 2026-10-30 after the close (it holds Oct 23 to Oct 30);
                              30 of them are the most traded hits of the full-universe 10:15
                              scan of Thu 2026-10-29, the rest large caps that did not break out
spark5m-2026-10-29.json       {"answers": [...]}: Yahoo's three raw spark answers (20 symbols a
                              request) for the same 50 symbols, range=5d interval=5m, fetched with
                              the wall; ASIANPAINT, LAURUSLABS and LTF each miss one real bar
                              before 10:15 on Oct 29 that the v8 chart has
daily-spark-2026-10-30.json   a raw spark answer, 20 large caps, range=3mo interval=1d
"""

import copy
import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

scan = load("trader-scanner", "scan")    # its bootstrap puts the repo root on sys.path
from trader_common import clock  # noqa: E402

SDIR = os.path.join(FIX, "trader-scanner")
EQL = os.path.join(SDIR, "EQUITY_L.csv")
WALL = os.path.join(SDIR, "wall5m-2026-10-29.json.gz")
SPARK5 = os.path.join(SDIR, "spark5m-2026-10-29.json")
DAILY = os.path.join(SDIR, "daily-spark-2026-10-30.json")
DT = datetime.datetime
D = datetime.date
DAY = D(2026, 10, 29)
AT_1015 = DT(2026, 10, 29, 10, 15)

_WALL = None
_SPARK = None


def wall():
    global _WALL
    if _WALL is None:
        _WALL = scan.load_offline(WALL)[0]
    return copy.deepcopy(_WALL)


def wall_raw():
    with gzip.open(WALL, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def spark5():
    global _SPARK
    if _SPARK is None:
        with open(SPARK5, encoding="utf-8") as fh:
            doc = json.load(fh)
        _SPARK = {}
        for ans in doc["answers"]:
            _SPARK.update(scan.parse_spark(ans))
    return copy.deepcopy(_SPARK)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """IST wall-clock text -> unix seconds."""
    fmt = "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2 else "%Y-%m-%d %H:%M"
    return clock.ist_ts(DT.strptime(ist_text, fmt))


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-minute bars from `start` IST on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [clock.ist_ts(t0 + datetime.timedelta(minutes=5 * i)) for i in range(len(closes))]
    s = {"t": t, "c": list(closes),
         "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


def drop_slot(series, ist_hhmm, day="2026-10-29"):
    """The series without the bar that starts at ist_hhmm on day."""
    s = copy.deepcopy(series)
    gone = ts("%s %s" % (day, ist_hhmm))
    keep = [i for i, t in enumerate(s["t"]) if t != gone]
    for k in ("t", "c", "h", "l", "v", "o"):
        if isinstance(s.get(k), list):
            s[k] = [s[k][i] for i in keep]
    return s


class Universe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(EQL, encoding="utf-8") as fh:
            cls.text = fh.read()
        cls.kept, cls.counts = scan.build_universe(cls.text)
        cls.rows = {r["SYMBOL"]: r for r in scan.parse_equity_list(cls.text)}

    def test_nse_list_of_2026_10_30(self):
        self.assertEqual(self.counts["rows"], 2620)
        self.assertEqual(len(self.kept), 2357)
        self.assertEqual(self.counts["series BE"], 230)
        self.assertEqual(self.counts["series BZ"], 33)
        self.assertEqual(self.counts["keep"], 2357)
        names = dict(self.kept)
        self.assertEqual(names["RELIANCE"], "Reliance Industries Limited")
        for sym in ("M&M", "BAJAJ-AUTO", "20MICRONS", "HDFCBANK", "GVT&D"):
            self.assertIn(sym, names, sym)
        self.assertEqual([s for s, _ in self.kept], sorted(s for s, _ in self.kept))

    def test_one_case_per_rule(self):
        self.assertEqual(scan.classify(self.rows["RELIANCE"]), "keep")
        be = next(r for r in self.rows.values() if r["SERIES"] == "BE")
        self.assertEqual(scan.classify(be), "series BE")
        self.assertEqual(scan.classify({"SYMBOL": "ABC", "SERIES": "SM"}), "series SM")
        self.assertEqual(scan.classify({"SYMBOL": "ABC", "SERIES": ""}), "series blank")
        self.assertEqual(scan.classify({"SYMBOL": "BRK.B", "SERIES": "EQ"}), "not a plain NSE symbol")
        self.assertEqual(scan.classify({"SYMBOL": "AB-", "SERIES": "EQ"}), "not a plain NSE symbol")
        self.assertEqual(scan.classify({"SYMBOL": "M&M", "SERIES": " eq "}), "keep")

    def test_header_with_spaces_and_a_bom(self):
        text = "﻿SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING\nABC,Abc Limited,EQ,01-JAN-2000\n\n"
        rows = scan.parse_equity_list(text)
        self.assertEqual(rows, [{"SYMBOL": "ABC", "NAME OF COMPANY": "Abc Limited", "SERIES": "EQ",
                                 "DATE OF LISTING": "01-JAN-2000"}])

    def test_not_an_equity_list(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_list("Symbol|Security Name\nAAPL|Apple Inc.\n")
        with self.assertRaises(scan.ScanError):
            scan.parse_equity_list("")


class Time(unittest.TestCase):
    def test_ist_has_no_daylight_saving(self):
        for text, utc in (("2026-10-29 10:15", DT(2026, 10, 29, 4, 45)), ("2026-01-15 09:15", DT(2026, 1, 15, 3, 45)),
                          ("2026-07-01 15:30", DT(2026, 7, 1, 10, 0))):
            self.assertEqual(scan.parse_now(text), utc)
            self.assertEqual(scan.to_ist(utc), DT.strptime(text, "%Y-%m-%d %H:%M"))
        self.assertEqual(scan.parse_now("2026-10-29T04:45:00Z"), DT(2026, 10, 29, 4, 45))
        self.assertEqual(scan.clock_label(datetime.time(9, 35)), "9:35 AM")
        self.assertEqual(scan.clock_label(datetime.time(15, 30)), "3:30 PM")

    def test_nse_calendar(self):
        self.assertEqual(scan.trading_day(DAY), (True, ""))
        self.assertEqual(scan.trading_day(D(2026, 10, 31)), (False, "weekend"))
        self.assertEqual(scan.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali-Balipratipada"))
        self.assertEqual(scan.session(DAY), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 10, 2)))

    def test_bad_times(self):
        for bad in ("10", "25:00", "10:61", "ten"):
            with self.assertRaises(scan.ScanError):
                scan.parse_hhmm(bad)
        with self.assertRaises(scan.ScanError):
            scan.parse_now("yesterday")


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        data = spark5()
        self.assertEqual(len(data), 50)
        for sym in ("M&M", "BAJAJ-AUTO", "GVT&D", "RELIANCE"):
            self.assertIn(sym, data, sym)
        self.assertFalse(any(k.endswith(".NS") for k in data))
        s = data["RELIANCE"]
        self.assertEqual(len(s["t"]), len(s["c"]))
        self.assertIsNone(s["h"])                       # spark: closes only
        with open(SPARK5, encoding="utf-8") as fh:
            first = json.load(fh)["answers"][0]
        wanted = {"M&M.NS": "M&M"}
        got = scan.parse_spark(first, wanted)
        self.assertTrue(set(got) <= set(data))

    def test_spark_errors(self):
        with self.assertRaises(scan.ScanError):
            scan.parse_spark({"nope": 1})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark({"spark": {"result": [], "error": {"code": "Bad Request", "description": "bad"}}})
        self.assertEqual(scan.parse_spark({"spark": {"result": [{"symbol": "X.NS", "response": []}]}}), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_chart({"chart": {"result": None, "error": {"description": "No data found"}}}, "ZZZZ")

    def test_urls_quote_nse_symbols(self):
        self.assertEqual(scan.spark_path(["M&M", "RELIANCE"], "1d", "5m"),
                         "/v7/finance/spark?symbols=M%26M.NS,RELIANCE.NS&range=1d&interval=5m")
        self.assertEqual(scan.chart_path("BAJAJ-AUTO", "5d", "5m"),
                         "/v8/finance/chart/BAJAJ-AUTO.NS?range=5d&interval=5m")


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        with open(SPARK5, encoding="utf-8") as fh:
            self.body = json.dumps(json.load(fh)["answers"][0])

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20, headers=None):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        self.assertEqual(scan.yahoo_get("/v7/finance/spark?symbols=RELIANCE.NS&range=1d&interval=5m"), self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        first = sorted(scan.parse_spark(self.body))
        syms = first[:19] + ["ZZZZQX"] + ["X%02d" % i for i in range(20)]   # batch 1 answers, batch 2 never does
        data, failed = scan.fetch_spark(syms, "5d", "5m", 0.3)
        self.assertEqual(len(data), 19)
        self.assertEqual(failed["ZZZZQX"], "no data from Yahoo")
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOPE.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Daily(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DAILY, encoding="utf-8") as fh:
            cls.data = scan.parse_spark(json.load(fh))
        cls.syms = sorted(cls.data)

    def run_daily(self, now, min_price=50, min_volume=500000, data=None):
        return scan.scan_daily(data or self.data, self.syms, scan.parse_now(now), min_price, min_volume)

    def test_setups_before_the_open(self):
        hits, counts, last = self.run_daily("2026-10-30 08:45")
        self.assertEqual(last, D(2026, 10, 29))
        self.assertEqual(counts["requested"], 20)
        self.assertEqual(counts["scanned"] + counts["illiquid"] + counts["short"], 20)
        got = {h["symbol"]: h["setups"] for h in hits}
        self.assertEqual(got["KOTAKBANK"], ["breakout_20d"])
        self.assertEqual(got["INFY"], ["bull_flag"])
        self.assertIn("reclaim_ma20", got["SBIN"])
        # the rules, recomputed from the closes here
        for h in hits:
            closes = [c for _, c in scan.daily_closes(self.data[h["symbol"]], scan.parse_now("2026-10-30 08:45"))]
            if "breakout_20d" in h["setups"]:
                self.assertGreater(closes[-1], max(closes[-21:-1]), h["symbol"])
            if "bull_flag" in h["setups"]:
                self.assertGreater(closes[-6] / closes[-16] - 1, 0.10, h["symbol"])
                self.assertLess((max(closes[-5:]) - min(closes[-5:])) / closes[-1], 0.05, h["symbol"])
        vals = [h["price"] * (h["volume"] or 0) for h in hits]
        self.assertEqual(vals, sorted(vals, reverse=True))   # most rupees traded first

    def test_the_in_progress_bar_is_dropped(self):
        _, _, during = self.run_daily("2026-10-30 11:00")
        _, _, after = self.run_daily("2026-10-30 16:00")
        self.assertEqual(during, D(2026, 10, 29))
        self.assertEqual(after, D(2026, 10, 30))

    def test_liquidity_filter(self):
        hits, counts, _ = self.run_daily("2026-10-30 08:45", min_volume=10 ** 12)
        self.assertEqual((hits, counts["illiquid"]), ([], 20))
        _, counts, _ = self.run_daily("2026-10-30 08:45", min_price=100000, min_volume=0)
        self.assertEqual(counts["illiquid"], 20 - sum(1 for s in self.syms
                                                      if self.data[s]["meta"]["regularMarketPrice"] >= 100000))

    def test_short_and_empty_charts(self):
        data = {"ABC": bars_series("2026-10-28", [100.0] * 10), "XYZ": None}
        data["ABC"]["meta"] = {"regularMarketPrice": 100.0, "regularMarketVolume": 10 ** 7}
        hits, counts, last = scan.scan_daily(data, ["ABC", "XYZ"], scan.parse_now("2026-10-30 08:45"), 50, 0)
        self.assertEqual((hits, counts["fetched"], counts["short"], last), ([], 1, 1, None))

    def test_rsi_is_wilders(self):
        closes = [c for _, c in scan.daily_closes(self.data["TCS"], scan.parse_now("2026-10-30 08:45"))]
        n = 14
        diffs = [b - a for a, b in zip(closes, closes[1:])]
        up = sum(max(d, 0) for d in diffs[:n]) / n
        dn = sum(max(-d, 0) for d in diffs[:n]) / n
        for d in diffs[n:]:
            up, dn = (up * 13 + max(d, 0)) / 14, (dn * 13 + max(-d, 0)) / 14
        self.assertAlmostEqual(scan.rsi(closes), 100 - 100 / (1 + up / dn), places=9)
        self.assertIsNone(scan.rsi(closes[:14]))
        self.assertEqual(scan.rsi([1.0 + i for i in range(20)]), 100.0)


class Orb(unittest.TestCase):
    def test_the_10_15_scan_of_2026_10_29(self):
        hits, counts, failed = scan.scan_orb(wall(), sorted(wall()), DAY, AT_1015, 15, 50, 0)
        self.assertEqual((counts["fetched"], counts["scanned"], counts["candidates"], counts["hits"]), (50, 50, 42, 38))
        self.assertEqual(failed, {})
        by = {h["symbol"]: h for h in hits}
        r = by["RELIANCE"]
        self.assertEqual((r["at"], r["price"], r["or_low"], r["or_high"], r["pct_over"], r["bar_start"], r["bar_index"]),
                         ("9:40 AM", 1174.3, 1156.2, 1170.5, 0.32, "09:35", 4))
        h = by["HDFCBANK"]
        self.assertEqual((h["at"], h["price"], h["or_high"]), ("9:35 AM", 682.5, 682.45))
        self.assertEqual(hits[-1]["symbol"], "SBIN")            # sorted by the time it broke out
        self.assertEqual(hits[-1]["at"], "10:10 AM")
        self.assertNotIn("M&M", by)                              # it broke out at 1:00 PM, not by 10:15
        self.assertNotIn("BAJAJ-AUTO", by)

    def test_by_hand_from_the_reliance_bars(self):
        bars = scan.session_bars(wall()["RELIANCE"], DAY, AT_1015)
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars][:4], ["09:15", "09:20", "09:25", "09:30"])
        self.assertEqual(len(bars), 12)
        self.assertAlmostEqual(max(b["h"] for b in bars[:3]), 1170.5, places=2)
        self.assertAlmostEqual(min(b["l"] for b in bars[:3]), 1156.2, places=2)
        self.assertLess(bars[3]["c"], 1170.5)                    # the 9:30 bar closes under the high
        self.assertGreater(bars[4]["c"], 1170.5)                 # the 9:35 bar closes over it

    def test_price_floor(self):
        w = wall()
        hits, counts, _ = scan.scan_orb(w, sorted(w), DAY, AT_1015, 15, 1000, 0)
        under = [s for s in w if scan.session_bars(w[s], DAY, AT_1015)[-1]["c"] < 1000]
        self.assertEqual(counts["illiquid"], len(under))
        self.assertTrue(all(h["price"] >= 1000 for h in hits))
        self.assertIn("RELIANCE", {h["symbol"] for h in hits})

    def test_in_progress_bar_and_no_breakout_yet(self):
        w = wall()
        hits, counts, _ = scan.scan_orb(w, ["RELIANCE"], DAY, DT(2026, 10, 29, 9, 39), 15)
        self.assertEqual((hits, counts["scanned"]), ([], 1))    # the 9:35 bar closes at 9:40
        hits, _, _ = scan.scan_orb(w, ["RELIANCE"], DAY, DT(2026, 10, 29, 9, 40), 15)
        self.assertEqual(hits[0]["at"], "9:40 AM")

    def test_the_live_tick_is_dropped(self):
        s = bars_series("2026-10-29", [100, 101, 102, 101, 100], extra=[("2026-10-29 09:41:45", 110)])
        bars = scan.session_bars(s, DAY, DT(2026, 10, 29, 9, 42))
        self.assertEqual(len(bars), 5)
        self.assertEqual(scan.scan_orb({"ABC": s}, ["ABC"], DAY, DT(2026, 10, 29, 9, 42), 15)[0], [])

    def test_the_null_15_20_bar(self):
        """Yahoo sends NSE's 15:20 bar with null prices: dropped, and the session's other bars are all there."""
        bars = scan.session_bars(wall()["RELIANCE"], DAY, DT(2026, 10, 29, 15, 30))
        starts = [b["start"].strftime("%H:%M") for b in bars]
        self.assertNotIn("15:20", starts)
        self.assertEqual(len(starts), 74)
        self.assertEqual((starts[0], starts[-1]), ("09:15", "15:25"))
        self.assertTrue(scan.usable(scan.session_bars(wall()["RELIANCE"], DAY, DT(2026, 10, 29, 14, 45)),
                                    DAY, DT(2026, 10, 29, 14, 45), 3))

    def test_stage_one_is_a_necessary_condition(self):
        w = wall()
        for sym, s in w.items():
            bars = scan.session_bars(s, DAY, AT_1015)
            if scan.orb_hit(bars, 3):
                self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_confirm_uses_the_v8_highs(self):
        sp = spark5()
        hits, counts, _ = scan.scan_orb(sp, ["RELIANCE", "HDFCBANK"], DAY, AT_1015, 15,
                                        confirm=lambda s: wall()[s])
        self.assertEqual(counts["confirmed"], 2)
        by = {h["symbol"]: h for h in hits}
        self.assertEqual(by["RELIANCE"]["or_high"], 1170.5)      # the v8 high, not the highest range close
        with self.assertRaises(TypeError):
            sum(sp["RELIANCE"]["h"])                             # spark has no highs at all

    def test_a_missing_v8_bar_makes_a_chart_unusable(self):
        w = wall()
        w["RELIANCE"] = drop_slot(w["RELIANCE"], "09:50")
        hits, counts, failed = scan.scan_orb(w, ["RELIANCE"], DAY, AT_1015, 15)
        self.assertEqual((hits, counts["unusable"]), ([], 1))
        sp = spark5()
        _, counts, failed = scan.scan_orb(sp, ["RELIANCE"], DAY, AT_1015, 15, confirm=lambda s: w[s])
        self.assertEqual(failed, {"RELIANCE": "the v8 chart has a missing bar"})

    def test_a_30_minute_range(self):
        hits, _, _ = scan.scan_orb(wall(), ["RELIANCE"], DAY, AT_1015, 30)
        self.assertEqual((hits[0]["at"], hits[0]["price"], hits[0]["or_high"]), ("9:50 AM", 1175.9, 1175.7))

    def test_window_messages(self):
        now = scan.parse_now("2026-10-29 10:20")
        until = datetime.time(14, 45)
        self.assertEqual(scan.orb_window(now, datetime.time(10, 15), 15, until), (DAY, AT_1015))
        cases = [
            (scan.parse_now("2026-10-29 09:33"), None, 15, "first bar after it closes at 9:35 AM"),
            (scan.parse_now("2026-11-10 10:15"), None, 15, "NSE holiday: Diwali-Balipratipada"),
            (scan.parse_now("2026-10-31 10:15"), None, 15, "weekend"),
            (scan.parse_now("2026-10-29 15:45"), None, 15, "The market closed at 3:30 PM IST"),
            (now, datetime.time(11, 0), 15, "later than now"),
            (scan.parse_now("2026-10-29 15:00"), None, 15, "after 14:45, your last time for new entries"),
            (now, None, 7, "multiple of 5"),
        ]
        for when, at, orm, words in cases:
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(when, at, orm, until)
            self.assertIn(words, str(cm.exception), words)


class SparkGaps(unittest.TestCase):
    """Yahoo's spark answer for NSE symbols drops real bars; stage 1 tolerates a few."""

    def test_the_real_gaps(self):
        sp, w = spark5(), wall()
        gappy = {}
        for sym in sorted(sp):
            want, missing = scan.gaps(scan.session_bars(sp[sym], DAY, AT_1015), DAY, AT_1015)
            self.assertEqual(want, 12)
            if missing:
                gappy[sym] = missing
                self.assertTrue(scan.usable(scan.session_bars(w[sym], DAY, AT_1015), DAY, AT_1015, 3), sym)
        self.assertEqual(gappy, {"ASIANPAINT": 1, "LAURUSLABS": 1, "LTF": 1})

    def test_gappy_charts_go_to_stage_two(self):
        sp = spark5()
        hits, counts, failed = scan.scan_orb(sp, sorted(sp), DAY, AT_1015, 15, 50, 0, confirm=lambda s: wall()[s])
        self.assertEqual((counts["gappy"], counts["unusable"], counts["hits"]), (3, 0, 38))
        self.assertIn("LAURUSLABS", {h["symbol"] for h in hits})
        self.assertEqual(failed, {})

    def test_offline_without_stage_two_stays_strict(self):
        sp = spark5()
        _, counts, _ = scan.scan_orb(sp, sorted(sp), DAY, AT_1015, 15, 50, 0)
        self.assertEqual((counts["gappy"], counts["unusable"]), (0, 3))

    def test_too_gappy_is_unusable_and_a_gap_on_the_breakout_bar_is_still_found(self):
        day = "2026-10-29"
        closes = [100, 100, 100, 100, 100, 100, 105, 105, 105, 105, 105, 105]
        full = bars_series(day, closes)
        holes = drop_slot(drop_slot(full, "09:45"), "09:50")      # 2 of 12 missing: over 10 %
        _, counts, _ = scan.scan_orb({"A": holes}, ["A"], DAY, AT_1015, 15, confirm=lambda s: full)
        self.assertEqual(counts["unusable"], 1)
        one = drop_slot(full, "09:45")                            # the breakout bar itself is the hole
        hits, counts, _ = scan.scan_orb({"A": one}, ["A"], DAY, AT_1015, 15, confirm=lambda s: full)
        self.assertEqual((counts["gappy"], hits[0]["at"], hits[0]["price"]), (1, "9:50 AM", 105.0))


class StageTwoCap(unittest.TestCase):
    def test_the_cap_takes_the_most_traded(self):
        sp, w = spark5(), wall()
        asked = []

        def confirm(sym):
            asked.append(sym)
            return w[sym]
        hits, counts, _ = scan.scan_orb(sp, sorted(sp), DAY, AT_1015, 15, 50, 0, confirm=confirm, max_confirm=5)
        self.assertEqual((counts["candidates"], counts["confirmed"], counts["unconfirmed"]), (42, 5, 37))
        traded = {}
        for s in sp:
            bars = scan.session_bars(sp[s], DAY, AT_1015)
            vol = sum(b["v"] for b in bars) if sp[s].get("v") else sp[s]["meta"].get("regularMarketVolume") or 0
            traded[s] = bars[-1]["c"] * vol
        cands = [s for s in sp if scan.orb_candidate(scan.session_bars(sp[s], DAY, AT_1015), 3)
                 or scan.gaps(scan.session_bars(sp[s], DAY, AT_1015), DAY, AT_1015)[1]]
        self.assertEqual(asked, sorted(cands, key=lambda s: -traded[s])[:5])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_universe_offline(self):
        code, text, _ = run(["universe", "--offline", EQL, "--out", "trader/universe.txt"])
        self.assertEqual(code, 0)
        self.assertIn("2,620 rows", text)
        self.assertIn("Kept 2,357 series-EQ equities", text)
        self.assertIn("230 series BE, 33 series BZ", text)
        with open(os.path.join("trader", "universe.txt"), encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertEqual(len(lines), 2357)
        self.assertIn("M&M|Mahindra & Mahindra Limited", lines)
        os.mkdir("dir")
        shutil.copy(EQL, os.path.join("dir", "EQUITY_L.csv"))
        code, text, _ = run(["universe", "--offline", "dir", "--no-write", "--json"])
        self.assertEqual(json.loads(text)["count"], 2357)
        code, _, err = run(["universe", "--offline", "nowhere"])
        self.assertEqual(code, 1)
        self.assertIn("no EQUITY_L.csv", err)

    def test_orb_end_to_end_offline(self):
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--out", "w.json"])
        self.assertEqual(code, 0)
        self.assertIn("Opening-range breakout scan, Thu 2026-10-29 10:15 AM IST: 15-minute range (9:15 to 9:30 AM)",
                      text)
        self.assertIn("38 of 50 charts closed above their opening-range high by 10:15 AM", text)
        self.assertIn("9:40 AM  RELIANCE      ₹1,174.30  range 1,156.20 to 1,170.50  +0.32% over", text)
        with open("w.json") as fh:
            doc = json.load(fh)
        self.assertEqual((doc["mode"], doc["as_of"], doc["universe"], len(doc["hits"])),
                         ("orb", "2026-10-29 10:15 IST", 50, 38))
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--json", "--no-write",
                             "--symbols", "M&M,bajaj-auto.ns"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)["hits"], [])
        self.assertFalse(os.path.exists(os.path.join("trader", "watchlist.json")))

    def test_offline_replay_needs_a_time(self):
        code, _, err = run(["orb", "--offline", WALL, "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("give --at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:15", "--no-write", "--json"])
        self.assertEqual(json.loads(text)["as_of"], "2026-10-30 10:15 IST")   # the file's last day

    def test_partial_stage_two_is_said(self):
        raw = wall_raw()
        real_now, real_fetch, real_get = scan.parse_now, scan.fetch_spark, scan.yahoo_get
        scan.parse_now = lambda t: real_now(t or "2026-10-29 10:20")
        scan.fetch_spark = lambda syms, rng, interval, pause, a=None: ({s: spark5()[s] for s in syms}, {})

        def get(path):
            sym = scan.nse.from_yahoo(path.split("/chart/")[1].split("?")[0].replace("%26", "&"))
            return json.dumps(raw[sym])
        scan.yahoo_get = get
        try:
            code, text, _ = run(["orb", "--now", "2026-10-29 10:15", "--at", "10:15", "--pause", "0", "--max-confirm",
                                 "10", "--symbols", ",".join(sorted(raw)), "--no-write"])
        finally:
            scan.parse_now, scan.fetch_spark, scan.yahoo_get = real_now, real_fetch, real_get
        self.assertEqual(code, 0)
        self.assertIn("PARTIAL: 32 more candidates were not checked (only the 10 most traded are", text)
        self.assertIn("3 of the candidates were sent to stage 2 because Yahoo's spark answer was missing a bar", text)

    def test_plain_messages_and_exit_codes(self):
        for argv, words in ((["orb", "--offline", WALL, "--now", "2026-11-10 10:15"], "NSE holiday"),
                            (["orb", "--offline", WALL, "--now", "2026-10-29 09:30"], "Run orb again after 9:35 AM"),
                            (["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--symbols", "BRK.B"],
                             "is not an NSE symbol"),
                            (["daily", "--universe", "missing.txt"], "no missing.txt yet: run `scan.py universe`"),
                            (["orb", "--offline", "nowhere.json", "--now", "2026-10-29 10:15"], "no offline file")):
            code, out, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(words, err, argv)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_offline_folder_of_v8_files(self):
        raw = wall_raw()
        os.mkdir("charts")
        for sym in ("RELIANCE", "M&M", "HDFCBANK"):
            with open(os.path.join("charts", sym + ".json"), "w") as fh:
                json.dump(raw[sym], fh)
        code, text, _ = run(["orb", "--offline", "charts", "--now", "2026-10-29 10:15", "--json", "--no-write"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["universe"], 3)
        self.assertEqual([h["symbol"] for h in doc["hits"]], ["HDFCBANK", "RELIANCE"])

    def test_symbol_lists(self):
        with open("list.txt", "w") as fh:
            fh.write("# my names\nRELIANCE|Reliance Industries Limited\nm&m\nreliance.ns\n")
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--watchlist", "list.txt",
                             "--json", "--no-write"])
        doc = json.loads(text)
        self.assertEqual((doc["universe"], [h["symbol"] for h in doc["hits"]]), (2, ["RELIANCE"]))
        with open("wl.json", "w") as fh:
            json.dump({"hits": [{"symbol": "HDFCBANK"}, {"symbol": "TCS"}]}, fh)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--watchlist", "wl.json",
                             "--json", "--no-write", "--limit", "1"])
        self.assertEqual(json.loads(text)["universe"], 1)

    def test_daily_offline_json(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-30 08:45", "--json", "--no-write"])
        doc = json.loads(text)
        self.assertEqual(code, 0)
        self.assertEqual((doc["mode"], doc["as_of"], doc["universe"]), ("daily", "2026-10-29 close", 20))
        self.assertEqual(doc["filters"], {"min_price": 50.0, "min_volume": 500000})
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-30 08:45", "--out", "d.json"])
        self.assertIn("passed the liquidity filter (₹50+ and 5,00,000+ shares)", text)
        self.assertTrue(os.path.exists("d.json"))


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-scanner", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-scanner\n"))
        low = " ".join(text.lower().split())
        for must in ("equity_l.csv", "2,357", "9:15", "ist", "not a list of stocks to buy", "relianc", "1,174.30",
                     "not financial advice"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "nasdaq", "nyse", "tsla", "new york"):
            self.assertNotIn(never, low, never)


if __name__ == "__main__":
    unittest.main()
