import json, math, os
from datetime import date, datetime, timedelta
import numpy as np
import pytest
from pairbot import alerts as A, config as C, contracts as K, data as D, state as S, runner as R
from pairbot.calendar_nyse import NY, clock, is_full_session, session_open
from pairbot.engine import evaluate, Trade
from pairbot.indicators import PastSession, noise_bands, bollinger_reversal_state, session_vwap, valid_sessions
from pairbot.tradier import Tradier


def test_clock_and_calendar():
    assert clock(0) == '09:30' and clock(30) == '10:00' and clock(385) == '15:55'
    assert not is_full_session(date(2026, 11, 26)) and not is_full_session(date(2026, 11, 27)) and is_full_session(date(2026, 11, 13))
    assert not is_full_session(date(2026, 11, 14))


def test_expiry_rule_matches_backtest():
    # Mon..Fri of one week -> 11, 17, 16, 15, 14 calendar days (the backtest's rule)
    got = [(K.target_expiry(date(2024, 3, 11) + timedelta(days=i)) - (date(2024, 3, 11) + timedelta(days=i))).days for i in range(5)]
    assert got == [11, 17, 16, 15, 14]
    assert [K.business_days_after(date(2024, 3, 11) + timedelta(days=i), K.target_expiry(date(2024, 3, 11) + timedelta(days=i))) for i in range(5)] == [9, 13, 12, 11, 10]
    d = date(2026, 11, 13); t = K.target_expiry(d)
    assert K.pick_listed_expiry(d, [t - timedelta(days=7), t, t + timedelta(days=7)]) == t
    assert K.pick_listed_expiry(d, [t - timedelta(days=7), t - timedelta(days=1)]) == t - timedelta(days=1)   # holiday Friday
    assert K.pick_listed_expiry(d, [d + timedelta(days=2)]) is None
    assert K.occ_symbol('SPY', date(2026, 11, 27), 1, 672) == 'SPY261127C00672000'
    assert K.occ_symbol('IWM', date(2026, 11, 27), -1, 241.5) == 'IWM261127P00241500'


def test_black_scholes_and_model_contract():
    c, dc = K.bs(500, 495, 12 / 252, 0.04, 0.016, 0.18, 1); p, dp = K.bs(500, 495, 12 / 252, 0.04, 0.016, 0.18, -1)
    assert abs(c - p - (500 * math.exp(-0.016 * 12 / 252) - 495 * math.exp(-0.04 * 12 / 252))) < 1e-9
    assert 0 < dc < 1 and -1 < dp < 0
    for cp in (1, -1):
        m = K.model_contract('SPY', date(2026, 11, 13), 60, cp, 680.0, 678.0, 18.0, 0.04)
        assert abs(abs(m['delta']) - 0.65) < 0.05 and m['strike'] == int(m['strike']) and (m['strike'] < 680) == (cp > 0)
    assert abs(K.house_spread('SPY', 500) - 0.194) < 1e-9 and abs(K.house_spread('QQQ', 400) - 0.2328) < 1e-9


def _rows(day, minutes, px=100.0, extra=None):
    t0 = int(session_open(day).timestamp())
    rows = [(t0 + 60 * m, px + m * 0.01, px + m * 0.01 + 0.05, px + m * 0.01 - 0.05, px + m * 0.01 + 0.02, 1000.0) for m in minutes]
    return rows + (extra or [])


def test_grid_drops_in_progress_bar_fills_holes_and_fails_closed():
    day = date(2026, 11, 13); t0 = int(session_open(day).timestamp())
    b = D.to_grid(_rows(day, [0, 1, 2, 4, 5], extra=[(t0 + 6 * 60 + 37, 1, 1, 1, 1, 5.0)]), 'SPY', day, 390)
    assert b.ok and b.n == 6 and b.holes == [3] and b.v[3] == 0 and b.c[3] == b.c[2] and b.last_real == 5
    assert D.to_grid(_rows(day, [0, 1, 2, 4, 5]), 'SPY', day, 3).n == 3                      # nothing at or after `upto`
    assert not D.to_grid(_rows(day, [1, 2, 3]), 'SPY', day, 390).ok
    p = D.pad_to(b, 10); assert p.n == 10 and p.holes[-1] == 9 and p.c[9] == b.c[5]


def test_history_keeps_only_full_complete_sessions():
    today = date(2026, 11, 13); rows = []
    rows += _rows(date(2026, 11, 10), range(390)) + _rows(date(2026, 11, 11), range(380)) + _rows(date(2026, 11, 12), [m for m in range(390) if m not in (7, 8)])
    rows += _rows(today, range(100))
    h = D.build_history(rows, 'SPY', today)
    assert [s.day for s in h] == ['2026-11-10', '2026-11-12'] and all(len(s.c5) == 78 for s in h)
    assert h[0].open0 == 100.0 and abs(h[0].c5[0] - (100.0 + 4 * 0.01 + 0.02)) < 1e-12


