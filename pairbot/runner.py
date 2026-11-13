"""Live loop and end-of-day reconciliation.

live: wakes a few seconds after every 5-minute boundary from 10:00 to 15:55 ET, pulls completed 1-minute bars, asks the
      engine for every event known so far and posts the ones not yet sent. A late start or a restart is harmless: the
      engine replays the day and the sent ledger de-duplicates.
eod : replays the session on the final bars, writes the trade ledger and the forward-test scorecard, posts a summary.
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import requests

from . import alerts as A
from . import config as C
from . import data as D
from . import state as S
from .calendar_nyse import NY, clock, is_full_session, now_et, session_open
from .contracts import house_spread, model_contract
from .engine import evaluate
from .tradier import Tradier

BOUNDARIES = list(range(30, C.EOD_MIN + 1, 5))      # 10:00 .. 15:55 ET


class Context:
    def __init__(self, http: requests.Session, day: date):
        self.http, self.day = http, day
        self.fomc = day in C.FOMC_DATES
        self.hist: Dict[str, list] = {}
        self.status: Dict[str, dict] = {}
        self.vol: Dict[str, Optional[float]] = {}
        self.rate = 0.04
        for tk in C.TICKERS:
            try:
                h = D.load_history(http, tk, day)
                from .indicators import valid_sessions
                self.hist[tk] = h
                self.status[tk] = {'ok': len(h) >= 1, 'sessions': len(h), 'noise': len(valid_sessions(h)) >= C.NOISE_MIN_SESSIONS,
                                   'reason': '' if h else 'no earlier sessions from the feed'}
            except D.FeedError as e:
                self.hist[tk] = []
                self.status[tk] = {'ok': False, 'sessions': 0, 'noise': False, 'reason': f'history feed error: {e}'}
            self.vol[tk] = D.prev_vol_index(http, C.VOL_INDEX[tk], day)
        irx = D.prev_daily_close(http, '^IRX', day)
        if irx and 0 < irx < 20:
            self.rate = irx / 100.0


def _bars(ctx: Context, tk: str, upto: int, deadline: float):
    """Today's bars with every minute before `upto` present, waiting for the feed until `deadline`. Returns (bars, padded)."""
    b = None
    while True:
        try:
            b = D.to_grid(D.fetch_today(ctx.http, tk), tk, ctx.day, upto)
        except D.FeedError as e:
            b = D.DayBars(tk, ctx.day, *([np.array([])] * 5), ok=False, reason=str(e))
        if b.ok and b.last_real >= upto - 1:
            return b, False
        if time.time() >= deadline:
            break
        time.sleep(C.POLL_RETRY_S)
    if b is not None and b.ok:
        return D.pad_to(b, upto), True
    return b, True


def _entry_info(ctx: Context, td: Optional[Tradier], t, px: float) -> Optional[dict]:
    if td is not None:
        try:
            info = td.select(t.ticker, ctx.day, t.side)
            if info:
                return info
        except Exception as e:                      # quotes are optional: never let them stop an alert
            print(f'tradier select failed for {t.ticker}: {e!r}')
    hist = ctx.hist.get(t.ticker) or []
    if hist and ctx.vol.get(t.ticker):
        try:
            return model_contract(t.ticker, ctx.day, t.entry_min, t.side, px, hist[-1].close, ctx.vol[t.ticker], ctx.rate)
        except (ValueError, ZeroDivisionError):
            return None
    return None


def _exit_quote(td: Optional[Tradier], entry: dict) -> Optional[dict]:
    if td is None or not entry or entry.get('source') != 'tradier':
        return None
    try:
        return td.bid_ask(entry['symbol'])
    except Exception as e:
        print(f'tradier quote failed for {entry.get("symbol")}: {e!r}')
        return None


