import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")
from trader_common import clock, orders as kite  # noqa: E402  (the tools put the repo root on sys.path)

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS = os.path.join(FIX, "trader-risk", "orders-reliance.json")   # trader-budget's real order, 2026-10-29
NOW = "2026-10-29 10:15"
TODAY = "2026-10-29"


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def order(**over):
    with open(ORDERS, encoding="utf-8") as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return {k: v for k, v in o.items() if v is not None}


def gail(**over):
    o = {"symbol": "GAIL", "side": "buy", "qty": 100, "order_type": "LIMIT", "price": 168.67, "product": "MIS",
         "stop": 166.43, "stop_limit": 165.60, "target": 173.15, "tick_size": 0.01}
    o.update(over)
    return o


def sell(qty=48, **over):
    o = {"symbol": "RELIANCE", "side": "sell", "qty": qty, "order_type": "LIMIT", "price": 1190.40,
         "product": "MIS", "tick_size": 0.1}
    o.update(over)
    return o


def holding(qty=48, cash=43460.80):
    return {"cash": cash, "positions": [{"ticker": "RELIANCE", "shares": qty,
                                         "lots": [{"date": TODAY, "shares": qty, "cost": round(qty * 1177.90, 2)}]}]}


def placed(symbol="TCS", max_loss=900.0, kind="entry", status="placed", side="buy", date=TODAY, **extra):
    r = {"date": date, "status": status, "symbol": symbol, "side": side, "kind": kind, "max_loss": max_loss,
         "dry_run": True}
    r.update(extra)
    return r


def run_check(orders, c=None, now=NOW, **kw):
    return check.check(orders, c if c is not None else cfg(), now=check.parse_now(now), **kw)


