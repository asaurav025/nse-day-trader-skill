"""trader-timemachine tests, offline, on real NSE data: RELIANCE's Yahoo v8 chart, range=60d
interval=5m (tests/fixtures/trader-timemachine/RELIANCE-60d5m.json.gz, 2026-08-05 to 2026-10-30).
The plan is the reel's: entry 1,174.30 at 9:40 AM on 2026-10-29, opening-range-midpoint stop
1,163.30 (on RELIANCE's 0.10 tick), 1R target 1,185.30, flat by 15:10. The clock is pinned to
2026-10-29 10:15 IST, so the fixture's later bars (the rest of Oct 29 and Oct 30) are never used."""

import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
RELIANCE = os.path.join(DIR, "RELIANCE-60d5m.json.gz")
with gzip.open(RELIANCE, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
NOW = "2026-10-29 10:15"
NOW_UTC = tm.parse_now(NOW)

# the reel's plan, as trader-exit writes it (target 1R here; trader-exit's default is 2R)
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-29", "setup": "orb",
        "entry": 1174.30, "entry_time": "9:40 AM", "entry_bar": 4, "interval": "5m",
        "opening_range": {"high": 1170.50, "low": 1156.20, "minutes": 15},
        "stop": 1163.30, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1185.30, "target_r": 1.0, "target_label": "1R (one times the risk)",
        "risk_per_share": 11.0, "reward_risk": 1.0, "flat_by": "15:10", "tick_size": 0.1}

