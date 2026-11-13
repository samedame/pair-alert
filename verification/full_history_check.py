"""Faithfulness check against the backtest (needs the research workspace: cleaned minute arrays and trade ledgers).

1. Replays every session 2008-01-02..2026-03-31 for SPY, QQQ, IWM through the BOT's engine (final bars) and compares the
   trades with the backtest ledgers: S1 vs the earlier study's rule (REF_loose), S2 vs the tournament winner (VWAP+BB_R, 60 min).
2. Streaming: on a random sample of sessions, feeds the bars boundary by boundary and checks that every event the bot
   would have announced at each 5-minute boundary is exactly the final event (nothing is revised, nothing is missed).
Usage: python verification/full_history_check.py /path/to/tour
"""
import json, sys, os, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pairbot.engine import evaluate
from pairbot.indicators import PastSession
from pairbot import config as C

TOUR = sys.argv[1] if len(sys.argv) > 1 else '/home/claude/tour'
TK = ['SPY', 'QQQ', 'IWM']; K = 20
fomc_all = set(json.load(open(f'{TOUR}/calib/fomc_dates.json')))
ref = {'S1': dict(np.load(f'{TOUR}/results/trades/REF_loose.npz')), 'S2': dict(np.load(f'{TOUR}/results/trades/VWAP__BB_R.npz'))}
ref['S2'] = {k: v[ref['S2']['hold'] == 60] for k, v in ref['S2'].items()}
res = {'sessions': 0, 'S1': {'bot': 0, 'backtest': 0, 'mismatch_days': 0, 'max_bp_diff': 0.0}, 'S2': {'bot': 0, 'backtest': 0, 'mismatch_days': 0, 'max_bp_diff': 0.0},
       'stream': {'days': 0, 'boundaries': 0, 'events': 0, 'violations': 0}}
rng = np.random.default_rng(20261112); t0 = time.time()
for ii, tk in enumerate(TK):
    z = np.load(f'{TOUR}/data/arrays/{tk}.npz'); a = {k: z[k] for k in 'ohlcv'}; dates = z['dates'].astype(str); D = len(dates)
    sess = [PastSession(dates[d], float(a['o'][d, 0]), a['c'][d].reshape(78, 5)[:, 4].copy()) for d in range(D)]
    exp = {}
    for s in ('S1', 'S2'):
        e = ref[s]; m = e['inst'] == ii
        for d, j, side, me, mx, g in zip(e['d'][m], e['j'][m], e['side'][m], e['me'][m], e['mx'][m], e['g'][m]):
            exp.setdefault((s, int(d)), []).append((int(j), int(side), int(me), int(mx), float(g)))
    sample = set(rng.choice(np.arange(1, D), 120, replace=False).tolist())
    for d in range(1, D):
        hist = sess[max(0, d - K):d]; fomc = dates[d] in fomc_all
        o, h, l, c, v = (a[k][d] for k in 'ohlcv')
        trades, _ = evaluate(tk, hist, o, h, l, c, v, 390, fomc)
        res['sessions'] += 1
        for s in ('S1', 'S2'):
            got = [(t.bar, t.side, t.entry_min, t.exit_min, t.gross_bp) for t in trades if t.setup == s]
            want = exp.get((s, d), [])
            res[s]['bot'] += len(got); res[s]['backtest'] += len(want)
            if [x[:4] for x in got] != [x[:4] for x in want]:
                res[s]['mismatch_days'] += 1
                if res[s]['mismatch_days'] <= 3: print('MISMATCH', tk, dates[d], s, got, want)
            else:
                for x, y in zip(got, want): res[s]['max_bp_diff'] = max(res[s]['max_bp_diff'], abs(x[4] - y[4]))
        if d in sample:
            final = {}
            for t in trades:
                final[(t.key, 'in')] = (t.entry_min, t.side)
                final[(t.key, 'out')] = (t.exit_min, t.exit_reason)
            seen = {}
            for M in range(30, 386, 5):
                tr, _ = evaluate(tk, hist, o[:M], h[:M], l[:M], c[:M], v[:M], M, fomc)
                res['stream']['boundaries'] += 1
                now = {}
                for t in tr:
                    now[(t.key, 'in')] = (t.entry_min, t.side)
                    if t.exit_min is not None: now[(t.key, 'out')] = (t.exit_min, t.exit_reason)
                for k_, val in now.items():
                    if val[0] > M or final.get(k_) != val: res['stream']['violations'] += 1      # announced something that is not final
                    seen.setdefault(k_, M)
                for k_, val in final.items():                                                    # due by now but not announced
                    if val[0] is not None and val[0] <= M and k_ not in now: res['stream']['violations'] += 1
                for k_ in seen:
                    if k_ not in now: res['stream']['violations'] += 1                           # announced earlier, gone now
            res['stream']['days'] += 1; res['stream']['events'] += len(final)
            for k_, val in final.items():
                if seen.get(k_) != val[0]: res['stream']['violations'] += 1                      # not announced at its own boundary
    print(tk, 'done', round(time.time() - t0, 1), 's', flush=True)
res['pass'] = all(res[s]['mismatch_days'] == 0 and res[s]['bot'] == res[s]['backtest'] for s in ('S1', 'S2')) and res['stream']['violations'] == 0
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'full_history_result.json'), 'w'), indent=1)
print(json.dumps(res, indent=1))