def one(orders, **kw):
    return run_check(orders, **kw)["results"][0]


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = check.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheReel(unittest.TestCase):
    """48 RELIANCE, LIMIT 1177.90, SL 1163.30 / 1157.40, target 1196.30, tick 0.10, on a fresh ₹1,00,000."""

    def test_the_reliance_ticket_passes_every_check(self):
        res = run_check([order()])
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["kind"], "entry")
        self.assertEqual((r["entry_ref"], r["stop"], r["stop_limit"], r["target"], r["tick_size"]),
                         (1177.90, 1163.30, 1157.40, 1196.30, 0.1))
        self.assertEqual(r["risk_per_share"], 20.50)
        self.assertEqual(r["max_loss"], 984.0)
        self.assertEqual(r["reward_risk"], 1.26)
        self.assertEqual(r["value_inr"], 56539.20)
        self.assertEqual(res["cash_after"], 43460.80)
        self.assertIs(res["dry_run"], True)

    def test_49_shares_is_blocked_by_the_max_loss_only(self):
        r = one([order(qty=49)])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertEqual(len(r["reasons"]), 1, r["reasons"])
        self.assertIn("49 shares x 20.50 risk a share = ₹1,004.50 if the SL fills at 1157.40", r["reasons"][0])

    def test_the_fingerprint_is_shared_with_the_broker(self):
        o = order()
        r = one([o])
        self.assertEqual(r["fingerprint"], kite.fingerprint(o))
        self.assertEqual(r["fingerprint"], ticket.fingerprint(o))
        self.assertEqual(kite.fingerprint(o), kite.fingerprint(dict(o, qty="48", price="1177.900", why="other")))
        for k, v in (("qty", 47), ("stop", 1163.20), ("stop_limit", 1157.30), ("target", 1196.40),
                     ("tick_size", 0.05), ("product", "CNC"), ("price", 1178.00)):
            self.assertNotEqual(kite.fingerprint(dict(o, **{k: v})), kite.fingerprint(o), k)

    def test_cli_records_the_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "risk.json")
            code, out, _ = cli(["--orders", ORDERS, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"),
                                "--prices", os.path.join(tmp, "none.json"), "--state", os.path.join(tmp, "b.json"),
                                "--out", out_path, "--now", NOW])
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy RELIANCE 48 shares, entry", out)
            self.assertIn("limit 1177.90, SL trigger 1163.30 limit 1157.40, target 1196.30", out)
            self.assertIn("max loss ₹984 (limit ₹1,000), reward:risk 1.26 (min 1)", out)
            self.assertIn("Cash after the passed orders: ₹43,460.80 (dry run: nothing goes to Zerodha)", out)
            self.assertIn("not financial advice", out)
            with open(out_path) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["fingerprint"], kite.fingerprint(order()))

    def test_cli_json_and_exit_code_on_a_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "o.json")
            with open(path, "w") as fh:
                json.dump({"orders": [order(qty=49)]}, fh)
            code, out, _ = cli(["--orders", path, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"),
                                "--prices", os.path.join(tmp, "none.json"), "--state", os.path.join(tmp, "b.json"),
                                "--no-write", "--now", NOW, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["results"][0]["verdict"], "BLOCK")


class Format(unittest.TestCase):
    def test_market_and_sl_m_style_orders_are_blocked(self):
        r = one([order(order_type="MARKET")])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("market protection", " ".join(r["reasons"]))

    def test_a_price_off_the_stocks_tick(self):
        r = one([order(price=1177.85)])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("price 1177.85 is not a multiple of RELIANCE's ₹0.10 tick", r["reasons"][0])

    def test_paise_prices_pass_under_250(self):
        r = one([gail()])
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["tick_size"], 0.01)
        r = one([gail(tick_size=None)])                 # the coarse tick of 168.67 is still 0.01
        self.assertEqual(r["verdict"], "PASS", r["reasons"])

    def test_a_tick_nse_does_not_use(self):
        r = one([order(tick_size=0.25)])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("not an NSE tick", r["reasons"][0])

    def test_the_config_override_wins_and_warns(self):
        r = one([order(price=1177.85)], c=cfg(tick_sizes={"reliance": 0.05}))
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertTrue(any("tick_sizes says 0.05" in w for w in r["warnings"]))

    def test_a_limit_needs_a_price(self):
        r = one([order(price=None)])
        self.assertIn("format: a LIMIT order needs a price", r["reasons"])

    def test_product(self):
        self.assertIn("product must be MIS", " ".join(one([order(product="CO")])["reasons"]))
        self.assertEqual(one([order(product="CNC")])["verdict"], "PASS")

    def test_quantity(self):
        for q in (0, -5, 2.5, "lots"):
            self.assertEqual(one([order(qty=q)])["verdict"], "BLOCK", q)
        self.assertEqual(one([order(qty="48")])["verdict"], "PASS")

    def test_cancel_order_id_belongs_on_an_exit(self):
        r = one([order(cancel_order_id="251029000222")])      # the legacy single id is still read
        self.assertIn("cancel_order_ids belong on an exit", " ".join(r["reasons"]))
        r = one([order(cancel_order_ids=["251029000222", "251029000333"])])
        self.assertIn("cancel_order_ids belong on an exit", " ".join(r["reasons"]))

    def test_the_sl_leg(self):
        r = one([order(stop=None)])
        self.assertIn("stop_limit needs a stop", " ".join(r["reasons"]))
        r = one([order(stop_limit=1165.00)])
        self.assertIn("the SL limit 1165.00 must be under its trigger 1163.30", " ".join(r["reasons"]))
        r = one([order(stop_limit=None)])
        self.assertIn("no stop_limit", " ".join(r["reasons"]))


