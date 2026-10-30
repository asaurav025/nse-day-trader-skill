#!/usr/bin/env python3
"""Pre-trade risk check for trader-risk. Standard library only, no network.

Reads the proposed orders (trader/orders.json) and checks every one against
your rules (trader/config.json), your account (trader/portfolio.json),
prices (trader/prices.json) and what was placed today (trader/state/broker.json).
Each order gets PASS or BLOCK with the reasons. Orders are checked in file
order as if each PASS had filled, so buys that are fine one by one but break
a limit together are caught.

    python3 check.py                                 # the trader/ files, writes trader/state/risk.json
    python3 check.py --now "2026-10-29 10:15"        # pin the clock (IST)
    python3 check.py --orders trader/orders.json --json
    python3 check.py --no-write                      # check without recording the verdicts

Entries and exits. An ENTRY opens or adds to a position (a buy, or a sell
that would go short). An EXIT only reduces what you hold. Getting out must
always be possible: an exit is checked for format, asset and short sales
only; every other rule turns into a warning for it.

What blocks an order (every order):

  format        missing symbol or side; qty not a whole number of shares
                (NSE equity has no fractions); order_type other than LIMIT
                (Kite takes no API MARKET / SL-M order without market
                protection, which kite-mcp-server v0.3.2 cannot send); no
                limit price; a tick_size NSE does not use; a price, stop,
                stop_limit or target that is not a whole number of the stock's
                tick (the order's tick_size, a "tick_sizes" override in the
                config, else the coarse tick for its price: NSE's ticks go by
                price band, trader_common/money.py); product other than MIS (or
                CNC); a stop_limit without a stop, or not under it
  asset         F&O contracts (NIFTY26OCTFUT, ...24500CE / PE): out of scope
                for this pack; anything that is not an NSE equity symbol
  short         selling more than you hold, unless "allow_short": true

What blocks an entry (on top):

  blocked       a symbol on your "blocked" list
  size          under min_order_inr, over max_order_inr
  position      the stock would be over max_position_pct of the account
  cash          the buy would dip into your cash buffer, or need margin
  duplicate     the same symbol and side twice in this batch, or already
                placed today (trader/state/broker.json)
  daily limit   max_orders_per_day orders placed today
  stop          no stop (the SL leg's trigger) while "require_stop": true; a
                stop on the wrong side of the limit price
  loss          qty x (limit - stop_limit) over max_loss_per_trade_inr: the worst
                entry fill and the worst SL fill (trader_common/orders.py).
                Without a stop_limit, qty x (limit - stop)
  reward:risk   (target - limit) / (limit - stop) under min_reward_risk
  daily loss    the max losses of today's entries plus this one over
                max_daily_loss_inr (win or lose, each counts in full)
  trades        max_trades_per_day entries already placed today
  window        before no_entries_before or after no_entries_after (IST),
                or the market is closed (weekend, NSE holiday, outside 9:15
                to the 15:30 close)

Money is in rupees: every limit key ends in _inr, and a config that still has
an _usd key is refused (no silent unit mix-ups). Prices are rounded to the NSE
tick before any risk math.

Not checked: circuit limits (the stock's daily price band). Yahoo's chart data
has no price-band fields, so this tool cannot see them; a stop or target beyond
the band can sit unfilled while the stock is locked at its circuit.

Exit code 0 when every order passes, 1 when any order is blocked.
The verdicts go to trader/state/risk.json with a fingerprint of each order,
so trader-broker only shows an order as ready if THIS exact order passed today.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader_common import clock, money as rupees, orders as kite, symbols as nse  # noqa: E402

DEFAULTS = {
    "budget": None, "cash_buffer_pct": 10, "max_position_pct": 75, "max_order_inr": 75000,
    "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3,
    "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": clock.hm(clock.NO_ENTRIES_BEFORE), "no_entries_after": clock.hm(clock.NO_ENTRIES_AFTER),
    "flat_by": clock.hm(clock.FLAT_BY),
    "dry_run": True, "blocked": [], "allow_margin": False, "allow_short": False,
}
DROPPED_KEYS = {
    "pdt_guard": "pdt_guard is a US (FINRA) rule with no NSE equivalent: ignored",
    "fractional": "fractional is ignored: NSE equity trades whole shares only",
    "allow_options": "allow_options is ignored: F&O is out of scope for this pack",
    "paper": "paper is ignored: Zerodha has no paper account; \"dry_run\": true (the default) rehearses instead",
}
# NSE F&O trading symbols: an underlying, then an expiry with digits, then FUT, CE or PE
# (NIFTY26OCTFUT, BANKNIFTY26OCT52000CE, NIFTY2610624500PE).
FNO = re.compile(r"^[A-Z&-]+\d[0-9A-Z]*(FUT|CE|PE)$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
CENT = 0.005    # half a paisa: price comparisons tolerate float noise
FRESH_MINUTES = 15
REF_WORDS = {"limit": "the limit price"}

CALENDAR_YEARS = clock.CALENDAR_YEARS
OPEN = clock.OPEN
CLOSE = clock.CLOSE


class RiskError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, from trader_common.clock)

to_ist = clock.to_ist
trading_day = clock.trading_day


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    try:
        return clock.parse_now(text)
    except clock.ClockError as e:
        raise RiskError(str(e))


def parse_utc(text):
    if not text:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


def hhmm(value, key):
    """"09:30" -> time(9, 30); None or "" -> None (that bound is off)."""
    if value in (None, "", False):
        return None
    if not HHMM.match(str(value).strip()):
        raise RiskError("%s must be an IST time like \"09:30\", got %r" % (key, value))
    try:
        return clock.parse_hhmm(value, key)
    except clock.ClockError as e:
        raise RiskError(str(e))


def window_check(now_ist, c):
    """Reasons an entry cannot be placed now (IST). Empty when the window is off."""
    before = hhmm(c.get("no_entries_before"), "no_entries_before")
    after = hhmm(c.get("no_entries_after"), "no_entries_after")
    if before is None and after is None:
        return [], []
    warns = []
    d, t = now_ist.date(), now_ist.time().replace(second=0, microsecond=0)
    if not clock.calendar_known(d):
        warns.append("the holiday list covers %s only: check nseindia.com for %s (or add it under "
                     "\"holidays\" in trader/config.json)" % (" and ".join(map(str, CALENDAR_YEARS)), d.isoformat()))
    ok, why = trading_day(d)
    stamp = now_ist.strftime("%a %Y-%m-%d %H:%M")
    if not ok:
        return ["window: the market is closed today (%s, %s IST)" % (why, stamp)], warns
    if t < OPEN or now_ist.time() >= CLOSE:
        return ["window: the market is closed (%s IST; the session is %s to %s)"
                % (stamp, OPEN.strftime("%H:%M"), CLOSE.strftime("%H:%M"))], warns
    if before is not None and t < before:
        return ["window: no new entries before %s IST (it is %s)" % (before.strftime("%H:%M"),
                                                                     t.strftime("%H:%M"))], warns
    if after is not None and t > after:
        return ["window: no new entries after %s IST (it is %s)" % (after.strftime("%H:%M"),
                                                                    t.strftime("%H:%M"))], warns
    return [], warns


# ---------------------------------------------------------------- files and helpers

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise RiskError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))


fingerprint = kite.fingerprint   # the same function trader-broker uses: the exact order checked


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(x):
    """Rupees, Indian grouping; whole rupees without the paise (\u20b91,00,000, \u20b9812.50)."""
    x = float(x)
    return rupees.inr(x, 0 if abs(x - round(x)) < 0.005 else 2)


def _tick(x, step=None):
    """A price on the stock's tick, nearest (1174.32 -> 1174.30 on a 0.10 tick)."""
    return rupees.tick(x, "nearest", step)


