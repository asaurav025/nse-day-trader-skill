#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker. Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes. A ticket is one Zerodha
day trade in two legs (trader_common/orders.py has the shape and the reasons):

  leg 1  entry   place_order BUY LIMIT, product MIS
  leg 2  stop    place_order SELL SL (trigger at the stop, a limit under it),
                 for the shares that filled, placed only after the entry fills
  target         not an order: a trader-alerts alert, then a flatten ticket

  show      renders each proposed order as a plain ticket plus the exact
            kite-mcp-server place_order arguments for each leg. An order is
            READY only if trader-risk passed THIS exact order today (matched by
            fingerprint) and nobody has decided on it yet.
  approve   records your explicit yes for ONE ticket, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any ticket that is not READY. Your
            yes covers that ticket's legs; each call still gets its own
            approval prompt in Claude.
  decline   records a no.
  result    records what happened after the approval prompt for a leg:
            placed (with Zerodha's order id), denied, or failed.
  stop      after the entry fills: records the fill and prints leg 2, the SL
            for exactly the shares that filled.
  fill      records any other fill in trader/portfolio.json (the SL, an exit).
  flatten   builds the exit ticket for a symbol: cancel the open SL, then a
            LIMIT sell exit_limit_buffer_pct under the last price. It still goes
            through trader-risk and your yes.
  log       prints today's decisions.

    python3 ticket.py show                    # every ticket, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 251029000123456
    python3 ticket.py stop --index 1 --filled-qty 48 --filled-price 1176.90
    python3 ticket.py result --index 1 --leg stop --status placed --broker-id 251029000123789
    python3 ticket.py flatten --symbol RELIANCE --last 1196.40
    python3 ticket.py fill --symbol RELIANCE --side sell --qty 48 --price 1190.45
    python3 ticket.py log

Dry run ("dry_run": true in trader/config.json, the default; Zerodha has no
paper account): every step above still runs and is logged, a yes is recorded
as a simulated placement (order id DRYRUN-...), and every leg is printed under
"DRY RUN: do not call". Claude never calls an order tool in dry run. Going live
is the user's own edit of their config.

Everything is written to trader/state/broker.json (append only). Dates are
IST dates; --now "2026-10-29 10:15" (IST) pins the clock.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trader_common import clock, money as rupees, orders as kite, symbols as nse  # noqa: E402

YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
TOOL_PLACE = "place_order"
TOOL_CANCEL = "cancel_order"


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, UTC+5:30)

to_ist = clock.to_ist


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    try:
        return clock.parse_now(text)
    except clock.ClockError as e:
        raise TicketError(str(e))


def ist_iso(now_utc):
    """naive UTC -> "2026-10-29T10:15:00+05:30" (IST wall clock with its offset)."""
    return "%s+05:30" % to_ist(now_utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


def dry_run(cfg):
    """Dry run unless the config says "dry_run": false in so many words."""
    return cfg.get("dry_run", True) is not False


# ---------------------------------------------------------------- the order

fingerprint = kite.fingerprint   # the same function trader-risk uses


def _str_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _money(v):
    return rupees.inr(float(v))


def _p(v):
    return rupees.num(float(v))


def is_entry_ticket(order):
    return str(order.get("side", "")).lower() == "buy"


def max_loss(order):
    """(rupees, stop) lost if the SL fills at its limit (at the stop without one), or (None, stop)."""
    return kite.governing_loss(order), _f(order.get("stop"))


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("side", "")).upper()
    sym = order.get("symbol", "?")
    q = _str_num(order.get("qty"))
    product = str(order.get("product") or "MIS").upper()
    parts = []
    if order.get("cancel_order_id"):
        parts.append("first cancel the open SL stop, order %s" % order["cancel_order_id"])
    parts.append("%s %s %s, LIMIT %s, %s, good for today" % (side, q, sym, _p(order.get("price")), product))
    if is_entry_ticket(order) and order.get("stop") is not None:
        leg = "then, once it fills: SELL those shares with an SL stop, trigger %s" % _p(order["stop"])
        if order.get("stop_limit") is not None:
            leg += ", limit %s" % _p(order["stop_limit"])
        parts.append(leg)
    if is_entry_ticket(order) and order.get("target") is not None:
        parts.append("target %s: an alert, not an order" % _p(order["target"]))
    m = kite.max_loss(order)
    if m["at_stop_limit"] is not None:
        parts.append("max loss about %s if the SL fills at its limit %s (%s at the trigger %s); a gap below %s "
                     "leaves the SL unfilled" % (_money(m["at_stop_limit"]), _p(order["stop_limit"]),
                                                 _money(m["at_stop"]), _p(order["stop"]), _p(order["stop_limit"])))
    elif m["at_stop"] is not None:
        parts.append("max loss about %s if the stop fills at %s" % (_money(m["at_stop"]), _p(order["stop"])))
    return parts