class DayTradeChecks(unittest.TestCase):
    def test_a_stop_is_required(self):
        r = one([order(stop=None, stop_limit=None)])
        self.assertIn("stop: no stop", " ".join(r["reasons"]))
        r = one([order(stop=None, stop_limit=None)], c=cfg(require_stop=False, min_reward_risk=None))
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertTrue(any("no stop" in w for w in r["warnings"]))

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the limit", " ".join(one([order(stop=1180.00, stop_limit=1175.00)])["reasons"]))
        self.assertIn("must be above the limit", " ".join(one([order(target=1170.00)])["reasons"]))

    def test_reward_risk(self):
        r = one([order()], c=cfg(min_reward_risk=1.5))
        self.assertIn("reward:risk: 1.26 (target 1196.30, limit 1177.90, stop 1163.30) is under your minimum 1.5",
                      r["reasons"])

    def test_a_plan_stop_that_differs_warns(self):
        o = order()
        o["plan"] = dict(o["plan"], stop=1160.00)
        self.assertTrue(any("differs from the plan's stop" in w for w in one([o])["warnings"]))

    def test_a_fresh_price_above_the_limit_warns_but_does_not_move_the_risk(self):
        now = check.parse_now(NOW)
        res = check.check([order()], cfg(), prices={"RELIANCE": 1185.00}, now=now, price_times={"RELIANCE": now})
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["max_loss"]), ("PASS", 984.0))
        self.assertTrue(any("will rest unfilled" in w for w in r["warnings"]))

    def test_a_stale_price_is_ignored(self):
        now = check.parse_now(NOW)
        old = check.parse_now("2026-10-29 09:20")
        r = check.check([order()], cfg(), prices={"RELIANCE": 1185.00}, now=now,
                        price_times={"RELIANCE": old})["results"][0]
        self.assertFalse(any("rest unfilled" in w for w in r["warnings"]))

    def test_daily_loss_counts_dry_run_placements(self):
        r = one([order()], state={"orders": [placed(max_loss=2500.0)]})
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("daily loss: ₹2,500 already at risk today plus ₹984 for this one", r["reasons"][0])

    def test_trades_per_day(self):
        st = {"orders": [placed("TCS"), placed("INFY"), placed("SBIN")]}
        r = one([order()], state=st, c=cfg(max_daily_loss_inr=100000))
        self.assertIn("trades: 3 entries placed today", " ".join(r["reasons"]))

    def test_exits_stop_legs_declines_and_other_days_do_not_count_as_trades(self):
        st = {"orders": [placed("TCS", kind="exit", side="sell", max_loss=None),
                         placed("TCS", kind="stop", side="sell", max_loss=None),
                         placed("INFY", status="declined"), placed("SBIN", date="2026-10-28")]}
        res = run_check([order()], state=st)
        self.assertEqual(res["results"][0]["verdict"], "PASS", res["results"][0]["reasons"])
        self.assertEqual(res["today"]["entries_placed"], 0)

    def test_the_batch_counts_toward_the_daily_limits(self):
        res = run_check([gail(), order()], c=cfg(max_daily_loss_inr=1200))
        self.assertEqual([r["verdict"] for r in res["results"]], ["PASS", "BLOCK"])
        self.assertIn("daily loss", res["results"][1]["reasons"][0])

    def test_duplicates_and_the_daily_order_limit(self):
        res = run_check([order(), order()], c=cfg(max_position_pct=100, max_order_inr=200000, budget=300000,
                                                  max_daily_loss_inr=100000))
        self.assertIn("duplicate: a second buy RELIANCE in this batch", res["results"][1]["reasons"])
        r = one([order()], state={"orders": [placed("RELIANCE")]})
        self.assertIn("duplicate: a buy RELIANCE was already placed today", r["reasons"])
        st = {"orders": [placed("X%d" % i, kind="exit", side="sell", max_loss=None) for i in range(10)]}
        self.assertIn("daily limit: 10 placed today", " ".join(one([order()], state=st)["reasons"]))

    def test_entry_window_in_ist(self):
        self.assertIn("no new entries before 09:30 IST (it is 09:20)",
                      " ".join(one([order()], now="2026-10-29 09:20")["reasons"]))
        self.assertIn("no new entries after 14:45 IST (it is 14:50)",
                      " ".join(one([order()], now="2026-10-29 14:50")["reasons"]))
        self.assertIn("market is closed (Thu 2026-10-29 15:40 IST",
                      " ".join(one([order()], now="2026-10-29 15:40")["reasons"]))

    def test_holidays_and_weekends(self):
        self.assertIn("NSE holiday: Diwali-Balipratipada",
                      " ".join(one([order()], now="2026-11-10 10:15")["reasons"]))
        self.assertIn("weekend", " ".join(one([order()], now="2026-10-31 10:15")["reasons"]))

    def test_a_config_holiday_override(self):
        self.addCleanup(clock.set_overrides, None)
        clock.set_overrides({"2026-10-29": "exchange outage"})
        self.assertIn("NSE holiday: exchange outage", " ".join(one([order()])["reasons"]))
        clock.set_overrides({})
        self.assertEqual(one([order()])["verdict"], "PASS")

    def test_the_window_uses_ist_not_the_machine(self):
        r = check.check([order()], cfg(), now=check.parse_now("2026-10-29T04:45:00Z"))["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(check.check([order()], cfg(), now=check.parse_now("2026-10-29T04:45:00Z"))["now_ist"],
                         "2026-10-29 10:15")

    def test_outside_the_calendar_warns(self):
        res = run_check([order()], now="2027-01-05 10:15", today="2027-01-05")
        self.assertTrue(any("holiday list covers 2026 only" in w for w in res["warnings"]))


