"""trader-exit tests, offline, on real NSE data: RELIANCE's 5-minute bars of 2026-10-29
(tests/fixtures/trader-entry/RELIANCE-2026-10-29-5m.json, Yahoo v8 range=5d) and its 3-month
daily chart (tests/fixtures/trader-exit/RELIANCE-1d.json) for the month-end close that sets the
tick: 2026-09-30 close 1,187.00, so RELIANCE trades on the 0.10 tick in October."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")
entry = load("trader-entry", "entry")

RELIANCE_5M = os.path.join(FIX, "trader-entry", "RELIANCE-2026-10-29-5m.json")
DAILY = os.path.join(FIX, "trader-exit")          # RELIANCE-1d.json, M&M-1d.json
DAY = exitp.datetime.date(2026, 10, 29)

# trader/plan.json for the reel, as the contract writes it (the default 2R target)
REEL_PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-29", "setup": "orb",
             "entry": 1174.3, "entry_time": "9:40 AM", "entry_bar": 4, "interval": "5m",
             "opening_range": {"high": 1170.5, "low": 1156.2, "minutes": 15},
             "stop": 1163.3, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
             "target": 1196.3, "target_r": 2.0, "target_label": "2R (two times the risk)",
             "risk_per_share": 11.0, "reward_risk": 2.0, "flat_by": "15:10", "tick_size": 0.1,
             "tick_basis": "NSE band of the RELIANCE close ₹1,187.00 on 2026-09-30"}
REEL_ENTRY = {"created_by": "trader-entry", "symbol": "RELIANCE", "setup": "orb", "side": "buy", "date": "2026-10-29",
              "status": "triggered", "time": "9:40 AM", "bar_start": "09:35", "bar_index": 4, "price": 1174.3,
              "level": 1170.5, "interval": "5m", "opening_range": {"high": 1170.5, "low": 1156.2, "minutes": 15},
              "source": "yahoo", "checked_at": "2026-10-29T04:45:00Z"}


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1170.50, ol=1156.20, atr=None, n=None, step=0.10):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n, step)


def no_network(*_a, **_k):
    raise exitp.PlanError("the tests never call Yahoo")


class Math(unittest.TestCase):
    def test_the_reel_or_mid_1r(self):
        p = c(1174.30, "or-mid", "1R")
        self.assertEqual(p["stop"], Decimal("1163.30"))          # (1170.50 + 1156.20) / 2 = 1163.35, down to 0.10
        self.assertEqual(p["risk"], Decimal("11.00"))
        self.assertEqual(p["target"], Decimal("1185.30"))
        self.assertEqual(p["reward_risk"], Decimal("1.00"))
        self.assertEqual(p["target_label"], "1R (one times the risk)")
        self.assertIn("= ₹1,163.35, down to the ₹0.10 tick", p["stop_detail"])

    def test_the_default_2r(self):
        p = c(1174.30, "or-mid")
        self.assertEqual(p["target"], Decimal("1196.30"))          # 1174.30 + 2 x 11.00
        self.assertEqual(p["target_label"], "2R (two times the risk)")

    def test_raw_yahoo_floats_give_the_same_prices(self):
        # float32 values as Yahoo sends them: 1174.30 arrives as 1174.300048828125
        p = c(1174.300048828125, "or-mid", "1R", 1170.5, 1156.199951171875)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("1174.30"), Decimal("1163.30"), Decimal("1185.30")))
        self.assertEqual(exitp.tick(1283.5999755859375, 0.10), Decimal("1283.60"))
        self.assertEqual(exitp.tick_down(1283.5999755859375, 0.10), Decimal("1283.60"))   # not a whole tick lower

    def test_other_stops(self):
        p = c(1174.30, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1156.20"), Decimal("18.10"), Decimal("1210.50")))
        p = c(1174.30, "price:1163.37")
        self.assertEqual(p["stop"], Decimal("1163.30"))             # rounds away from the entry, to the 0.10 tick
        p = c(1174.30, "pct:1")                                     # 1174.30 x 0.99 = 1162.557
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1162.50"), Decimal("11.80"), Decimal("1197.90")))
        p = c(1174.30, "atr:1.5", atr=5.84, n=5)                    # 1174.30 - 8.76 = 1165.54
        self.assertEqual(p["stop"], Decimal("1165.50"))
        self.assertEqual(p["stop_label"], "1.5 x ATR(5) under the entry")

    def test_the_tick_follows_the_price_band(self):
        # NSE's ladder: 0.01 under 250, 0.05 to 1,000, 0.10 to 5,000, 0.50 to 10,000, 1 to 20,000
        p = c(168.67, "price:166.433", "1R", None, None, step=0.01)          # GAIL-like, paise
        self.assertEqual((p["stop"], p["target"]), (Decimal("166.43"), Decimal("170.91")))
        p = c(682.50, "price:678.33", "1R", None, None, step=0.05)           # HDFCBANK-like
        self.assertEqual((p["stop"], p["target"]), (Decimal("678.30"), Decimal("686.70")))
        p = c(10383, "price:10301.7", "1R", None, None, step=1.0)            # BAJAJ-AUTO-like, whole rupees
        self.assertEqual((p["stop"], p["target"]), (Decimal("10301.00"), Decimal("10465.00")))
        p = c(1174.30, "or-mid", "1R", step=None)                            # no tick given: the coarse one, 0.10
        self.assertEqual(p["stop"], Decimal("1163.30"))

    def test_target_rounds_half_up_and_price_targets(self):
        p = c(1174.30, "price:1163.20", "1.5R", None, None)
        self.assertEqual(p["target"], Decimal("1191.00"))           # 1174.30 + 16.65 = 1190.95, half up
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        for half, up in (("1000.05", "1000.10"), ("100.05", "100.10"), ("1163.45", "1163.50")):
            self.assertEqual(exitp.tick(Decimal(half), 0.10), Decimal(up), half)   # never a float round-down
        p = c(1174.30, "or-mid", "price:1190")
        self.assertEqual(p["reward_risk"], Decimal("1.43"))
        self.assertEqual(p["target_label"], "your price (1.43R)")
        self.assertEqual(p["target_r"], Decimal("1.43"))

    def test_refusals_say_why(self):
        cases = [("price:1174.30", "2R", "at or above the entry"),
                 ("price:1180", "2R", "at or above the entry"),
                 ("or-mid", "price:1174", "at or below the entry"),
                 ("price:1173.80", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1174.30, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1174.30 is 0.587: a 0.60 risk is allowed (one tick more), 0.50 is not
        self.assertEqual(c(1174.30, "price:1173.70")["risk"], Decimal("0.60"))
        with self.assertRaises(exitp.PlanError):
            c(1163.00, "or-mid")                                    # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1174.30, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1174.30, "or-mid", oh=1156.20, ol=1170.50)
        self.assertEqual(exitp.parse_stop("price:₹1,163.30"), ("price", Decimal("1163.30")))

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, RELIANCE_5M)
        bars = exitp.session_bars(ch, DAY, "5m", upto_slot=4)
        self.assertEqual(len(bars), 5)                              # 9:15 ... the 9:35 entry bar
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1162.20 - 1156.20, places=3)  # the 9:15 bar: no overnight gap
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 5)
        self.assertAlmostEqual(atr, sum(trs) / 5, places=9)
        self.assertAlmostEqual(atr, 5.8400, places=3)
        # the whole session: 74 bars on the grid (Yahoo sends the 15:20 bar with no prices), the last 14 count
        all_bars = exitp.session_bars(ch, DAY, "5m")
        self.assertEqual(len(all_bars), 74)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)


class Tick(unittest.TestCase):
    def setUp(self):
        self._get = exitp.yahoo_get
        exitp.yahoo_get = no_network

    def tearDown(self):
        exitp.yahoo_get = self._get

    def test_the_month_end_close_sets_the_tick(self):
        self.assertEqual(exitp.month_end_close("RELIANCE", DAY, DAILY), (1187.0, exitp.datetime.date(2026, 9, 30)))
        self.assertEqual(exitp.month_end_close("M&M", DAY, DAILY)[0], 2950.0)
        step, basis = exitp.resolve_tick("RELIANCE", DAY, {"tick_sizes": {}}, 1174.30, DAILY)
        self.assertEqual(step, 0.10)
        self.assertEqual(basis, "NSE band of the RELIANCE close ₹1,187.00 on 2026-09-30")
        # November is banded on the October close, which this file also holds
        nov = exitp.month_end_close("RELIANCE", exitp.datetime.date(2026, 11, 2), DAILY)
        self.assertEqual(nov[1], exitp.datetime.date(2026, 10, 30))

    def test_no_daily_file_falls_back_to_the_coarse_tick(self):
        empty = tempfile.mkdtemp()
        try:
            step, basis = exitp.resolve_tick("RELIANCE", DAY, {"tick_sizes": {}}, 1174.30, empty)
        finally:
            shutil.rmtree(empty)
        self.assertEqual(step, 0.10)
        self.assertIn("no month-end close", basis)
        # online with Yahoo down: the same fallback, no crash
        self.assertEqual(exitp.resolve_tick("RELIANCE", DAY, {}, 1174.30, None)[0], 0.10)
        self.assertEqual(exitp.resolve_tick("X", DAY, {}, 990, None)[0], 0.10)    # near the 1,000 edge: coarser

    def test_the_config_override_wins(self):
        step, basis = exitp.resolve_tick("RELIANCE", DAY, {"tick_sizes": {"RELIANCE": 0.05}}, 1174.30, DAILY)
        self.assertEqual((step, basis), (0.05, "trader/config.json tick_sizes"))
        with self.assertRaises(exitp.PlanError):
            exitp.resolve_tick("RELIANCE", DAY, {"tick_sizes": {"RELIANCE": 0.25}}, 1174.30, DAILY)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")
        self.data = os.path.join(self.d, "data")       # what --offline reads: the 5m bars and the daily chart
        os.mkdir(self.data)
        shutil.copy(RELIANCE_5M, os.path.join(self.data, "RELIANCE.json"))
        shutil.copy(os.path.join(DAILY, "RELIANCE-1d.json"), self.data)
        self._get = exitp.yahoo_get
        exitp.yahoo_get = no_network

    def tearDown(self):
        exitp.yahoo_get = self._get
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-29 09:41", "--offline", self.data] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        code, _, _ = run(entry, ["check", "RELIANCE", "--offline", RELIANCE_5M, "--now", "2026-10-29 09:40", "--write"])
        self.assertEqual(code, 0)
        code, text, err = self.plan("--write")                    # the defaults: or-mid, 2R
        self.assertEqual(code, 0, err)
        p = self.read_plan()
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Stop          ₹1,163.30  opening-range midpoint", text)
        self.assertIn("Target        ₹1,196.30  2R (two times the risk)", text)
        self.assertIn("Risk a share  ₹11.00 (0.94% of the entry)", text)
        self.assertIn("Reward:risk   2.0", text)
        self.assertIn("Flat by       15:10 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertIn("/trader-timemachine", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        code, text, _ = self.plan("--target", "1R", "--write")
        self.assertEqual((self.read_plan()["target"], self.read_plan()["target_r"]), (1185.3, 1.0))
        code, text, _ = run(exitp, ["show", "--now", "2026-10-29 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("₹1,185.30", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-30 10:20"])
        self.assertIn("not today", text)

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance.ns", "--entry", "1174.30", "--or-high", "1170.50",
                                  "--or-low", "1156.20", "--time", "9:40", "--entry-bar", "4", "--date",
                                  "2026-10-29", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in REEL_PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-29 09:41", "--symbol", "M&M", "--entry", "2891.60",
                                    "--stop", "pct:1", "--date", "2026-10-29", "--json"])
        p = json.loads(text)
        self.assertEqual((p["symbol"], p["stop"], p["tick_size"]), ("M&M", 2862.6, 0.1))   # 2862.684 down to 0.10
        self.assertIn("no month-end close", p["tick_basis"])                               # no daily file given
        code, _, err = self.plan("--symbol", "BRK.B", "--entry", "100", "--stop", "pct:1")
        self.assertEqual(code, 1)
        self.assertIn("not an NSE symbol", err)

    def test_atr_stop_reads_bars(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        code, text, err = self.plan("--stop", "atr:1.5", "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1165.5, 8.8, 1191.9))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 5))
        self.assertAlmostEqual(p["atr"], 5.84, places=2)
        code, _, err = self.plan("--stop", "atr:1.5", "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)

    def test_refusals_from_the_cli(self):
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, status="waiting", time=None, price=None,
                                                              bar_index=None, bar_start=None))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1170.60", "--json")    # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1170.6, 1163.3, None))

        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, setup="level", level=1170.0))
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("longs only", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, side="sell"))
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1174.30", "--or-high", "1170.50")
        self.assertIn("both --or-high and --or-low", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by_config_and_late_entries(self):
        self.write(os.path.join("trader", "entry.json"), REEL_ENTRY)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:00", "min_reward_risk": 2.5})
        code, text, _ = self.plan("--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:00")
        self.assertIn("under your min_reward_risk 2.5", p["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))

        code, text, _ = self.plan("--flat-by", "15:25", "--json")      # after the MIS square-off: a warning
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:25")
        self.assertIn("squaring off MIS positions", p["warnings"][0])
        code, text, _ = self.plan("--flat-by", "15:20", "--json")
        self.assertEqual(json.loads(text)["warnings"], [])
        for late in ("15:30", "16:00"):
            code, _, err = self.plan("--flat-by", late)
            self.assertEqual(code, 1, late)
            self.assertIn("at or after the 3:30 PM IST close", err)
        self.write(os.path.join("trader", "entry.json"), dict(REEL_ENTRY, time="3:12 PM"))
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("no time left to hold it", err)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        for must in ("before the entry", "a stop is a plan, not a guarantee", "loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", "₹1,163.30", "₹1,185.30",
                     "₹11.00", "1,196.30", "too tight to survive the spread", "0.10 tick", "tick_sizes", "15:10"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "new york"):
            self.assertNotIn(never, low)
        self.assertNotRegex(low, r"\$\d")                         # no dollar amounts left from the US pack


if __name__ == "__main__":
    unittest.main()
