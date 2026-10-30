"""trader-entry tests, offline. Fixtures in tests/fixtures/trader-entry/:

<SYMBOL>-2026-10-29-5m.json   real Yahoo v8 charts (range=5d, interval=5m) for RELIANCE, HDFCBANK,
                              M&M, BAJAJ-AUTO and GAIL, fetched 2026-10-30 after the close
                              (meta.regularMarketTime 15:28 IST); they hold Oct 23 to Oct 30, the
                              15:20 bar of every day null, as Yahoo sends it
The one-minute DEMO chart is SYNTHETIC (built here, not market data): a wick above the range
high, a close AT the high, a null bar and a live tick.
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

entry = load("trader-entry", "entry")   # its bootstrap puts the repo root on sys.path
from trader_common import clock  # noqa: E402

EDIR = os.path.join(FIX, "trader-entry")
REL = os.path.join(EDIR, "RELIANCE-2026-10-29-5m.json")
HDFC = os.path.join(EDIR, "HDFCBANK-2026-10-29-5m.json")
MM = os.path.join(EDIR, "M&M-2026-10-29-5m.json")
BAJAJ = os.path.join(EDIR, "BAJAJ-AUTO-2026-10-29-5m.json")
GAIL = os.path.join(EDIR, "GAIL-2026-10-29-5m.json")
DT = datetime.datetime
DAY = datetime.date(2026, 10, 29)
OR_HIGH = 1170.5


def ist(text):
    return entry.parse_now(text)


def doc(path=REL):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def one_day(d=None, day=DAY):
    """The chart cut to one session, the way a range=1d answer looks after that day's close."""
    d = copy.deepcopy(d if d is not None else doc())
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    keep = [i for i, t in enumerate(r["timestamp"]) if clock.ts_to_ist(t).date() == day]
    r["timestamp"] = [r["timestamp"][i] for i in keep]
    for k in q:
        q[k] = [q[k][i] for i in keep]
    r["meta"]["regularMarketTime"] = clock.ist_ts(DT.combine(day, datetime.time(15, 29, 50)))
    return d


def chart(d=None, sym="RELIANCE"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def no_breakout(d=None):
    """Every bar after the range stays under the 1,170.50 high."""
    d = copy.deepcopy(d if d is not None else one_day())
    r = d["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    for i, t in enumerate(r["timestamp"]):
        if clock.ts_to_ist(t).time() < datetime.time(9, 30) or q["close"][i] is None:
            continue
        q["close"][i] = min(q["close"][i], 1170.0)
        q["high"][i] = min(q["high"][i], 1170.2)
        q["open"][i] = min(q["open"][i], 1170.0)
    return d


def demo_1m():
    """SYNTHETIC one-minute bars on 2026-10-29 for DEMO: range 9:15-9:29 high 100.50; 9:30 wicks to
    101 and closes 100.40; 9:31 closes AT 100.50; 9:32 is null; 9:33 closes 100.80 (the trigger);
    then a live tick at 9:34:40."""
    t0 = DT(2026, 10, 29, 9, 15)
    ts, o, h, lo, c, v = [], [], [], [], [], []

    def add(minute, hi, cl, vol=1000, sec=0):
        ts.append(clock.ist_ts(t0 + datetime.timedelta(minutes=minute, seconds=sec)))
        o.append(cl), h.append(hi), lo.append(None if cl is None else min(cl, 99.5)), c.append(cl), v.append(vol)
    for m in range(15):
        add(m, 100.5 if m == 7 else 100.2, 100.0)
    add(15, 101.0, 100.4)
    add(16, 100.5, 100.5)
    add(17, None, None, None)
    add(18, 100.9, 100.8, 3000)
    add(19, 101.0, 101.0, 50, sec=40)
    meta = {"symbol": "DEMO.NS", "dataGranularity": "1m", "regularMarketTime": ts[-1], "previousClose": 99.0}
    return {"chart": {"result": [{"meta": meta, "timestamp": ts,
                                  "indicators": {"quote": [{"open": o, "high": h, "low": lo, "close": c,
                                                            "volume": v}]}}], "error": None}}


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheReel(unittest.TestCase):
    def test_triggers_at_9_40_on_the_9_35_bar(self):
        r = at("2026-10-29 10:15")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["high"], 1170.5)
        self.assertEqual(r["opening_range"]["low"], 1156.2)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"], t["pct_over"]),
                         ("9:40 AM", "09:35", 4, 1174.3, 0.32))
        lines = entry.describe(r)
        self.assertIn("Opening range 9:15-9:30: high ₹1,170.50, low ₹1,156.20 (3 of 3 bars).", lines)
        self.assertIn("TRIGGERED 9:40 AM at ₹1,174.30: the 9:35 bar (bar 4) closed above the range high "
                      "₹1,170.50 (+0.32%).", lines)
        self.assertIn("Nothing was placed.", lines)

    def test_at_9_37_the_9_35_bar_is_still_forming(self):
        r = at("2026-10-29 09:37")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"], {"bar_start": "09:35", "closes": "9:40 AM", "price": 1174.3})
        lines = entry.describe(r)
        self.assertIn("WAITING  last close ₹1,169.50 (9:30 bar), ₹1.00 under the range high "
                      "₹1,170.50 (-0.09%).", lines)
        self.assertIn("The 9:35 bar is still forming (₹1,174.30 so far, closes 9:40 AM); it does not count "
                      "until it closes.", lines)

    def test_before_the_open_and_while_the_range_forms(self):
        r = at("2026-10-29 09:00")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("the NSE opens at 9:15", r["reason"])
        r = at("2026-10-29 09:22")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertIn("1 of 3 range bars closed; the range is set at 9:30", r["reason"])

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        d = one_day()
        r_ = d["chart"]["result"][0]
        q = r_["indicators"]["quote"][0]
        live = clock.ist_ts(DT(2026, 10, 29, 9, 41, 45))
        r_["timestamp"].append(live)
        for k, v in (("open", 1190.0), ("high", 1190.0), ("low", 1190.0), ("close", 1190.0), ("volume", 0)):
            q[k].append(v)
        r = at("2026-10-29 09:39", d)
        self.assertEqual(r["bars_closed"], 4)                    # 9:15 to 9:30; the 9:35 bar is still open
        self.assertEqual(r["status"], "waiting")

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at("2026-10-29 15:40")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertGreater(r["minutes_since"], 300)
        text = "\n".join(entry.describe(r))
        self.assertIn("A late entry is a different trade", text)
        self.assertNotIn("This is the moment to get in", text)


