"""Discord message text and delivery. Messages are built only from engine output; nothing here changes a decision."""
from __future__ import annotations

import os
import time
from datetime import date
from typing import Dict, List, Optional

import requests

from . import config as C
from .calendar_nyse import clock
from .contracts import house_spread

DISCORD_LIMIT = 1900
_UA = {'User-Agent': 'pair-alert-bot (github actions)'}
FOOT = 'Research alert, not advice. Backtest label for this setup: NEGATIVE EXPECTANCY (PROXY).'


def _side(s: int) -> str:
    return 'CALL' if s > 0 else 'PUT'


def _exp(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f'{d.day} {d:%b}'


def contract_line(ticker: str, side: int, px: float, info: Optional[dict]) -> List[str]:
    """Contract, live or model quote, and what that spread does to the backtest's zero-cost edge."""
    if not info:
        return ['Contract: none found (no quote source); rule is the Friday 7-21 days out closest to 14, strike nearest 0.65 delta.']
    name = f"{ticker} {_exp(info['expiry'])} {info['strike']:g} {'C' if side > 0 else 'P'} (delta {abs(info['delta']):.2f})"
    hs = house_spread(ticker, px)
    if info.get('source') == 'tradier' and info.get('bid') is not None and info.get('ask') is not None:
        sp = info['ask'] - info['bid']
        return [f"Contract: {name} · bid {info['bid']:.2f} / ask {info['ask']:.2f} · spread ${sp:.2f} = {sp / hs:.2f}x the backtest's house spread (${hs:.2f})"]
    if info.get('source') == 'tradier':
        return [f'Contract: {name} · no two-sided quote right now']
    return [f"Contract (model, no live quote): {name} · model mid {info['mid']:.2f} · backtest house spread ${hs:.2f}"]


def cost_line(setup: str, ticker: str, px: float, info: Optional[dict]) -> str:
    edge = C.SETUPS[setup]['usd_zero_cost']
    comm = 2 * C.COMMISSION_PER_SIDE
    if info and info.get('source') == 'tradier' and info.get('bid') is not None and info.get('ask') is not None:
        sp, what = info['ask'] - info['bid'], 'this spread'
    else:
        sp, what = house_spread(ticker, px), 'the house spread'
    cost = sp * 100 + comm
    return (f'Cost check (model): zero-cost edge {edge:+.2f} dollars per contract; {what} plus commission costs '
            f'{cost:.2f} -> about {edge - cost:+.2f} per contract expected')


def _late(late_s: float, padded: bool) -> str:
    bits = []
    if late_s > C.LATE_GRACE_S:
        bits.append(f'LATE by {late_s / 60:.1f} min, prices have moved')
    if padded:
        bits.append('feed was lagging, decision made on padded bars')
    return ('  ⚠ ' + '; '.join(bits)) if bits else ''


def fmt_entry(t, dv, info: Optional[dict], late_s: float = 0.0, padded: bool = False) -> str:
    s = C.SETUPS[t.setup]
    j = t.bar
    head = '🟢' if (late_s <= C.LATE_GRACE_S and not padded) else '🟠'
    L = [f"{head} **PAIR-BOT ENTRY · {t.setup} {s['name']}** · {t.ticker} **{_side(t.side)}**{_late(late_s, padded)}"]
    if t.setup == 'S1':
        rel = 'above' if t.side > 0 else 'below'
        L.append(f'{clock(t.entry_min)} ET close {t.signal_close:.2f} is {rel} the noise band {t.line_a:.2f} and {rel} VWAP {t.line_b:.2f}')
        stop = min(dv.ub[j], dv.vw[j]) if t.side > 0 else max(dv.lb[j], dv.vw[j])
        L.append(f"Plan: in now · out by {clock(t.planned_exit_min)} ET · out earlier on a 5-min close {'below' if t.side > 0 else 'above'} "
                 f"the {'lower' if t.side > 0 else 'higher'} of band and VWAP (now {stop:.2f}, it moves)")
    else:
        band = dv.bb_lower if t.side > 0 else dv.bb_upper
        L.append(f"{clock(t.entry_min)} ET close {t.signal_close:.2f} is {'above' if t.side > 0 else 'below'} VWAP {t.line_a:.2f} and "
                 f"{'below the lower' if t.side > 0 else 'above the upper'} Bollinger band {band:.2f}")
        L.append(f'Plan: in now · out at {clock(t.planned_exit_min)} ET (time exit only, no price stop)')
    L += contract_line(t.ticker, t.side, t.signal_close, info)
    L.append(cost_line(t.setup, t.ticker, t.signal_close, info))
    L.append(FOOT)
    return '\n'.join(L)[:DISCORD_LIMIT]


_WHY = {'time': '60 minutes up', 'eod': '15:55 flat', 'fomc': 'FOMC day 13:55 flat', 'stop': 'stop: 5-min close back through the band/VWAP line'}


def fmt_exit(t, last_px: float, entry_info: Optional[dict], exit_quote: Optional[dict], late_s: float = 0.0,
             padded: bool = False) -> str:
    L = [f"🔴 **PAIR-BOT EXIT · {t.setup}** · {t.ticker} {_side(t.side)} · {_WHY.get(t.exit_reason, t.exit_reason)} at {clock(t.exit_min)} ET{_late(late_s, padded)}"]
    if t.entry_px is not None:
        bp = t.side * (last_px / t.entry_px - 1) * 1e4
        L.append(f'Underlying: in {t.entry_px:.2f} ({clock(t.entry_min)} open) -> {last_px:.2f} now · about {bp:+.1f} bp in the trade direction (final figure in the daily summary)')
    if entry_info and exit_quote and entry_info.get('ask') is not None and exit_quote.get('bid') is not None:
        pnl = (exit_quote['bid'] - entry_info['ask']) * 100
        L.append(f"Option: bid {exit_quote['bid']:.2f} / ask {exit_quote['ask']:.2f} vs entry ask {entry_info['ask']:.2f} -> "
                 f"{pnl:+.0f} dollars per contract at live quotes ({pnl - 2 * C.COMMISSION_PER_SIDE:+.2f} after commission)")
    elif entry_info and entry_info.get('source') == 'tradier':
        L.append('Option: no exit quote available')
    return '\n'.join(L)[:DISCORD_LIMIT]


def fmt_start(day: date, status: Dict[str, dict], fomc: bool, quotes: bool) -> str:
    L = [f'🟦 **PAIR-BOT** session {day.isoformat()} · setups {", ".join(C.ENABLED_SETUPS)} · alert-only']
    for tk, s in status.items():
        if not s['ok']:
            L.append(f'{tk}: OFF today ({s["reason"]})')
        else:
            L.append(f"{tk}: {s['sessions']} earlier sessions loaded, noise band {'ready' if s['noise'] else 'NOT available (S1 off)'}")
    L.append(f"FOMC day: {'yes (no entries from 13:30, flat 13:55)' if fomc else 'no'} · live option quotes: {'Tradier' if quotes else 'off (model contract only)'}")
    L.append('S1 decisions at 10:00, 10:30 ... 15:00 ET; S2 at every 5-minute close 10:00-15:00 ET.')
    return '\n'.join(L)[:DISCORD_LIMIT]


def fmt_summary(day: date, rows: List[dict], card: Dict[str, dict], notes: List[str]) -> str:
    L = [f'📒 **PAIR-BOT daily summary** {day.isoformat()} · {len(rows)} trade(s)']
    for r in rows:
        opt = f" · option {float(r['opt_pnl_usd']):+.0f} dollars at live quotes" if r.get('opt_pnl_usd') not in (None, '') else ''
        bp = f"{float(r['gross_bp']):+.1f} bp" if r.get('gross_bp') not in (None, '') else 'n/a'
        L.append(f"{r['setup']} {r['ticker']} {r['side']} {r['entry_time']}-{r['exit_time']} ({r['exit_reason']}): {bp}{opt}")
    for k, c in card.items():
        b = C.SETUPS[k]
        live = f"{c['n']} trade(s), {c['mean_bp']:+.2f} bp avg, {c['win']:.0%} up" if c['n'] else 'no trades yet'
        opt = f"; option at live quotes {c['mean_opt']:+.2f} dollars avg over {c['n_opt']} (avg spread {c['mean_spread']:.2f})" if c['n_opt'] else ''
        L.append(f"**{k} forward test:** {live}{opt} · backtest: {b['gross_bp']:+.2f} bp, {b['usd_zero_cost']:+.2f} dollars at zero cost, {b['usd_house']:+.2f} at the house spread")
    L += notes
    return '\n'.join(L)[:DISCORD_LIMIT]


def post(text: str, retries: int = 4) -> bool:
    """Send to Discord. With DRY_RUN or no webhook the text is printed instead."""
    url = (os.environ.get('DISCORD_WEBHOOK_URL') or '').strip()
    if C.DRY_RUN or not url:
        print('--- [not posted] ---\n' + text + '\n')
        return True
    for i in range(retries):
        try:
            r = requests.post(url, json={'content': text}, headers=_UA, timeout=15)
            if r.status_code in (200, 204):
                return True
            if r.status_code == 429:
                time.sleep(min(10.0, float((r.json() or {}).get('retry_after', 1.5)) + 0.3))
                continue
            print(f'discord HTTP {r.status_code}: {r.text[:200]}')
        except (requests.RequestException, ValueError) as e:
            print(f'discord error: {e!r}')
        time.sleep(1.5 * (i + 1))
    return False
