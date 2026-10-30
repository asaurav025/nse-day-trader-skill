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

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")              # real 5-day Yahoo charts to Thu 2026-10-29
PLAN = os.path.join(QDIR, "plan-reliance-1r.json")      # trader-exit's real plan for that day, 1R
HOLIDAYS = os.path.join(FIX, "trader-common", "nse-holiday-master-2026.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; nse-day-trader-skill/1.0; +https://github.com/asaurav025/nse-day-trader-skill)"


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def setUp(self):
        alerts.clock.set_overrides({})

    def tearDown(self):
        alerts.clock.set_overrides(None)

    def test_holidays_match_nses_published_list(self):
        with open(HOLIDAYS) as fh:
            cm = json.load(fh)["CM"]
        published = {datetime.datetime.strptime(x["tradingDate"], "%d-%b-%Y").date().isoformat() for x in cm}
        self.assertEqual(set(alerts.clock.NSE_HOLIDAYS), published)
        for d in published:
            ok, why = alerts.trading_day(datetime.date.fromisoformat(d))
            self.assertFalse(ok, d)

    def test_ist_is_a_fixed_offset(self):
        self.assertEqual(ist("2026-10-29 09:15"), DT(2026, 10, 29, 3, 45))
        self.assertEqual(ist("2026-03-09 09:15"), DT(2026, 3, 9, 3, 45))     # no daylight saving, ever
        self.assertEqual(ist("2026-10-29T15:30:00Z"), DT(2026, 10, 29, 15, 30))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-29 10:15"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"]), ("09:15", "15:30"))
        self.assertIn("now_ist", st)
        self.assertNotIn("now_new_york", st)
        before = alerts.market_status(ist("2026-10-29 09:14"))
        self.assertFalse(before["open"])
        self.assertEqual(before["next_open"], "2026-10-29 09:15")
        after = alerts.market_status(ist("2026-10-29 15:30"))
        self.assertFalse(after["open"])
        self.assertEqual(after["next_open"], "2026-10-30 09:15")

    def test_holiday_and_weekend(self):
        diwali = alerts.market_status(ist("2026-11-10 10:00"))
        self.assertFalse(diwali["open"])
        self.assertIn("Diwali-Balipratipada", diwali["reason"])
        self.assertEqual(diwali["next_open"], "2026-11-11 09:15")
        sat = alerts.market_status(ist("2026-10-31 10:00"))
        self.assertEqual(sat["reason"], "weekend")
        self.assertEqual(sat["next_open"], "2026-11-02 09:15")
        self.assertFalse(alerts.market_status(ist("2027-03-01 10:00"))["calendar_known"])

    def test_config_holiday_override(self):
        alerts.clock.set_overrides({"2026-10-29": "ad-hoc closure", "2026-11-10": None})
        self.assertFalse(alerts.market_status(ist("2026-10-29 10:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-11-10 10:00"))["open"])


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual((q["price"], q["prev_close"], q["currency"]), (1167.4, 1158.0, "INR"))
        self.assertEqual(q["time"], "2026-10-29T09:59:03Z")
        with open(os.path.join(QDIR, "yahoo-M&M.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "M&M")
        self.assertEqual((q["symbol"], q["price"], q["prev_close"]), ("M&M", 2890.0, 2869.9))

    def test_a_chart_for_another_symbol_is_refused(self):
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            with self.assertRaises(alerts.AlertError):
                alerts.parse_yahoo(fh.read(), "TCS")

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("M&M"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d")
        self.assertIn("//query1.finance.yahoo.com/v8/finance/chart/BAJAJ-AUTO.NS",
                      alerts.yahoo_url("BAJAJ-AUTO", alerts.YAHOO_HOSTS[1]))
        self.assertFalse(hasattr(alerts, "stooq_url"))
        self.assertFalse(hasattr(alerts, "parse_stooq"))

    def test_offline_quotes_symbols_and_errors(self):
        q, errors = alerts.get_quotes(["reliance.ns", "M&M", "BRK.B", "NOPE"], QDIR)
        self.assertEqual(sorted(q), ["M&M", "RELIANCE"])
        self.assertEqual(len(errors), 2)
        self.assertIn("not an NSE symbol", errors[0])
        self.assertIn("no offline quote file", errors[1])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-RELIANCE.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["RELIANCE"], d)
            self.assertEqual((q["RELIANCE"]["price"], errors), (1167.4, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            self.body = fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent")))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _ in f.calls]

    def test_query2_first_with_the_user_agent(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual((q["RELIANCE"]["price"], errors), (1167.4, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", UA)])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["RELIANCE"])[0]["RELIANCE"]["price"], 1167.4)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx_and_says_there_is_no_other_source(self):
        f = self.net([429, 429, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        self.net([500, 502])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("HTTP 502 for RELIANCE (no other price source)", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_symbols(self):
        f = self.net([200, 200])
        alerts.get_quotes(["RELIANCE", "RELIANCE.NS"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["RELIANCE", "M&M", "BAJAJ-AUTO", "HDFCBANK"], QDIR)
        self.now = ist("2026-10-29 10:15")
        self.open = alerts.market_status(self.now)

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once_in_rupees(self):
        rules = [{"id": "rel-1160", "symbol": "RELIANCE", "type": "above", "price": 1160}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("RELIANCE is ₹1,167.40: at or above your alert level ₹1,160.00", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])

    def test_stop_and_rearm(self):
        rules = [{"id": "baj-stop", "symbol": "BAJAJ-AUTO", "type": "stop", "price": 10400, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertIn("BAJAJ-AUTO is ₹10,383.00: at or below your stop ₹10,400.00", rings[0]["message"])
        self.q["BAJAJ-AUTO"]["price"] = 10450.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("baj-stop", st["rung"])
        self.q["BAJAJ-AUTO"]["price"] = 10390.0
        self.assertEqual(len(alerts.evaluate(rules, self.q, self.open, st, self.now)[0]), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "rel-3", "symbol": "RELIANCE", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        up = [{"id": "rel-up", "symbol": "RELIANCE", "type": "pct_move", "pct": 0.5, "direction": "up"}]
        rings, st = alerts.evaluate(up, self.q, self.open, {}, self.now)
        self.assertIn("RELIANCE moved +0.81% to ₹1,167.40 (from ₹1,158.00)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(up, self.q, self.open, st, self.now)[0], [])
        down = [{"id": "baj-down", "symbol": "BAJAJ-AUTO", "type": "pct_move", "pct": 0.5, "direction": "down"}]
        self.assertIn("BAJAJ-AUTO moved -0.97%", alerts.evaluate(down, self.q, self.open, {}, self.now)[0][0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        self.assertEqual(self.at("2026-10-29 09:00", rules)[0], [])
        rings, st = self.at("2026-10-29 09:15", rules)
        self.assertIn("Opening bell: the NSE is open (2026-10-29 09:15 IST)", rings[0]["message"])
        self.assertEqual(self.at("2026-10-29 11:00", rules, st)[0], [])
        self.assertEqual(len(self.at("2026-10-30 09:16", rules, st)[0]), 1)
        self.assertEqual(self.at("2026-11-10 10:00", rules)[0], [])                 # Diwali-Balipratipada

    def test_the_plan_gives_a_stop_a_target_and_flat_by_on_its_tick(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("reliance-plan-stop", "stop"), ("reliance-plan-target", "target"),
                          ("reliance-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1163.3, 1185.3))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:10", "flat by close"))
        self.assertNotIn("before_close", rules[2])
        self.assertTrue(all(r["date"] == "2026-10-29" for r in rules))
        self.assertEqual(self.at("2026-10-29 10:15", rules)[0], [])           # 1,167.40: between the two
        q = json.loads(json.dumps(self.q))
        q["RELIANCE"]["price"] = 1163.1
        rings, st = self.at("2026-10-29 11:30", rules, {}, q)
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-stop"])
        self.assertIn("RELIANCE is ₹1,163.10: at or below your stop ₹1,163.30 (plan stop: opening-range midpoint)",
                      rings[0]["message"])
        q["RELIANCE"]["price"] = 1185.3
        self.assertEqual([r["id"] for r in self.at("2026-10-29 12:00", rules, st, q)[0]], ["reliance-plan-target"])

    def test_plan_levels_snap_to_the_plans_tick(self):
        rules = alerts.rules_from_plan({"symbol": "RELIANCE", "stop": 1163.33, "target": 1185.27,
                                        "tick_size": 0.1, "date": "2026-10-29"})
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1163.3, 1185.3))
        gail = alerts.rules_from_plan({"symbol": "GAIL", "stop": 166.433, "target": 173.151, "tick_size": 0.01})
        self.assertEqual((gail[0]["price"], gail[1]["price"]), (166.43, 173.15))

    def test_flat_by_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-29 15:09", rules)[0], [])
        rings, st = self.at("2026-10-29 15:10", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat by close: it is 15:10 IST (set for 15:10), the market closes at 15:30", rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol RELIANCE", rings[0]["message"])
        self.assertEqual(self.at("2026-10-29 15:15", rules, st)[0], [])
        rings, _ = self.at("2026-10-29 15:45", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-30 15:11", rules, st)[0], [])     # plan rules: their day only

    def test_a_time_alert_needs_a_session(self):
        rule = [{"id": "flat", "type": "time", "at": "15:10", "label": "flat by close"}]
        self.assertEqual(self.at("2026-10-31 15:11", rule)[0], [])         # Saturday
        self.assertEqual(self.at("2026-11-10 15:11", rule)[0], [])         # Diwali-Balipratipada
        rings, st = self.at("2026-12-01 15:10", rule)
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:40:00Z")

    def test_before_close_is_refused(self):
        rule = [{"id": "flat", "type": "time", "at": "15:10", "label": "flat by close", "before_close": True}]
        with self.assertRaises(alerts.AlertError) as cm:
            self.at("2026-10-29 15:10", rule)
        self.assertIn("NSE has no early closes; remove before_close", str(cm.exception))

    def test_the_example_rules_are_valid_and_match_the_plan(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("reliance-")], plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "RELIANCE"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "4pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "BRK.B", "price": 1}], {}, self.open, {},
                            self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "RELIANCE", "stop": 1163.3})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now])

    def test_the_reel_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", os.path.join(self.d, "none")])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance-flat-by", text)
        self.assertIn("SL stop at Zerodha", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell"])[0], 0)

        self.assertEqual(self.check("2026-10-29 10:15")[0], 0)            # the opening bell rings first
        code, text, _ = self.check("2026-10-29 10:16")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit. 4 rule(s) watched today, RELIANCE ₹1,167.40.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual((doc["prices"], doc["currency"]), ({"RELIANCE": 1167.4}, "INR"))
        self.assertEqual(doc["detail"]["RELIANCE"]["fetched"], "2026-10-29T04:46:00Z")

        code, text, _ = self.check("2026-10-29 15:10")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by close", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-10-29 15:12")[0], 1)
        code, text, _ = self.check("2026-10-30 09:20")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-30 09:25")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "reliance-flat-by", "reliance-plan-stop",
                                       "reliance-plan-target"])
        with open(self.state) as fh:
            self.assertNotIn("reliance-flat-by", json.load(fh)["rung"])

    def test_portfolio_alerts_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "RELIANCE", "--above", "1200"],
                     ["--symbol", "HDFCBANK", "--stop", "650"]):
            self.assertEqual(run(["add", "--rules", self.rules] + args)[0], 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "RELIANCE.NS", "--above", "1200"])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-29 16:00")
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        with open(self.prices) as fh:
            self.assertEqual(json.load(fh)["prices"], {"RELIANCE": 1167.4, "HDFCBANK": 683.3})
        code, text, _ = self.check("2026-10-30 09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        self.assertEqual(self.check("2026-10-30 09:45")[0], 1)
        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, _, _ = run(["add", "--rules", self.rules, "--at", "15:10", "--label", "flat by close",
                          "--symbol", "m&m"])
        self.assertEqual(code, 0)
        code, _, _ = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--target", "1185.3",
                          "--date", "2026-10-29"])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1510", "type": "time", "at": "15:10", "label": "flat by close",
                                    "symbol": "M&M"})
        self.assertEqual(rules[1], {"id": "reliance-target-1185.30", "symbol": "RELIANCE", "type": "target",
                                    "price": 1185.3, "date": "2026-10-29"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "4pm"])[0], 1)
        with self.assertRaises(SystemExit):                     # --before-close is gone: no early closes
            run(["add", "--rules", self.rules, "--at", "15:10", "--before-close"])
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none")])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_a_rule_file_with_before_close_is_refused(self):
        with open(self.rules, "w") as fh:
            json.dump({"rules": [{"id": "flat", "type": "time", "at": "15:10", "before_close": True}]}, fh)
        code, _, err = self.check("2026-10-29 15:10")
        self.assertEqual(code, 1)
        self.assertIn("NSE has no early closes", err)

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "RELIANCE", "M&M", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-29T10:00:00Z"])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE   ₹1,167.40 (+0.81% vs prev close ₹1,158.00)", text)
        self.assertIn("delayed", text)
        run(["quote", "HDFCBANK", "--offline", QDIR, "--write", self.prices])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["HDFCBANK", "M&M", "RELIANCE"])
        self.assertEqual(doc["detail"]["M&M"]["prev_close"], 2869.9)
        self.assertEqual(doc["detail"]["M&M"]["fetched"], "2026-10-29T10:00:00Z")

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "HDFCBANK", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["HDFCBANK", "RELIANCE"])

    def test_clock_exit_codes(self):
        code, text, _ = run(["clock", "--now", "2026-10-29 10:15"])
        self.assertEqual(code, 0)
        self.assertIn("NSE OPEN", text)
        code, text, _ = run(["clock", "--now", "2026-12-25 10:00"])
        self.assertEqual(code, 1)
        self.assertIn("Christmas", text)
        self.assertIn("Next open: 2026-12-28 09:15 IST.", text)

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
