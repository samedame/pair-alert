"""Live option quotes from Tradier (optional). Read-only market-data calls; the bot never places orders.

With TRADIER_API_KEY set, every ENTRY and EXIT alert carries the real bid and ask of the contract the backtest's rule
selects, and the ledger records them. That is the one input the backtest could not measure.
"""
from __future__ import annotations

import os
from datetime import date
from typing import List, Optional

import requests

from . import config as C
from .contracts import pick_listed_expiry

BASE = 'https://api.tradier.com/v1'


class Tradier:
    def __init__(self, token: str, http: Optional[requests.Session] = None, timeout: float = 10.0):
        self._h = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        self._http = http or requests.Session()
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> Optional['Tradier']:
        tok = (os.environ.get('TRADIER_API_KEY') or '').strip()
        return cls(tok) if tok else None

    def _get(self, path: str, params: dict) -> dict:
        r = self._http.get(BASE + path, params=params, headers=self._h, timeout=self._timeout)
        r.raise_for_status()
        return r.json() or {}

    def expirations(self, symbol: str) -> List[date]:
        d = (self._get('/markets/options/expirations', {'symbol': symbol, 'includeAllRoots': 'true'}).get('expirations') or {}).get('date') or []
        if isinstance(d, str):
            d = [d]
        return [date.fromisoformat(x) for x in d]

    def chain(self, symbol: str, expiry: date) -> List[dict]:
        o = (self._get('/markets/options/chains', {'symbol': symbol, 'expiration': expiry.isoformat(), 'greeks': 'true'}).get('options') or {}).get('option') or []
        return [o] if isinstance(o, dict) else o

    def quote(self, option_symbol: str) -> Optional[dict]:
        q = (self._get('/markets/quotes', {'symbols': option_symbol, 'greeks': 'false'}).get('quotes') or {}).get('quote')
        if isinstance(q, list):
            q = q[0] if q else None
        return q

    # ------------------------------------------------------------------
    def select(self, ticker: str, d: date, cp: int) -> Optional[dict]:
        """The contract the backtest's rule picks, with its live quote: Friday expiry closest to 14 days out,
        whole-dollar strike whose delta is closest to 0.65."""
        exp = pick_listed_expiry(d, self.expirations(ticker))
        if exp is None:
            return None
        kind = 'call' if cp > 0 else 'put'
        best, dist = None, 9.0
        for row in self.chain(ticker, exp):
            if row.get('option_type') != kind or row.get('root_symbol', ticker) != ticker:
                continue
            k = row.get('strike'); delta = (row.get('greeks') or {}).get('delta')
            if k is None or delta is None or abs(float(k) - round(float(k))) > 1e-9:
                continue
            x = abs(abs(float(delta)) - C.TARGET_DELTA)
            if x < dist:
                best, dist = row, x
        if best is None:
            return None
        return {'symbol': best['symbol'], 'expiry': exp.isoformat(), 'strike': float(best['strike']),
                'delta': float(best['greeks']['delta']), 'bid': _num(best.get('bid')), 'ask': _num(best.get('ask')),
                'source': 'tradier'}

    def bid_ask(self, option_symbol: str) -> Optional[dict]:
        q = self.quote(option_symbol)
        if not q:
            return None
        return {'bid': _num(q.get('bid')), 'ask': _num(q.get('ask'))}


def _num(x) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None
