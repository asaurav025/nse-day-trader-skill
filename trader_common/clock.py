"""The NSE clock, shared by every script. Standard library only.

India Standard Time is a fixed UTC+5:30: no daylight saving, ever. The NSE
cash market's normal session runs 09:15 to 15:30 IST. Intraday (MIS)
positions are squared off by the broker from about 15:20, so the default
flat_by is 15:10 and the default last new entry is 14:45.

Times inside the tools are naive datetimes: naive UTC for "now", naive IST for
wall-clock reasoning. Nothing here reads the machine's time zone.

Holidays: the NSE capital-market (CM) trading holidays for 2026 below, plus an
optional override in trader/config.json:
    "holidays": {"2026-12-31": "ad-hoc closure", "2026-11-24": null}
A date mapped to a name is added as a holiday; a date mapped to null is
removed (the exchange reversed a closure). Weekend special sessions (budget
day, Muhurat trading) are not modelled.
"""

import datetime
import json
import os
import re

IST_OFFSET = datetime.timedelta(hours=5, minutes=30)
EPOCH = datetime.datetime(1970, 1, 1)
OPEN = datetime.time(9, 15)
CLOSE = datetime.time(15, 30)
FLAT_BY = datetime.time(15, 10)            # MIS auto square-off starts about 15:20
MIS_SQUARE_OFF = datetime.time(15, 20)     # Zerodha starts squaring off MIS positions about then
NO_ENTRIES_BEFORE = datetime.time(9, 30)   # the 15-minute opening range is set at 09:30
NO_ENTRIES_AFTER = datetime.time(14, 45)
TZ_LABEL = "IST"

# Source: NSE, "Trading holidays" for the Capital Market segment, calendar 2026,
# https://www.nseindia.com/resources/exchange-communication-holidays
# (API: https://www.nseindia.com/api/holiday-master?type=trading, key "CM", fetched 2026-10-30;
# a copy is in tests/fixtures/trader-common/nse-holiday-master-2026.json).
# Holidays that fall on a weekend are listed for completeness.
NSE_HOLIDAYS = {
    "2026-01-15": "Municipal Corporation Election - Maharashtra",
    "2026-01-26": "Republic Day",
    "2026-02-15": "Mahashivratri",
    "2026-03-03": "Holi",
    "2026-03-21": "Id-Ul-Fitr (Ramadan Eid)",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-09-14": "Ganesh Chaturthi",
    "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra",
    "2026-11-08": "Diwali Laxmi Pujan",
    "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
    "2026-12-25": "Christmas",
}
CALENDAR_YEARS = (2026,)
DEFAULT_CONFIG = "trader/config.json"

_overrides = None   # {date: name or None}, loaded on first use


class ClockError(ValueError):
    pass


# ---------------------------------------------------------------- conversions

def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def to_ist(utc):
    """Naive (or aware) UTC -> naive IST."""
    return utc.replace(tzinfo=None) + IST_OFFSET


def ist_to_utc(local):
    """Naive IST -> naive UTC."""
    return local.replace(tzinfo=None) - IST_OFFSET


def utc_of(ts):
    """Unix seconds -> naive UTC."""
    return EPOCH + datetime.timedelta(seconds=int(ts))


def ts_of(utc):
    return int((utc.replace(tzinfo=None) - EPOCH).total_seconds())


def ts_to_ist(ts):
    """Unix seconds -> naive IST."""
    return to_ist(utc_of(ts))


def ist_ts(local):
    """Naive IST -> unix seconds."""
    return ts_of(ist_to_utc(local))


def iso(utc):
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_now(text):
    """--now -> naive UTC. "2026-10-29 10:15" (or ...T10:15, or just 10:15 for
    today) is IST; an offset or a Z makes it absolute. Empty -> the real clock."""
    if not text:
        return utc_now()
    t = text.strip()
    if t.upper().endswith(" IST"):
        t = t[:-4].strip()
    if re.match(r"^\d{1,2}:\d{2}$", t):
        t = "%s %s" % (to_ist(utc_now()).date().isoformat(), t.zfill(5))
    t = t.replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise ClockError('--now wants an IST time like "2026-10-29 10:15" (or ...Z for UTC)')
    if dt.tzinfo is None:
        return ist_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def parse_hhmm(text, what="--at"):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ClockError("%s wants an IST time like 10:15, not %r" % (what, text))
    return datetime.time(int(m.group(1)), int(m.group(2)))


# ---------------------------------------------------------------- calendar

def load_overrides(path=None):
    """Read "holidays" from trader/config.json (or $TRADER_CONFIG). Bad files are ignored here:
    each tool reports its own config errors."""
    path = path or os.environ.get("TRADER_CONFIG") or DEFAULT_CONFIG
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    hol = doc.get("holidays") if isinstance(doc, dict) else None
    if not isinstance(hol, dict):
        return {}
    return {str(k): (str(v) if v else None) for k, v in hol.items()}


def set_overrides(overrides):
    """Tests and tools that already read the config: {date: name or None}; None resets to the file."""
    global _overrides
    _overrides = dict(overrides) if overrides is not None else None


def holidays():
    global _overrides
    if _overrides is None:
        _overrides = load_overrides()
    out = dict(NSE_HOLIDAYS)
    for k, v in _overrides.items():
        if v:
            out[k] = v
        else:
            out.pop(k, None)
    return out


def trading_day(d):
    """(is_trading_day, reason). Weekends, the NSE holiday list and the config override."""
    if d.weekday() >= 5:
        return False, "weekend"
    name = holidays().get(d.isoformat())
    if name:
        return False, "NSE holiday: " + name
    return True, ""


def session(d):
    """(open_time, close_time) in IST, or None if the market is closed all day."""
    ok, _ = trading_day(d)
    return (OPEN, CLOSE) if ok else None


def next_open(d):
    nd = d + datetime.timedelta(days=1)
    for _ in range(15):
        if session(nd):
            return "%s %s" % (nd.isoformat(), OPEN.strftime("%H:%M"))
        nd += datetime.timedelta(days=1)
    return None


def prev_trading_day(d):
    pd = d - datetime.timedelta(days=1)
    for _ in range(15):
        if session(pd):
            return pd
        pd -= datetime.timedelta(days=1)
    return None


def calendar_known(d):
    return d.year in CALENDAR_YEARS


# ---------------------------------------------------------------- words

def hm(t):
    """10:10, 09:45: the contract's wall-clock form."""
    return "%02d:%02d" % (t.hour, t.minute)


def short(t):
    """9:45, 10:10: for sentences."""
    return "%d:%02d" % (t.hour, t.minute)


def ampm(t):
    h = t.hour % 12 or 12
    return "%d:%02d %s" % (h, t.minute, "AM" if t.hour < 12 else "PM")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())
