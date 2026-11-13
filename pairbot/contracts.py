"""Which option the backtest would have bought: Friday expiry 7-21 calendar days out (closest to 14), strike on the
1-dollar grid with absolute delta closest to 0.65. Includes the backtest's Black-Scholes proxy as a fallback."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import List, Optional, Tuple

from . import config as C


def target_expiry(d: date) -> date:
    fridays = [d + timedelta(days=k) for k in range(C.DTE_MIN, C.DTE_MAX + 1) if (d + timedelta(days=k)).weekday() == 4]
    return min(fridays, key=lambda f: abs((f - d).days - C.DTE_TARGET))


def pick_listed_expiry(d: date, listed: List[date]) -> Optional[date]:
    """The listed expiry for the target Friday; if that Friday is a holiday the Thursday before it; otherwise the
    listed date in the 7-21 day window closest to 14 days."""
    t = target_expiry(d)
    for cand in (t, t - timedelta(days=1)):
        if cand in listed:
            return cand
    window = [x for x in listed if C.DTE_MIN <= (x - d).days <= C.DTE_MAX]
    return min(window, key=lambda x: abs((x - d).days - C.DTE_TARGET)) if window else None


def business_days_after(d: date, expiry: date) -> int:
    return sum(1 for k in range(1, (expiry - d).days + 1) if (d + timedelta(days=k)).weekday() < 5)


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(S: float, K: float, T: float, r: float, q: float, sig: float, cp: int) -> Tuple[float, float]:
    """Black-Scholes price and delta with a continuous dividend yield. cp = +1 call, -1 put."""
    st = sig * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sig * sig) * T) / st
    d2 = d1 - st
    if cp > 0:
        return S * math.exp(-q * T) * _N(d1) - K * math.exp(-r * T) * _N(d2), math.exp(-q * T) * _N(d1)
    return K * math.exp(-r * T) * _N(-d2) - S * math.exp(-q * T) * _N(-d1), -math.exp(-q * T) * _N(-d1)


def proxy_iv(ticker: str, cp: int, vol_index_prev: float, S: float, prev_close: float) -> float:
    return (C.K_CALL if cp > 0 else C.K_PUT) * vol_index_prev / 100.0 * (S / prev_close) ** (-C.BETA)


def model_contract(ticker: str, d: date, minute: int, cp: int, S: float, prev_close: float, vol_index_prev: float,
                   rate: float) -> dict:
    """The backtest's proxy contract: expiry, strike, model mid price and delta."""
    exp = target_expiry(d)
    T = (business_days_after(d, exp) + (C.SESSION_MIN - minute) / C.SESSION_MIN) / 252.0
    sig = proxy_iv(ticker, cp, vol_index_prev, S, prev_close)
    q = C.DIV_YIELD[ticker]
    K = min(range(max(1, int(S * 0.85)), int(S * 1.15) + 2),
            key=lambda k: abs(abs(bs(S, k, T, rate, q, sig, cp)[1]) - C.TARGET_DELTA))
    mid, delta = bs(S, K, T, rate, q, sig, cp)
    return {'expiry': exp.isoformat(), 'strike': float(K), 'delta': delta, 'mid': mid, 'iv': sig, 'source': 'model'}


def house_spread(ticker: str, S: float) -> float:
    """Full quoted spread assumed by the backtest's 1x cost level, in dollars per share."""
    return max(0.01, C.HOUSE_SPREAD * C.INST_SPREAD_MULT[ticker] * S)


def occ_symbol(ticker: str, expiry: date, cp: int, strike: float) -> str:
    return f"{ticker}{expiry:%y%m%d}{'C' if cp > 0 else 'P'}{int(round(strike * 1000)):08d}"