class Exits(unittest.TestCase):
    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        st = {"orders": [placed("TCS"), placed("INFY"), placed("SBIN", max_loss=2900.0)]}
        r = one([sell(cancel_order_id="DRYRUN-stop-20812921")], portfolio=holding(), state=st,
                now="2026-10-29 15:05")
        self.assertEqual((r["verdict"], r["kind"], r["reasons"]), ("PASS", "exit", []))
        self.assertIsNone(r["max_loss"])               # an exit adds nothing to the day's risk

    def test_a_partial_exit_and_the_rest(self):
        res = run_check([sell(20), sell(28)], portfolio=holding())
        self.assertEqual([(r["verdict"], r["kind"]) for r in res["results"]], [("PASS", "exit"), ("PASS", "exit")])

    def test_format_and_short_still_apply_to_a_sell(self):
        self.assertEqual(one([sell(order_type="MARKET")], portfolio=holding())["verdict"], "BLOCK")
        self.assertEqual(one([sell(price=1190.45)], portfolio=holding())["verdict"], "BLOCK")
        r = one([sell(60)], portfolio=holding())
        self.assertIn("short: selling more RELIANCE than you hold (48 shares)", " ".join(r["reasons"]))

    def test_buy_then_sell_in_one_batch(self):
        res = run_check([order(), sell(48)])
        self.assertEqual([(r["verdict"], r["kind"]) for r in res["results"]], [("PASS", "entry"), ("PASS", "exit")])

    def test_a_short_entry_only_when_allowed(self):
        o = sell(10, stop=1200.00, stop_limit=1206.00, target=1150.00)
        self.assertIn("short:", " ".join(one([o])["reasons"]))
        r = one([o], c=cfg(allow_short=True))
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "entry"), r["reasons"])


