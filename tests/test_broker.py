import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

BDIR = os.path.join(FIX, "trader-broker")
CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
ORDERS_PATH = os.path.join(BDIR, "orders-reliance.json")   # trader-budget's real order for Thu 2026-10-29
PLAN_PATH = os.path.join(BDIR, "plan-reliance-2r.json")    # trader-exit's real 2R plan, tick 0.10
NOW = "2026-10-29 10:15"
TODAY = "2026-10-29"
BATCH = ["RELIANCE", "TCS", "M&M"]

# kite-mcp-server v0.3.2, mcp/post_tools.go: place_order and cancel_order parameters
PLACE_ORDER_PARAMS = {"variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product",
                      "order_type", "price", "validity", "validity_ttl", "disclosed_quantity", "trigger_price",
                      "iceberg_legs", "iceberg_quantity", "tag"}
PLACE_ORDER_REQUIRED = {"variety", "exchange", "tradingsymbol", "transaction_type", "quantity", "product",
                        "order_type"}
ENTRY_CALL = {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE", "transaction_type": "BUY",
              "quantity": 48, "product": "MIS", "order_type": "LIMIT", "price": 1177.9, "validity": "DAY"}
PLAIN = ("BUY 48 RELIANCE, LIMIT 1,177.90, MIS, good for today / then, once it fills: SELL those shares with an "
         "SL stop, trigger 1,163.30, limit 1,157.40 / target 1,196.30: an alert, not an order / max loss about "
         "₹984.00 if the SL fills at its limit 1,157.40 (₹700.80 at the trigger 1,163.30); a gap below 1,157.40 "
         "leaves the SL unfilled")


def reliance(**over):
    with open(ORDERS_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


def json_after(text, marker):
    """The first JSON object printed after marker."""
    i = text.index("{", text.index(marker))
    depth = 0
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return json.loads(text[i:j + 1])
    raise AssertionError("no JSON after %r" % marker)


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=None, batch=BATCH):
        return ticket.parse_yes(reply, order or reliance(), batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, buy it", "yes RELIANCE", "yes 48 RELIANCE",
                  "yes 48"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes TCS", "yes 63", "yes but make it 40", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 1170"):
            self.assertFalse(self.yes(r), r)

    def test_a_yes_that_names_another_nse_symbol_does_not_count(self):
        # upstream's pattern only saw short US tickers: these used to pass for a RELIANCE ticket
        batch = ["RELIANCE", "HDFCBANK", "BAJAJ-AUTO", "M&M", "TCS", "20MICRONS"]
        for r in ("yes HDFCBANK", "yes BAJAJ-AUTO", "yes M&M", "yes m&m", "yes hdfcbank.ns", "yes TCS",
                  "yes 20MICRONS", "yes RELIANCE and HDFCBANK"):
            self.assertFalse(self.yes(r, batch=batch), r)
        for r in ("yes", "yes RELIANCE", "yes, 48 RELIANCE", "yes reliance.ns", "yes 1177.90"):
            self.assertTrue(self.yes(r, batch=batch), r)
        ok, why = ticket.parse_yes("yes BAJAJ-AUTO", reliance(), batch)
        self.assertIn("names BAJAJ-AUTO, but this order is for RELIANCE", why)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "RELIANCE", "side": "sell", "qty": 48, "order_type": "LIMIT", "price": 1190.4,
                "product": "MIS", "tick_size": 0.1}
        self.assertTrue(self.yes("yes sell 48 RELIANCE", sell))
        self.assertTrue(self.yes("yes 1190.40", sell))
        self.assertFalse(self.yes("yes sell 50", sell))

    def test_a_two_leg_yes(self):
        for r in ("yes 1163.30", "yes max loss 984", "yes target 1196.30", "yes limit 1177.90", "yes 1157.40"):
            self.assertTrue(self.yes(r), r)
        for r in ("yes 49", "yes 50 RELIANCE", "yes but stop at 1160", "yes, stop 1163.30", "yes M&M stuff TCS",
                  "sure"):
            self.assertFalse(self.yes(r), r)


