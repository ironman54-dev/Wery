# Capital Code combined site (24 Nov 2026)

One site, three parts. Originals are untouched; this is a copy.

| URL | Source copied from |
|---|---|
| `/` (screener, calculators, blog) | `thecapitalcode/capital-code-screener/public` (Video 10 site) |
| `/nexus/` | `hub/website` |
| `/codeflow/` | `trading terminal/codeflow-landing` |

- Screener top tabs "Nexus AI" and "Codeflow" now open `/nexus/` and `/codeflow/`. Both pages carry a slim bar back to Home / Screener.
- Run locally: launch entry `capital-code-site` (port 5410). Deploy: `vercel.json` serves `public/`.
- `_nexus_shots_src/` is an unneeded copy of the Nexus raw screenshots (53 MB); safe to delete when you say so.

## Parts
| URL | What |
|---|---|
| `/` | Screener, calculators, blog, contact (the Video 10 site) |
| `/nexus/`, `/codeflow/`, `/greekwatch/` | Open inside `/` under the same top panel (an iframe per site, preloaded). Opened on their own they redirect to `/#<name>` |
| `/privacy/ /terms/ /disclaimer/ /cookies/` | Policy pages, linked from the footer only |
| GreekWatch "Open the app" | https://greekwatchv2.vercel.app |

Top panel recolours per site (set in the `html[data-site=...]` rules in `public/index.html`): Nexus blue, Codeflow graphite, GreekWatch orange, screener green. Light and dark follow the toggle and are remembered.

## Remove or add a tab
Edit `public/js/sites.js`: set `on:false` (or delete the line) and the tab, its page and its preloading all disappear; the folder can stay or go. To add one, add a line plus a folder `/<id>/` (copy `greekwatch/index.html`) and a colour rule `html[data-site="<id>"]` in `public/index.html`.

## Backend (waitlists + Contact)
- Every form goes through `public/js/cc-api.js`. Run `sql/cc_backend.sql` on the Supabase project (currently egpfpbulplqdixcsmuyd) to create `cc_waitlist`, `cc_contact` and the three functions. NOT APPLIED YET.
- Until then: waitlist joins fall back to the old `join_waitlist` function (name, mobile, email, code work; the suggestion box is NOT saved), and Contact shows "messages are not switched on yet".
- The ★ priority and the "each friend moves you up 50" rule are implemented in `cc_join_waitlist`.
- Nexus counter: 1,285 + real count + one more every 47 minutes (`BASE`, `TICK_FROM`, `TICK_EVERY` at the top of the Nexus page script).
- Unused copies left in `public/greekwatch/img/` (live-board.jpg, lab-results.jpg, research-tests.jpg) and `_nexus_shots_src/`; delete when you say so.