def plain(order):
    """BUY 48 RELIANCE, LIMIT 1,177.90, MIS, good for today / then, once it fills: ... / max loss about ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("qty", "price", "stop", "stop_limit", "target"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    m = kite.max_loss(order)
    for k in ("at_stop", "at_stop_limit"):
        if m[k] is not None:
            nums.append(m[k])
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = str(order.get("symbol", "")).upper()
    tokens = set(re.findall(r"[A-Z0-9][A-Z0-9&\-]{0,19}", raw.upper()))   # whole NSE symbols: M&M, BAJAJ-AUTO
    others = {s.upper() for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_str_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- the legs

def calls_for(order):
    """The tool calls a ticket makes, in order, before any fill: [(tool, arguments)]."""
    calls = []
    if order.get("cancel_order_id"):
        calls.append((TOOL_CANCEL, kite.cancel_args(order["cancel_order_id"])))
    calls.append((TOOL_PLACE, kite.entry_leg(order)))
    return calls


def call_lines(calls, indent="    "):
    lines = []
    for n, (tool, args) in enumerate(calls, 1):
        lines.append("%s%d. %s with exactly:" % (indent, n, tool))
        for l in json.dumps(args, indent=2).splitlines():
            lines.append(indent + "   " + l)
    return lines


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today, leg="entry"):
    """The latest decision recorded today for this fingerprint and leg, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today and rec.get("leg", "entry") == leg:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for the %s call" % (rec.get("at"), TOOL_PLACE), rv
        if rec.get("dry_run"):
            return False, "DONE (dry run): %s at %s, nothing was sent" % (st, rec.get("at")), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if is_entry_ticket(order) else "exit"


def open_stop(state, symbol, today):
    """Today's SL stop for symbol that is placed and not yet cancelled or filled: the record, or None."""
    stops, closed = [], set()
    for rec in state.get("orders", []):
        if rec_day(rec) != today or str(rec.get("symbol", "")).upper() != symbol:
            continue
        if rec.get("leg") == "stop" and rec.get("status") == "placed" and rec.get("broker_order_id"):
            stops.append(rec)
        if rec.get("closes_order_id"):
            closed.add(rec["closes_order_id"])
    live = [r for r in stops if r["broker_order_id"] not in closed]
    return live[-1] if live else None


def dry_id(fp, leg):
    return "DRYRUN-%s-%s" % (leg, fp[:8])


# ---------------------------------------------------------------- rendering

def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    dry = dry_run(cfg)
    lines = ["ORDER %d of %d, Zerodha (Kite), %s" % (idx, total,
                                                       "DRY RUN: nothing is sent" if dry else "LIVE, REAL MONEY")]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    calls = calls_for(order)
    if dry:
        lines.append("  DRY RUN: do not call. In a live account Claude would call:")
    else:
        lines.append("  Claude will call, one at a time, each with its own approval prompt:")
    lines.extend(call_lines(calls))
    if is_entry_ticket(order) and order.get("stop") is not None:
        lines.append("  Leg 2, the SL stop, comes after the entry fills: ticket.py stop --index %d --filled-qty Q "
                     "--filled-price P prints it for exactly the shares that filled." % idx)
    if ready:
        lines.append("  Reply \"yes\" to %s this one ticket, or \"no\" to skip it." % ("rehearse" if dry else "place"))
        if not dry:
            lines.append("  Claude's own prompt then asks again for each call: allow it once, or deny it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    m = kite.max_loss(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "dry_run": dry_run(cfg), "broker": "zerodha",
            "calls": [{"tool": t, "arguments": a} for t, a in calls_for(order)],
            "stop_leg_after_fill": is_entry_ticket(order) and order.get("stop") is not None,
            "why": order.get("why", ""), "max_loss": m["at_stop_limit"] if m["at_stop_limit"] is not None
            else m["at_stop"], "max_loss_at_stop": m["at_stop"], "stop": _f(order.get("stop")),
            "stop_limit": _f(order.get("stop_limit")), "target": _f(order.get("target"))}


# ---------------------------------------------------------------- fills -> portfolio

