"""The Zerodha (Kite) order shape, shared by trader-budget, trader-risk and
trader-broker so the fingerprint and the max loss can never drift between them.
Standard library only.

One proposed order in trader/orders.json is one ticket:

  entry  {"symbol": "RELIANCE", "side": "buy", "qty": 63, "order_type": "LIMIT",
          "price": 1177.85, "product": "MIS", "stop": 1163.35, "stop_limit": 1157.50,
          "target": 1196.20, "why": "...", "plan": {...}}
  exit   {"symbol": "RELIANCE", "side": "sell", "qty": 63, "order_type": "LIMIT",
          "price": 1190.25, "product": "MIS", "cancel_order_id": "<the open SL stop>",
          "why": "..."}

It becomes these calls to kite-mcp-server v0.3.2 (argument names and types from its
mcp/post_tools.go; numbers are JSON numbers):

  leg 1, entry   place_order  BUY  LIMIT at price
  leg 2, stop    place_order  SELL SL, trigger_price = stop, price = stop_limit,
                 quantity = what the entry filled; only after the entry has filled
  target         no order: a trader-alerts alert, then an exit ticket
  exit           cancel_order the open SL (if any), then place_order SELL LIMIT

Why LIMIT and SL, not MARKET and SL-M: Kite rejects API MARKET and SL-M orders
without a market_protection value, and kite-mcp-server v0.3.2 cannot send one.
A LIMIT a small buffer through the market and an SL with a limit a buffer under
its trigger behave like market protection, under our control. The price of that:
if the market gaps below stop_limit, the SL triggers but does not fill. The
max loss is therefore measured at stop_limit, the worst fill that still happens.
"""

import hashlib
import json

from trader_common import money

EXCHANGE = "NSE"
VARIETY = "regular"
VALIDITY = "DAY"
PRODUCTS = ("MIS", "CNC")       # MIS: intraday (the day trade); CNC: delivery (weights mode)
ORDER_TYPES = ("LIMIT",)        # see the module docstring: no MARKET, no SL-M on v0.3.2
DEFAULT_BUFFERS = {             # percent; config keys of the same names override them
    "entry_limit_buffer_pct": 0.3,
    "stop_limit_buffer_pct": 0.5,
    "exit_limit_buffer_pct": 0.5,
}
FP_FIELDS = ("symbol", "side", "qty", "order_type", "price", "product", "stop", "stop_limit", "target",
             "cancel_order_id")
PRICE_KEYS = ("price", "stop", "stop_limit", "target")
TAG_PREFIX = "ndt"              # Kite tag: alphanumeric, at most 20 characters


def buffer_pct(cfg, key):
    v = (cfg or {}).get(key, DEFAULT_BUFFERS[key])
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError("%s must be a percentage like %s" % (key, DEFAULT_BUFFERS[key]))
    if not 0 <= v <= 5:
        raise ValueError("%s must be between 0 and 5 (percent), got %s" % (key, v))
    return v


def limit_above(price, pct):
    """A buy limit pct% above price, up to the tick (1174.30 at 0.3% -> 1177.85)."""
    return money.tick(float(price) * (1 + pct / 100.0), "up")


def limit_below(price, pct):
    """A sell limit pct% under price, down to the tick (1163.35 at 0.5% -> 1157.50)."""
    return money.tick(float(price) * (1 - pct / 100.0), "down")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _canon(k, v):
    """One canonical form per field, so 63, "63" and 63.0 (or 1177.85 and "1177.850") match."""
    if k == "qty":
        f = _num(v)
        return int(f) if f is not None and f == int(f) else str(v)
    if k in PRICE_KEYS:
        f = _num(v)
        return "%.2f" % f if f is not None else str(v)
    if k in ("side",):
        return str(v).strip().lower()
    if k in ("order_type", "product", "symbol"):
        return str(v).strip().upper()
    return str(v)


def fingerprint(order):
    """16 hex digits that identify the exact order trader-risk checked: every field
    that changes what reaches the broker, the stop and the target included."""
    core = {k: _canon(k, order[k]) for k in FP_FIELDS if order.get(k) is not None}
    core["exchange"] = EXCHANGE
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def tag(order):
    """The Kite order tag both legs carry, so get_orders finds them: ndt + 12 hex digits."""
    return TAG_PREFIX + fingerprint(order)[:12]


def _base(order, side, qty):
    return {"variety": VARIETY, "exchange": EXCHANGE, "tradingsymbol": str(order["symbol"]).upper(),
            "transaction_type": side, "quantity": int(qty), "product": str(order.get("product") or "MIS").upper()}


def entry_leg(order):
    """place_order arguments for the order itself (the entry, or an exit's sell)."""
    leg = _base(order, "BUY" if str(order.get("side")).lower() == "buy" else "SELL", _num(order["qty"]))
    leg.update({"order_type": "LIMIT", "price": round(float(order["price"]), 2), "validity": VALIDITY,
                "tag": tag(order)})
    return leg


def stop_leg(order, filled_qty):
    """place_order arguments for the protective SL sell, for the shares that actually filled."""
    leg = _base(order, "SELL", filled_qty)
    leg.update({"order_type": "SL", "trigger_price": round(float(order["stop"]), 2),
                "price": round(float(order["stop_limit"]), 2), "validity": VALIDITY, "tag": tag(order)})
    return leg


def cancel_args(order_id):
    """cancel_order arguments."""
    return {"variety": VARIETY, "order_id": str(order_id)}


def max_loss(order):
    """{"at_stop": qty x (price - stop), "at_stop_limit": qty x (price - stop_limit), "per_share": ...}
    in rupees, worked in whole paise; None values when the order has no stop. The entry is the
    LIMIT price: the worst price the entry can fill at."""
    q, p = _num(order.get("qty")), _num(order.get("price"))
    s, sl = _num(order.get("stop")), _num(order.get("stop_limit"))
    out = {"at_stop": None, "at_stop_limit": None, "per_share": None, "per_share_at_stop": None}
    if not q or not p or not s:
        return out
    paise = lambda x: int(round(x * 100))
    rs = paise(p) - paise(s)
    out["per_share_at_stop"] = rs / 100.0
    out["at_stop"] = round(q * rs / 100.0, 2)
    if sl:
        rsl = paise(p) - paise(sl)
        out["per_share"] = rsl / 100.0
        out["at_stop_limit"] = round(q * rsl / 100.0, 2)
    return out


def governing_loss(order):
    """The max loss the limits are held to: at stop_limit when there is one, else at the stop."""
    m = max_loss(order)
    return m["at_stop_limit"] if m["at_stop_limit"] is not None else m["at_stop"]
