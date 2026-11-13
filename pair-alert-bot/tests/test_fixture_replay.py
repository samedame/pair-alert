"""The bot's engine must reproduce the backtest's trades on real data (80 sessions x 3 ETFs, Dec 2025 - Mar 2026).
If this fails, the bot no longer trades the strategy that was backtested."""
import json, os
import numpy as np
import pytest
from pairbot.engine import evaluate
from pairbot.indicators import PastSession

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'verification')
FX = np.load(os.path.join(HERE, 'fixtures.npz'))
EXP = json.load(open(os.path.join(HERE, 'expected_trades.json')))
FOMC = set(EXP['fomc_dates'])
TK = ['SPY', 'QQQ', 'IWM']


def sessions(tk):
    a = {k: FX[f'{tk}_{k}'] for k in 'ohlcv'}; dates = [str(x) for x in FX[f'{tk}_dates']]
    past = [PastSession(dates[d], float(a['o'][d, 0]), a['c'][d].reshape(78, 5)[:, 4].copy()) for d in range(len(dates))]
    return a, dates, past


@pytest.mark.parametrize('tk', TK)
def test_final_bar_replay_matches_backtest(tk):
    a, dates, past = sessions(tk)
    want = {}
    for e in EXP['trades']:
        if e['ticker'] == tk:
            want.setdefault((e['setup'], e['day']), []).append((e['bar'], e['side'], e['entry_min'], e['exit_min'], e['gross_bp']))
    n = 0
    for d in range(20, len(dates)):
        trades, _ = evaluate(tk, past[d - 20:d], *(a[k][d] for k in 'ohlcv'), 390, dates[d] in FOMC)
        for s in ('S1', 'S2'):
            got = [(t.bar, t.side, t.entry_min, t.exit_min, t.gross_bp) for t in trades if t.setup == s]
            exp = want.get((s, dates[d]), [])
            assert [g[:4] for g in got] == [e[:4] for e in exp], (tk, dates[d], s)
            for g, e in zip(got, exp):
                assert abs(g[4] - e[4]) < 1e-9
            n += len(got)
    assert n > 50


@pytest.mark.parametrize('tk', TK)
def test_streaming_never_revises_or_misses(tk):
    a, dates, past = sessions(tk)
    for d in range(20, len(dates), 7):
        o, h, l, c, v = (a[k][d] for k in 'ohlcv'); fomc = dates[d] in FOMC
        final, _ = evaluate(tk, past[d - 20:d], o, h, l, c, v, 390, fomc)
        fin = {}
        for t in final:
            fin[(t.key, 'in')] = t.entry_min; fin[(t.key, 'out')] = t.exit_min
        first_seen = {}
        for M in range(30, 386, 5):
            now, _ = evaluate(tk, past[d - 20:d], o[:M], h[:M], l[:M], c[:M], v[:M], M, fomc)
            cur = {}
            for t in now:
                cur[(t.key, 'in')] = t.entry_min
                if t.exit_min is not None:
                    cur[(t.key, 'out')] = t.exit_min
            for k, m in cur.items():
                assert m <= M and fin.get(k) == m
                first_seen.setdefault(k, M)
            for k, m in fin.items():
                assert not (m is not None and m <= M and k not in cur)
        assert first_seen == {k: m for k, m in fin.items() if m is not None}