def process(ctx: Context, td: Optional[Tradier], sent: Dict[str, dict], M: int, deadline: float) -> bool:
    """Evaluate every ticker with minutes 0..M-1 and post the events not yet sent. Returns True if anything was sent."""
    changed = False
    events = []
    for tk in C.TICKERS:
        if not ctx.status[tk]['ok']:
            continue
        b, padded = _bars(ctx, tk, M, deadline)
        if b is None or not b.ok:
            key = f'feed:{tk}'
            if key not in sent:
                A.post(f'⚠ PAIR-BOT {tk}: no usable bars at {clock(M)} ET ({b.reason if b is not None else "feed error"}); skipping this check.')
                sent[key] = {'minute': M}; changed = True
            continue
        trades, dv = evaluate(tk, ctx.hist[tk], b.o, b.h, b.l, b.c, b.v, M, ctx.fomc, C.ENABLED_SETUPS)
        for t in trades:
            events.append((t.entry_min, 0, t, dv, b, padded))
            if t.exit_min is not None:
                events.append((t.exit_min, 1, t, dv, b, padded))
    for minute, kind, t, dv, b, padded in sorted(events, key=lambda e: (e[0], e[1], e[2].setup, e[2].ticker)):
        key = f"{t.key}:{'exit' if kind else 'entry'}"
        if key in sent:
            continue
        late_s = max(0.0, (now_et() - (session_open(ctx.day) + timedelta(minutes=minute))).total_seconds())
        if kind == 0:
            info = _entry_info(ctx, td, t, t.signal_close)
            if not A.post(A.fmt_entry(t, dv, info, late_s, padded)):
                continue                                # Discord refused it: try again at the next boundary (it will be flagged late)
            sent[key] = {'minute': minute, 'late_s': round(late_s, 1), 'padded': padded, 'info': info}
        else:
            entry = (sent.get(f'{t.key}:entry') or {}).get('info')
            q = _exit_quote(td, entry)
            if not A.post(A.fmt_exit(t, float(b.c[min(minute, b.n) - 1]), entry, q, late_s, padded)):
                continue
            sent[key] = {'minute': minute, 'late_s': round(late_s, 1), 'padded': padded, 'quote': q, 'reason': t.exit_reason}
        changed = True
    return changed


def run_live(state_dir: str = 'state', ledger_dir: str = 'ledger', max_runtime_min: float = 330.0) -> str:
    """Returns 'done' or 'more' (the caller should start a fresh run to finish the session)."""
    t0 = time.time()
    now = now_et(); day = now.date()
    if not is_full_session(day):
        print(f'{day}: not a full NYSE session, nothing to do'); return 'done'
    mins = (now - session_open(day)).total_seconds() / 60.0
    if mins < -30:
        print('more than 30 minutes before the open (the off-season backup cron): exiting'); return 'done'
    if mins > C.EOD_MIN + 20:
        print('session is over: run `python -m pairbot eod`'); return 'done'
    if day > C.FOMC_LAST_COVERED:
        A.post('⚠ PAIR-BOT: the FOMC and holiday lists in config.py and calendar_nyse.py have run out; update them.')
    http = requests.Session(); td = Tradier.from_env()
    ctx = Context(http, day)
    sent = S.load_sent(state_dir, day)
    if 'status:start' not in sent:
        A.post(A.fmt_start(day, ctx.status, ctx.fomc, td is not None))
        sent['status:start'] = {'minute': 0}; S.save_sent(state_dir, day, sent)
        S.git_commit_push([state_dir], f'pair-bot start {day}')
    for i, M in enumerate(BOUNDARIES):
        due = session_open(day).timestamp() + M * 60 + C.POLL_DELAY_S
        nxt = session_open(day).timestamp() + BOUNDARIES[i + 1] * 60 + C.POLL_DELAY_S if i + 1 < len(BOUNDARIES) else None
        if nxt is not None and time.time() >= nxt:
            continue                                   # late start: jump to the most recent boundary, it replays the rest
        if (due - t0) / 60.0 > max_runtime_min:
            print(f'runtime budget reached before {clock(M)} ET: handing off'); return 'more'
        wait = due - time.time()
        if wait > 0:
            time.sleep(wait)
        if process(ctx, td, sent, M, due + C.POLL_GIVE_UP_S - C.POLL_DELAY_S):
            S.save_sent(state_dir, day, sent)
            S.git_commit_push([state_dir], f'pair-bot events {day} {clock(M)}')
        print(f'{clock(M)} ET checked', flush=True)
    time.sleep(max(0.0, session_open(day).timestamp() + (C.EOD_MIN + 2) * 60 + 30 - time.time()))
    run_eod(day, state_dir, ledger_dir)
    return 'done'