# fingerprints of the reel run (seed 19): any change to the draws or the data shows up here
BANDS = "aba91bd54708445a"
PATHS = "91484db09a456528"
TRADES = "ef2245f7d7fd87d1"
HIST = {"lo": 1149.84, "hi": 1197.23,
        "bins": [124, 126, 162, 222, 331, 339, 490, 619, 649, 717, 757, 759, 746, 711, 647, 579, 517, 380, 319,
                 256, 143, 115, 84, 51]}


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def ist(ts):
    return tm.to_ist(tm.utc_of(ts))


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Reel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_reel_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:10"))
        self.assertEqual(m["pool_sessions"], 57)
        self.assertEqual(m["bars_left"], 66)                        # 9:40 to 3:10 PM
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-05", "2026-10-28"))
        self.assertEqual(m["p_target"], 0.2392)
        self.assertEqual(m["p_stop"], 0.3113)
        self.assertEqual(m["p_neither"], 0.4495)
        self.assertEqual(m["p_green"], 0.4453)
        self.assertEqual(m["median_close"], 1172.57)
        self.assertEqual(m["p5_close"], 1156.07)
        self.assertEqual(m["p95_close"], 1189.23)
        self.assertEqual(m["avg_R"], -0.095)
        self.assertEqual(m["r_at_target"], 1.0)

    def test_bit_for_bit_with_the_reel_run(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), PATHS)
        self.assertEqual(m["hist"], HIST)
        self.assertEqual(m["bands"][-1], [1156.3, 1166.44, 1172.85, 1179.7, 1189.78])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (57, 27))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (13, 11, 3, 14))
        self.assertEqual(h["avg_R"], 0.038)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 1.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), TRADES)
        first = h["trades"][0]
        # 2026-08-06: range 1,283.60 to 1,300.50; entry 1,300.90; the midpoint 1,292.05 down to the 0.10 tick
        self.assertEqual((first["date"], first["entry"], first["stop"], first["target"], first["outcome"]),
                         ("2026-08-06", 1300.9, 1292.0, 1309.8, "target"))
        for t in h["trades"]:                                       # every price on the tick
            for k in ("entry", "stop", "target"):
                self.assertAlmostEqual(t[k] * 10, round(t[k] * 10), places=6, msg=(t["date"], k))

    def test_the_verdict_leans_toward_the_stop(self):
        self.assertEqual(self.doc["verdict"], "Leans toward the stop: target first 23.9 % vs stop first 31.1 %.")
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), even), (0.435, 0.5))   # 1R breaks even at 50 %; it got 43.5 %
        self.assertEqual(self.doc["verdict_notes"],
                         ["45.0 % of the replays hit neither by 3:10 PM; the price at 3:10 PM decides those."])

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1174.3] * 5)
        self.assertEqual(len(m["bands"]), 67)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("09:40", "15:10", 67))
        self.assertTrue(all(p[0] == 1174.3 and len(p) == 67 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE", 1174.3, 1163.3, 1185.3, 4, "9:40 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (11.0, 1.0, "15:10"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["data"]["yahoo_symbol"], "RELIANCE.NS")

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE buy, Thursday 2026-10-29: entry ₹1,174.30 at 9:40 AM (bar 4), stop ₹1,163.30, "
                     "target ₹1,185.30", "10,000 replays", "66 five-minute bars", "flat by 3:10 PM", "seed 19",
                     "57 full sessions", "target first   23.9 %", "stop first     31.1 %",
                     "neither        45.0 %   still open at 3:10 PM", "green          44.5 %",
                     "median ₹1,172.57, 90 % of replays between ₹1,156.07 and ₹1,189.23",
                     "Average R: -0.095", "fired 27 times: 13 target, 11 stop, 3 neither. Average R +0.038",
                     "Leans toward the stop: target first 23.9 % vs stop first 31.1 %.\n45.0 % of the replays "
                     "hit neither by 3:10 PM; the price at 3:10 PM decides those.",
                     "Left out 1 short session(s) (gaps or missing bars): 2026-10-15.", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick", "$", "ET"):
            self.assertNotIn(word, text.replace("PM", "").replace("AM", ""))


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2392, 0.3113, 1172.57))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; the reel numbers above
        # are the real proof, this pins the first draws so a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(57)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE")

    def test_later_bars_and_the_null_closing_bars_are_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        # 114 bars with no price: Yahoo sends NSE's 15:20 bar empty every day (and often 15:25 / 15:15);
        # 136 bars come after the pinned clock (the rest of Oct 29 and all of Oct 30)
        self.assertEqual(dropped, {"no_price": 114, "off_grid": 0, "not_complete": 136, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-29", "10:10", 11))
        entry = [b for b in bars if b["date"] == "2026-10-29" and b["slot"] == 4][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:35", 1174.300048828125))

    def test_a_full_session_runs_to_flat_by(self):
        days = tm.split_sessions(tm.parse_bars(self.result, NOW_UTC)[1])
        full, short = tm.full_sessions(days, "2026-10-29", 71)
        self.assertEqual(len(full), 57)
        self.assertTrue(all(len(b) == 71 for _, b in full))       # 9:15 to the 15:05 bar
        self.assertEqual(short, [{"date": "2026-10-15", "bars": 69}])
        self.assertEqual(sorted(set(range(71)) - set(days["2026-10-15"])), [30, 31])   # 11:45 and 11:50 missing
        # to 15:30 no session is ever full: the 15:20 bar never has a price
        self.assertEqual(tm.full_sessions(days, "2026-10-29")[0], [])
        self.assertEqual(tm.SESSION_BARS, 75)

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-29 09:37"))[1]
        self.assertEqual(len([b for b in bars if b["date"] == "2026-10-29"]), 4)   # 9:15 to 9:30
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-29 09:37"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:30 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-29 09:40"), replays=200)["mc"]["pool_sessions"], 57)

    def test_ist_has_no_daylight_saving(self):
        # 03:45 UTC is 9:15 IST before and after the US clocks change on Nov 1
        stamps = [1793331900,   # 2026-10-30 03:45Z = 9:15 IST
                  1793591100,   # 2026-11-02 03:45Z = 9:15 IST
                  1793330100,   # 2026-10-30 03:15Z = 8:45 IST, the pre-open
                  1793354400,   # 2026-10-30 10:00Z = 15:30 IST, the closing print
                  1793353500]   # 2026-10-30 09:45Z = 15:15 IST
        res = {"meta": {"symbol": "X"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-10-30", "09:15", 0), ("2026-10-30", "15:15", 72), ("2026-11-02", "09:15", 0)])
        self.assertEqual(dropped["outside_hours"], 2)

    def test_a_short_session_is_left_out(self):
        def cut(res):   # 2026-09-01 loses its 12:00 bar
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-01"
                                           and ist(ts).strftime("%H:%M") == "12:00"))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 56)
        self.assertEqual(doc["data"]["short_sessions_left_out"],
                         [{"date": "2026-09-01", "bars": 70}, {"date": "2026-10-15", "bars": 69}])
        self.assertEqual(doc["history"]["sessions"], 56)
        self.assertNotIn("2026-09-01", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 2 short session", "\n".join(tm.report(doc)))

    def test_the_plan_day_and_later_are_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-28")
        self.assertNotIn("2026-10-29", [t["date"] for t in doc["history"]["trades"]])
        later = machine(now=tm.parse_now("2026-10-30 18:00"), replays=200)   # Oct 29 and 30 are in the data
        self.assertEqual((later["mc"]["pool_to"], later["mc"]["pool_sessions"]), ("2026-10-28", 57))

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            res["indicators"]["quote"][0]["close"][5] = None      # 2026-08-05, the 9:40 bar
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 115)
        self.assertEqual(doc["mc"]["pool_sessions"], 56)

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-29")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: ist(ts).date().isoformat() >= "2026-09-28")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_an_earlier_flat_by_counts_more_of_the_day(self):
        # flat by 14:30: a session is full with 63 bars (9:15 to 14:25), so a hole at 15:00 does not matter
        def late_hole(res):
            keep_bars(res, lambda ts: not (ist(ts).date().isoformat() == "2026-09-01"
                                           and ist(ts).strftime("%H:%M") == "15:00"))
        doc = machine(plan(flat_by="14:30"), text=edited(late_hole), replays=300)
        self.assertEqual((doc["mc"]["until"], doc["mc"]["bars_left"], doc["mc"]["pool_sessions"]), ("14:30", 58, 57))
        self.assertIn("to 2:30 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(text=edited(late_hole), replays=300)["mc"]["pool_sessions"], 56)

    def test_the_data_must_be_the_plans_symbol(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="M&M"))
        self.assertIn("data is for RELIANCE.NS", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-31"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))
        with self.assertRaises(tm.TMError) as cm:      # 10:15 has not closed in this data yet
            machine(plan(entry_time="10:20 AM", entry_bar=None))
        self.assertIn("the data stops at the 10:10 bar", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="9:42 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:05 PM"),
            (dict(entry_bar=5), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1180.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "NSE symbol"),
            (dict(symbol="BRK.B"), "NSE symbol"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 29"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "9:40 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 4)
        self.assertEqual(plan(entry_time="9:40", entry_bar=None)["entry_bar"], 4)
        self.assertEqual(plan(entry_time="9:40am", entry_bar=None)["entry_bar"], 4)
        self.assertEqual(plan(symbol="reliance.ns")["symbol"], "RELIANCE")

    def test_until_cuts_the_replay(self):
        doc = machine(until="14:30", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("14:30", 58, "14:30"))
        self.assertIn("still open at 2:30 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="2:30 PM", replays=500)["mc"], m)
        for bad, words in (("09:35 AM", "nothing to replay"), ("15:15", "no later than flat_by 3:10 PM"),
                           ("14:32", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1180.0, stop=1170.0, target=1190.0), replays=200)
        self.assertIn("outside that bar's range ₹1,169.00 to ₹1,175.70", doc["warnings"][0])

    def test_stop_rounds_away_from_the_entry(self):
        self.assertEqual(tm.tick_away(1163.35, True, 0.10), 1163.3)
        self.assertEqual(tm.tick_away(1163.35, False, 0.10), 1163.4)
        self.assertEqual(tm.tick_away(1283.5999755859375, True, 0.10), 1283.6)   # float32 noise: not a tick lower
        self.assertEqual(tm.tick_away(1163.35, True), 1163.3)                    # no tick: the coarse one, 0.10
        self.assertEqual(tm.tick_away(682.47, True), 682.45)                     # 0.05 band, far from 1,000
        self.assertEqual(tm.tick_away(900.47, True), 900.4)                      # within 20 % of 1,000: coarse 0.10
        self.assertEqual(tm.tick_away(900.47, True, 0.05), 900.45)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1156.20, target=1192.40), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 1.0))
        rule = machine(plan(stop_rule="atr", stop=1165.50, target=1192.10), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.7494, 2.02))

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="flag"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 57)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1185.30, target=1163.30, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        self.assertEqual(doc["history"]["triggered"], 40)
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "14:30"}),
                         "No replay reached the target or the stop by 2:30 PM: the price at 2:30 PM decides every one.")

    def test_a_2r_target_is_judged_against_its_break_even(self):
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_with_a_closer_stop_leans(self):
        # stop 1,168.30, 2R target 1,186.30: 18.5 % vs 57.6 %, and 2R needs only a third of them
        doc = machine(plan(stop=1168.30, target=1186.30, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["avg_R"]), (2.0, 0.1854, 0.5759, -0.101))
        self.assertEqual(doc["verdict"], "Leans toward the stop: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 24 % (target first 18.5 % vs stop "
                                         "first 57.6 %).")
        self.assertEqual(doc["verdict_notes"], [])

    def test_the_2r_default_a_clear_lean(self):
        # trader-exit's default 2R target, 1,196.30: target first only 2.5 % vs stop first 31.4 %
        doc = machine(plan(target=1196.30, target_r=2.0))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (2.0, 0.0249, 0.3135, 0.6616, -0.115))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 2R the target has to come first in 33 % of "
                                         "the replays that hit one side; it did in 7 % (target first 2.5 % vs stop "
                                         "first 31.4 %).")
        self.assertEqual(doc["verdict_notes"],
                         ["66.2 % of the replays hit neither by 3:10 PM; the price at 3:10 PM decides those."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0], text)


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("M&M", "query1"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("BAJAJ-AUTO"))
        self.assertEqual(tm.UA, "Mozilla/5.0 (compatible; nse-day-trader-skill/1.0; "
                                "+https://github.com/asaurav025/nse-day-trader-skill)")

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the NSE symbol", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        with open(self.plan, "w") as fh:
            json.dump(PLAN, fh, indent=2)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   23.9 %", text)
        self.assertIn("Leans toward the stop: target first 23.9 % vs stop first 31.1 %.", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.2392, -0.095))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (27, 0.038))
        self.assertEqual(doc["plan"]["from"], self.plan)

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.3113)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:21], "Leans toward the stop")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-10-29", "--entry", "1174.30",
                               "--stop", "1163.30", "--target", "1185.30", "--entry-time", "9:40 AM",
                               "--offline", RELIANCE, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.2392, 0.4453))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (13, 11, 0.038))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1174.30", "--offline", RELIANCE])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(RELIANCE, self.d)                       # RELIANCE-60d5m.json.gz in the folder
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
            code, _, err = run(["run", "--offline", self.d, "--symbol", "M&M", "--date", "2026-10-29",
                                "--entry", "2891.60", "--stop", "2862.60", "--target", "2920.60",
                                "--entry-time", "1:00 PM", "--now", NOW])
            self.assertEqual(code, 1)
            self.assertIn("no M&M-60d5m.json", err)
        finally:
            os.chdir(cwd)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", RELIANCE, "--until", "9:00"],
                     ["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--until", "15:20"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("RELIANCE 2026-10-29, 2,000 replay paths from the 9:40 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1185.30 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  1163.30 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  1174.30 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("9:40", text)
        self.assertIn("3:10", text)
        self.assertEqual(lines[-2:], ["Leans toward the stop: target first 23.9 % vs stop first 31.1 %.",
                                      "45.0 % of the replays hit neither by 3:10 PM; the price at 3:10 PM decides those."])
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW])
        self.assertEqual(again, text)


if __name__ == "__main__":
    unittest.main()