def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = symbol.upper()
    qty, price = float(qty), float(price)
    if qty <= 0 or price <= 0:
        raise TicketError("a fill needs a positive qty and price")
    if abs(qty - round(qty)) > 1e-9:
        raise TicketError("NSE equity fills are whole shares, got %s" % _str_num(qty))
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if str(p.get("ticker", "")).upper() == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0.0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = round(float(pos.get("shares", 0)) + qty, 6)
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (_str_num(qty), symbol, pos.get("shares", 0) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 1e-9:
                break
            take = min(left, float(lot["shares"]))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = round(float(lot["shares"]) - take, 6)
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 1e-9]
        pos["shares"] = round(float(pos["shares"]) - qty, 6)
        if pos["shares"] <= 1e-9:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price, closes=None, dry=False):
    """A fill in the log; closes = the Zerodha order id it ends (an SL that filled)."""
    state = load_state(state_path)
    rec = {"at": ist_iso(now), "date": today, "status": "filled", "symbol": symbol.upper(), "side": side,
           "qty": _str_num(float(qty)), "price": float(price),
           "plain": "%s %s %s at %s" % (side.upper(), _str_num(float(qty)), symbol.upper(), _p(price))}
    if closes:
        rec["closes_order_id"] = closes
    if dry:
        rec["dry_run"] = True
    state.setdefault("orders", []).append(rec)
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-29")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def _record(order, a, today, now, status, **extra):
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"), "plain": plain(order),
           "status": status, "broker": "zerodha"}
    rec.update({k: v for k, v in extra.items() if v is not None})
    return rec


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne ticket at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    dry = dry_run(cfg)
    loss, stop = max_loss(order)
    kind = kind_of(order, rv)
    common = dict(qty=order.get("qty"), kind=kind, leg="entry", max_loss=loss if kind == "entry" else None,
                  stop=stop, stop_limit=_f(order.get("stop_limit")), dry_run=True if dry else None)
    state.setdefault("orders", []).append(_record(order, a, today, now, "approved", reply=a.reply, **common))
    fp = fingerprint(order)
    sim_id = dry_id(fp, "entry" if kind == "entry" else "exit")
    if dry:
        sim = _record(order, a, today, now, "placed", broker_order_id=sim_id, **common)
        if order.get("cancel_order_id"):
            sim["closes_order_id"] = order["cancel_order_id"]
        state["orders"].append(sim)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    calls = calls_for(order)
    if dry:
        print("DRY RUN (\"dry_run\": true): logged as placed with id %s. Do NOT call %s. In a live account "
              "these would be the calls:" % (sim_id, " or ".join(sorted({t for t, _ in calls}))))
        for l in call_lines(calls, "  "):
            print(l)
        if is_entry_ticket(order) and order.get("stop") is not None:
            print("Next (rehearsal): ticket.py stop --index %d --filled-qty %s --filled-price %s"
                  % (a.index, _str_num(order.get("qty")), _p(order.get("price")).replace(",", "")))
        return 0
    print("Now make these calls, one at a time, each through Claude's own approval prompt (allow once):")
    for l in call_lines(calls, "  "):
        print(l)
    if order.get("cancel_order_id"):
        print("If the cancel fails because the SL already filled, do NOT sell: check get_positions, record the "
              "SL fill (ticket.py fill ... --closes %s) and stop." % order["cancel_order_id"])
    print("Then: ticket.py result --index %d --status placed|denied|failed --broker-id <order id>" % a.index)
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    state.setdefault("orders", []).append(_record(order, a, today, now, "declined", reply=a.reply or "no",
                                                  leg="entry"))
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    if dry_run(cfg):
        raise TicketError("dry run: approve and stop already log a simulated placement; there is no live result "
                          "to record")
    fp = fingerprint(order)
    if a.leg == "entry":
        rec = latest(state, fp, today, "entry")
        if not rec or rec.get("status") != "approved":
            raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    else:
        rec = latest(state, fp, today, "stop")
        if not rec or rec.get("status") != "ready":
            raise TicketError("order %d has no SL leg waiting (run ticket.py stop first)" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status, "leg": a.leg, "broker": "zerodha"}
    for k in ("qty", "kind", "max_loss", "stop", "stop_limit"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    if a.leg == "entry" and a.status == "placed" and order.get("cancel_order_id"):
        new["closes_order_id"] = order["cancel_order_id"]
    if a.status == "placed" and not a.broker_id:
        raise TicketError("a placed order needs --broker-id (the order id Zerodha returned)")
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d %s leg as %s." % (a.index, a.leg, a.status))
    if a.status != "placed":
        if a.leg == "stop":
            print("THE POSITION HAS NO STOP AT ZERODHA. Fix it now (ticket.py stop again) or flatten it.")
        return 0
    if a.leg == "entry" and is_entry_ticket(order) and order.get("stop") is not None:
        print("When it fills (get_order_history %s, or get_orders), place the SL for what filled:" % a.broker_id)
        print("  ticket.py stop --index %d --filled-qty Q --filled-price P" % a.index)
    elif a.leg == "entry":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
    else:
        print("The SL rests at Zerodha until it triggers, you flatten, or the day ends. trader-alerts watches the "
              "target and flat_by.")
    return 0


def cmd_stop(a):
    """After the entry fills: record the fill, then leg 2 for exactly those shares."""
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    if not is_entry_ticket(order) or order.get("stop") is None or order.get("stop_limit") is None:
        raise TicketError("order %d has no SL leg (an exit, or no stop / stop_limit)" % a.index)
    fp = fingerprint(order)
    entry = latest(state, fp, today, "entry")
    if not entry or entry.get("status") != "placed":
        raise TicketError("order %d's entry is not recorded as placed today; the SL follows a placed, filled "
                          "entry" % a.index)
    prior = latest(state, fp, today, "stop")
    if prior and prior.get("status") in ("ready", "placed"):
        raise TicketError("order %d already has its SL leg (%s at %s)" % (a.index, prior["status"], prior["at"]))
    q, px = _f(a.filled_qty), _f(a.filled_price)
    if q is None or q <= 0 or abs(q - round(q)) > 1e-9:
        raise TicketError("--filled-qty wants the whole number of shares that filled")
    if q > float(order["qty"]) + 1e-9:
        raise TicketError("%s filled, but the ticket was for %s" % (_str_num(q), _str_num(order["qty"])))
    if px is None or px <= 0:
        raise TicketError("--filled-price wants the average fill price")
    stop = float(order["stop"])
    ref = _f(a.last) or px
    dry = dry_run(cfg)
    apply_fill(a.portfolio, cfg, order["symbol"], "buy", q, px, today)
    log_fill(a.state, now, today, order["symbol"], "buy", q, px, dry=dry)
    state = load_state(a.state)
    print("Recorded the entry fill: BUY %s %s at %s." % (_str_num(q), order["symbol"], _p(px)))
    if ref <= stop + 1e-9:
        state["orders"].append(_record(order, a, today, now, "skipped", leg="stop", kind="stop", qty=int(q),
                                       note="price %s at or under the stop %s" % (_p(ref), _p(stop))))
        write_json(a.state, state)
        print("The price %s is already at or under the stop %s: an SL sell there would trigger at once. Do NOT "
              "place it. Get out instead: ticket.py flatten --symbol %s --last <price>." % (_p(ref), _p(stop),
                                                                                         order["symbol"]))
        return 1
    leg = kite.stop_leg(order, q)
    words = "SELL %s %s, SL trigger %s limit %s, %s (the stop for order %d)" % (
        _str_num(q), order["symbol"], _p(stop), _p(order["stop_limit"]), leg["product"], a.index)
    rec = _record(order, a, today, now, "ready", leg="stop", kind="stop", qty=int(q), stop=stop,
                  stop_limit=float(order["stop_limit"]), dry_run=True if dry else None)
    rec["plain"] = words
    state["orders"].append(rec)
    if dry:
        sim = _record(order, a, today, now, "placed", leg="stop", kind="stop", qty=int(q), stop=stop,
                      stop_limit=float(order["stop_limit"]), broker_order_id=dry_id(fp, "stop"), dry_run=True)
        sim["plain"] = words
        state["orders"].append(sim)
    write_json(a.state, state)
    if dry:
        print("DRY RUN: logged the SL as placed with id %s. Do NOT call %s. Live, it would be:" % (dry_id(fp, "stop"),
                                                                                                 TOOL_PLACE))
    else:
        print("Leg 2, covered by your yes for order %d: call %s (Claude's prompt asks again) with exactly:"
              % (a.index, TOOL_PLACE))
    for l in json.dumps(leg, indent=2).splitlines():
        print("  " + l)
    if not dry:
        print("Then: ticket.py result --index %d --leg stop --status placed --broker-id <order id>. Until it is "
              "placed the position has no stop." % a.index)
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = nse.normalize(a.symbol)
    if sym is None:
        raise TicketError("%r is not an NSE symbol (RELIANCE, M&M, BAJAJ-AUTO)" % a.symbol)
    now = parse_now(a.now)
    date = a.date or to_ist(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-29")
    pf = apply_fill(a.portfolio, cfg, sym, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, sym, a.side, a.qty, a.price, closes=a.closes, dry=dry_run(cfg))
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, sym, _money(a.price), date,
                                                                a.portfolio, _money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = nse.normalize(a.symbol)
    if sym is None:
        raise TicketError("%r is not an NSE symbol (RELIANCE, M&M, BAJAJ-AUTO)" % a.symbol)
    now, today = _clock(a)
    last = _f(a.last)
    if last is None or last <= 0:
        raise TicketError("--last wants the last traded price (Kite get_ltp NSE:%s, read only)" % sym)
    try:
        buf = kite.buffer_pct(cfg, "exit_limit_buffer_pct")
    except ValueError as e:
        raise TicketError(str(e))
    source = "Kite get_positions"
    if a.qty is not None:
        qty = _f(a.qty)
        if qty is None or qty <= 0 or abs(qty - round(qty)) > 1e-9:
            raise TicketError("--qty wants the whole number of shares you hold, like --qty 48")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("qty"))) or 0 for p in pf.get("positions", [])
                  if (nse.normalize(p.get("ticker") or p.get("symbol")) or "") == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check Kite (get_positions) and pass --qty "
                              "if you do hold some" % (sym, a.portfolio))
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if (nse.normalize(plan.get("symbol")) or "") == sym else None) \
        or cfg.get("flat_by") or clock.hm(clock.FLAT_BY)
    state = load_state(a.state)
    stop = open_stop(state, sym, today)
    try:
        step = kite.tick_of({"symbol": sym, "price": last}, cfg,
                            plan if (nse.normalize(plan.get("symbol")) or "") == sym else None)
    except ValueError as e:
        raise TicketError(str(e))
    price = kite.limit_below(last, buf, step)
    order = {"symbol": sym, "side": "sell", "qty": int(round(qty)), "order_type": "LIMIT", "price": price,
             "product": "MIS", "tick_size": step,
             "why": "flat by %s IST: closes the %s day trade, LIMIT %s%% under the last %s (qty from %s)"
             % (flat_by, sym, rupees.num(buf, 1) if buf != int(buf) else int(buf), _p(last), source)}
    if stop:
        order["cancel_order_id"] = stop["broker_order_id"]
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "dry_run": dry_run(cfg),
           "note": "Exit ticket. Nothing is sent until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if stop:
        print("It cancels the open SL first (order %s, logged %s): Zerodha would otherwise hold those shares "
              "for it." % (stop["broker_order_id"], stop["at"]))
    else:
        print("No open SL is logged today for %s. If one is open at Zerodha (get_orders), cancel it first." % sym)
    if a.qty is None:
        print("The qty comes from %s. Check it against Kite get_positions before you say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one ticket.")
    print("  3. A LIMIT under the market fills at the best bid or better; if the price drops through it, it rests:")
    print("     check get_orders and flatten again with a fresh --last.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = ((" max loss %s" % _money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed"
                else "")
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        leg = (" [%s]" % r["leg"]) if r.get("leg") and r.get("leg") != "entry" else ""
        dry = " (dry run)" if r.get("dry_run") else ""
        print("%s  %s %-9s%s %s%s%s%s%s" % (r.get("at"), idx, r.get("status"), leg, r.get("plain"), reply, loss,
                                           extra, dry))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock_args(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-29 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock_args(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one ticket")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the approval prompt for a leg (live only)")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--leg", choices=["entry", "stop"], default="entry")
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id", help="the order id Zerodha returned")
    p.add_argument("--note")
    p = sub.add_parser("stop", help="after the entry fills: record the fill and print the SL leg")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--filled-qty", required=True)
    p.add_argument("--filled-price", required=True)
    p.add_argument("--last", help="the last traded price now, if it has moved since the fill")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--closes", metavar="ORDER_ID", help="the Zerodha order this fill ends (the SL that filled)")
    p.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="the exit ticket: cancel the open SL, then a LIMIT sell")
    p.add_argument("--symbol", required=True)
    p.add_argument("--last", required=True, help="the last traded price (Kite get_ltp, read only)")
    p.add_argument("--qty", help="shares you hold, from Kite get_positions (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    clock_args(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock_args(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "stop": cmd_stop, "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
