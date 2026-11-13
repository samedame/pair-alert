"""Yahoo Finance 1-minute bars on the 09:30..15:59 ET clock grid. Only completed bars are used."""
from __future__ import annotations

import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import requests

from . import config as C
from .calendar_nyse import NY, is_full_session, session_open
from .indicators import PastSession

_UA = {'User-Agent': 'Mozilla/5.0 (pair-alert-bot)'}
_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/{sym}'


class FeedError(RuntimeError):
    pass


def _get(http: requests.Session, sym: str, params: dict, retries: int = 3, timeout: float = 15.0) -> dict:
    last = None
    for i in range(retries):
        try:
            r = http.get(_URL.format(sym=requests.utils.quote(sym)), params=params, headers=_UA, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            last = f'HTTP {r.status_code}'
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        except (requests.RequestException, ValueError) as e:
            last = repr(e)
        _time.sleep(1.5 * (i + 1))
    raise FeedError(f'{sym}: {last}')


def _rows(payload: dict) -> List[tuple]:
    """(epoch_seconds, open, high, low, close, volume) rows with complete prices, sorted, de-duplicated."""
    try:
        res = payload['chart']['result'][0]
    except (KeyError, IndexError, TypeError):
        raise FeedError('no chart result')
    ts = res.get('timestamp') or []
    if not ts:
        return []
    q = res['indicators']['quote'][0]
    rows = {}
    for i, t in enumerate(ts):
        o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
        if None in (o, h, l, c):
            continue
        rows[int(t)] = (int(t), float(o), float(h), float(l), float(c), float(q['volume'][i] or 0.0))
    return [rows[k] for k in sorted(rows)]


@dataclass
class DayBars:
    ticker: str
    day: date
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray
    n: int = 0                  # completed minutes on the grid (index 0 = 09:30)
    last_real: int = -1         # index of the last minute that came from the feed
    holes: List[int] = field(default_factory=list)
    ok: bool = True
    reason: str = ''


def to_grid(rows: List[tuple], ticker: str, day: date, upto: int) -> DayBars:
    """Place a session's completed bars on the grid for minutes 0..upto-1. Interior gaps are filled with the previous
    close (flat bar, zero volume). A missing 09:30 bar fails the ticker for the day."""
    e = np.array([])
    open_ts = int(session_open(day).timestamp())
    got: Dict[int, tuple] = {}
    for r in rows:
        d = r[0] - open_ts
        if d < 0 or d % 60 != 0:          # the in-progress bar is stamped with the last-trade second
            continue
        m = d // 60
        if m < min(upto, C.SESSION_MIN):
            got[m] = r
    if 0 not in got:
        return DayBars(ticker, day, e, e, e, e, e, ok=False, reason='09:30 bar missing')
    last = max(got)
    n = last + 1
    o = np.empty(n); h = np.empty(n); l = np.empty(n); c = np.empty(n); v = np.zeros(n)
    holes = []
    for m in range(n):
        if m in got:
            _, o[m], h[m], l[m], c[m], v[m] = got[m]
        else:
            o[m] = h[m] = l[m] = c[m] = c[m - 1]
            holes.append(m)
    return DayBars(ticker, day, o, h, l, c, v, n=n, last_real=last, holes=holes)


def pad_to(b: DayBars, n: int) -> DayBars:
    """Extend with flat bars to n minutes when the feed is lagging (flagged by the caller)."""
    if not b.ok or b.n >= n:
        return b
    k = n - b.n
    f = np.full(k, b.c[-1])
    return DayBars(b.ticker, b.day, np.concatenate([b.o, f]), np.concatenate([b.h, f]), np.concatenate([b.l, f]),
                   np.concatenate([b.c, f]), np.concatenate([b.v, np.zeros(k)]), n=n, last_real=b.last_real,
                   holes=b.holes + list(range(b.n, n)))


def fetch_today(http: requests.Session, sym: str) -> List[tuple]:
    return _rows(_get(http, sym, {'interval': '1m', 'range': '1d', 'includePrePost': 'false'}))


def fetch_range(http: requests.Session, sym: str, start: date, end: date) -> List[tuple]:
    """1-minute rows for [start, end]; Yahoo allows about 7 calendar days per request."""
    out: Dict[int, tuple] = {}
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=6))
        p1 = int(datetime.combine(cur, datetime.min.time(), tzinfo=NY).timestamp())
        p2 = int(datetime.combine(stop + timedelta(days=1), datetime.min.time(), tzinfo=NY).timestamp())
        for r in _rows(_get(http, sym, {'interval': '1m', 'period1': p1, 'period2': p2, 'includePrePost': 'false'})):
            out[r[0]] = r
        cur = stop + timedelta(days=1)
        _time.sleep(0.2)
    return [out[k] for k in sorted(out)]


def build_history(rows: List[tuple], ticker: str, today: date, min_bars: int = 386) -> List[PastSession]:
    """Earlier full sessions (oldest first) with at least 386 of 390 minutes and the 09:30 bar, as in the backtest."""
    days = sorted({datetime.fromtimestamp(r[0], NY).date() for r in rows})
    out: List[PastSession] = []
    for d in days:
        if d >= today or not is_full_session(d):
            continue
        open_ts = int(session_open(d).timestamp())
        day_rows = [r for r in rows if 0 <= r[0] - open_ts < C.SESSION_MIN * 60]
        real = {(r[0] - open_ts) // 60 for r in day_rows if (r[0] - open_ts) % 60 == 0}
        if 0 not in real or len(real) < min_bars:
            continue
        b = pad_to(to_grid(day_rows, ticker, d, C.SESSION_MIN), C.SESSION_MIN)
        out.append(PastSession(d.isoformat(), float(b.o[0]), b.c.reshape(C.BARS5, 5)[:, 4].copy()))
    return out


def load_history(http: requests.Session, sym: str, today: date) -> List[PastSession]:
    rows = fetch_range(http, sym, today - timedelta(days=C.HISTORY_CALENDAR_DAYS), today - timedelta(days=1))
    return build_history(rows, sym, today)


def prev_daily_close(http: requests.Session, sym: str, today: date) -> Optional[float]:
    """Last daily close strictly before today (used for the vol index and the T-bill yield)."""
    try:
        rows = _rows(_get(http, sym, {'interval': '1d', 'range': '10d', 'includePrePost': 'false'}))
    except FeedError:
        return None
    prior = [r for r in rows if datetime.fromtimestamp(r[0], NY).date() < today]
    return prior[-1][4] if prior else None


_CBOE = 'https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv'


def prev_vol_index(http: requests.Session, sym: str, today: date) -> Optional[float]:
    """Previous close of a Cboe volatility index: Yahoo first, then Cboe's own daily file (Yahoo does not carry ^RVX)."""
    v = prev_daily_close(http, sym, today)
    if v:
        return v
    try:
        r = http.get(_CBOE.format(name=sym.lstrip('^')), headers=_UA, timeout=20)
        if r.status_code != 200:
            return None
        last = None
        for line in r.text.strip().splitlines()[-15:]:
            f = line.split(',')
            try:
                d = datetime.strptime(f[0], '%m/%d/%Y').date()
            except ValueError:
                continue
            if d < today:
                last = float(f[4])
        return last
    except (requests.RequestException, ValueError, IndexError):
        return None