class TicketShape(unittest.TestCase):
    def test_the_entry_leg_is_kites_place_order(self):
        calls = ticket.calls_for(reliance())
        self.assertEqual([t for t, _ in calls], ["place_order"])
        args = calls[0][1]
        self.assertEqual({k: v for k, v in args.items() if k != "tag"}, ENTRY_CALL)
        self.assertTrue(set(args) <= PLACE_ORDER_PARAMS)
        self.assertTrue(PLACE_ORDER_REQUIRED <= set(args))
        self.assertIsInstance(args["quantity"], int)
        self.assertIsInstance(args["price"], float)
        self.assertRegex(args["tag"], r"^ndt[0-9a-f]{12}$")
        self.assertLessEqual(len(args["tag"]), 20)
        self.assertNotIn("why", args)
        self.assertNotIn("plan", args)

    def test_the_stop_leg_and_the_cancel(self):
        leg = ticket.kite.stop_leg(reliance(), 30)
        self.assertEqual({k: v for k, v in leg.items() if k != "tag"},
                         {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
                          "transaction_type": "SELL", "quantity": 30, "product": "MIS", "order_type": "SL",
                          "trigger_price": 1163.3, "price": 1157.4, "validity": "DAY"})
        self.assertTrue(set(leg) <= PLACE_ORDER_PARAMS)
        self.assertEqual(leg["tag"], ticket.calls_for(reliance())[0][1]["tag"])   # both legs findable together
        exit_order = {"symbol": "RELIANCE", "side": "sell", "qty": 48, "order_type": "LIMIT", "price": 1190.4,
                      "product": "MIS", "tick_size": 0.1, "cancel_order_id": "251029000222"}
        calls = ticket.calls_for(exit_order)
        self.assertEqual(calls[0], ("cancel_order", {"variety": "regular", "order_id": "251029000222"}))
        self.assertEqual((calls[1][0], calls[1][1]["transaction_type"], calls[1][1]["order_type"]),
                         ("place_order", "SELL", "LIMIT"))

    def test_plain_words_and_max_loss(self):
        self.assertEqual(ticket.plain(reliance()), PLAIN)
        self.assertEqual(ticket.max_loss(reliance()), (984.0, 1163.3))
        m = ticket.kite.max_loss(reliance())
        self.assertEqual((m["at_stop"], m["at_stop_limit"], m["per_share"]), (700.8, 984.0, 20.5))
        no_limit = reliance(stop_limit=None)
        self.assertEqual(ticket.max_loss(no_limit), (700.8, 1163.3))

    def test_the_fingerprint_is_shared_with_trader_risk(self):
        o = reliance()
        self.assertEqual(ticket.fingerprint(o), check.fingerprint(o))
        self.assertEqual(ticket.fingerprint(o), ticket.fingerprint(reliance(qty="48", price="1177.90")))
        for change in ({"qty": 47}, {"stop": 1163.2}, {"stop_limit": 1157.3}, {"target": 1196.4},
                       {"price": 1178.0}, {"tick_size": 0.05}):
            self.assertNotEqual(ticket.fingerprint(reliance(**change)), ticket.fingerprint(o), change)
        self.assertEqual(ticket.fingerprint(reliance(why="other words")), ticket.fingerprint(o))

    def test_no_alpaca_left(self):
        for name in ("tool_input", "ALPACA_FIELDS", "is_bracket", "TIF_WORDS"):
            self.assertFalse(hasattr(ticket, name), name)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")))


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        shutil.copy(ORDERS_PATH, self.orders)
        self.cfg = os.path.join(self.d, "config.json")
        shutil.copy(CFG_PATH, self.cfg)
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.none = os.path.join(self.d, "none")

    def tearDown(self):
        shutil.rmtree(self.d)

    def go_live(self):
        with open(self.cfg) as fh:
            c = json.load(fh)
        c["dry_run"] = False
        with open(self.cfg, "w") as fh:
            json.dump(c, fh)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def flatten(self, *extra, **kw):
        return run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                    "--plan", PLAN_PATH, "--config", self.cfg, "--state", self.state,
                    "--now", kw.get("now", "2026-10-29 11:40")] + list(extra))

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_dry_run_flow(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, Zerodha (Kite), DRY RUN: nothing is sent", text)
        for part in PLAIN.split(" / "):
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn("DRY RUN: do not call", text)
        self.assertEqual({k: v for k, v in json_after(text, "1. place_order").items() if k != "tag"}, ENTRY_CALL)
        self.assertNotIn('"why"', text)
        self.assertIn('Reply "yes" to rehearse', text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("Do NOT call place_order", text)
        st = self.state_doc()["orders"]
        self.assertEqual([r["status"] for r in st], ["approved", "placed"])
        self.assertTrue(all(r["dry_run"] for r in st))
        self.assertTrue(st[1]["broker_order_id"].startswith("DRYRUN-entry-"))
        self.assertEqual((st[0]["reply"], st[0]["kind"], st[0]["max_loss"], st[0]["date"]),
                         ("yes", "entry", 984.0, TODAY))
        self.assertEqual(st[0]["at"], "2026-10-29T10:15:00+05:30")
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("DONE (dry run)", err)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "x"))
        self.assertEqual(code, 1)
        self.assertIn("dry run", err)

        code, text, _ = run(self.args("stop", "--index", "1", "--filled-qty", "48", "--filled-price", "1176.90",
                                      "--portfolio", self.pf, now="2026-10-29 10:16"))
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN: logged the SL as placed", text)
        self.assertEqual({k: v for k, v in json_after(text, "Live, it would be").items() if k != "tag"},
                         {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
                          "transaction_type": "SELL", "quantity": 48, "product": "MIS", "order_type": "SL",
                          "trigger_price": 1163.3, "price": 1157.4, "validity": "DAY"})
        legs = [(r.get("leg"), r["status"]) for r in self.state_doc()["orders"]]
        self.assertEqual(legs[2:], [(None, "filled"), ("stop", "ready"), ("stop", "placed")])
        code, _, err = run(self.args("stop", "--index", "1", "--filled-qty", "48", "--filled-price", "1176.90",
                                     "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("already has its SL leg", err)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"][0]["shares"]), (43508.8, 48))

        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("(dry run)", text)
        self.assertIn("max loss ₹984.00", text)
        self.assertIn("[stop] SELL 48 RELIANCE, SL trigger 1,163.30 limit 1,157.40", text)
        # trader-risk counts the rehearsal: a second RELIANCE buy is a duplicate and 984 is in use
        res = check.check([reliance()], {"budget": 100000}, state=self.state_doc(),
                          now=check.parse_now("2026-10-29 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 984.0))

    def test_the_live_flow_with_a_partial_fill_and_the_exit(self):
        self.go_live()
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("LIVE, REAL MONEY", text)
        self.assertIn("each with its own approval prompt", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes 48 RELIANCE"))
        self.assertEqual(code, 0)
        self.assertIn("Now make these calls, one at a time", text)
        self.assertEqual([r["status"] for r in self.state_doc()["orders"]], ["approved"])
        code, _, err = run(self.args("stop", "--index", "1", "--filled-qty", "30", "--filled-price", "1177.10",
                                     "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("not recorded as placed", err)
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("--broker-id", err)
        self.assertEqual(run(self.args("result", "--index", "1", "--status", "placed",
                                       "--broker-id", "251029000111"))[0], 0)

        code, text, _ = run(self.args("stop", "--index", "1", "--filled-qty", "30", "--filled-price", "1177.10",
                                      "--portfolio", self.pf, now="2026-10-29 10:17"))
        self.assertEqual(code, 0)
        self.assertIn("Leg 2, covered by your yes for order 1", text)
        self.assertEqual(json_after(text, "with exactly")["quantity"], 30)
        code, _, err = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed"))
        self.assertIn("--broker-id", err)
        self.assertEqual(run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed",
                                       "--broker-id", "251029000222", now="2026-10-29 10:18"))[0], 0)

        code, text, _ = self.flatten("--last", "1196.40")
        self.assertEqual(code, 0)
        self.assertIn("first cancel the open SL stop, order 251029000222 / SELL 30 RELIANCE, LIMIT 1,190.40", text)
        with open(self.orders) as fh:
            exit_order = json.load(fh)["orders"][0]
        self.assertEqual((exit_order["cancel_order_id"], exit_order["price"], exit_order["tick_size"]),
                         ("251029000222", 1190.4, 0.1))
        self.assertEqual(self.run_risk(now="2026-10-29 11:40"), 0)
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now="2026-10-29 11:40"))
        self.assertEqual(json_after(text, "1. cancel_order"), {"variety": "regular", "order_id": "251029000222"})
        self.assertEqual(json_after(text, "2. place_order")["transaction_type"], "SELL")
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes sell 30 RELIANCE",
                                      now="2026-10-29 11:41"))
        self.assertEqual(code, 0)
        self.assertIn("If the cancel fails because the SL already filled, do NOT sell", text)
        self.assertEqual(run(self.args("result", "--index", "1", "--status", "placed", "--broker-id",
                                       "251029000333", now="2026-10-29 11:41"))[0], 0)
        self.assertEqual(self.state_doc()["orders"][-1]["closes_order_id"], "251029000222")
        self.assertIsNone(ticket.open_stop(self.state_doc(), "RELIANCE", TODAY))
        run(["fill", "--symbol", "RELIANCE", "--side", "sell", "--qty", "30", "--price", "1190.45", "--date", TODAY,
             "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        code, _, err = self.flatten("--last", "1196.40")
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_no_sl_when_the_price_is_already_under_the_stop(self):
        self.go_live()
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "111"))
        code, text, _ = run(self.args("stop", "--index", "1", "--filled-qty", "48", "--filled-price", "1177.90",
                                      "--last", "1162.00", "--portfolio", self.pf))
        self.assertEqual(code, 1)
        self.assertIn("Do NOT place it. Get out instead: ticket.py flatten --symbol RELIANCE", text)
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "skipped")
        self.assertIsNone(ticket.open_stop(self.state_doc(), "RELIANCE", TODAY))

    def test_a_failed_stop_says_the_position_has_no_stop(self):
        self.go_live()
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "111"))
        run(self.args("stop", "--index", "1", "--filled-qty", "48", "--filled-price", "1177.90",
                      "--portfolio", self.pf))
        code, text, _ = run(self.args("result", "--index", "1", "--leg", "stop", "--status", "failed",
                                      "--note", "trigger price must be lower than LTP"))
        self.assertEqual(code, 0)
        self.assertIn("THE POSITION HAS NO STOP AT ZERODHA", text)

    def test_flatten_on_the_plans_tick_and_with_the_broker_qty(self):
        code, text, _ = self.flatten("--last", "1196.43", "--qty", "48", now=NOW)
        self.assertEqual(code, 0)
        with open(self.orders) as fh:
            o = json.load(fh)["orders"][0]
        self.assertEqual((o["qty"], o["price"], o["tick_size"], o.get("cancel_order_id")), (48, 1190.4, 0.1, None))
        self.assertIn("No open SL is logged today for RELIANCE", text)
        self.assertNotIn("Check it against Kite get_positions", text)
        code, _, _ = run(["flatten", "--symbol", "GAIL", "--last", "168.67", "--qty", "100", "--orders", self.orders,
                          "--plan", self.none, "--config", self.cfg, "--state", self.state, "--now", NOW])
        self.assertEqual(code, 0)
        with open(self.orders) as fh:
            o = json.load(fh)["orders"][0]
        self.assertEqual((o["price"], o["tick_size"]), (167.82, 0.01))   # under 250: the paise tick
        self.assertEqual(self.flatten("--last", "1196.40", "--qty", "0")[0], 1)
        code, _, err = run(["flatten", "--symbol", "BRK.B", "--last", "1", "--qty", "1", "--orders", self.orders])
        self.assertIn("not an NSE symbol", err)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        with open(self.orders) as fh:
            doc = json.load(fh)
        doc["orders"][0]["stop"] = 1163.2
        with open(self.orders, "w") as fh:
            json.dump(doc, fh)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-28 10:15")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-10-29 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-29 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-29 09:20"))
        self.assertEqual(code, 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.go_live()
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "1"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, _, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)

    def test_fill_sell_relieves_the_oldest_lot_in_whole_shares(self):
        pf = {"cash": 0, "positions": [{"ticker": "ITC", "shares": 3, "lots": [
            {"id": "ITC-1", "date": "2026-01-02", "shares": 2, "cost": 800.0},
            {"id": "ITC-2", "date": "2026-06-02", "shares": 1, "cost": 420.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "itc.ns", "--side", "sell", "--qty", "2", "--price", "410",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Cash now ₹820.00", text)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual([l["id"] for l in pf["positions"][0]["lots"]], ["ITC-2"])
        self.assertEqual(self.state_doc()["orders"][-1]["status"], "filled")
        for qty in ("0.5", "5"):
            code, _, err = run(["fill", "--symbol", "ITC", "--side", "sell", "--qty", qty, "--price", "410",
                                "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
            self.assertEqual(code, 1, qty)
            self.assertNotIn("Traceback", err)

    def test_fill_closes_names_the_sl_that_filled(self):
        self.go_live()
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "111"))
        run(self.args("stop", "--index", "1", "--filled-qty", "48", "--filled-price", "1177.90",
                      "--portfolio", self.pf))
        run(self.args("result", "--index", "1", "--leg", "stop", "--status", "placed", "--broker-id", "222"))
        self.assertEqual(ticket.open_stop(self.state_doc(), "RELIANCE", TODAY)["broker_order_id"], "222")
        code, _, _ = run(["fill", "--symbol", "RELIANCE", "--side", "sell", "--qty", "48", "--price", "1163.10",
                          "--closes", "222", "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg,
                          "--state", self.state, "--now", "2026-10-29 12:00"])
        self.assertEqual(code, 0)
        self.assertIsNone(ticket.open_stop(self.state_doc(), "RELIANCE", TODAY))


if __name__ == "__main__":
    unittest.main()
