import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")
from trader_common import orders as kite  # noqa: E402  (_load puts the repo root on sys.path via the tool)

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")      # trader-exit's real plan, 2026-10-29
ORDERS = os.path.join(FIX, "trader-risk", "orders-reliance.json")    # what this tool writes from it
TARGETS = os.path.join(ROOT, "trader-budget", "targets.example.json")
NOW = "2026-10-29 10:15"
TODAY = "2026-10-29"
PRICES = {"RELIANCE": 1174.30, "HDFCBANK": 682.50, "TCS": 2478.00, "INFY": 1496.60, "M&M": 2890.00}
# the example config, key for key: rupees, dry run, Zerodha, the buffers of the two-leg ticket
CONTRACT_CFG = {
    "budget": 100000, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:45", "flat_by": "15:10",
    "product": "MIS", "entry_limit_buffer_pct": 0.3, "stop_limit_buffer_pct": 0.5, "exit_limit_buffer_pct": 0.5,
    "broker": "zerodha", "blocked": [], "allow_margin": False, "allow_short": False, "dry_run": True,
}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def write(tmp, name, doc):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    return path


def prices_doc(price, quote_time="2026-10-29T04:44:00Z"):
    return {"as_of": quote_time, "source": "yahoo", "prices": {"RELIANCE": price},
            "detail": {"RELIANCE": {"prev_close": 1167.40, "time": quote_time, "source": "yahoo"}}}


def on_tick(x, step):
    return abs(x / step - round(x / step)) < 1e-6


