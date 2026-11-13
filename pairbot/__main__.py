"""python -m pairbot live | eod [--date YYYY-MM-DD] [--force] | replay --date YYYY-MM-DD"""
from __future__ import annotations

import argparse
import os
from datetime import date

from . import config as C
from . import runner


def main() -> None:
    p = argparse.ArgumentParser(prog='pairbot')
    sub = p.add_subparsers(dest='cmd', required=True)
    lv = sub.add_parser('live'); lv.add_argument('--max-runtime-min', type=float, default=330.0)
    ed = sub.add_parser('eod'); ed.add_argument('--date', default=''); ed.add_argument('--force', action='store_true')
    rp = sub.add_parser('replay'); rp.add_argument('--date', required=True)
    for s in (lv, ed, rp):
        s.add_argument('--state-dir', default='state'); s.add_argument('--ledger-dir', default='ledger')
    a = p.parse_args()
    if a.cmd == 'live':
        res = runner.run_live(a.state_dir, a.ledger_dir, a.max_runtime_min)
        out = os.environ.get('GITHUB_OUTPUT')
        if out:
            with open(out, 'a') as fh:
                fh.write(f"more={'true' if res == 'more' else 'false'}\n")
    elif a.cmd == 'eod':
        runner.run_eod(date.fromisoformat(a.date) if a.date else None, a.state_dir, a.ledger_dir, a.force)
    else:
        replay(date.fromisoformat(a.date))


def replay(day: date) -> None:
    """Print every alert the bot would have sent on a past session (within Yahoo's 30-day window). Posts nothing."""
    import requests
    from . import alerts as A, data as D
    from .engine import evaluate
    C.DRY_RUN = True
    http = requests.Session()
    ctx = runner.Context(http, day)
    for tk in C.TICKERS:
        if not ctx.status[tk]['ok']:
            print(f'{tk}: {ctx.status[tk]["reason"]}'); continue
        b = D.to_grid(D.fetch_range(http, tk, day, day), tk, day, C.SESSION_MIN)
        if not b.ok:
            print(f'{tk}: {b.reason}'); continue
        seen = set()
        for M in runner.BOUNDARIES:
            trades, dv = evaluate(tk, ctx.hist[tk], b.o, b.h, b.l, b.c, b.v, min(M, b.n), ctx.fomc, C.ENABLED_SETUPS)
            for t in trades:
                if (t.key, 'in') not in seen:
                    seen.add((t.key, 'in')); A.post(A.fmt_entry(t, dv, runner._entry_info(ctx, None, t, t.signal_close)))
                if t.exit_min is not None and (t.key, 'out') not in seen:
                    seen.add((t.key, 'out')); A.post(A.fmt_exit(t, float(b.c[min(t.exit_min, b.n) - 1]), None, None))
        final, _ = evaluate(tk, ctx.hist[tk], b.o, b.h, b.l, b.c, b.v, b.n, ctx.fomc, C.ENABLED_SETUPS)
        for t in final:
            print(f'FINAL {t.setup} {tk} side {t.side:+d} in {t.entry_min} out {t.exit_min} ({t.exit_reason}) '
                  f'gross {t.gross_bp if t.gross_bp is None else round(t.gross_bp, 2)} bp')


if __name__ == '__main__':
    main()