# ----------------------------------------------------------------------------------------------- end of day
def scorecard(rows: List[dict]) -> Dict[str, dict]:
    out = {}
    for k in C.SETUPS:
        r = [x for x in rows if x['setup'] == k and x.get('gross_bp') not in (None, '')]
        g = [float(x['gross_bp']) for x in r]
        o = [x for x in r if x.get('opt_pnl_usd') not in (None, '')]
        out[k] = {'n': len(g), 'mean_bp': sum(g) / len(g) if g else 0.0, 'win': sum(1 for v in g if v > 0) / len(g) if g else 0.0,
                  'n_opt': len(o), 'mean_opt': sum(float(x['opt_pnl_usd']) for x in o) / len(o) if o else 0.0,
                  'mean_spread': sum(float(x['entry_spread']) for x in o) / len(o) if o else 0.0}
    return out


def write_scorecard(ledger_dir: str, card: Dict[str, dict], n_days: int) -> None:
    L = ['# pair-alert-bot forward test', '', f'Sessions in the ledger: {n_days}. Updated after every session by the bot.', '',
         '| Setup | Forward trades | Mean gross bp | Trades up | Trades with live quotes | Mean option result at live quotes (dollars per contract, after commission) | Mean entry spread | Backtest gross bp | Backtest at zero cost | Backtest at house spread |',
         '|---|---|---|---|---|---|---|---|---|---|']
    for k, c in card.items():
        b = C.SETUPS[k]
        L.append(f"| {k} {b['name']} | {c['n']} | {c['mean_bp']:+.2f} | {c['win']:.0%} | {c['n_opt']} | "
                 f"{(format(c['mean_opt'], '+.2f') if c['n_opt'] else 'n/a')} | {(format(c['mean_spread'], '.2f') if c['n_opt'] else 'n/a')} | "
                 f"{b['gross_bp']:+.2f} | {b['usd_zero_cost']:+.2f} | {b['usd_house']:+.2f} |")
    L += ['', 'Backtest columns are out-of-sample 2022-01 to 2026-03, Black-Scholes proxy. A forward mean needs a few hundred trades '
              'before it says anything: the backtest standard error was about 0.7 bp on 3,089 trades.', '']
    os.makedirs(ledger_dir, exist_ok=True)
    with open(os.path.join(ledger_dir, 'scorecard.md'), 'w') as fh:
        fh.write('\n'.join(L))