class TheReel(unittest.TestCase):
    """₹1,00,000, 10% buffer, ₹1,000 max loss. RELIANCE 2026-10-29: entry 1174.30, stop 1163.30,
    target 1196.30, tick 0.10 -> LIMIT 1177.90, SL 1163.30 / 1157.40, 48 shares, max loss ₹984."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_reliance_is_48_shares_bound_by_the_max_loss(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["symbol"], "RELIANCE")
        self.assertEqual(s["tick_size"], 0.1)
        self.assertEqual(s["entry_base"], 1174.30)
        self.assertEqual(s["entry"], 1177.90)          # 1174.30 x 1.003 = 1177.82, up to the 0.10 tick
        self.assertEqual(s["stop"], 1163.30)
        self.assertEqual(s["stop_limit"], 1157.40)     # 1163.30 x 0.995 = 1157.48, down to the tick
        self.assertEqual(s["risk_per_share"], 20.50)   # 1177.90 - 1157.40
        self.assertEqual(s["risk_per_share_at_stop"], 14.60)
        self.assertEqual(s["qty"], 48)                 # floor(1000 / 20.50) = 48
        self.assertEqual(s["bound_by"], "max loss")
        self.assertEqual(s["cost"], 56539.20)
        self.assertEqual(s["max_loss"], 984.0)
        self.assertEqual(s["max_loss_at_stop"], 700.80)
        self.assertEqual(s["reward_risk"], 1.26)       # (1196.30 - 1177.90) / 14.60
        self.assertIsNone(s["refusal"])

    def test_the_four_limits(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        got = {l["name"]: l["shares"] for l in s["limits"]}
        self.assertEqual(got, {"max loss": 48, "position cap": 63, "max order": 63, "cash": 76})
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["max loss"])

    def test_the_kite_order(self):
        o = size.kite_order(size.size_trade(plan(), cfg(), today=TODAY))
        self.assertEqual({k: o[k] for k in ("symbol", "side", "qty", "order_type", "price", "product", "stop",
                                            "stop_limit", "target", "tick_size")},
                         {"symbol": "RELIANCE", "side": "buy", "qty": 48, "order_type": "LIMIT", "price": 1177.90,
                          "product": "MIS", "stop": 1163.30, "stop_limit": 1157.40, "target": 1196.30,
                          "tick_size": 0.1})
        self.assertIn("48 x 20.50 = ₹984", o["why"])
        self.assertEqual(o["plan"]["max_loss"], 984.0)
        self.assertEqual(kite.governing_loss(o), 984.0)
        self.assertEqual(kite.max_loss(o)["at_stop"], 700.80)

    def test_every_price_is_on_the_stocks_tick(self):
        o = size.kite_order(size.size_trade(plan(), cfg(), today=TODAY))
        for k in ("price", "stop", "stop_limit", "target"):
            self.assertTrue(on_tick(o[k], 0.10), (k, o[k]))

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "orders.json")
            code, _, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"),
                              "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as fh:
                got = json.load(fh)
        with open(ORDERS, encoding="utf-8") as fh:
            want = json.load(fh)
        self.assertEqual(got["orders"], want["orders"])
        self.assertIs(got["dry_run"], True)
        self.assertEqual(kite.fingerprint(got["orders"][0]), kite.fingerprint(want["orders"][0]))

    def test_your_own_limit_price(self):
        s = size.size_trade(plan(), cfg(), limit=1176.00, today=TODAY)
        self.assertEqual(s["entry"], 1176.00)
        self.assertEqual(s["entry_from"], "limit")
        self.assertEqual(s["risk_per_share"], 18.60)   # 1176.00 - 1157.40
        self.assertEqual(s["qty"], 53)                 # floor(1000 / 18.60)

    def test_a_limit_off_the_tick_is_refused(self):
        with self.assertRaises(size.SizeError) as cm:
            size.size_trade(plan(), cfg(), limit=1176.05, today=TODAY)
        self.assertIn("0.10 tick", str(cm.exception))

    def test_cli_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"),
                                "--now", NOW])
        self.assertEqual(code, 0)
        self.assertIn("Limit 1177.90: the plan's entry 1174.30 plus 0.30%", out)
        self.assertIn("SL stop: triggers at 1163.30, sells no lower than 1157.40", out)
        self.assertIn("Size: 48 shares of RELIANCE = ₹56,539.20 at 1177.90", out)
        self.assertIn("₹1,00,000", out)
        self.assertIn("leg 1  BUY 48 RELIANCE, LIMIT 1177.90, MIS, day", out)
        self.assertIn("leg 2  after it fills: SELL those shares, SL trigger 1163.30 limit 1157.40", out)
        self.assertIn("target 1196.30: an alert, not an order", out)
        self.assertIn("Flat by 15:10 IST", out)
        self.assertIn("not financial advice", out)

    def test_cli_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"),
                                "--now", NOW, "--json", "--limit", "1176"])
        self.assertEqual(code, 0)
        doc = json.loads(out)
        self.assertEqual(doc["sizing"]["qty"], 53)
        self.assertEqual(doc["orders"][0]["price"], 1176.0)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=10), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIsNone(size.kite_order(s))
        self.assertIn("one share risks ₹20.50", s["refusal"])
        self.assertIn("No order", s["refusal"])

    def test_each_limit_can_bind(self):
        cases = [(cfg(max_position_pct=20), "position cap", 16),   # 20,000 / 1177.90
                 (cfg(max_order_inr=10000), "max order", 8),        # 10,000 / 1177.90
                 (cfg(cash_buffer_pct=60), "cash", 33)]             # 40,000 / 1177.90
        for c, bound, qty in cases:
            s = size.size_trade(plan(), c, today=TODAY)
            self.assertEqual((s["bound_by"], s["qty"]), (bound, qty))

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        s = size.size_trade(plan(), cfg(), price=1180.00, today=TODAY)
        self.assertEqual(s["entry_from"], "price")
        self.assertEqual(s["entry"], 1183.60)          # 1180 x 1.003 = 1183.54, up to 0.10
        self.assertEqual(s["risk_per_share"], 26.20)
        self.assertEqual(s["qty"], 38)
        self.assertIn("reward:risk 0.63", s["refusal"])
        lower = size.size_trade(plan(), cfg(), price=1170.00, today=TODAY)
        self.assertEqual(lower["entry_from"], "plan")
        self.assertEqual(lower["qty"], 48)

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = write(tmp, "fresh.json", prices_doc(1180.00, "2026-10-29T04:44:00Z"))    # 10:14 IST
            stale = write(tmp, "stale.json", prices_doc(1180.00, "2026-10-29T03:50:00Z"))    # 09:20 IST
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"), "--now", NOW]
            code, out, _ = run(base + ["--prices", fresh])
            self.assertIn("is above the plan's entry 1174.30, so the risk is bigger: sized at 1180.00", out)
            self.assertIn("38 shares  <- binds", out)
            self.assertIn("No order: reward:risk 0.63", out)   # the move it waited for has mostly happened
            code, out, _ = run(base + ["--prices", stale])
            self.assertIn("not used (older than 15 minutes)", out)
            self.assertIn("Size: 48 shares", out)

    def test_the_portfolio_cash_caps_the_size(self):
        s = size.size_trade(plan(), cfg(), cash=30000, today=TODAY)
        self.assertEqual(s["cash_available"], 20000)   # 30,000 minus the 10,000 buffer
        self.assertEqual((s["bound_by"], s["qty"]), ("cash", 16))

    def test_bad_plans(self):
        bad = [(plan(symbol=""), "no symbol"), (plan(side="sell"), "long (buy) day trades only"),
               (plan(stop=1180.00), "not below the entry"), (plan(target=1177.00), "the move has already happened"),
               (plan(target=None), "positive target"), (plan(symbol="BRK.B"), "not an NSE symbol")]
        for p, msg in bad:
            with self.assertRaises(size.SizeError) as cm:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(msg, str(cm.exception))
        with self.assertRaises(size.SizeError) as cm:
            size.size_trade(plan(), cfg(), today="2026-10-30")
        self.assertIn("the plan is for 2026-10-29", str(cm.exception))

    def test_under_the_minimum_order(self):
        s = size.size_trade(plan(), cfg(min_order_inr=60000), today=TODAY)
        self.assertIn("under your ₹60,000 minimum order", s["refusal"])

    def test_reward_risk_under_the_minimum_is_refused(self):
        s = size.size_trade(plan(), cfg(min_reward_risk=1.5), today=TODAY)
        self.assertIn("reward:risk 1.26", s["refusal"])

    def test_missing_plan_and_weights_only_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(["--plan", os.path.join(tmp, "nope.json"), "--config", CFG])
            self.assertEqual(code, 1)
            self.assertIn("no plan", err)
            code, _, err = run(["--plan", PLAN, "--config", CFG, "--top", "3"])
            self.assertEqual(code, 1)
            self.assertIn("work with --weights or --targets", err)

    def test_dollar_keys_are_refused_by_name(self):
        with self.assertRaises(size.SizeError) as cm:
            size.config_units({"max_loss_per_trade_usd": 50, "budget": 100000})
        self.assertIn("max_loss_per_trade_usd -> max_loss_per_trade_inr", str(cm.exception))
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, "c.json", {"budget": 5000, "max_order_usd": 4000})
            code, _, err = run(["--plan", PLAN, "--config", path, "--now", NOW])
            self.assertEqual(code, 1)
            self.assertIn("max_order_usd -> max_order_inr", err)

    def test_us_only_keys_are_ignored_with_a_note(self):
        user, notes = size.config_units({"pdt_guard": True, "fractional": True, "paper": True, "budget": 1})
        self.assertEqual(user, {"budget": 1})
        self.assertEqual(len(notes), 3)
        self.assertTrue(any("FINRA" in n for n in notes))
        self.assertTrue(any("dry_run" in n for n in notes))

    def test_a_tick_size_override_in_the_config(self):
        s = size.size_trade(plan(), cfg(tick_sizes={"reliance.ns": 0.05}), today=TODAY)
        self.assertEqual(s["tick_size"], 0.05)
        self.assertEqual(s["entry"], 1177.85)          # 1177.82 up to 0.05
        self.assertEqual(s["stop_limit"], 1157.45)     # 1157.48 down to 0.05
        with self.assertRaises(size.SizeError):
            size.size_trade(plan(), cfg(tick_sizes={"RELIANCE": 0.25}), today=TODAY)

    def test_without_a_plan_tick_the_coarse_tick_is_used(self):
        p = plan()
        p.pop("tick_size")
        s = size.size_trade(p, cfg(), today=TODAY)
        self.assertEqual(s["tick_size"], 0.1)          # 1174.30 +-20% stays in the 1,000 to 5,000 band
        self.assertEqual(s["qty"], 48)

    def test_symbols_are_normalised(self):
        p = plan(symbol="m&m.ns", entry=2891.60, stop=2872.10, target=2930.60, tick_size=0.1)
        s = size.size_trade(p, cfg(), today=TODAY)
        o = size.kite_order(s)
        self.assertEqual(o["symbol"], "M&M")
        self.assertEqual(o["price"], 2900.30)          # 2891.60 x 1.003 = 2900.27, up to 0.10
        self.assertEqual(o["stop_limit"], 2857.70)     # 2872.10 x 0.995 = 2857.74, down to 0.10

    def test_a_day_trade_is_mis_and_the_buffers_are_checked(self):
        with self.assertRaises(size.SizeError) as cm:
            size.size_trade(plan(), cfg(product="CNC"), today=TODAY)
        self.assertIn("product MIS", str(cm.exception))
        with self.assertRaises(size.SizeError) as cm:
            size.size_trade(plan(), cfg(stop_limit_buffer_pct=6), today=TODAY)
        self.assertIn("between 0 and 5", str(cm.exception))

    def test_zero_buffers(self):
        s = size.size_trade(plan(), cfg(entry_limit_buffer_pct=0, stop_limit_buffer_pct=0), today=TODAY)
        self.assertEqual(s["entry"], 1174.30)
        self.assertEqual(s["stop_limit"], 1163.20)     # an SL needs its limit under the trigger: one tick
        self.assertEqual(s["risk_per_share"], 11.10)


class WeightsMode(unittest.TestCase):
    def setUp(self):
        with open(TARGETS) as fh:
            self.rows = size.parse_weights(json.load(fh))

    def test_the_example_weights_file(self):
        self.assertEqual([r["ticker"] for r in self.rows], ["RELIANCE", "HDFCBANK", "TCS", "INFY", "M&M"])
        self.assertEqual(sum(r["weight"] for r in self.rows), 100.0)

    def test_inline_weights_and_symbol_normalising(self):
        rows = size.parse_weights(size.weights_from_arg("reliance.ns=60, m&m=40"))
        self.assertEqual([(r["ticker"], r["weight"]) for r in rows], [("RELIANCE", 60.0), ("M&M", 40.0)])
        with self.assertRaises(size.SizeError):
            size.weights_from_arg("RELIANCE")
        with self.assertRaises(size.SizeError):
            size.parse_weights({"BRK.B": 10})

    def test_whole_shares_need_prices(self):
        with self.assertRaises(size.SizeError) as cm:
            size.size(self.rows, cfg(), prices={"RELIANCE": 1174.30})
        self.assertIn("need a price for", str(cm.exception))

    def test_whole_shares_with_prices(self):
        r = size.size(self.rows, cfg(max_position_pct=40), prices=PRICES)
        for p in r["positions"]:
            self.assertEqual(p["shares"], int(p["shares"]))
            self.assertAlmostEqual(p["inr"], round(p["shares"] * p["price"], 2))
            self.assertLessEqual(p["inr"], r["cap"] + 1e-6)
        self.assertLessEqual(r["invested"], r["investable"] + 1e-6)
        self.assertAlmostEqual(r["idle"], round(r["investable"] - r["invested"], 2))

    def test_orders_are_cnc_limit_buys_on_the_tick(self):
        r = size.size(self.rows, cfg(max_position_pct=40), prices=PRICES)
        orders = size.orders_from(r, "example")
        self.assertEqual(len(orders), 5)
        for o in orders:
            self.assertEqual((o["side"], o["order_type"], o["product"]), ("buy", "LIMIT", "CNC"))
            self.assertTrue(on_tick(o["price"], o["tick_size"]), o)
            self.assertGreaterEqual(o["price"], PRICES[o["symbol"]])
        rel = [o for o in orders if o["symbol"] == "RELIANCE"][0]
        self.assertEqual((rel["tick_size"], rel["price"]), (0.1, 1177.90))
        hdfc = [o for o in orders if o["symbol"] == "HDFCBANK"][0]
        self.assertEqual((hdfc["tick_size"], hdfc["price"]), (0.05, 684.55))   # 682.50 x 1.003 = 684.55

    def test_cap_redistributes_until_nobody_is_over(self):
        r = size.size(self.rows, cfg(max_position_pct=22), prices=PRICES)
        self.assertTrue(all(p["inr"] <= r["cap"] + 1e-6 for p in r["positions"]))
        self.assertTrue(any(p["capped"] for p in r["positions"]))

    def test_everything_capped_leaves_money_idle(self):
        rows = size.parse_weights({"RELIANCE": 50, "HDFCBANK": 50})
        r = size.size(rows, cfg(max_position_pct=20), prices=PRICES)
        self.assertTrue(all(p["capped"] for p in r["positions"]))
        self.assertGreater(r["idle"], 40000)

    def test_slices_under_the_minimum_are_dropped_and_shared_out(self):
        rows = size.parse_weights({"RELIANCE": 99.6, "HDFCBANK": 0.4})
        r = size.size(rows, cfg(max_position_pct=100, min_order_inr=1000), prices=PRICES)
        self.assertEqual([p["ticker"] for p in r["positions"]], ["RELIANCE"])
        self.assertEqual(r["dropped"][0]["ticker"], "HDFCBANK")

    def test_blocked_dropped_first_and_top_n(self):
        r = size.size(self.rows, cfg(blocked=["TCS"], max_position_pct=40), prices=PRICES)
        self.assertNotIn("TCS", [p["ticker"] for p in r["positions"]])
        self.assertEqual(r["dropped"][0], {"ticker": "TCS", "reason": "on your blocked list"})
        r = size.size(self.rows, cfg(max_position_pct=40), prices=PRICES, top=2)
        self.assertEqual([p["ticker"] for p in r["positions"]], ["RELIANCE", "HDFCBANK"])

    def test_no_budget_and_duplicates_are_errors(self):
        with self.assertRaises(size.SizeError):
            size.size(self.rows, cfg(budget=None), prices=PRICES)
        with self.assertRaises(size.SizeError) as cm:
            size.size(size.parse_weights([{"ticker": "RELIANCE", "weight": 1}, {"ticker": "RELIANCE.NS",
                                                                               "weight": 2}]), cfg(), prices=PRICES)
        self.assertIn("listed twice", str(cm.exception))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            pp = write(tmp, "prices.json", {"prices": PRICES})
            out_path = os.path.join(tmp, "orders.json")
            code, out, _ = run(["--targets", TARGETS, "--config", CFG, "--prices", pp, "--write", out_path,
                                "--now", NOW])
            self.assertEqual(code, 0)
            self.assertIn("Budget ₹1,00,000, cash buffer 10% = ₹10,000", out)
            self.assertIn("LIMIT", out)
            self.assertIn("CNC, day", out)
            self.assertIn("These buys have no stop", out)
            with open(out_path) as fh:
                doc = json.load(fh)
            self.assertIs(doc["dry_run"], True)
            self.assertEqual(len(doc["orders"]), 5)


if __name__ == "__main__":
    unittest.main()