def on_tick(x, step):
    return rupees.on_tick(x, step)


def config_units(cfg):
    """(config, notes). Refuses any *_usd key: the limits are rupees now, and a dollar
    number read as rupees would be off by ~85x."""
    bad = sorted(k for k in (cfg or {}) if str(k).endswith("_usd"))
    if bad:
        raise RiskError("trader/config.json still has %s: the limits are in rupees now. Rename %s and "
                        "set the amounts in INR (see trader-budget/config.example.json)"
                        % (", ".join(bad), ", ".join("%s -> %s" % (k, k[:-4] + "_inr") for k in bad)))
    notes = [DROPPED_KEYS[k] for k in DROPPED_KEYS if k in (cfg or {})]
    return {k: v for k, v in (cfg or {}).items() if k not in DROPPED_KEYS}, notes


def _p(x):
    return "%.2f" % float(x)


def account(cfg, portfolio, prices):
    """Starting account: cash and position values. No portfolio = a fresh
    account funded with the budget."""
    warnings = []
    if portfolio is None:
        budget = _f(cfg.get("budget"))
        if not budget:
            raise RiskError("no trader/portfolio.json and no budget in the config: "
                            "nothing to check the orders against")
        return {"cash": budget, "shares": {}, "values": {}}, warnings
    cash = _f(portfolio.get("cash"))
    if cash is None:
        raise RiskError("portfolio.json needs a \"cash\" number")
    shares, values = {}, {}
    for p in portfolio.get("positions", []):
        raw = p.get("ticker") or p.get("symbol") or ""
        t = nse.normalize(raw) or str(raw).upper()
        if not t:
            continue
        n = _f(p.get("shares", p.get("qty")))
        if n is None:
            n = sum(_f(l.get("shares")) or 0 for l in p.get("lots", []))
        shares[t] = shares.get(t, 0) + n
        if prices.get(t):
            values[t] = shares[t] * prices[t]
        else:
            basis = sum(_f(l.get("cost")) or 0 for l in p.get("lots", []))
            values[t] = basis
            warnings.append("no price for %s: valued at its cost basis %s" % (t, money(basis)))
    return {"cash": cash, "shares": shares, "values": values}, warnings


