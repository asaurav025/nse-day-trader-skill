import datetime
import json
from decimal import Decimal
import os
import struct
import sys
import unittest

from _load import ROOT, FIX

sys.path.insert(0, ROOT)
from trader_common import clock, money, orders, symbols  # noqa: E402

DT = datetime.datetime
D = datetime.date
HOLIDAY_MASTER = os.path.join(FIX, "trader-common", "nse-holiday-master-2026.json")


def f32(x):
    """The float32 Yahoo sends: 1283.60 -> 1283.5999755859375."""
    return struct.unpack("f", struct.pack("f", x))[0]


class Clock(unittest.TestCase):
    def tearDown(self):
        clock.set_overrides(None)

    def test_ist_is_a_fixed_five_thirty_all_year(self):
        for utc in (DT(2026, 1, 15, 3, 45), DT(2026, 7, 15, 3, 45), DT(2026, 11, 2, 3, 45)):
            self.assertEqual(clock.to_ist(utc), utc + datetime.timedelta(hours=5, minutes=30))
            self.assertEqual(clock.ist_to_utc(clock.to_ist(utc)), utc)

    def test_unix_seconds_round_trip(self):
        ist = DT(2026, 10, 29, 9, 15)
        ts = clock.ist_ts(ist)
        self.assertEqual(ts, 1793245500)   # Yahoo's own tradingPeriods start for RELIANCE.NS on 2026-10-29
        self.assertEqual(clock.ts_to_ist(ts), ist)

    def test_parse_now(self):
        self.assertEqual(clock.parse_now("2026-10-29 10:15"), DT(2026, 10, 29, 4, 45))
        self.assertEqual(clock.parse_now("2026-10-29 10:15 IST"), DT(2026, 10, 29, 4, 45))
        self.assertEqual(clock.parse_now("2026-10-29T04:45:00Z"), DT(2026, 10, 29, 4, 45))
        self.assertEqual(clock.parse_now("2026-10-29T10:15:00+05:30"), DT(2026, 10, 29, 4, 45))
        with self.assertRaises(clock.ClockError):
            clock.parse_now("half past ten")

    def test_parse_hhmm(self):
        self.assertEqual(clock.parse_hhmm("9:15"), datetime.time(9, 15))
        for bad in ("24:00", "10:60", "1015", None):
            with self.assertRaises(clock.ClockError):
                clock.parse_hhmm(bad)

    def test_session_and_defaults(self):
        self.assertEqual(clock.session(D(2026, 10, 29)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(clock.hm(clock.FLAT_BY), "15:10")
        self.assertEqual(clock.hm(clock.NO_ENTRIES_BEFORE), "09:30")
        self.assertEqual(clock.hm(clock.NO_ENTRIES_AFTER), "14:45")
        self.assertEqual(clock.hm(clock.MIS_SQUARE_OFF), "15:20")
        self.assertLess(clock.FLAT_BY, clock.MIS_SQUARE_OFF)

    def test_weekends_and_holidays(self):
        clock.set_overrides({})
        self.assertEqual(clock.trading_day(D(2026, 10, 31)), (False, "weekend"))
        self.assertEqual(clock.trading_day(D(2026, 11, 10)), (False, "NSE holiday: Diwali-Balipratipada"))
        self.assertEqual(clock.trading_day(D(2026, 10, 30)), (True, ""))
        self.assertIsNone(clock.session(D(2026, 1, 26)))
        self.assertEqual(clock.next_open(D(2026, 11, 9)), "2026-11-11 09:15")
        self.assertEqual(clock.next_open(D(2026, 10, 30)), "2026-11-02 09:15")
        self.assertEqual(clock.prev_trading_day(D(2026, 11, 11)), D(2026, 11, 9))

    def test_the_holiday_list_is_nse_s_own(self):
        # every CM holiday NSE published for 2026 is in the list, with the same name, and nothing else
        with open(HOLIDAY_MASTER, encoding="utf-8") as fh:
            cm = json.load(fh)["CM"]
        nse_list = {DT.strptime(x["tradingDate"], "%d-%b-%Y").date().isoformat(): x["description"].rstrip("*")
                    for x in cm}
        self.assertEqual(nse_list, clock.NSE_HOLIDAYS)
        self.assertEqual(clock.CALENDAR_YEARS, (2026,))
        self.assertTrue(clock.calendar_known(D(2026, 12, 31)))
        self.assertFalse(clock.calendar_known(D(2027, 1, 1)))

    def test_a_tools_config_file_brings_its_own_holidays(self):
        # review finding 6: a --config outside ./trader/ had its "holidays" ignored
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "acct.json")
            with open(p, "w") as fh:
                json.dump({"holidays": {"2026-12-31": "ad-hoc closure", "2026-11-10": None}}, fh)
            clock.use_config(p)
            self.assertEqual(clock.trading_day(D(2026, 12, 31)), (False, "NSE holiday: ad-hoc closure"))
            self.assertEqual(clock.trading_day(D(2026, 11, 10)), (True, ""))
        clock.set_overrides({})
        clock.use_config(os.path.join(ROOT, "no-such-file.json"))      # a missing file changes nothing
        self.assertEqual(clock.trading_day(D(2026, 12, 31)), (True, ""))

    def test_config_override_adds_and_removes(self):
        clock.set_overrides({"2026-12-31": "ad-hoc closure", "2026-11-24": None})
        self.assertEqual(clock.trading_day(D(2026, 12, 31)), (False, "NSE holiday: ad-hoc closure"))
        self.assertEqual(clock.trading_day(D(2026, 11, 24)), (True, ""))

    def test_overrides_load_from_a_config_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "config.json")
            with open(p, "w") as fh:
                json.dump({"holidays": {"2026-11-30": "test closure", "2026-12-25": None}}, fh)
            self.assertEqual(clock.load_overrides(p), {"2026-11-30": "test closure", "2026-12-25": None})
            with open(p, "w") as fh:
                fh.write("not json")
            self.assertEqual(clock.load_overrides(p), {})
        self.assertEqual(clock.load_overrides("/nonexistent/config.json"), {})

    def test_words(self):
        t = datetime.time(9, 5)
        self.assertEqual((clock.hm(t), clock.short(t), clock.ampm(t)), ("09:05", "9:05", "9:05 AM"))
        self.assertEqual(clock.ampm(datetime.time(15, 10)), "3:10 PM")
        self.assertEqual(clock.day_label(D(2026, 10, 29)), "Thu 2026-10-29")


