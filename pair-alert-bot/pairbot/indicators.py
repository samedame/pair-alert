"""The three indicators, exactly as backtested, on completed 5-minute regular-session bars."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import config as C


@dataclass
class PastSession:
    """What the bot needs from an earlier full session: its 09:30 open and its 78 five-minute closes."""
    day: str
    open0: float
    c5: np.ndarray            # shape (78,)

    @property
    def close(self) -> float:
        return float(self.c5[-1])


def bars5(o, h, l, c, v, n_minutes: int):
    """Completed 5-minute bars from the first n_minutes completed 1-minute bars."""
    nb = min(n_minutes, C.SESSION_MIN) // 5
    k = nb * 5
    r = lambda x: np.asarray(x[:k], dtype=float).reshape(nb, 5)
    return r(h).max(1), r(l).min(1), r(c)[:, 4].copy(), r(v).sum(1)


def session_vwap(h5, l5, c5, v5) -> np.ndarray:
    """TradingView session VWAP (HLC3 source) on 5-minute bars; NaN until the first traded share."""
    tp = (h5 + l5 + c5) / 3.0
    cv = np.cumsum(v5)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(cv > 0, np.cumsum(tp * v5) / cv, np.nan)


def valid_sessions(hist: Sequence[PastSession]) -> List[PastSession]:
    """Sessions usable for the noise average: neither the overnight gap nor the open-to-close move above 25%."""
    out = []
    for i, s in enumerate(hist):
        gap = 0.0 if i == 0 else s.open0 / hist[i - 1].close - 1.0
        if abs(gap) > C.BAD_MOVE or abs(s.close / s.open0 - 1.0) > C.BAD_MOVE:
            continue
        out.append(s)
    return out


def noise_bands(hist: Sequence[PastSession], open0: float, nb: int) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], bool]:
    """Upper and lower Noise Area bands (k = 1) for today's first nb bars, plus the bad-gap flag.

    sigma[j] = mean over the previous 14 valid sessions of |close of bar j / that session's open - 1|
    upper = max(today's open, previous close) * (1 + sigma);  lower = min(today's open, previous close) * (1 - sigma)
    """
    if not hist:
        return None, None, False
    pc = hist[-1].close
    badgap = abs(open0 / pc - 1.0) > C.BAD_MOVE
    w = valid_sessions(hist)[-C.NOISE_SESSIONS:]
    if len(w) < C.NOISE_MIN_SESSIONS:
        return None, None, badgap
    sig = np.mean([np.abs(s.c5[:nb] / s.open0 - 1.0) for s in w], axis=0)
    return max(open0, pc) * (1.0 + sig), min(open0, pc) * (1.0 - sig), badgap


def bollinger_reversal_state(hist: Sequence[PastSession], c5_today: np.ndarray) -> np.ndarray:
    """+1 where the close is below the lower band (bullish reversal reading), -1 above the upper band, else 0.
    The 20-bar window runs continuously across sessions, as on a regular-session TradingView chart."""
    tail = np.concatenate([s.c5 for s in hist[-1:]]) if hist else np.array([])
    x = np.concatenate([tail[-(C.BB_N - 1):], np.asarray(c5_today, dtype=float)])
    off = len(x) - len(c5_today)
    st = np.zeros(len(c5_today), dtype=int)
    for i in range(len(c5_today)):
        k = i + off
        if k < C.BB_N - 1:
            continue
        w = x[k - C.BB_N + 1:k + 1]
        mu = float(w.sum()) / C.BB_N
        sd = math.sqrt(float(((w - mu) ** 2).sum()) / C.BB_N)
        if x[k] < mu - C.BB_K * sd:
            st[i] = 1
        elif x[k] > mu + C.BB_K * sd:
            st[i] = -1
    return st


def bollinger_levels(hist: Sequence[PastSession], c5_today: np.ndarray) -> Tuple[float, float]:
    """Upper and lower band at the latest completed bar (for the alert text only)."""
    tail = hist[-1].c5 if hist else np.array([])
    x = np.concatenate([tail, np.asarray(c5_today, dtype=float)])[-C.BB_N:]
    if len(x) < C.BB_N:
        return float('nan'), float('nan')
    mu = float(x.mean()); sd = float(x.std())
    return mu + C.BB_K * sd, mu - C.BB_K * sd
