"""Every rule constant, frozen to the backtest (claude/two-indicator-tournament-verdict-2026-11-12.md).

Minute index m = minutes after 09:30 ET. A 5-minute bar j covers minutes 5j..5j+4 and closes at minute 5(j+1).
Changing anything in the RULES block means the bot no longer trades the strategy that was backtested;
tests/test_fixture_replay.py will fail if you do.
"""
from __future__ import annotations

import os
from datetime import date

# ---------------------------------------------------------------- RULES (do not edit)
SESSION_MIN = 390
BARS5 = 78
NOISE_SESSIONS = 14          # sigma = mean move from the open over the previous 14 valid sessions
NOISE_MIN_SESSIONS = 10      # fewer valid sessions than this -> no Noise Area band -> S1 does not trade
BAD_MOVE = 0.25              # |overnight gap| or |open-to-close| above 25% = bad data
BB_N, BB_K = 20, 2.0         # Bollinger Bands on 5-minute closes, population standard deviation
HOLD_MIN = 60                # time exit, minutes after entry
EOD_MIN = 385                # 15:55 ET: everything flat
FOMC_FLAT_MIN = 265          # 13:55 ET on FOMC decision days
FOMC_BLOCK_BAR = 47          # no entries from the bar that closes at 13:30 ET on FOMC days
MAX_TRADES_PER_DAY = 2       # per setup, per ticker
S1_CHECK_BARS = tuple(range(5, 66, 6))   # closes at 10:00, 10:30, ..., 15:00 ET
S2_FIRST_BAR, S2_LAST_BAR = 6, 65        # closes from 10:00 to 15:00 ET

# option contract rule (same as the backtest's option layer)
DTE_MIN, DTE_MAX, DTE_TARGET = 7, 21, 14
TARGET_DELTA = 0.65
K_CALL, K_PUT, BETA = 0.949, 0.764, 4.45          # IV proxy: k * index vol * (S / prev close)^-beta
HOUSE_SPREAD = 3.88e-4                            # full quoted spread as a fraction of the underlying price
INST_SPREAD_MULT = {'SPY': 1.0, 'QQQ': 1.5, 'IWM': 1.5}
DIV_YIELD = {'SPY': 0.016, 'QQQ': 0.006, 'IWM': 0.012}
VOL_INDEX = {'SPY': '^VIX', 'QQQ': '^VXN', 'IWM': '^RVX'}

# FOMC decision days. 2027 dates are the Federal Reserve's tentative schedule as published 2026-11-12.
# 2027-11-17 is listed by the Fed as a "Monetary Policy Strategy Meeting"; it is treated as an FOMC day here
# (the cautious reading). Confirm and extend this list every December.
FOMC_DATES = {
    date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9), date(2027, 7, 28),
    date(2027, 9, 15), date(2027, 10, 27), date(2027, 11, 17),
}
FOMC_LAST_COVERED = date(2027, 12, 31)

# ---------------------------------------------------------------- backtest reference numbers (PROXY, out of sample 2022-01..2026-03)
SETUPS = {
    'S1': {
        'name': 'Noise Area + VWAP',
        'rule': 'half-hour checks, 5-min close beyond the noise band and on the same side of VWAP; exit on a 5-min close '
                'back through the band/VWAP line, after 60 minutes, or at 15:55',
        'gross_bp': 1.01, 'usd_zero_cost': 1.88, 'usd_house': -17.95, 'usd_quarter': -4.05, 'n_oos': 3089,
        'win_underlying': 0.49, 'label': 'NEGATIVE EXPECTANCY (PROXY)',
    },
    'S2': {
        'name': 'VWAP + Bollinger pullback',
        'rule': 'first 5-min close on the trend side of VWAP and outside the opposite Bollinger band (20, 2); '
                'exit after 60 minutes or at 15:55',
        'gross_bp': 0.94, 'usd_zero_cost': -0.03, 'usd_house': -20.02, 'usd_quarter': -6.00, 'n_oos': 694,
        'win_underlying': 0.57, 'label': 'NEGATIVE EXPECTANCY (PROXY)',
    },
}

# ---------------------------------------------------------------- runtime settings (safe to change)
def _list(name: str, default: str):
    return [x.strip().upper() for x in (os.environ.get(name) or default).split(',') if x.strip()]

TICKERS = [t for t in _list('TICKERS', 'SPY,QQQ,IWM') if t in INST_SPREAD_MULT]
ENABLED_SETUPS = [s for s in _list('SETUPS', 'S1,S2') if s in SETUPS]
COMMISSION_PER_SIDE = float(os.environ.get('COMMISSION_PER_SIDE') or 0.65)   # backtest value; set 0 for a zero-commission broker
DRY_RUN = (os.environ.get('DRY_RUN') or '').lower() in ('1', 'true', 'yes')
POLL_DELAY_S = 8.0           # wait this long after a 5-minute boundary before asking the feed for the closed bar
POLL_RETRY_S = 5.0
POLL_GIVE_UP_S = 75.0        # after this the decision is made on padded data and the alert is flagged
LATE_GRACE_S = 150.0         # an alert posted later than this after its bar close is flagged LATE
HISTORY_CALENDAR_DAYS = 29   # Yahoo serves about 30 days of 1-minute bars
