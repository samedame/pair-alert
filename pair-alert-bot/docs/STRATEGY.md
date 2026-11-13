# Strategy definitions (frozen to the backtest)

Source: `claude/two-indicator-tournament-verdict-2026-11-12.md` and, for S1, `claude/noise-area-vwap-options-verdict-2026-11-12.md`.
Minute m counts from 09:30 ET. Bar j covers minutes 5j to 5j+4; its close is known at minute 5(j+1).

## Indicators

- **Session VWAP**: cumulative (HLC3 x volume) / cumulative volume over the session's 5-minute bars (TradingView default).
- **Noise Area band** (Zarattini, Aziz, Barbon): sigma(j) = average over the previous 14 valid sessions of
  |close of bar j / that session's 09:30 open - 1|. Upper = max(today's open, previous close) x (1 + sigma);
  lower = min(today's open, previous close) x (1 - sigma). Needs at least 10 valid sessions. A session is not valid if
  its overnight gap or its open-to-close move exceeds 25% (bad data). No trades at all if today's gap exceeds 25%.
- **Bollinger Bands (20, 2)**: 20 five-minute closes, population standard deviation, running continuously across
  regular sessions.

## S1: Noise Area + VWAP (the earlier study's rule, its "loose exit" variant)

1. Decisions only at the closes of the bars ending 10:00, 10:30, ..., 15:00 ET.
2. Call if close > upper band and close > VWAP. Put if close < lower band and close < VWAP. The condition does not have
   to be new: if price is still outside at a half-hour mark and the book is flat, that is an entry.
3. Fill: open of the next minute.
4. Exit at the next minute's open after a 5-minute close below min(upper band, VWAP) for a call (above
   max(lower band, VWAP) for a put); or 60 minutes after entry; or 15:55 ET.
5. One position per ETF, at most two entries per ETF per day, the next decision only after the exit minute.
6. FOMC decision days: no entries from the 13:30 check; flat at 13:55.

## S2: VWAP + Bollinger pullback (the pre-registered tournament winner)

1. Decisions at every 5-minute close from 10:00 to 15:00 ET.
2. State = +1 if close > VWAP and close < lower Bollinger band; -1 if close < VWAP and close > upper band; else 0.
   Entry when the state is +1 or -1 and differs from the previous bar's state (a fresh agreement).
3. Fill: open of the next minute. Exit: 60 minutes after entry or 15:55 ET. No price stop.
4. Same book and FOMC rules as S1.

## Contract (both)

Friday expiry 7 to 21 calendar days out, the one closest to 14 (Monday 11 days, Tuesday 17, Wednesday 16, Thursday 15,
Friday 14). Strike on the whole-dollar grid whose absolute delta is closest to 0.65. With a Tradier key the delta comes
from the chain; without one the backtest's own proxy is used (Black-Scholes, IV = k x previous-day VIX / VXN / RVX x
(S / previous close)^-4.45, k = 0.949 calls, 0.764 puts).

## Backtest numbers the alerts quote (out of sample, 2022-01 to 2026-03, SPY + QQQ + IWM pooled, PROXY)

| | S1 | S2 |
|---|---|---|
| Trades | 3,089 | 694 |
| Gross move, bp per trade | +1.01 (90% interval -0.10 to +2.14) | +0.94 (-1.97 to +3.87) |
| Trades up on the underlying | 49% | 57% |
| Dollars per contract at zero cost | +1.88 | -0.03 |
| At a one-cent spread | -0.42 | -2.33 |
| At a quarter of the modelled spread | -4.05 | -6.00 |
| At the modelled spread (3.88 bp of the underlying, 1.5x for QQQ and IWM) | -17.95 | -20.02 |

The "cost check" line in each ENTRY alert is: zero-cost edge minus (live spread x 100 + two commissions). It is an
average over years, not a forecast for that trade.

## Differences between live and backtest that the bot cannot remove

- Data source: the backtest used a consolidated 1-minute history; the bot uses Yahoo's 1-minute bars. Volumes differ a
  little, so VWAP can differ by a cent or two and a borderline signal can differ.
- The backtest filtered bad prints with an 11-minute median; the bot does not (Yahoo bars are already cleaned).
- The backtest fills at the next minute's open. The alert reaches you about 10 to 15 seconds after the bar close.
- Option results in the backtest are a model. The ledger's option columns are real quotes at alert time, not your fills.