def test_indicators_basic():
    hist = [PastSession(f'd{i}', 100.0, np.full(78, 100.0) * (1 + 0.001 * np.arange(78) / 78)) for i in range(15)]
    ub, lb, bad = noise_bands(hist, 100.2, 78)
    assert not bad and ub[0] == 100.2 and ub[-1] > 100.2 and lb[-1] < hist[-1].close and ub.shape == (78,)
    assert noise_bands(hist[:9], 100.0, 10)[0] is None
    assert noise_bands(hist, 140.0, 10)[2] is True
    assert len(valid_sessions(hist + [PastSession('x', 100.0, np.full(78, 130.0))])) == 15
    st = bollinger_reversal_state(hist, np.array([100.0, 100.05, 90.0, 130.0]))
    assert st.tolist()[2:] == [1, -1]
    vw = session_vwap(np.array([2.0, 4.0]), np.array([0.0, 2.0]), np.array([1.0, 3.0]), np.array([0.0, 10.0]))
    assert math.isnan(vw[0]) and vw[1] == 3.0


class FakeHTTP:
    def __init__(self, payloads): self.payloads = payloads; self.calls = []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params))
        class R:
            status_code = 200
            def __init__(s, p): s._p = p
            def json(s): return s._p
            def raise_for_status(s): pass
        return R(self.payloads[url.rsplit('/', 1)[-1]])


def test_tradier_picks_whole_dollar_strike_nearest_065_delta():
    d = date(2026, 11, 13); exp = K.target_expiry(d)
    chain = [{'symbol': f'SPY_{k}', 'strike': k, 'option_type': 'call', 'bid': 10.0, 'ask': 10.04, 'greeks': {'delta': dl}}
             for k, dl in ((670, 0.72), (672, 0.66), (672.5, 0.65), (675, 0.58))]
    chain += [{'symbol': 'SPY_P', 'strike': 690, 'option_type': 'put', 'bid': 9.0, 'ask': 9.1, 'greeks': {'delta': -0.64}},
              {'symbol': 'SPY_nogreeks', 'strike': 673, 'option_type': 'call', 'bid': 1, 'ask': 2, 'greeks': None}]
    http = FakeHTTP({'expirations': {'expirations': {'date': [(exp - timedelta(days=7)).isoformat(), exp.isoformat()]}},
                     'chains': {'options': {'option': chain}}, 'quotes': {'quotes': {'quote': {'symbol': 'SPY_672', 'bid': 10.5, 'ask': 10.56}}}})
    td = Tradier('tok', http)
    c = td.select('SPY', d, 1)
    assert c['symbol'] == 'SPY_672' and c['expiry'] == exp.isoformat() and c['bid'] == 10.0 and c['ask'] == 10.04
    assert td.select('SPY', d, -1)['symbol'] == 'SPY_P'
    assert td.bid_ask('SPY_672') == {'bid': 10.5, 'ask': 10.56}
    assert http.calls[1][1]['greeks'] == 'true' and http.calls[1][1]['expiration'] == exp.isoformat()


def _trade(setup='S1', side=1, exit_min=None, reason=''):
    return Trade(setup, 'SPY', side, 11, 60, exit_min, 120, reason, 682.41, 681.90 if setup == 'S1' else 680.77, 680.77, 682.45, None)


class DV:
    ub = np.full(78, 681.9); lb = np.full(78, 676.0); vw = np.full(78, 680.77); bb_upper = 684.0; bb_lower = 682.6


def test_alert_text():
    live = {'symbol': 'SPY261127C00672000', 'expiry': '2026-11-27', 'strike': 672.0, 'delta': 0.66, 'bid': 12.31, 'ask': 12.36, 'source': 'tradier'}
    m = A.fmt_entry(_trade(), DV, live)
    assert 'S1 Noise Area + VWAP' in m and 'SPY **CALL**' in m and '10:30 ET' in m and 'out by 11:30 ET' in m
    assert 'bid 12.31 / ask 12.36' in m and 'spread $0.05' in m and 'NEGATIVE EXPECTANCY' in m and len(m) <= 1900
    assert '-4.42' in m                                           # +1.88 - (5.00 + 1.30)
    m2 = A.fmt_entry(_trade('S2', -1), DV, {'expiry': '2026-11-27', 'strike': 690.0, 'delta': -0.65, 'mid': 11.0, 'iv': 0.2, 'source': 'model'}, late_s=400)
    assert 'PUT' in m2 and 'upper Bollinger band 684.00' in m2 and 'LATE by 6.7 min' in m2 and 'model, no live quote' in m2 and 'time exit only' in m2
    x = A.fmt_exit(_trade(exit_min=80, reason='stop'), 680.10, live, {'bid': 11.80, 'ask': 11.85})
    assert 'stop' in x and '10:50 ET' in x and '-56 dollars' in x and 'bp' in x
    assert 'none found' in A.fmt_entry(_trade(), DV, None)


