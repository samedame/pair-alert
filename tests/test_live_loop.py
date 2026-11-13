"""Simulated full session with a fake clock and a fake feed built from a real fixture day: start-up post, every event at
its own 5-minute boundary, end-of-day ledger, no duplicates after a restart, hand-off when the runtime budget runs out."""
import json, os
from datetime import date, datetime, timedelta
import numpy as np
from pairbot import alerts as A, config as C, data as D, runner as R, state as S
from pairbot.calendar_nyse import NY, session_open
from pairbot.indicators import PastSession

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'verification')
FX = np.load(os.path.join(HERE, 'fixtures.npz')); EXP = json.load(open(os.path.join(HERE, 'expected_trades.json')))


class FakeTime:
    def __init__(self, t): self.t = t
    def time(self): return self.t
    def sleep(self, x): self.t += max(0.0, x)


def _setup(monkeypatch, start_hm, tmp_path):
    counts = {}
    for e in EXP['trades']:
        counts[e['day']] = counts.get(e['day'], 0) + 1
    day_s = max(counts, key=counts.get); day = date.fromisoformat(day_s)
    ft = FakeTime(datetime.combine(day, datetime.min.time(), tzinfo=NY).timestamp() + start_hm[0] * 3600 + start_hm[1] * 60)
    posted = []
    monkeypatch.setattr(R, 'time', ft)
    monkeypatch.setattr(R, 'now_et', lambda: datetime.fromtimestamp(ft.t, NY))
    monkeypatch.setattr(A, 'post', lambda text: posted.append(text) or True)
    monkeypatch.setattr(R.Tradier, 'from_env', classmethod(lambda cls: None))
    arr = {}
    for tk in ('SPY', 'QQQ', 'IWM'):
        dates = [str(x) for x in FX[f'{tk}_dates']]; d = dates.index(day_s); a = {k: FX[f'{tk}_{k}'] for k in 'ohlcv'}
        arr[tk] = (a, d, [PastSession(dates[i], float(a['o'][i, 0]), a['c'][i].reshape(78, 5)[:, 4].copy()) for i in range(d - 20, d)])

    def fetch_today(http, tk):
        a, d, _ = arr[tk]; t0 = int(session_open(day).timestamp())
        done = int((ft.t - t0) // 60)                     # minutes fully elapsed
        return [(t0 + 60 * m, a['o'][d, m], a['h'][d, m], a['l'][d, m], a['c'][d, m], a['v'][d, m]) for m in range(max(0, min(done, 390)))]
    monkeypatch.setattr(D, 'fetch_today', fetch_today)

    class Ctx:
        def __init__(self, http, dd):
            self.http, self.day, self.fomc = http, dd, day_s in EXP['fomc_dates']
            self.hist = {tk: arr[tk][2] for tk in arr}; self.status = {tk: {'ok': True, 'sessions': 20, 'noise': True, 'reason': ''} for tk in arr}
            self.vol = {tk: 18.0 for tk in arr}; self.rate = 0.04
    monkeypatch.setattr(R, 'Context', Ctx)
    want = [e for e in EXP['trades'] if e['day'] == day_s]
    return day, ft, posted, want, str(tmp_path / 'state'), str(tmp_path / 'ledger')


def test_full_session_then_restart(monkeypatch, tmp_path):
    day, ft, posted, want, sd, ld = _setup(monkeypatch, (9, 22), tmp_path)
    assert R.run_live(sd, ld, 400) == 'done'
    sent = S.load_sent(sd, day)
    assert len(posted) == 1 + 2 * len(want) + 1 and posted[0].startswith('🟦') and posted[-1].startswith('📒')
    for e in want:
        k = f"{e['setup']}:{e['ticker']}:{e['entry_min']}"
        assert sent[k + ':entry']['minute'] == e['entry_min'] and sent[k + ':entry']['late_s'] < 60
        assert sent[k + ':exit']['minute'] == e['exit_min'] and sent[k + ':exit']['late_s'] < 60
    led = S.read_ledger(ld)
    assert len(led) == len(want) and all(r['alerted'] == 'yes' for r in led)
    got = sorted((r['setup'], r['ticker'], r['entry_time'], round(float(r['gross_bp']), 3)) for r in led)
    exp = sorted((e['setup'], e['ticker'], f"{(570 + e['entry_min']) // 60:02d}:{(570 + e['entry_min']) % 60:02d}", round(e['gross_bp'], 3)) for e in want)
    assert got == exp
    assert os.path.exists(os.path.join(ld, 'scorecard.md'))
    # restart the same afternoon: nothing is posted twice
    n = len(posted); ft.t = session_open(day).timestamp() + 300 * 60
    assert R.run_live(sd, ld, 400) == 'done' and len(posted) == n


def test_late_start_flags_and_handoff(monkeypatch, tmp_path):
    day, ft, posted, want, sd, ld = _setup(monkeypatch, (12, 1), tmp_path)
    assert R.run_live(sd, ld, 30) == 'more'               # budget ends about 12:31
    sent = S.load_sent(sd, day)
    early = [e for e in want if e['entry_min'] <= 140]
    assert early and all(sent[f"{e['setup']}:{e['ticker']}:{e['entry_min']}:entry"]['late_s'] > C.LATE_GRACE_S for e in early)
    assert any('LATE by' in p for p in posted) and not any(p.startswith('📒') for p in posted)
    assert not any(k.endswith(':entry') and v['minute'] > 185 for k, v in sent.items())
    assert R.run_live(sd, ld, 400) == 'done'              # the hand-off run finishes the day
    assert len(S.read_ledger(ld)) == len(want) and sum(p.startswith('🟦') for p in posted) == 1
