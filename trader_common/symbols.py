"""NSE symbols. Inside the tools a symbol is the bare NSE trading symbol
(RELIANCE, M&M, BAJAJ-AUTO); Yahoo's form (RELIANCE.NS) appears only in the
fetch layer. Standard library only."""

import re
import urllib.parse

SUFFIX = ".NS"
# NSE trading symbols: capitals and digits, with & and - inside (M&M, BAJAJ-AUTO, 20MICRONS).
TICKER = re.compile(r"^[A-Z0-9](?:[A-Z0-9&-]{0,18}[A-Z0-9])?$")


def normalize(sym):
    """User text -> the bare NSE symbol, or None when it is not one. A trailing
    .NS (Yahoo's form) is accepted and dropped; .BO (BSE) is not."""
    s = str(sym or "").strip().upper()
    if s.endswith(SUFFIX):
        s = s[:-len(SUFFIX)]
    return s if TICKER.match(s) else None


def valid(sym):
    return bool(TICKER.match(str(sym or "")))


def to_yahoo(sym):
    """RELIANCE -> RELIANCE.NS (not URL-quoted)."""
    return str(sym).upper() + SUFFIX


def yahoo_quoted(sym):
    """M&M -> M%26M.NS, for a URL path or query."""
    return urllib.parse.quote(to_yahoo(sym), safe=".-")


def from_yahoo(ysym):
    """M&M.NS -> M&M. Anything without the .NS suffix is returned upper-cased."""
    s = str(ysym or "").upper()
    return s[:-len(SUFFIX)] if s.endswith(SUFFIX) else s