def test_state_roundtrip_and_ledger(tmp_path):
    d = date(2026, 11, 13)
    S.save_sent(str(tmp_path), d, {'a': {'minute': 1}}); assert S.load_sent(str(tmp_path), d) == {'a': {'minute': 1}}
    assert S.load_sent(str(tmp_path), date(2026, 11, 14)) == {}
    row = {'date': '2026-11-13', 'setup': 'S1', 'ticker': 'SPY', 'side': 'CALL', 'entry_time': '10:30', 'gross_bp': 2.5}
    S.replace_day_in_ledger(str(tmp_path), d, [row]); S.replace_day_in_ledger(str(tmp_path), d, [row])
    led = S.read_ledger(str(tmp_path)); assert len(led) == 1 and led[0]['gross_bp'] == '2.5' and led[0]['opt_pnl_usd'] == ''
    assert S.git_commit_push([str(tmp_path)], 'x') is False       # disabled outside Actions


def test_process_posts_each_event_once(monkeypatch):
    """Run the live step on a real fixture day: every event is posted once, at its own boundary, and never again."""
    HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'verification')
    fx = np.load(os.path.join(HERE, 'fixtures.npz')); exp = json.load(open(os.path.join(HERE, 'expected_trades.json')))
    day_s = next(e['day'] for e in exp['trades'] if e['ticker'] == 'SPY' and e['setup'] == 'S1')
    dates = [str(x) for x in fx['SPY_dates']]; d = dates.index(day_s); a = {k: fx[f'SPY_{k}'] for k in 'ohlcv'}
    past = [PastSession(dates[i], float(a['o'][i, 0]), a['c'][i].reshape(78, 5)[:, 4].copy()) for i in range(d - 20, d)]
    posted = []
    monkeypatch.setattr(A, 'post', lambda text: posted.append(text) or True)
    monkeypatch.setattr(C, 'TICKERS', ['SPY'])
    monkeypatch.setattr(R, '_bars', lambda ctx, tk, upto, deadline: (D.DayBars('SPY', ctx.day, a['o'][d][:upto], a['h'][d][:upto], a['l'][d][:upto], a['c'][d][:upto], a['v'][d][:upto], n=upto, last_real=upto - 1), False))
    monkeypatch.setattr(R, 'now_et', lambda: datetime(2020, 1, 1, tzinfo=NY))
    ctx = R.Context.__new__(R.Context); ctx.http = None; ctx.day = date.fromisoformat(day_s); ctx.fomc = day_s in exp['fomc_dates']
    ctx.hist = {'SPY': past}; ctx.status = {'SPY': {'ok': True}}; ctx.vol = {'SPY': 18.0}; ctx.rate = 0.04
    sent = {}
    for M in R.BOUNDARIES:
        R.process(ctx, None, sent, M, 0.0); R.process(ctx, None, sent, M, 0.0)
    want = [e for e in exp['trades'] if e['ticker'] == 'SPY' and e['day'] == day_s]
    assert len(posted) == 2 * len(want) and len([k for k in sent if k.endswith(':entry')]) == len(want)
    for e in want:
        assert sent[f"{e['setup']}:SPY:{e['entry_min']}:entry"]['minute'] == e['entry_min']
        assert sent[f"{e['setup']}:SPY:{e['entry_min']}:exit"]['minute'] == e['exit_min']
    assert all(s['info']['source'] == 'model' for k, s in sent.items() if k.endswith(':entry'))


def test_failed_post_is_retried_not_recorded(monkeypatch):
    HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'verification')
    fx = np.load(os.path.join(HERE, 'fixtures.npz')); exp = json.load(open(os.path.join(HERE, 'expected_trades.json')))
    e0 = next(e for e in exp['trades'] if e['ticker'] == 'QQQ' and e['setup'] == 'S1')
    dates = [str(x) for x in fx['QQQ_dates']]; d = dates.index(e0['day']); a = {k: fx[f'QQQ_{k}'] for k in 'ohlcv'}
    past = [PastSession(dates[i], float(a['o'][i, 0]), a['c'][i].reshape(78, 5)[:, 4].copy()) for i in range(d - 20, d)]
    answers = iter([False] + [True] * 50); posted = []
    monkeypatch.setattr(A, 'post', lambda text: (posted.append(text) or True) and next(answers))
    monkeypatch.setattr(C, 'TICKERS', ['QQQ'])
    monkeypatch.setattr(R, '_bars', lambda ctx, tk, upto, deadline: (D.DayBars('QQQ', ctx.day, a['o'][d][:upto], a['h'][d][:upto], a['l'][d][:upto], a['c'][d][:upto], a['v'][d][:upto], n=upto, last_real=upto - 1), False))
    monkeypatch.setattr(R, 'now_et', lambda: session_open(date.fromisoformat(e0['day'])) + timedelta(minutes=e0['entry_min'], seconds=10))
    ctx = R.Context.__new__(R.Context); ctx.http = None; ctx.day = date.fromisoformat(e0['day']); ctx.fomc = False
    ctx.hist = {'QQQ': past}; ctx.status = {'QQQ': {'ok': True}}; ctx.vol = {'QQQ': 22.0}; ctx.rate = 0.04
    sent = {}; key = f"S1:QQQ:{e0['entry_min']}:entry"
    R.process(ctx, None, sent, e0['entry_min'], 0.0); assert key not in sent and len(posted) == 1
    R.process(ctx, None, sent, e0['entry_min'], 0.0); assert key in sent and len(posted) == 2
