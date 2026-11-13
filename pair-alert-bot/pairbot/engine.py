"""Both setups as pure functions of (history, today's completed 1-minute bars).

evaluate() recomputes the whole day from scratch every time it is called. Every rule only looks backwards, so a trade
or exit that has been reported never changes when more minutes arrive; the runner de-duplicates by event key.
An event stamped with minute M is known as soon as all minutes before M are complete (n >= M).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence

import numpy as np

from . import config as C
from .indicators import PastSession, bars5, session_vwap, noise_bands, bollinger_reversal_state, bollinger_levels


@dataclass
class Trade:
    setup: str
    ticker: str
    side: int                 # +1 call, -1 put
    bar: int                  # signal bar j (its close is the decision)
    entry_min: int            # fill at the OPEN of this minute
    exit_min: Optional[int]   # None while the position is open
    planned_exit_min: int     # time exit if nothing else happens
    exit_reason: str          # '' while open; 'time' | 'eod' | 'fomc' | 'stop'
    signal_close: float
    line_a: float             # S1: noise band on the signal side; S2: VWAP
    line_b: float             # S1: VWAP; S2: the Bollinger band that was crossed
    entry_px: Optional[float] = None
    exit_px: Optional[float] = None

    @property
    def key(self) -> str:
        return f'{self.setup}:{self.ticker}:{self.entry_min}'

    @property
    def gross_bp(self) -> Optional[float]:
        if self.entry_px is None or self.exit_px is None:
            return None
        return self.side * (self.exit_px / self.entry_px - 1.0) * 1e4

    def to_dict(self) -> dict:
        d = asdict(self); d['key'] = self.key; d['gross_bp'] = self.gross_bp
        return d


@dataclass
class DayView:
    """Indicator values on today's completed 5-minute bars (also used by the alert text)."""
    nb: int
    c5: np.ndarray
    vw: np.ndarray
    ub: Optional[np.ndarray]
    lb: Optional[np.ndarray]
    bb: np.ndarray
    badgap: bool
    bb_upper: float
    bb_lower: float


def day_view(hist: Sequence[PastSession], o, h, l, c, v, n: int) -> DayView:
    h5, l5, c5, v5 = bars5(o, h, l, c, v, n)
    nb = len(c5)
    ub, lb, badgap = noise_bands(hist, float(o[0]), nb)
    up, lo = bollinger_levels(hist, c5) if nb else (float('nan'), float('nan'))
    return DayView(nb, c5, session_vwap(h5, l5, c5, v5), ub, lb, bollinger_reversal_state(hist, c5), badgap, up, lo)


def _time_exit(entry_min: int, fomc: bool):
    mx, why = entry_min + C.HOLD_MIN, 'time'
    if mx >= C.EOD_MIN:
        mx, why = C.EOD_MIN, ('time' if entry_min + C.HOLD_MIN == C.EOD_MIN else 'eod')
    if fomc and mx > C.FOMC_FLAT_MIN:
        mx, why = C.FOMC_FLAT_MIN, 'fomc'
    return mx, why


def _fill(t: Trade, o, n: int) -> Trade:
    if n > t.entry_min:
        t.entry_px = float(o[t.entry_min])
    if t.exit_min is not None and n > t.exit_min:
        t.exit_px = float(o[t.exit_min])
    return t


def setup1(ticker: str, dv: DayView, o, n: int, fomc: bool) -> List[Trade]:
    """Noise Area + VWAP: half-hour checks; enter whenever the close is beyond the band and on the same side of VWAP;
    exit on a 5-minute close back through the lower of (band, VWAP) for calls / higher for puts, after 60 minutes, at 15:55."""
    out: List[Trade] = []
    if dv.badgap or dv.ub is None:
        return out
    ntr, free = 0, 0
    for j in C.S1_CHECK_BARS:
        if j >= dv.nb:
            break
        me = 5 * (j + 1)
        if ntr > 0 and me <= free:
            continue
        if ntr >= C.MAX_TRADES_PER_DAY or (fomc and j >= C.FOMC_BLOCK_BAR):
            break
        cl, up, lo, vw = dv.c5[j], dv.ub[j], dv.lb[j], dv.vw[j]
        side = 1 if (cl > up and cl > vw) else (-1 if (cl < lo and cl < vw) else 0)
        if side == 0:
            continue
        mx, why = _time_exit(me, fomc)
        if mx <= me:
            continue
        t = Trade('S1', ticker, side, j, me, None, mx, '', float(cl), float(up if side == 1 else lo), float(vw))
        closed = False
        for jj in range(j + 1, C.BARS5):
            mm = 5 * (jj + 1)
            if mm >= mx:
                break
            if jj >= dv.nb:           # this bar is not complete yet: the trade is still open
                break
            line = min(dv.ub[jj], dv.vw[jj]) if side == 1 else max(dv.lb[jj], dv.vw[jj])
            if (side == 1 and dv.c5[jj] < line) or (side == -1 and dv.c5[jj] > line):
                t.exit_min, t.exit_reason, closed = mm, 'stop', True
                break
        if not closed and n >= mx:
            t.exit_min, t.exit_reason, closed = mx, why, True
        out.append(_fill(t, o, n))
        if not closed:
            break
        ntr += 1; free = t.exit_min
    return out


def setup2(ticker: str, dv: DayView, o, n: int, fomc: bool) -> List[Trade]:
    """VWAP + Bollinger pullback: first 5-minute close above VWAP and below the lower band (call), or below VWAP and
    above the upper band (put); time exit only."""
    out: List[Trade] = []
    if dv.badgap:
        return out
    vst = np.where(dv.c5 > dv.vw, 1, np.where(dv.c5 < dv.vw, -1, 0))
    st = np.where(vst == dv.bb, vst, 0)
    ntr, free = 0, 0
    for j in range(C.S2_FIRST_BAR, C.S2_LAST_BAR + 1):
        if j >= dv.nb:
            break
        s = int(st[j])
        if s == 0 or int(st[j - 1]) == s:
            continue
        me = 5 * (j + 1)
        if me < free:
            continue
        if ntr >= C.MAX_TRADES_PER_DAY or (fomc and j >= C.FOMC_BLOCK_BAR):
            break
        mx, why = _time_exit(me, fomc)
        if mx <= me:
            continue
        t = Trade('S2', ticker, s, j, me, None, mx, '', float(dv.c5[j]), float(dv.vw[j]), float('nan'))
        if n >= mx:
            t.exit_min, t.exit_reason = mx, why
        out.append(_fill(t, o, n))
        if t.exit_min is None:
            break
        ntr += 1; free = mx
    return out


def evaluate(ticker: str, hist: Sequence[PastSession], o, h, l, c, v, n: int, fomc: bool,
             setups: Sequence[str] = ('S1', 'S2')):
    """All trades of the day that are known after n completed minutes. Returns (trades, DayView)."""
    n = min(int(n), len(c), C.SESSION_MIN)
    if n < 1:
        e = np.array([])
        return [], DayView(0, e, e, None, None, e.astype(int), False, float('nan'), float('nan'))
    dv = day_view(hist, o, h, l, c, v, n)
    trades: List[Trade] = []
    if 'S1' in setups:
        trades += setup1(ticker, dv, o, n, fomc)
    if 'S2' in setups:
        trades += setup2(ticker, dv, o, n, fomc)
    return trades, dv
