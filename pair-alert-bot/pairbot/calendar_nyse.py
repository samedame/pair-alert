"""NYSE session calendar (2026-2027). The backtest only used full 390-minute sessions, so early closes are no-trade days.
Update HOLIDAYS / EARLY_CLOSES every December."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
HOLIDAYS = {
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
    date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26), date(2027, 5, 31),
    date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6), date(2027, 11, 25), date(2027, 12, 24),
}
EARLY_CLOSES = {date(2026, 11, 27), date(2026, 12, 24), date(2027, 11, 26)}
LAST_COVERED = date(2027, 12, 31)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def is_full_session(d: date) -> bool:
    return is_trading_day(d) and d not in EARLY_CLOSES


def session_open(d: date) -> datetime:
    return datetime.combine(d, time(9, 30), tzinfo=NY)


def clock(m: int) -> str:
    """Wall-clock ET for minute index m (0 = 09:30), e.g. 30 -> '10:00'."""
    t = 9 * 60 + 30 + m
    return f'{t // 60:02d}:{t % 60:02d}'


def now_et() -> datetime:
    return datetime.now(NY)


def minutes_since_open(now: datetime) -> float:
    return (now - session_open(now.date())).total_seconds() / 60.0