def rec_day(rec):
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def rec_is_entry(rec):
    """trader-broker logs the kind trader-risk gave; older records: a buy is an entry."""
    k = rec.get("kind")
    if k:
        return k == "entry"
    return str(rec.get("side", "")).lower() == "buy"


def placed_today(state, today):
    """Orders recorded as placed today by trader-broker."""
    out = []
    for rec in (state or {}).get("orders", []):
        if rec.get("status") == "placed" and rec_day(rec) == today:
            out.append(rec)
    return out


# ---------------------------------------------------------------- the check

def check(orders, cfg, portfolio=None, prices=None, state=None, today=None, now=None, price_times=None):
    """Pure function. Returns {"results": [...], "pass": n, "block": n, "warnings": [...]}.

    now          naive UTC (default: the real clock); its IST date is "today"
    price_times  {SYM: naive UTC time of that price}; None = every price counts as fresh"""
    cfg, cfg_notes = config_units(cfg)
    c = dict(DEFAULTS)
    c.update(cfg)
    prices = {(nse.normalize(k) or str(k).upper()): float(v) for k, v in (prices or {}).items() if _f(v)}
    now_utc = now or parse_now(None)
    now_ist = to_ist(now_utc)
    today = today or now_ist.date().isoformat()
    acct, top_warn = account(c, portfolio, prices)
    top_warn.extend(cfg_notes)
    blocked = {nse.normalize(b) or str(b).upper() for b in c.get("blocked") or []}
    done_today = placed_today(state, today)
    count_today = len(done_today)
    entries_today = [r for r in done_today if rec_is_entry(r)]
    loss_today = 0.0
    for r in entries_today:
        ml = _f(r.get("max_loss"))
        if ml is None:
            top_warn.append("an entry placed today (%s %s) has no max loss in the log; not counted in "
                            "the daily loss" % (r.get("side"), r.get("symbol")))
        else:
            loss_today += ml
    placed_keys = {(nse.normalize(r.get("symbol")) or str(r.get("symbol", "")).upper(), r.get("side"))
                   for r in done_today}
    win_reasons, win_warns = window_check(now_ist, c)
    seen = set()
    results = []
    batch_entries, batch_loss = 0, 0.0
    if not c.get("dry_run", True):
        top_warn.append("LIVE (\"dry_run\": false): trader-broker will send these orders to Zerodha, real money")
    top_warn.extend(win_warns)

    def fresh(sym):
        if sym not in prices:
            return None
        if price_times is None:
            return prices[sym]
        t = price_times.get(sym)
        if t is None:
            return None
        age = (now_utc - t).total_seconds()
        return prices[sym] if -60 <= age <= FRESH_MINUTES * 60 else None

    for i, o in enumerate(orders, 1):
        reasons, warns, info = [], [], []
        later = []                       # entry-only reasons, turned into warnings for an exit
        raw_sym = str(o.get("symbol", "")).strip().upper()
        sym = nse.normalize(raw_sym) or raw_sym
        side = str(o.get("side", "")).strip().lower()
        otype = str(o.get("order_type", "")).strip().upper()
        product = str(o.get("product", "")).strip().upper()
        qty = o.get("qty")
        plan = o.get("plan") if isinstance(o.get("plan"), dict) else {}
        value = None

        # format
        if not sym:
            reasons.append("format: no symbol")
        if side not in ("buy", "sell"):
            reasons.append("format: side must be buy or sell, got %r" % o.get("side"))
        q = _f(qty)
        if q is None or q <= 0:
            reasons.append("format: qty must be a positive number of shares, got %r" % qty)
        elif abs(q - round(q)) > 1e-9:
            reasons.append("format: NSE equity trades whole shares only, got qty %r" % qty)
        if otype not in kite.ORDER_TYPES:
            reasons.append("format: order_type %r: only LIMIT. Kite rejects API MARKET and SL-M orders without "
                           "market protection, which kite-mcp-server v0.3.2 cannot send" % (o.get("order_type"),))
        if product not in kite.PRODUCTS:
            reasons.append("format: product must be MIS (intraday) or CNC (delivery), got %r" % (o.get("product"),))
        try:
            step = kite.tick_of(o, c, plan)
        except ValueError as e:
            reasons.append("format: %s" % e)
            step = None
        if step and o.get("tick_size") and abs(float(o["tick_size"]) - step) > 1e-9:
            warns.append("the order says tick %s but trader/config.json tick_sizes says %s for %s: checked on %s"
                         % (o["tick_size"], step, sym, step))
        for k in kite.PRICE_KEYS:
            if o.get(k) is not None:
                v = _f(o.get(k))
                if v is None or v <= 0:
                    reasons.append("format: %s must be a positive price, got %r" % (k, o.get(k)))
                elif step and not on_tick(v, step):
                    reasons.append("format: %s %s is not a multiple of %s's \u20b9%.2f tick (NSE rejects it; "
                                   "nearest is %.2f)" % (k, o.get(k), sym, step, _tick(v, step)))
        if o.get("price") is None:
            reasons.append("format: a LIMIT order needs a price")
        if o.get("stop_limit") is not None and o.get("stop") is None:
            reasons.append("format: stop_limit needs a stop (the SL trigger)")
        if kite.cancels_of(o) and side != "sell":
            reasons.append("format: cancel_order_ids belong on an exit (sell) ticket")

        # asset
        if sym and FNO.match(sym.replace(" ", "")):
            reasons.append("asset: %s is an F&O contract; this pack trades NSE cash equities only" % sym)
        elif sym and nse.normalize(raw_sym) is None:
            reasons.append("asset: %s does not look like an NSE equity symbol (RELIANCE, M&M, BAJAJ-AUTO); "
                           "this pack trades NSE cash equities only" % raw_sym)

        # the reference price: the LIMIT, the worst price this order can fill at
        ref = _f(o.get("price"))
        ref = _tick(ref, step) if ref else None
        ref_from = "limit" if ref else None
        live = fresh(sym)
        live = _tick(live, step) if live else live
        if ref and live and side == "buy" and live > ref + CENT:
            warns.append("%s last %s is above your limit %s: the buy will rest unfilled unless it comes back"
                         % (sym, _p(live), _p(ref)))
        if ref and live and side == "sell" and live < ref - CENT:
            warns.append("%s last %s is under your sell limit %s: it will rest unfilled unless it comes back"
                         % (sym, _p(live), _p(ref)))
        if q and ref:
            value = q * ref

        # entry or exit
        held = acct["shares"].get(sym, 0.0)
        held_value = acct["values"].get(sym, 0.0)
        exiting = False
        if side == "sell" and held > 1e-9:
            if q is not None:
                exiting = q <= held + 1e-9
        elif side == "buy" and held < -1e-9 and q is not None:
            exiting = q <= -held + 1e-9
        kind = "exit" if exiting else "entry"

        # blocked list and size
        if sym in blocked:
            later.append("blocked: %s is on your blocked list" % sym)
        if value is not None:
            if value < float(c.get("min_order_inr") or 0) - CENT:
                later.append("size: %s is under your %s minimum order"
                             % (money(value), money(c["min_order_inr"])))
            if c.get("max_order_inr") and value > float(c["max_order_inr"]) + CENT:
                later.append("size: %s is over your %s max order"
                             % (money(value), money(c["max_order_inr"])))

        # account effects, simulated in order
        cash = acct["cash"]
        total = cash + sum(acct["values"].values())
        buffer_inr = total * float(c.get("cash_buffer_pct") or 0) / 100.0
        if value is not None and side == "buy" and kind == "entry":
            after_cash = cash - value
            if after_cash < -CENT and not c.get("allow_margin"):
                later.append("cash: needs %s but only %s cash; that would be margin (borrowed money)"
                             % (money(value), money(cash)))
            elif after_cash < buffer_inr - CENT:
                later.append("cash: leaves %s cash, under your %s%% buffer of %s"
                             % (money(max(after_cash, 0)), _pct(c["cash_buffer_pct"]), money(buffer_inr)))
            pos_after = held_value + value
            limit = total * float(c.get("max_position_pct") or 100) / 100.0
            if total > 0 and pos_after > limit + CENT:
                later.append("position: %s would be %s, %.1f%% of the %s account, over your %s%% cap"
                             % (sym, money(pos_after), 100.0 * pos_after / total, money(total),
                                _pct(c["max_position_pct"])))
        if side == "sell" and sym and kind == "entry":
            over = q > held + 1e-9 if q is not None else held <= 0
            if over and not c.get("allow_short"):
                reasons.append("short: selling more %s than you hold (%s shares); short selling is off. "
                               "If you bought it, record the fill first (ticket.py fill)" % (sym, _num(held)))

        # day-trade checks: stop, loss, reward:risk, daily loss, trades, window
        max_loss = risk_ps = rr = stop = stop_limit = target = None
        if kind == "entry" and side in ("buy", "sell"):
            long = side == "buy"
            stop = _f(o.get("stop"))
            stop = _tick(stop, step) if stop else None
            stop_limit = _f(o.get("stop_limit"))
            stop_limit = _tick(stop_limit, step) if stop_limit else None
            target = _f(o.get("target"))
            target = _tick(target, step) if target else None
            plan_stop = _f(plan.get("stop"))
            if stop and plan_stop and abs(_tick(plan_stop, step) - stop) > CENT:
                warns.append("the order's stop %s differs from the plan's stop %s: the order's is the SL "
                             "trigger the broker gets" % (_p(stop), _p(plan_stop)))
            if not stop:
                if c.get("require_stop", True):
                    later.append("stop: no stop. Every entry needs one (\"require_stop\": true): the SL leg's "
                                 "trigger, from trader-exit and trader-budget")
                else:
                    warns.append("no stop: the max loss is not capped, so it is not counted in the daily loss")
            elif ref and q is not None and q > 0:   # a bad qty is already a format BLOCK
                if (long and stop >= ref - CENT) or (not long and stop <= ref + CENT):
                    later.append("stop: the stop %s must be %s the limit %s for a %s"
                                 % (_p(stop), "below" if long else "above", _p(ref), side))
                elif stop_limit and ((long and stop_limit >= stop - CENT) or (not long and stop_limit <= stop + CENT)):
                    later.append("stop: the SL limit %s must be %s its trigger %s"
                                 % (_p(stop_limit), "under" if long else "over", _p(stop)))
                else:
                    if not stop_limit:
                        later.append("stop: no stop_limit. The stop goes to Zerodha as an SL order, which needs a "
                                     "limit under its trigger %s (trader-budget sets one)" % _p(stop))
                    risk_c = int(round(abs(ref - stop) * 100))
                    worst = stop_limit or stop
                    risk_ps = int(round(abs(ref - worst) * 100)) / 100.0
                    max_loss = round(q * risk_ps, 2)
                    cap = _f(c.get("max_loss_per_trade_inr"))
                    if cap and max_loss > cap + CENT:
                        later.append("loss: %s shares x %s risk a share = %s if the SL fills at %s, over your %s "
                                     "max loss per trade" % (_num(q), _p(risk_ps), money(max_loss), _p(worst),
                                                             money(cap)))
                    min_rr = _f(c.get("min_reward_risk"))
                    if target:
                        if (long and target <= ref + CENT) or (not long and target >= ref - CENT):
                            later.append("target: the target %s must be %s the limit %s"
                                         % (_p(target), "above" if long else "below", _p(ref)))
                        else:
                            rr = round(int(round(abs(target - ref) * 100)) / float(risk_c), 2)
                            if min_rr and rr < min_rr - 1e-9:
                                later.append("reward:risk: %.2f (target %s, limit %s, stop %s) is under your "
                                             "minimum %s" % (rr, _p(target), _p(ref), _p(stop), _num(min_rr)))
                    elif min_rr:
                        later.append("reward:risk: no target to measure it against (min_reward_risk %s)"
                                     % _num(min_rr))
            daily = _f(c.get("max_daily_loss_inr"))
            if daily and max_loss is not None:
                used = loss_today + batch_loss
                if used + max_loss > daily + CENT:
                    later.append("daily loss: %s already at risk today plus %s for this one = %s, over your %s "
                                 "daily loss limit" % (money(used), money(max_loss), money(used + max_loss),
                                                       money(daily)))
            cap_t = int(c.get("max_trades_per_day") or 0)
            if cap_t and len(entries_today) + batch_entries >= cap_t:
                later.append("trades: %d entries placed today plus %d passed earlier in this batch, your limit "
                             "is %d a day" % (len(entries_today), batch_entries, cap_t))
            later.extend(win_reasons)
            if stop and ref and risk_ps:
                info.append("limit %s, SL trigger %s limit %s, target %s: risk %s a share to the SL limit"
                            % (_p(ref), _p(stop), _p(stop_limit) if stop_limit else "-",
                               _p(target) if target else "none", _p(risk_ps)))
                info.append("max loss %s%s, reward:risk %s%s"
                            % (money(max_loss), (" (limit %s)" % money(c["max_loss_per_trade_inr"]))
                               if _f(c.get("max_loss_per_trade_inr")) else "",
                               ("%.2f" % rr) if rr is not None else "n/a",
                               (" (min %s)" % _num(c["min_reward_risk"])) if _f(c.get("min_reward_risk")) else ""))
            if daily or cap_t:
                info.append("today before this: %d of %s trades, %s of %s daily loss"
                            % (len(entries_today) + batch_entries, cap_t or "unlimited",
                               money(loss_today + batch_loss), money(daily) if daily else "no limit on"))

        # duplicates and the daily order limit
        key = (sym, side)
        if key in seen:
            later.append("duplicate: a second %s %s in this batch" % (side, sym))
        if key in placed_keys:
            later.append("duplicate: a %s %s was already placed today" % (side, sym))
        passed_so_far = sum(1 for r in results if r["verdict"] == "PASS")
        cap_n = int(c.get("max_orders_per_day") or 0)
        if cap_n and count_today + passed_so_far >= cap_n:
            later.append("daily limit: %d placed today plus %d passed earlier in this batch, "
                         "your limit is %d a day" % (count_today, passed_so_far, cap_n))
        seen.add(key)

        if kind == "entry":
            reasons.extend(later)
        else:
            warns.extend("exit, not blocked: " + x for x in later)

        verdict = "BLOCK" if reasons else "PASS"
        if verdict == "PASS":
            if kind == "entry" and side in ("buy", "sell"):
                batch_entries += 1
                batch_loss += max_loss or 0.0
            if side == "buy":
                if value is not None:
                    acct["cash"] -= value
                    acct["values"][sym] = acct["values"].get(sym, 0.0) + value
                if q is not None:
                    acct["shares"][sym] = held + q
            elif side == "sell":
                if value is not None:
                    acct["cash"] += value
                    acct["values"][sym] = max(acct["values"].get(sym, 0.0) - value, 0.0)
                if q is not None:
                    acct["shares"][sym] = held - q
        results.append({
            "index": i, "symbol": sym, "side": side, "kind": kind,
            "amount": "%s shares" % qty,
            "value_inr": round(value, 2) if value is not None else None,
            "entry_ref": round(ref, 2) if ref else None, "entry_ref_from": ref_from,
            "stop": stop, "stop_limit": stop_limit, "target": target, "tick_size": step, "risk_per_share": risk_ps,
            "max_loss": max_loss,
            "reward_risk": rr, "fingerprint": fingerprint(o), "verdict": verdict,
            "reasons": reasons, "warnings": warns, "info": info,
        })

    return {
        "date": today,
        "now_ist": now_ist.strftime("%Y-%m-%d %H:%M"),
        "dry_run": bool(c.get("dry_run", True)),
        "results": results,
        "pass": sum(1 for r in results if r["verdict"] == "PASS"),
        "block": sum(1 for r in results if r["verdict"] == "BLOCK"),
        "warnings": top_warn,
        "cash_after": round(acct["cash"], 2),
        "today": {"orders_placed": count_today, "entries_placed": len(entries_today),
                  "loss_at_risk": round(loss_today, 2)},
    }