class OtherStocks(unittest.TestCase):
    def test_hdfcbank_at_9_35(self):
        r = entry.evaluate(entry.get_chart("HDFCBANK", "5m", offline=HDFC), ist("2026-10-29 10:15"))
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"], r["level"]), ("9:35 AM", 682.5, 682.45))

    def test_m_and_m_breaks_out_after_lunch(self):
        ch = entry.get_chart("m&m.ns", "5m", offline=MM)
        self.assertEqual(ch["symbol"], "M&M")
        self.assertEqual(entry.evaluate(ch, ist("2026-10-29 10:15"))["status"], "waiting")
        r = entry.evaluate(ch, ist("2026-10-29 14:50"))
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"], r["trigger"]["bar_index"]),
                         ("1:00 PM", 2891.6, 44))

    def test_bajaj_auto_expires_at_14_45(self):
        r = entry.evaluate(entry.get_chart("BAJAJ-AUTO", "5m", offline=BAJAJ), ist("2026-10-29 14:50"))
        self.assertEqual((r["status"], r["no_entries_after"]), ("expired", "14:45"))
        self.assertIn("no close above the range high ₹10,539.00 by 14:45", r["reason"])

    def test_gail_trades_in_paise(self):
        r = entry.evaluate(entry.get_chart("GAIL", "5m", offline=GAIL), ist("2026-10-29 14:50"))
        self.assertEqual((r["level"], r["trigger"]["time"], r["trigger"]["price"]), (164.99, "12:15 PM", 165.05))


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at("2026-10-29 10:15", min_volume_x=1.0)
        self.assertEqual(r["opening_range"]["avg_volume"], 368006)
        t = r["trigger"]
        self.assertEqual((t["time"], t["price"], t["bar_index"], t["volume_x"]), ("10:00 AM", 1173.8, 8, 2.26))
        thin = at("2026-10-29 09:52", min_volume_x=1.0)
        self.assertEqual(thin["status"], "waiting")
        self.assertIn("under your 1.00x", thin["reason"])

    def test_the_zero_volume_9_15_bar_is_left_out_of_the_average(self):
        bars = [b for b in chart()["bars"] if clock.ts_to_ist(b["t"]).date() == DAY][:3]
        self.assertEqual([b["v"] for b in bars], [0, 285741, 450272])
        self.assertEqual(at("2026-10-29 10:15")["opening_range"]["avg_volume"], (285741 + 450272) // 2)

    def test_level_setup(self):
        r = at("2026-10-29 14:00", setup="level", level=1175)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"], r["level"]), ("9:50 AM", 1175.9, 1175.0))
        r = at("2026-10-29 14:00", setup="level", level=1180)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("the level ₹1,180.00", r["reason"])

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        self.assertEqual(at("2026-10-29 14:40", d)["status"], "waiting")
        r = at("2026-10-29 14:46", d)
        self.assertEqual((r["status"], r["phase"]), ("expired", "after-cutoff"))
        self.assertIn("by 14:45, the last time for a new entry", r["reason"])

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = no_breakout()
        r_ = d["chart"]["result"][0]
        q = r_["indicators"]["quote"][0]
        keep = [k for k, t in enumerate(r_["timestamp"]) if clock.ts_to_ist(t).strftime("%H:%M") <= "14:40"]
        r_["timestamp"] = [r_["timestamp"][k] for k in keep]          # live: nothing after the 14:40 bar yet
        for k in q:
            q[k] = [q[k][j] for j in keep]
        q["close"][-1] = 1171.0
        d["chart"]["result"][0]["meta"]["regularMarketTime"] = clock.ist_ts(DT(2026, 10, 29, 14, 44, 50))
        lag = at("2026-10-29 14:45:20", d)
        self.assertEqual(lag["status"], "waiting")                  # Yahoo is 30 s behind the 14:45 close
        self.assertIn("lag_note", lag)
        r = at("2026-10-29 14:46:00", d)
        self.assertEqual((r["status"], r["trigger"]["time"]), ("triggered", "2:45 PM"))

    def test_config_window(self):
        cfg = {"no_entries_before": "09:45", "no_entries_after": "14:45"}
        r = at("2026-10-29 10:15", cfg=cfg)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"]), ("9:45 AM", 1174.5))
        r = at("2026-10-29 09:42", cfg=cfg)
        self.assertIn("no entries before 9:45 (config)", r["reason"])
        r = at("2026-10-29 10:15", cfg={"no_entries_before": "09:30", "no_entries_after": "09:35"})
        self.assertEqual(r["status"], "expired")

    def test_bad_inputs(self):
        for kw in ({"interval": "15m"}, {"or_minutes": 7}, {"setup": "vwap"}, {"setup": "level"}):
            with self.assertRaises(entry.EntryError):
                at("2026-10-29 10:15", **kw)
        with self.assertRaises(entry.EntryError):
            entry.read_config.__wrapped__ if False else entry.parse_hhmm("25:00", "no_entries_after")

    def test_or_minutes_30(self):
        r = at("2026-10-29 10:15", or_minutes=30)
        self.assertEqual((r["opening_range"]["high"], r["opening_range"]["of"]), (1175.7, 6))
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"]), ("9:50 AM", 1175.9))


class OneMinute(unittest.TestCase):
    def test_wick_and_close_at_the_high_do_not_trigger(self):
        r = entry.evaluate(chart(demo_1m(), "DEMO"), ist("2026-10-29 09:33"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["level"], 100.5)
        r = entry.evaluate(chart(demo_1m(), "DEMO"), ist("2026-10-29 09:34:30"), interval="1m")
        self.assertEqual((r["trigger"]["time"], r["trigger"]["price"], r["trigger"]["bar_start"]),
                         ("9:34 AM", 100.8, "09:33"))

    def test_null_bar_tick_and_volume(self):
        ch = chart(demo_1m(), "DEMO")
        self.assertEqual(len(ch["bars"]), 19)                           # the null 9:32 bar is dropped
        r = entry.evaluate(ch, ist("2026-10-29 09:36"), interval="1m", min_volume_x=2)
        self.assertEqual(r["trigger"]["volume_x"], 3.0)
        self.assertEqual(r["bars_closed"], 18)                          # the 9:34:40 tick is not a bar

    def test_the_file_and_the_interval_must_agree(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "DEMO.json")
            with open(p, "w") as fh:
                json.dump(demo_1m(), fh)
            with self.assertRaises(entry.EntryError) as cm:
                entry.get_chart("DEMO", "5m", offline=p)
            self.assertIn("DEMO has 1m bars, not 5m", str(cm.exception))
        finally:
            shutil.rmtree(tmp)


class Calendar(unittest.TestCase):
    def tearDown(self):
        clock.set_overrides(None)

    def test_ist_all_year(self):
        self.assertEqual(ist("2026-01-15 09:15"), DT(2026, 1, 15, 3, 45))
        self.assertEqual(ist("2026-07-15 09:15"), DT(2026, 7, 15, 3, 45))
        self.assertEqual(ist("2026-10-29T04:45:00Z"), DT(2026, 10, 29, 4, 45))
        self.assertEqual(ist("2026-10-29 10:15 IST"), DT(2026, 10, 29, 4, 45))
        with self.assertRaises(entry.EntryError):
            ist("tomorrow")

    def test_weekend_holiday_and_next_open(self):
        r = at("2026-10-31 10:00")
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertIn("weekend", r["reason"])
        r = at("2026-11-10 10:00")
        self.assertIn("NSE holiday: Diwali-Balipratipada", r["reason"])
        self.assertEqual(r["next_open"], "2026-11-11 09:15")
        self.assertIn("Next open: 2026-11-11 09:15 IST.", entry.describe(r))

    def test_a_holiday_override_from_the_config(self):
        self.addCleanup(clock.set_overrides, None)
        clock.set_overrides({"2026-10-29": "ad-hoc closure"})
        self.assertIn("ad-hoc closure", at("2026-10-29 10:15")["reason"])
        clock.set_overrides({"2026-11-10": None})
        self.assertNotEqual(at("2026-11-10 10:00")["phase"], "closed")

    def test_a_year_without_a_holiday_list_says_so(self):
        r = at("2027-01-05 10:00")
        self.assertFalse(r["calendar_known"])
        self.assertTrue(any("Holiday list covers 2026 only" in x for x in entry.describe(r)))

    def test_no_bars_for_the_day(self):
        r = at("2026-10-29 10:15", one_day(day=datetime.date(2026, 10, 30)))
        self.assertEqual(r["status"], "no-data")
        self.assertIn("Yahoo sent no bars", r["reason"])


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        self.body = json.dumps(one_day())

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("RELIANCE", "5m", DAY, today=DAY)
        self.assertEqual(len(c["bars"]), 74)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&"
                                      "interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("RELIANCE", "5m", DAY, today=DAY)
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("RELIANCE", "5m", DAY, today=DAY)
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "RELIANCE", "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_symbol_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("ZZZZ", "5m", DAY, today=DAY)
        self.assertIn("No data found", str(cm.exception))

    def test_ranges_and_quoted_symbols(self):
        today = datetime.date(2026, 10, 30)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(DAY, today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 9), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 10), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 9), today, "1m")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 7, 1), today, "5m")
        self.assertIn("/chart/M%26M.NS?", entry.chart_path("M&M", "1d", "5m"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", entry.chart_path("BAJAJ-AUTO", "1d", "5m"))


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar showing a
    spike to 1,171.00 (above the 1,170.50 high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 1171.0, max(b["h"], 1171.0)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 1171.0, "h": 1171.0, "l": 1171.0, "c": 1171.0, "v": 0})
        return {"symbol": "RELIANCE", "meta": {}, "bars": bars, "prev_close": 1158.0, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        sim = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, sim.now, sim.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, sim

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, sim = self.loop(live_provider(one_day(), calls=calls), "2026-10-29 09:32", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual((fired[0][0]["trigger"]["time"], fired[0][0]["trigger"]["price"]), ("9:40 AM", 1174.3))
        self.assertEqual([entry.to_ist(c).strftime("%H:%M:%S") for c in calls], ["09:32:00", "09:35:02", "09:40:02"])
        text = "\n".join(lines)
        self.assertIn(" 9:30 bar  closed ₹1,169.50  ₹1.00 under ₹1,170.50", text)
        self.assertIn(" 9:35 bar  closed ₹1,174.30  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist("2026-10-29 09:40:30")
        calls = []
        code, res, lines, fired, sim = self.loop(live_provider(one_day(), lag_until=lag, calls=calls),
                                                 "2026-10-29 09:37", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["09:37:00", "09:40:02", "09:40:32"])   # 9:40:02: Yahoo still at 9:39:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "9:40 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, sim = self.loop(live_provider(one_day()), "2026-10-29 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn(" 9:25 bar  closed ₹1,169.00  opening range set: high ₹1,170.50, low ₹1,156.20",
                      text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 5)    # 9:15 ... 9:35, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 12 minutes mid-wait
                    self.t += datetime.timedelta(minutes=12)
        sim = Dozing(ist("2026-10-29 09:21"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(one_day(), calls=calls), sim.now, sim.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_ist(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[:2], ["09:21:00", "09:34:30"])   # straight after waking, not a long sleep later
        text = "\n".join(lines)
        for start in (" 9:20", " 9:25", " 9:30"):
            self.assertIn(start + " bar  closed", text)    # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "9:40 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, sim = self.loop(live_provider(no_breakout()), "2026-10-29 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_ist(sim.now()).strftime("%H:%M"), "14:45")
        until = DT(2026, 10, 29, 11, 0)
        code, res, lines, fired, sim = self.loop(live_provider(no_breakout()), "2026-10-29 10:30", every=300,
                                                 until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(one_day()), "2026-10-31 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, "2026-10-29 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-29 10:15", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 9:40 AM at ₹1,174.30", text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy",
                "date": "2026-10-29", "status": "triggered", "time": "9:40 AM", "bar_start": "09:35",
                "bar_index": 4, "price": 1174.3, "level": 1170.5, "interval": "5m",
                "opening_range": {"high": 1170.5, "low": 1156.2, "minutes": 15}, "source": "yahoo",
                "checked_at": "2026-10-29T04:45:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-29 09:37", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-29 09:37", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_yahoo_names_and_mismatch(self):
        os.mkdir("charts")
        with open(REL, "rb") as src, gzip.open(os.path.join("charts", "RELIANCE.json.gz"), "wb") as dst:
            dst.write(src.read())
        shutil.copy(MM, os.path.join("charts", "M&M.NS.json"))
        code, text, _ = run(["check", "reliance.ns", "--offline", "charts", "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 0)
        code, text, _ = run(["check", "M&M", "--offline", "charts", "--now", "2026-10-29 14:50"])
        self.assertIn("TRIGGERED 1:00 PM at ₹2,891.60", text)
        code, _, err = run(["check", "HDFCBANK", "--offline", REL])
        self.assertEqual(code, 1)
        self.assertIn("holds RELIANCE, not HDFCBANK", err)
        code, _, err = run(["check", "BRK.B", "--offline", REL])
        self.assertIn("is not an NSE symbol", err)

    def test_offline_without_now_reads_the_file_as_of_its_own_time(self):
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--json"])
        r = json.loads(text)
        self.assertEqual(r["date"], "2026-10-30")
        self.assertEqual(r["data_time"], "2026-10-30 15:28:17")

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("longs only", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--level", "1180"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--setup", "level"])
        self.assertIn("needs --level", err)
        code, _, err = run(["check", "RELIANCE", "--offline", REL, "--min-volume-x", "0"])
        self.assertIn("--min-volume-x must be above 0", err)

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "09:35"}, fh)
        code, text, _ = run(["check", "RELIANCE", "--offline", REL, "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "RELIANCE", "--offline", REL])
        self.assertIn("not valid JSON", err)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", "2026-10-29 09:20", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn(" 9:35 bar  closed ₹1,174.30  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 4)
        code, text, _ = run(["watch", "RELIANCE", "--offline", REL, "--now", "2026-10-29 09:20", "--until", "09:35",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(REL, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(BAJAJ, os.path.join("charts", "BAJAJ-AUTO.json"))
        with open(os.path.join("charts", "DEMO.json"), "w") as fh:
            json.dump(demo_1m(), fh)
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"]}, {"symbol": "ZZZZ", "setups": ["orb"]},
                {"symbol": "DEMO", "setups": ["orb"]}, {"symbol": "BAJAJ-AUTO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertTrue(rows[1].startswith("RELIANCE    triggered  9:40 AM   ₹1,174.30"), rows[1])
        self.assertIn("ZZZZ        error: no offline file", rows[2])
        self.assertIn("DEMO        error: DEMO has 1m bars", rows[3])
        self.assertTrue(rows[4].startswith("BAJAJ-AUTO  waiting"), rows[4])
        self.assertIn("1 of 4 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", "2026-10-29 09:37", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "/trader-exit", "long side only", "in the background", "/loop",
                     "nothing was placed", "9:40 am", "₹1,174.30", "bar 4", "9:15", "14:45", "ist"):
            self.assertIn(must, low, must)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "tsla", "new york", "nyse"):
            self.assertNotIn(never, low, never)


if __name__ == "__main__":
    unittest.main()
