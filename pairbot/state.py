"""Persistence: sent-event ledger (de-duplication across restarts), trade ledger, git commit helper."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import time
from datetime import date
from typing import Dict, Iterable, List

LEDGER_COLUMNS = ['date', 'setup', 'ticker', 'side', 'signal_time', 'entry_time', 'exit_time', 'exit_reason',
                  'entry_px', 'exit_px', 'gross_bp', 'contract', 'expiry', 'strike', 'delta', 'quote_source',
                  'entry_bid', 'entry_ask', 'exit_bid', 'exit_ask', 'entry_spread', 'house_spread',
                  'opt_pnl_usd', 'opt_pnl_mid_usd', 'alerted', 'late_s']


def sent_path(state_dir: str, d: date) -> str:
    return os.path.join(state_dir, f'sent-{d.isoformat()}.json')


def load_sent(state_dir: str, d: date) -> Dict[str, dict]:
    try:
        with open(sent_path(state_dir, d)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_sent(state_dir: str, d: date, sent: Dict[str, dict]) -> None:
    os.makedirs(state_dir, exist_ok=True)
    tmp = sent_path(state_dir, d) + '.tmp'
    with open(tmp, 'w') as fh:
        json.dump(sent, fh, indent=1, sort_keys=True)
    os.replace(tmp, sent_path(state_dir, d))


def read_ledger(ledger_dir: str) -> List[dict]:
    p = os.path.join(ledger_dir, 'trades.csv')
    if not os.path.exists(p):
        return []
    with open(p, newline='') as fh:
        return list(csv.DictReader(fh))


def replace_day_in_ledger(ledger_dir: str, d: date, rows: Iterable[dict]) -> List[dict]:
    """The ledger holds the trades from the final-bar replay of each session (one block per date)."""
    os.makedirs(ledger_dir, exist_ok=True)
    keep = [r for r in read_ledger(ledger_dir) if r['date'] != d.isoformat()]
    keep += [{c: ('' if r.get(c) is None else r.get(c)) for c in LEDGER_COLUMNS} for r in rows]
    keep.sort(key=lambda r: (r['date'], r['entry_time'], r['setup'], r['ticker']))
    p = os.path.join(ledger_dir, 'trades.csv')
    with open(p + '.tmp', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
        w.writeheader(); w.writerows(keep)
    os.replace(p + '.tmp', p)
    return keep


def git_commit_push(paths: List[str], message: str, retries: int = 4) -> bool:
    """Commit and push state files (GitHub Actions only: enabled by PAIRBOT_GIT_PUSH=1). Never raises."""
    if os.environ.get('PAIRBOT_GIT_PUSH') != '1':
        return False
    try:
        subprocess.run(['git', 'config', 'user.name', 'pair-alert-bot'], check=True)
        subprocess.run(['git', 'config', 'user.email', 'pair-alert-bot@users.noreply.github.com'], check=True)
        subprocess.run(['git', 'add', '--'] + [p for p in paths if os.path.exists(p)], check=True)
        if subprocess.run(['git', 'diff', '--cached', '--quiet']).returncode == 0:
            return True
        subprocess.run(['git', 'commit', '-m', f'{message} [skip ci]'], check=True, capture_output=True)
        for i in range(retries):
            if subprocess.run(['git', 'push'], capture_output=True, text=True).returncode == 0:
                return True
            pull = subprocess.run(['git', 'pull', '--rebase', '--autostash'], capture_output=True, text=True)
            if pull.returncode != 0:
                subprocess.run(['git', 'rebase', '--abort'], capture_output=True)
            time.sleep(1.5 * (i + 1))
    except (subprocess.CalledProcessError, OSError) as e:
        print(f'git push failed: {e!r}')
    return False