def _pct(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.2f" % x)


def _num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ("%.4f" % x).rstrip("0")


def report(res):
    lines = []
    for w in res["warnings"]:
        lines.append("WARNING: " + w)
    if res["warnings"]:
        lines.append("")
    for r in res["results"]:
        lines.append("%-5s #%d %s %s %s, %s" % (r["verdict"], r["index"], r["side"], r["symbol"], r["amount"],
                                                r["kind"]))
        for x in r.get("info", []):
            lines.append("          " + x)
        for x in r["reasons"]:
            lines.append("        - " + x)
        for x in r["warnings"]:
            lines.append("        ~ " + x)
    lines.append("")
    lines.append("%d passed, %d blocked at %s IST. Cash after the passed orders: %s (%s)."
                 % (res["pass"], res["block"], res["now_ist"], money(res["cash_after"]),
                    "dry run: nothing goes to Zerodha" if res["dry_run"] else "LIVE: real money"))
    if res["block"]:
        lines.append("Blocked orders do not go to the broker. Fix the order or your rules, then check again.")
    else:
        lines.append("Every order passed. trader-broker still asks you yes or no for each one.")
    lines.append("Education and tooling, not financial advice.")
    return "\n".join(lines)


def load_prices(pdoc):
    """prices.json -> ({SYM: price}, {SYM: price time as naive UTC or None}).

    The price time is the quote's own time (detail.time), else when it was fetched
    (detail.fetched), else the file's as_of."""
    if not isinstance(pdoc, dict):
        return {}, {}
    prices = pdoc.get("prices", pdoc)
    if not isinstance(prices, dict):
        return {}, {}
    out, times = {}, {}
    as_of = parse_utc(pdoc.get("as_of"))
    detail = pdoc.get("detail") or {}
    for s, p in prices.items():
        if _f(p) is None:
            continue
        s = str(s).upper()
        out[s] = float(p)
        d = detail.get(s) or {}
        times[s] = parse_utc(d.get("time")) or parse_utc(d.get("fetched")) or as_of
    return out, times


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check proposed orders against your limits (trader-risk).")
    ap.add_argument("--orders", default="trader/orders.json")
    ap.add_argument("--config", default="trader/config.json")
    ap.add_argument("--portfolio", default="trader/portfolio.json",
                    help="your account; if missing, a fresh account funded with the budget")
    ap.add_argument("--prices", default="trader/prices.json")
    ap.add_argument("--state", default="trader/state/broker.json", help="what trader-broker placed today")
    ap.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-29 10:15\"")
    ap.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")
    ap.add_argument("--out", default="trader/state/risk.json", help="where the verdicts are recorded")
    ap.add_argument("--no-write", action="store_true", help="do not record the verdicts")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        now = parse_now(a.now)
        if a.today and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.today):
            raise RiskError("--today wants a date like 2026-10-29")
        doc = read_json(a.orders, "orders file (run trader-budget first)")
        orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
        if not isinstance(orders, list) or not orders:
            raise RiskError("no orders in %s" % a.orders)
        cfg = read_json(a.config, "config", required=False) or {}
        clock.use_config(a.config)
        portfolio = read_json(kite.portfolio_path(a.portfolio, cfg), "portfolio", required=False)
        pdoc = read_json(a.prices, "prices", required=False) or {}
        prices, times = load_prices(pdoc)
        state = read_json(a.state, "broker state", required=False)
        res = check(orders, cfg, portfolio, prices, state, a.today, now, times)
        if pdoc.get("as_of"):
            res["prices_as_of"] = pdoc["as_of"]
        if not a.no_write:
            d = os.path.dirname(a.out)
            if d:
                os.makedirs(d, exist_ok=True)
            rec = dict(res)
            rec["checked_at"] = datetime.datetime.now(datetime.timezone.utc).replace(
                microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
            rec["orders_file"] = a.orders
            tmp = a.out + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(rec, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, a.out)
    except RiskError as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2) if a.json else report(res))
    return 1 if res["block"] else 0


if __name__ == "__main__":
    sys.exit(main())