def run_eod(day: Optional[date] = None, state_dir: str = 'state', ledger_dir: str = 'ledger', force: bool = False) -> None:
    day = day or now_et().date()
    if not is_full_session(day):
        print(f'{day}: not a full NYSE session'); return
    now = now_et()
    if day == now.date() and (now - session_open(day)).total_seconds() < (C.EOD_MIN + 2) * 60:
        print('the session has not finished yet (before 15:57 ET): nothing written'); return
    http = requests.Session()
    ctx = Context(http, day)
    sent = S.load_sent(state_dir, day)
    rows, notes, final_keys, evaluated = [], [], set(), 0
    for tk in C.TICKERS:
        if not ctx.status[tk]['ok']:
            notes.append(f'{tk}: no history today ({ctx.status[tk]["reason"]}), not evaluated'); continue
        try:
            b = D.to_grid(D.fetch_today(http, tk) if day == now_et().date() else D.fetch_range(http, tk, day, day), tk, day, C.SESSION_MIN)
        except D.FeedError as e:
            notes.append(f'{tk}: final bars unavailable ({e})'); continue
        if not b.ok or b.n < C.EOD_MIN + 1:
            notes.append(f'{tk}: final bars incomplete ({b.reason or str(b.n) + " minutes"}), not evaluated'); continue
        trades, _ = evaluate(tk, ctx.hist[tk], b.o, b.h, b.l, b.c, b.v, b.n, ctx.fomc, C.ENABLED_SETUPS)
        evaluated += 1
        for t in trades:
            e = sent.get(f'{t.key}:entry') or {}; x = sent.get(f'{t.key}:exit') or {}
            info = e.get('info') or {}; q = x.get('quote') or {}
            final_keys.add(t.key)
            live = info.get('source') == 'tradier' and info.get('ask') is not None and q.get('bid') is not None
            mid_ok = live and info.get('bid') is not None and q.get('ask') is not None
            rows.append({
                'date': day.isoformat(), 'setup': t.setup, 'ticker': t.ticker, 'side': 'CALL' if t.side > 0 else 'PUT',
                'signal_time': clock(5 * (t.bar + 1)), 'entry_time': clock(t.entry_min), 'exit_time': clock(t.exit_min) if t.exit_min is not None else '',
                'exit_reason': t.exit_reason, 'entry_px': None if t.entry_px is None else round(t.entry_px, 4),
                'exit_px': None if t.exit_px is None else round(t.exit_px, 4),
                'gross_bp': None if t.gross_bp is None else round(t.gross_bp, 3),
                'contract': info.get('symbol', ''), 'expiry': info.get('expiry', ''), 'strike': info.get('strike', ''),
                'delta': info.get('delta', ''), 'quote_source': info.get('source', ''),
                'entry_bid': info.get('bid'), 'entry_ask': info.get('ask'), 'exit_bid': q.get('bid'), 'exit_ask': q.get('ask'),
                'entry_spread': round(info['ask'] - info['bid'], 4) if live and info.get('bid') is not None else None,
                'house_spread': round(house_spread(t.ticker, t.signal_close), 4),
                'opt_pnl_usd': round((q['bid'] - info['ask']) * 100 - 2 * C.COMMISSION_PER_SIDE, 2) if live else None,
                'opt_pnl_mid_usd': round(((q['bid'] + q['ask']) / 2 - (info['bid'] + info['ask']) / 2) * 100, 2) if mid_ok else None,
                'alerted': 'yes' if e else 'no', 'late_s': e.get('late_s', ''),
            })
    if evaluated == 0:
        print('eod: no ticker had complete final bars; nothing written\n' + '\n'.join(notes)); return
    alerted = {k.rsplit(':', 1)[0] for k in sent if k.endswith(':entry')}
    if 'status:start' not in sent:
        notes.append('The live loop did not run this session; this summary comes from the final bars only.')
    else:
        for k in sorted(alerted - final_keys):
            notes.append(f'⚠ alerted live but not in the final-bar replay: {k} (the feed revised a bar)')
        for k in sorted(final_keys - alerted):
            notes.append(f'⚠ in the final-bar replay but never alerted: {k}')
    allrows = S.replace_day_in_ledger(ledger_dir, day, rows)
    card = scorecard(allrows)
    write_scorecard(ledger_dir, card, len({r['date'] for r in allrows}))
    if force or 'summary' not in sent:
        A.post(A.fmt_summary(day, rows, card, notes))
        sent['summary'] = {'minute': C.SESSION_MIN, 'trades': len(rows)}
        S.save_sent(state_dir, day, sent)
    S.git_commit_push([state_dir, ledger_dir], f'pair-bot eod {day}')
    print(f'eod {day}: {len(rows)} trade(s) written')