class AccountChecks(unittest.TestCase):
    def test_max_and_min_order(self):
        self.assertIn("over your ₹50,000 max order", " ".join(one([order()], c=cfg(max_order_inr=50000))["reasons"]))
        self.assertIn("under your ₹60,000 minimum order",
                      " ".join(one([order()], c=cfg(min_order_inr=60000))["reasons"]))

    def test_position_cap(self):
        r = one([order()], c=cfg(max_position_pct=50))
        self.assertIn("position: RELIANCE would be ₹56,539.20, 56.5% of the ₹1,00,000 account",
                      " ".join(r["reasons"]))

    def test_cash_buffer_and_margin(self):
        r = one([order()], portfolio={"cash": 60000, "positions": []})
        self.assertIn("under your 10% buffer", " ".join(r["reasons"]))
        r = one([order()], portfolio={"cash": 50000, "positions": []})
        self.assertIn("that would be margin", " ".join(r["reasons"]))

    def test_a_sell_frees_cash_for_a_later_buy(self):
        alone = one([order()], portfolio=holding(cash=60000))
        self.assertIn("under your 10% buffer", " ".join(alone["reasons"]))
        res = run_check([sell(48), order()], portfolio=holding(cash=60000))
        self.assertEqual([r["verdict"] for r in res["results"]], ["PASS", "PASS"],
                         [r["reasons"] for r in res["results"]])

    def test_blocked_list(self):
        self.assertIn("blocked: RELIANCE is on your blocked list",
                      one([order()], c=cfg(blocked=["reliance.ns"]))["reasons"])

    def test_fno_and_junk_symbols(self):
        for sym in ("NIFTY26OCTFUT", "BANKNIFTY26OCT52000CE", "NIFTY2610624500PE"):
            self.assertIn("F&O contract", " ".join(one([order(symbol=sym)])["reasons"]), sym)
        self.assertIn("does not look like an NSE equity symbol", " ".join(one([order(symbol="BRK.B")])["reasons"]))

    def test_nse_symbols_are_normalised(self):
        r = one([order(symbol="reliance.ns")])
        self.assertEqual((r["symbol"], r["verdict"]), ("RELIANCE", "PASS"))
        for sym, price in (("M&M", 2900.30), ("BAJAJ-AUTO", 10400.00)):
            o = order(symbol=sym, price=price, stop=price * 0.99, stop_limit=None, target=price * 1.03)
            self.assertNotIn("asset", " ".join(one([o])["reasons"]), sym)

    def test_dollar_keys_are_refused_by_name(self):
        with self.assertRaises(check.RiskError) as cm:
            run_check([order()], c=cfg(max_loss_per_trade_usd=50))
        self.assertIn("max_loss_per_trade_usd -> max_loss_per_trade_inr", str(cm.exception))

    def test_us_only_keys_are_ignored_with_a_note(self):
        res = run_check([order()], c=cfg(pdt_guard=True, fractional=True, paper=False))
        self.assertEqual(res["results"][0]["verdict"], "PASS")
        notes = " ".join(res["warnings"])
        for word in ("pdt_guard", "fractional", "paper"):
            self.assertIn(word, notes)
        self.assertNotIn("LIVE", notes)                 # "paper": false is not a way to go live

    def test_live_warns(self):
        res = run_check([order()], c=cfg(dry_run=False))
        self.assertTrue(any("LIVE" in w and "real money" in w for w in res["warnings"]))


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as tmp:
            pp = os.path.join(tmp, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-29T04:44:00Z", "prices": {"RELIANCE": 1185.00}}, fh)
            base = ["--orders", ORDERS, "--config", CFG, "--portfolio", os.path.join(tmp, "none.json"),
                    "--prices", pp, "--state", os.path.join(tmp, "b.json"), "--no-write"]
            _, out, _ = cli(base + ["--now", NOW])
            self.assertIn("will rest unfilled", out)
            _, out, _ = cli(base + ["--now", "2026-10-29 11:30"])
            self.assertNotIn("will rest unfilled", out)

    def test_errors_are_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = cli(["--orders", os.path.join(tmp, "none.json"), "--now", NOW])
            self.assertEqual(code, 1)
            self.assertEqual(len(err.strip().splitlines()), 1)
            self.assertTrue(err.startswith("check.py: "))
            code, _, err = cli(["--orders", ORDERS, "--now", NOW, "--today", "29-10-2026", "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("--today wants a date", err)


if __name__ == "__main__":
    unittest.main()
