# Verification (2026-11-12)

What was checked, and what was not.

## The bot trades the strategy that was backtested

`verification/full_history_check.py` replays every session from 2008-01-02 to 2026-03-31 for SPY, QQQ and IWM through
the bot's own engine (`pairbot/engine.py`, the code the live loop calls) and compares the result with the backtest's
trade ledgers. Result (`full_history_result.json`):

| Check | Result |
|---|---|
| Sessions replayed | 13,526 (three ETFs) |
| S1 trades: bot vs the earlier study's rule (Noise Area + VWAP, half-hour checks, VWAP stop) | 13,373 vs 13,373, sessions with any difference: 0 |
| S2 trades: bot vs the tournament winner (VWAP + Bollinger reversal, 60 minutes) | 3,192 vs 3,192, sessions with any difference: 0 |
| Largest difference in a trade's gross result | 0.0 bp |
| Streaming: 360 random sessions fed boundary by boundary (25,920 five-minute boundaries, 946 events) | every event appeared at its own boundary and never changed afterwards: 0 violations |

The backtest's own ledgers were produced by code that had already been checked against a separate pure-Python
implementation and against the earlier study's published figures (see the verdict). The bot's engine is a third,
separately written implementation (plain loops on per-session inputs, no pandas, no numba).

The full-history check needs the research workspace (cleaned minute bars, 214 MB). The repository carries a trimmed copy
for CI: `fixtures.npz` holds 100 real sessions per ETF (2025-11-03 to 2026-03-31) and `expected_trades.json` the 283
backtest trades of the last 80 of them. `tests/test_fixture_replay.py` repeats both checks on that data on every push.

## The plumbing

19 offline tests (`python -m pytest -q`), including:
- a simulated full session with a fake clock and a fake feed built from a real fixture day: start line, every ENTRY and
  EXIT within a minute of its own boundary, ledger rows equal to the backtest's trades for that day, daily summary;
- a restart the same afternoon posts nothing twice;
- a late start flags the missed alerts as LATE, and a run that reaches its time budget hands off and the next run finishes the day;
- a Discord post that fails is retried at the next boundary instead of being recorded as sent;
- the grid drops the in-progress bar, fills interior gaps, and fails closed without a 09:30 bar;
- expiry rule (11 / 17 / 16 / 15 / 14 days for Monday to Friday), holiday-Friday fallback, OCC symbols, put-call parity;
- Tradier contract selection against a mocked chain.

## Live feed, dry run

On 2026-11-12 after the close, `python -m pairbot replay --date 2026-11-12` and `python -m pairbot eod --date 2026-11-12`
ran against Yahoo's real bars: 21 earlier sessions loaded for each ETF, noise bands available, three IWM trades found
(S1 put 10:00-10:45 stopped, -20.0 bp; S2 call 13:05-14:05, -55.4 bp; S1 put 15:00-15:55, +24.7 bp), none for SPY or
QQQ, ledger and scorecard written. The Cboe fallback supplied the RVX close that Yahoo does not carry.

## Not verified here

- A real GitHub Actions run, the hand-off between runs on GitHub, and the open-scheduler dispatch.
- A real Discord post (no webhook in the build environment).
- Tradier with a real key: endpoints and response fields follow Tradier's documentation and are tested against a mock
  only. If a field differs, the alert falls back to the model contract and the error is printed in the job log.
- Yahoo's behaviour from GitHub's runners at each 5-minute close (rate limits, how quickly the closing bar appears).
  The bot waits up to 75 seconds, then decides on padded bars and says so in the alert.
- Agreement between Yahoo's bars and the backtest's data source on the same day (they do not overlap: the backtest
  data ends 2026-03-31 and Yahoo serves the last 30 days).
