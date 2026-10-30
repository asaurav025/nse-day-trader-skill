"""Rupees and the NSE tick. Standard library only.

The NSE tick is not one number. Since NSE's April 2025 revision, a stock's tick
depends on its price band, and NSE sets it each month from the stock's close on
the last trading day of the previous month (NSE circular CMTR67133 and its
revisions; Zerodha bulletin "Revision in tick size for NSE derivatives and cash
segment from April 15, 2025"):

    below 250           0.01        5,000 to 10,000    0.50
    250 to 1,000        0.05        10,000 to 20,000   1.00
    1,000 to 5,000      0.10        above 20,000       5.00

Each tick divides the next one up, so a coarser tick is always valid: a price
on a 0.10 grid is also on the 0.05 and 0.01 grids. When the month-end close is
not known, tick_size() takes the coarsest tick within EDGE_MOVE of the price,
which is valid whichever band the stock was put in. A per-symbol override
("tick_sizes": {"RELIANCE": 0.10} in trader/config.json) beats both.
"""

import math
from decimal import ROUND_HALF_UP, Decimal

TICK = 0.05   # the most common NSE equity tick (250 to 1,000); see tick_size() for the real one
TICK_LADDER = ((250.0, 0.01), (1000.0, 0.05), (5000.0, 0.10), (10000.0, 0.50), (20000.0, 1.00))
TICK_TOP = 5.00
TICKS = tuple(t for _, t in TICK_LADDER) + (TICK_TOP,)
EDGE_MOVE = 0.20   # how far a price may have moved since the month-end close it was banded on


def band_tick(price):
    """The ladder's tick for a price: below 250 -> 0.01, 250 to 1,000 -> 0.05, ..."""
    p = float(price)
    if p < 250.0:
        return 0.01
    for top, t in TICK_LADDER[1:]:
        if p <= top:
            return t
    return TICK_TOP


def tick_size(month_close=None, price=None, override=None):
    """The tick for one stock: the override, else the band of the previous month-end close,
    else the coarsest band within EDGE_MOVE of price (always a valid tick)."""
    if override:
        t = float(override)
        if not any(abs(t - x) < 1e-9 for x in TICKS):
            raise ValueError("tick size %s is not an NSE tick (%s)" % (override, ", ".join("%g" % x for x in TICKS)))
        return t
    if month_close:
        return band_tick(month_close)
    if price:
        p = float(price)
        return max(band_tick(p / (1 + EDGE_MOVE)), band_tick(p), band_tick(p * (1 + EDGE_MOVE)))
    raise ValueError("a tick size needs a month-end close, a price or an override")


def on_tick(x, step):
    """Is x a whole number of ticks (float noise allowed)?"""
    n = float(x) / float(step)
    return abs(n - round(n)) < 1e-6 * max(1.0, abs(n)) or abs(n - round(n)) < SNAP / 10


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


SNAP = 0.1         # in ticks: on_tick's tolerance (half a paisa at the 0.05 tick)
FLOAT_NOISE = 2e-7  # relative: float32 prices are off by up to about 6e-8 of their value


def tick(x, how="nearest", step=None):
    """Round a price to its tick: 'nearest' (half up), 'down' (a stop or a sell) or 'up' (a buy limit).
    step is the stock's tick (tick_size()); without one, the coarse tick for x itself.
    Yahoo's prices are float32: 1283.60 arrives as 1283.5999755859375 (and the error grows with
    the price), so a value within SNAP of a tick counts as on it before rounding down or up;
    otherwise 'down' would lose a whole tick. Only float noise snaps (FLOAT_NOISE of the value): a
    computed price a real fraction of a tick away (a stop 0.08 tick above a grid point) still rounds
    the way it was asked to."""
    if x is None:
        return None
    step = float(step) if step else tick_size(price=x)
    n = float(x) / step
    k = round(n)
    if abs(n - k) > FLOAT_NOISE * max(1.0, abs(n)):
        if how == "down":
            k = math.floor(n)
        elif how == "up":
            k = math.ceil(n)
        else:   # half up, in exact decimals: 1000.05 on a 0.10 tick is 1000.10, not 1000.00
            k = int((Decimal(repr(float(x))) / Decimal(repr(step))).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return round(k * step, 2) + 0.0
