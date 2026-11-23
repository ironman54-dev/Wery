# The Capital Code site

One site, three product pages inside it.

| URL | What |
|---|---|
| `/` | Screener, calculators, blog, contact |
| `/nexus/`, `/codeflow/`, `/greekwatch/` | Product pages. They open inside `/` under the same top panel (one frame each, preloaded). Opened on their own they redirect to `/#<name>` |
| `/privacy/ /terms/ /disclaimer/ /cookies/` | Policy pages, linked from the footer only |

Education only. Nothing here is investment advice.

## Remove or add a tab
Edit `public/js/sites.js`: set `on:false` (or delete the line). To add one, add a line, a folder `/<id>/index.html` (copy `greekwatch/index.html`) and a colour rule `html[data-site="<id>"]` in `public/index.html`.

## Weekly screener update
`.github/workflows/weekly_screener.yml` runs `scripts/update_screener.py` every Friday 19:00 IST and again Saturday 08:00 IST as a backup. Each run tries up to three times. The script stops without writing anything if the data looks wrong (too few stocks, an old date, no Nifty 500). New data is committed to `public/data/` and Vercel redeploys on every push. Run it by hand from the Actions tab (Run workflow).

Local run: `pip install yfinance pandas`, then `python scripts/update_screener.py` (add `--rebuild` to rebuild all weekly snapshots).

## Forms (waitlists and Contact)
All forms go through `public/js/cc-api.js` to three Supabase functions (`sql/cc_backend.sql`). The tables have row-level security with no policies, so the browser can only call those functions. The key in `cc-api.js` is the public anon key by design. Never commit a `service_role` key or any `.env` file.

## Deploy
Vercel project `capital-code-site`, linked to this repo, output directory `public` (see `vercel.json`).
