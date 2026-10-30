"""Rupees and the NSE tick. Standard library only."""

import math

TICK = 0.05   # NSE equity tick size, in rupees


def group_in(n):
    """'1234567' -> '12,34,567' (Indian grouping: thousands, then pairs)."""
    neg = n.startswith("-")
    n = n.lstrip("-")
    if len(n) > 3:
        head, tail = n[:-3], n[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        n = ",".join(pairs + [tail])
    return ("-" if neg else "") + n


def inr(x, decimals=2):
    """1234567.5 -> '₹12,34,567.50'; negatives as '-₹500.00'."""
    x = float(x)
    text = ("%." + str(decimals) + "f") % abs(x)
    whole, _, frac = text.partition(".")
    out = "₹" + group_in(whole) + ("." + frac if frac else "")
    return ("-" + out) if x < 0 and float(text) != 0 else out


def num(x, decimals=2):
    """Indian-grouped number without the rupee sign: 12,34,567.50."""
    return inr(x, decimals).replace("₹", "")


def tick(x, how="nearest"):
    """Round a price to the 0.05 tick: 'nearest', 'down' (a stop or a sell) or 'up' (a buy limit)."""
    if x is None:
        return None
    n = float(x) / TICK
    if how == "down":
        k = math.floor(n + 1e-9)
    elif how == "up":
        k = math.ceil(n - 1e-9)
    else:
        k = math.floor(n + 0.5 + 1e-9)
    return round(k * TICK, 2) + 0.0