class Symbols(unittest.TestCase):
    def test_normalize(self):
        cases = {"reliance": "RELIANCE", " RELIANCE.NS ": "RELIANCE", "m&m.ns": "M&M", "BAJAJ-AUTO": "BAJAJ-AUTO",
                 "20MICRONS": "20MICRONS"}
        for raw, want in cases.items():
            self.assertEqual(symbols.normalize(raw), want, raw)
        for bad in ("RELIANCE.BO", "BRK.B", "-AUTO", "M&", "", None, "A" * 25):
            self.assertIsNone(symbols.normalize(bad), bad)

    def test_yahoo_mapping(self):
        self.assertEqual(symbols.to_yahoo("M&M"), "M&M.NS")
        self.assertEqual(symbols.yahoo_quoted("M&M"), "M%26M.NS")
        self.assertEqual(symbols.yahoo_quoted("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(symbols.from_yahoo("m&m.ns"), "M&M")
        self.assertEqual(symbols.from_yahoo("^NSEI"), "^NSEI")

    def test_every_eq_symbol_in_nse_s_list_is_valid(self):
        import csv
        with open(os.path.join(FIX, "trader-scanner", "EQUITY_L.csv"), encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        hdr = [h.strip() for h in rows[0]]
        eq = [r[0] for r in rows[1:] if r[hdr.index("SERIES")].strip() == "EQ"]
        self.assertGreater(len(eq), 2000)
        self.assertEqual([s for s in eq if not symbols.valid(s)], [])


class Money(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(money.inr(100000), "₹1,00,000.00")
        self.assertEqual(money.inr(1234567.5), "₹12,34,567.50")
        self.assertEqual(money.inr(999), "₹999.00")
        self.assertEqual(money.inr(-500), "-₹500.00")
        self.assertEqual(money.inr(-0.001), "₹0.00")
        self.assertEqual(money.inr(1000, 0), "₹1,000")
        self.assertEqual(money.num(56539.2), "56,539.20")
        self.assertEqual(money.group_in("123456789"), "12,34,56,789")

    def test_band_ticks(self):
        cases = [(168.67, 0.01), (249.99, 0.01), (250, 0.05), (1000, 0.05), (1000.05, 0.10), (1187.0, 0.10),
                 (5000, 0.10), (5000.5, 0.50), (10000, 0.50), (10383, 1.00), (20000, 1.00), (20000.5, 5.00)]
        for price, t in cases:
            self.assertEqual(money.band_tick(price), t, price)

    def test_tick_size_priority(self):
        self.assertEqual(money.tick_size(override=0.05, month_close=1187.0, price=1174.3), 0.05)
        self.assertEqual(money.tick_size(month_close=1187.0, price=990.0), 0.10)   # the month-end band wins
        self.assertEqual(money.tick_size(price=1174.3), 0.10)
        self.assertEqual(money.tick_size(price=990.0), 0.10)    # near an edge: the coarser tick, valid either way
        self.assertEqual(money.tick_size(price=168.67), 0.01)
        with self.assertRaises(ValueError):
            money.tick_size(override=0.25)
        with self.assertRaises(ValueError):
            money.tick_size()

    def test_coarse_tick_is_always_a_multiple_of_the_real_one(self):
        # the ticks are nested, so the coarse fallback is valid whatever band NSE put the stock in
        for price in (180, 240, 260, 900, 990, 1010, 4900, 5100, 9800, 10100, 19900, 20100, 30000):
            coarse = money.tick_size(price=price)
            for real in (money.band_tick(price / 1.2), money.band_tick(price), money.band_tick(price * 1.2)):
                n = coarse / real
                self.assertAlmostEqual(n, round(n), 9, (price, coarse, real))

    def test_rounding_modes(self):
        self.assertEqual(money.tick(1177.82, "up", 0.10), 1177.90)
        self.assertEqual(money.tick(1157.48, "down", 0.10), 1157.40)
        self.assertEqual(money.tick(1163.35, "down", 0.10), 1163.30)
        self.assertEqual(money.tick(168.673, "nearest", 0.01), 168.67)
        self.assertEqual(money.tick(682.47, "nearest", 0.05), 682.45)
        self.assertEqual(money.tick(10383.4, "down", 1.0), 10383.0)
        self.assertIsNone(money.tick(None))

    def test_nearest_rounds_exact_halves_up(self):
        for step in (0.01, 0.05, 0.10, 0.50, 1.0):
            for k in range(1, 400):
                base = round(1000 + k * step, 2)
                half = float(Decimal(repr(base)) + Decimal(repr(step)) / 2)   # an exact half, as a user would type it
                self.assertAlmostEqual(money.tick(half, "nearest", step), round(base + step, 2), 9, (half, step))
        self.assertEqual(money.tick(1000.05, "nearest", 0.10), 1000.10)
        self.assertEqual(money.tick(1163.35, "nearest", 0.10), 1163.40)

    def test_only_float_noise_snaps_so_down_stays_down(self):
        # review finding 8: a stop 0.08 tick above a grid point used to snap UP to the next tick
        self.assertEqual(money.tick(20104.60, "down", 5.0), 20100.0)
        self.assertEqual(money.tick(1163.358, "down", 0.10), 1163.30)
        self.assertEqual(money.tick(1177.8951, "up", 0.01), 1177.90)
        self.assertEqual(money.tick(1177.8949, "up", 0.01), 1177.90)
        self.assertEqual(money.tick(168.6701, "up", 0.01), 168.68)      # a paise stock: 0.01 tick over is over
        self.assertEqual(money.tick(168.6799, "down", 0.01), 168.67)
        self.assertEqual(money.tick(f32(168.67), "down", 0.01), 168.67)   # but float32 noise is still noise

    def test_float32_noise_never_costs_a_tick(self):
        self.assertEqual(f32(1283.6), 1283.5999755859375)
        self.assertEqual(money.tick(f32(1283.6), "down", 0.10), 1283.60)
        self.assertEqual(money.tick(f32(1283.6), "up", 0.10), 1283.60)
        self.assertEqual(money.tick(f32(140000.0), "down", 5.0), 140000.0)
        self.assertTrue(money.on_tick(f32(1283.6), 0.10))
        self.assertFalse(money.on_tick(1177.85, 0.10))
        self.assertTrue(money.on_tick(168.67, 0.01))


class Orders(unittest.TestCase):
    ORDER = {"symbol": "RELIANCE", "side": "buy", "qty": 48, "order_type": "LIMIT", "price": 1177.90,
             "product": "MIS", "stop": 1163.30, "stop_limit": 1157.40, "target": 1196.30, "tick_size": 0.10}

    def test_buffer_prices(self):
        self.assertEqual(orders.limit_above(1174.30, 0.3, 0.10), 1177.90)
        self.assertEqual(orders.limit_below(1163.30, 0.5, 0.10), 1157.40)
        self.assertEqual(orders.limit_below(1196.40, 0.5, 0.10), 1190.40)
        self.assertEqual(orders.buffer_pct({}, "stop_limit_buffer_pct"), 0.5)
        self.assertEqual(orders.buffer_pct({"entry_limit_buffer_pct": "0.25"}, "entry_limit_buffer_pct"), 0.25)
        for bad in (-1, 6, "lots"):
            with self.assertRaises(ValueError):
                orders.buffer_pct({"exit_limit_buffer_pct": bad}, "exit_limit_buffer_pct")

    def test_fingerprint_is_canonical_and_covers_the_stop_and_target(self):
        o = dict(self.ORDER)
        same = dict(o, qty="48", price="1177.900", side="BUY", symbol="reliance", tick_size="0.1",
                    why="different notes", plan={"anything": 1})
        self.assertEqual(orders.fingerprint(o), orders.fingerprint(same))
        for k, v in (("stop", 1163.20), ("stop_limit", 1157.30), ("target", 1196.40), ("qty", 47),
                     ("tick_size", 0.05), ("product", "CNC")):
            self.assertNotEqual(orders.fingerprint(o), orders.fingerprint(dict(o, **{k: v})), k)
        self.assertRegex(orders.fingerprint(o), r"^[0-9a-f]{16}$")

    def test_tag_fits_kite(self):
        t = orders.tag(self.ORDER)
        self.assertRegex(t, r"^ndt[0-9a-f]{12}$")
        self.assertLessEqual(len(t), 20)

    def test_entry_leg_matches_kite_mcp_place_order(self):
        leg = orders.entry_leg(self.ORDER)
        self.assertEqual(leg, {"variety": "regular", "exchange": "NSE", "tradingsymbol": "RELIANCE",
                               "transaction_type": "BUY", "quantity": 48, "product": "MIS",
                               "order_type": "LIMIT", "price": 1177.9, "validity": "DAY",
                               "tag": orders.tag(self.ORDER)})
        self.assertIsInstance(leg["quantity"], int)
        self.assertIsInstance(leg["price"], float)

    def test_stop_leg_is_an_sl_sell_for_what_filled(self):
        leg = orders.stop_leg(self.ORDER, 30)
        self.assertEqual((leg["transaction_type"], leg["order_type"], leg["quantity"]), ("SELL", "SL", 30))
        self.assertEqual((leg["trigger_price"], leg["price"]), (1163.3, 1157.4))
        self.assertNotIn("market_protection", leg)

    def test_exit_and_cancel_args(self):
        ex = {"symbol": "RELIANCE", "side": "sell", "qty": 48, "order_type": "LIMIT", "price": 1190.40,
              "product": "MIS", "tick_size": 0.10, "cancel_order_id": "251029000222"}
        self.assertEqual(orders.entry_leg(ex)["transaction_type"], "SELL")
        self.assertEqual(orders.cancel_args("251029000222"), {"variety": "regular", "order_id": "251029000222"})

    def test_max_loss_in_paise(self):
        m = orders.max_loss(self.ORDER)
        self.assertEqual(m, {"at_stop": 700.8, "at_stop_limit": 984.0, "per_share": 20.5, "per_share_at_stop": 14.6})
        self.assertEqual(orders.governing_loss(self.ORDER), 984.0)
        no_limit = {k: v for k, v in self.ORDER.items() if k != "stop_limit"}
        self.assertEqual(orders.governing_loss(no_limit), 700.8)
        self.assertIsNone(orders.governing_loss({"symbol": "X", "qty": 1, "price": 100}))

    def test_tick_of_priority(self):
        self.assertEqual(orders.tick_of(self.ORDER), 0.10)
        self.assertEqual(orders.tick_of(self.ORDER, {"tick_sizes": {"reliance.ns": 0.05}}), 0.05)
        self.assertEqual(orders.tick_of({"symbol": "GAIL", "price": 168.67}), 0.01)
        self.assertEqual(orders.tick_of({"symbol": "RELIANCE"}, plan={"tick_size": 0.10}), 0.10)
        with self.assertRaises(ValueError):
            orders.tick_of(dict(self.ORDER, tick_size=0.25))


if __name__ == "__main__":
    unittest.main()
