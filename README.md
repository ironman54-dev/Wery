# The Capital Code site

One site, three product pages inside it. Education only. Nothing here is investment advice.

| URL | What |
|---|---|
| `/` | NSE Top-1000 Stage-2 screener (with a chart on every stock card), calculators, blog, contact |
| `/nexus/`, `/codeflow/`, `/greekwatch/` | Product pages. They open inside `/` under the same top panel (one frame each, preloaded). Opened on their own they redirect to `/#<name>` |
| `/privacy/ /terms/ /disclaimer/ /cookies/` | Policy pages, linked from the footer only |

## Remove or add a tab
Edit `public/js/sites.js`: set `on:false` (or delete the line). To add one, add a line, a folder `/<id>/index.html` (copy `greekwatch/index.html`) and a colour rule `html[data-site="<id>"]` in `public/index.html`.

## Where the prices come from
One store in Supabase (`cc_bars`): raw NSE end-of-day prices for the ~960 screener stocks and the Nifty 500 index, 10 years (about 31 MB), plus a short list of split/bonus events (`cc_events`). Adjustment for splits and bonuses is applied when the data is read (the screener and the chart both do it the same way, no dividend adjustment, like the Video 7 backtest), so a new split can never damage old rows.

- **Nightly update.** The Edge Function `supabase/functions/bars-append` runs from pg_cron at 19:30 IST (Mon-Fri) with a backup at 08:00 IST. It downloads NSE's public `sec_bhavdata_full` and `ind_close_all` files for every new calendar day (weekends too: Muhurat and other special sessions). A file only counts if the date inside it is the day asked for. It needs no secret.
- **Splits and bonuses.** NSE's `PREV_CLOSE` in those files is not adjusted, so it cannot detect them. An event is applied automatically only when the raw price gap matches a clean fraction AND Yahoo lists a split on that date. Anything else goes to `cc_event_review`, and the screener holds that stock out of its run until it is checked against NSE's corporate actions (the NSE MCP `get_corporate_actions`). Verified events are kept in `scripts/verified_events.json` (CCAVENUE, CONCORDBIO so far).
- **Bulk (re)build.** `python scripts/bars_pipeline.py build --out FILE` then `load --in FILE --token-file TOKEN` (needs the one-time write token; its hash is in `cc_secrets`; the token itself is not in the repo). It reads the Capital Code backtest archives plus NSE's public archive, fills missing sessions, applies NSE's symbol-change list, and dates index rows by the day of the file (some years write dd-mm and some mm-dd).
- **Calendar check.** The store's sessions equal the Aria desk's session calendar exactly for Oct 2021 - Nov 2026 (1,276 days). Two stock files are missing for 4 Nov 2021 and 8 Aug 2022.

## Weekly screener update
`.github/workflows/weekly_screener.yml`: Friday 20:30 IST and a backup Saturday 09:00 IST (each run tries three times). It first nudges the nightly update, then runs `scripts/update_screener.py`, which reads the store (Yahoo is the fallback if the store is unreachable), builds the snapshot for the last COMPLETE week (last trading day of the week; a mid-week run never creates a half-week), and stops without writing if the data looks wrong:
fewer than 95% of stocks fresh, the week's Friday missing while NSE traded that day, unexplained split-like jumps (held out and listed), more than 3% of latest closes disagreeing with Yahoo, an older date than already published.
In CASH weeks (Nifty 500 below its 200-DMA) the screen shows a banner, no new entries and no "new" tags, as in the tested system. REITs are excluded. Sector groups smaller than 15 stocks are never ranked, led or cut. Stocks need Rs 1 crore median daily turnover.
Local run: `pip install yfinance==1.8.0 pandas==3.1.1`, `python scripts/update_screener.py` (`--rebuild` rebuilds all 53 weekly snapshots; older-method files are moved to `public/data/history/_superseded/`, never deleted, and are git-ignored).

## Red "Data issue" pill
`public/js/health.js` shows a red pill (with plain-words details) on the screener and on every chart card when: a price file was skipped, missing or in a new layout, NSE could not be reached, the nightly update has not run for 36 hours or no new prices for 4 days, a possible split is waiting for a check, the weekly update is overdue (8.5 days), the screener used backup data or its Yahoo cross-check failed. The data stays visible. Everything fine shows a quiet green "Data OK". Sources: `cc_health_status()` and `public/data/status.json`.

## Forms (waitlists and Contact)
All forms go through `public/js/cc-api.js` to Supabase functions (`sql/cc_backend.sql`). The tables have row-level security with no policies, so the browser can only call those functions. The key in `cc-api.js` is the public anon key by design. Never commit a `service_role` key, the ingest token or any `.env` file. SQL for the price store and health tables: `sql/cc_chart_store.sql`.

## Deploy
Vercel project `capital-code-site`, linked to this repo, output directory `public` (see `vercel.json`). Every push deploys.
