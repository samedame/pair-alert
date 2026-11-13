# pair-alert-bot

Alert-only forward test of the two setups from the two-indicator tournament of 2026-11-12
(`claude/two-indicator-tournament-verdict-2026-11-12.md` in the Personal investor project). It posts ENTRY and EXIT
alerts to Discord for SPY, QQQ and IWM, records the real option bid and ask at each alert when a Tradier key is set,
and keeps a ledger and scorecard so the live numbers can be read next to the backtest. It places no orders.

**Read this first.** Both setups carry the backtest label NEGATIVE EXPECTANCY (PROXY): before costs they moved the
underlying about 1 basis point per trade in the trade direction out of sample (2022 to March 2026); bought as options at
the modelled bid-ask they lost 18 to 20 dollars per contract per trade. The open question the backtest could not settle
is the real spread on the contract, which is what this bot measures. Treat every alert as research, not as a trade
recommendation.

## The two setups (5-minute chart, regular session)

| | S1 Noise Area + VWAP | S2 VWAP + Bollinger pullback |
|---|---|---|
| Indicators | Noise Area band (14-session average move from the open, multiplier 1) and session VWAP | Session VWAP and Bollinger Bands (20, 2) |
| When it looks | 5-minute closes at 10:00, 10:30, ..., 15:00 ET | Every 5-minute close from 10:00 to 15:00 ET |
| Call | Close above the upper noise band and above VWAP | Close above VWAP and below the lower Bollinger band, first bar on which that is true |
| Put | Close below the lower band and below VWAP | Close below VWAP and above the upper band, first bar on which that is true |
| Exit | A 5-minute close back through the lower of band and VWAP (calls; higher for puts), or 60 minutes, or 15:55 ET | 60 minutes, or 15:55 ET |
| Limits | One position per ETF, two entries per ETF per day; no entries from 13:30 and flat at 13:55 on FOMC days | Same |
| Contract | Friday expiry 7 to 21 days out (closest to 14), strike nearest 0.65 delta | Same |
| Backtest, before costs | +1.01 bp per trade (3,089 trades) | +0.94 bp per trade (694 trades) |
| Backtest, per contract | +1.88 dollars at zero cost, -4.05 at a quarter of the modelled spread, -17.95 at the modelled spread | -0.03, -6.00, -20.02 |

Details and the exact definitions: `docs/STRATEGY.md`. Proof that the code matches the backtest: `verification/VERIFICATION.md`.

## What you get in Discord

- A start-of-session line (which ETFs loaded, whether the noise band is ready, FOMC day, quotes on or off).
- ENTRY: the levels that triggered, the exit plan with clock times, the contract with its live bid, ask and spread
  (or the model contract if no Tradier key), and what that spread does to the backtest's zero-cost edge.
- EXIT: reason, move of the underlying, and the option result at live quotes.
- A daily summary with the forward-test scorecard next to the backtest numbers.

## Setup

See `docs/SETUP.md` (about 10 minutes): create the repo, push, add `DISCORD_WEBHOOK_URL` (and optionally
`TRADIER_API_KEY`), allow Actions to write, add `pair-session.yml` to the open-scheduler Worker at 09:40 ET.

## Commands

```
python -m pytest -q                              # 19 tests, offline
DRY_RUN=1 python -m pairbot replay --date 2026-11-12   # print the alerts a past session would have produced (last 30 days)
python -m pairbot live                           # the live loop (GitHub Actions runs this)
python -m pairbot eod [--date YYYY-MM-DD] [--force]    # rebuild a day's ledger block and summary
```

## Layout

```
pairbot/config.py        every rule constant, frozen to the backtest
pairbot/indicators.py    VWAP, Noise Area band, Bollinger state
pairbot/engine.py        both setups as pure functions of the bars
pairbot/data.py          Yahoo 1-minute bars on the 09:30-15:59 grid
pairbot/contracts.py     expiry and strike rule, Black-Scholes fallback
pairbot/tradier.py       optional live option quotes (read-only)
pairbot/alerts.py        Discord text and delivery
pairbot/runner.py        live loop and end-of-day reconciliation
pairbot/state.py         sent-event ledger, trade ledger, git push
state/                   one sent-YYYY-MM-DD.json per session (de-duplication)
ledger/trades.csv        one row per trade from the final bars, with live quotes where captured
ledger/scorecard.md      forward test next to the backtest
verification/            offline fixtures, the full-history check and its result
```

Not financial advice. The author of this code is not a licensed financial advisor.
