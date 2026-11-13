# Setup

1. **Create the repo.** On GitHub create `samedame/pair-alert` (private is fine), empty, no README.
2. **Push the code.** Unzip, then in the folder:
   ```
   git init -b main && git add -A && git commit -m "pair-alert-bot v1.0.0"
   git remote add origin https://github.com/samedame/pair-alert.git && git push -u origin main
   ```
   The `tests` workflow runs on the push and should go green (19 tests, about a minute).
3. **Let Actions commit.** Settings -> Actions -> General -> Workflow permissions -> Read and write permissions.
4. **Secrets** (Settings -> Secrets and variables -> Actions -> Secrets):
   - `DISCORD_WEBHOOK_URL` (required): the webhook of the channel that should receive the alerts.
   - `TRADIER_API_KEY` (optional, recommended): the same production key your other bots use. With it every alert shows
     the live bid and ask of the contract and the ledger records them. Without it the alerts show the model contract only.
5. **Variables** (same page, Variables tab; all optional):
   - `TICKERS` default `SPY,QQQ,IWM`. IWM was the weak leg in the backtest; `SPY,QQQ` is a reasonable narrower set.
   - `SETUPS` default `S1,S2`.
   - `COMMISSION_PER_SIDE` default `0.65` (what the backtest charged). Set `0` for a zero-commission broker.
   - `DRY_RUN` set `true` to print instead of posting.
6. **Start time.** GitHub's own schedule has been running late on this account, so treat the two cron lines in
   `pair-session.yml` as a backup and add the workflow to the Cloudflare `open-scheduler` Worker:
   repo `samedame/pair-alert`, workflow `pair-session.yml`, dispatch at 09:40 ET on weekdays, inputs
   `trigger=cf-scheduler` (and `scheduled_for_et` if the Worker sets it). Anything from 09:31 to 09:55 ET works; the first
   decision is the 10:00 close.
7. **First run.** Actions -> pair-session -> Run workflow. Outside market hours it exits at once ("session is over").
   During a session it posts the start line within a minute. To see real alert text right now without posting:
   `DRY_RUN=1 python -m pairbot replay --date <a session in the last 30 days>`.

## What happens each day

- 09:40 ET: the job starts, loads about 21 earlier sessions of minute bars from Yahoo, posts the start line.
- 10:00 to 15:55 ET: eight seconds after every 5-minute close it pulls the completed bars and posts new events.
  If the feed has not delivered the closing bar within 75 seconds the decision is made on padded bars and the alert says so.
- After 5.5 hours the job hands off to a fresh run (GitHub's limit is 6 hours per job); nothing is repeated.
- 15:57 ET: final-bar replay, `ledger/trades.csv`, `ledger/scorecard.md`, daily summary. `pair-eod.yml` is the backup.

## Maintenance

- Every December: extend `HOLIDAYS` / `EARLY_CLOSES` in `pairbot/calendar_nyse.py` and `FOMC_DATES` in `pairbot/config.py`.
  The bot posts a warning when the lists run out. The 2027 FOMC dates are the Fed's tentative schedule.
- Do not edit the RULES block in `config.py`; the replay test fails if the bot stops matching the backtest.
