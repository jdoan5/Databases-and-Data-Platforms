# tagline/dashboard: a static page over a snapshot of the marts (Stage 6)

**Live:** https://jdoan5.github.io/tagline/ (the portfolio site serves a copy of this folder's page and snapshot).

The dashboard is a static page. One command reads six small marts in `tagline_marts` and writes
`data/snapshot.json`, which is committed. The page reads that file and nothing else. It is an Import-style
dashboard, not a live connection: the numbers are as of the snapshot's stamp, and they change only when someone
runs the exporter again and republishes. No viewer's browser talks to BigQuery, and no credential sits anywhere
near the page.

```bash
cd tagline
make dashboard-snapshot-dry   # the exporter's queries as dry runs: free, writes nothing
make dashboard-snapshot       # LIVE: 6 query jobs on the marts (~60 MiB billed), write and validate data/snapshot.json
make dashboard-setup          # npm ci here: Playwright and Ajv, for the tests only
make dashboard-test           # the exporter's pytest, the snapshot against its JSON Schema, Playwright at 1280 and 375 px
make dashboard-serve          # http://127.0.0.1:5190/ until Ctrl-C (the page fetches its snapshot, so file:// fails)
make dashboard-publish        # validate, then copy the page and snapshot to ../jdoan5.github.io/tagline/ (no commit)
make dashboard-test-published # the same Playwright checks against the published copy
make dashboard-screenshots    # 1600 x 900 screenshots, light and dark (SHOTS=<dir>)
```

## What is in the snapshot

`export_snapshot.py` uses the pipeline's own configuration (`tagline/.env`) and BigQuery wrapper
(`tagline_pipeline.bq`): every job has `maximum_bytes_billed`, the query cache off, and labels (`kind=report`,
`component=dashboard`). The exporter caps each job at 200 MB, far under the pipeline's 10 GB default. The marts
come to about 3 MB in all, so each job bills BigQuery's 10 MiB minimum.

| Section | From | What the page shows |
|---|---|---|
| `sources`, `kpis`, `daily` | `mart_kpi_daily` | each source's date range and days, KPI totals (rates recomputed from the sums), the sample's day-by-day sessions, orders and revenue |
| `alerts` | `kpi_alerts` | the alerts grouped by day and source (count, worst severity, metrics and rules), marked under the daily charts |
| `funnel` | `mart_funnel_daily` | the closed funnel summed over the days, and each step's rate from the step before |
| `attribution` | `mart_attribution_daily` | revenue share by channel under the six models: the sample's complete-lookback orders, top 8 channels by name and the rest folded into one row; the site's 8 attributed orders in dollars |
| `tag_health` | `mart_tag_health_daily` | check rows by status and by kind, per source, and the documented expectations behind the sample's `expected` rows |
| `campaigns` | `mart_campaign_daily` | the site's rows: sessions, orders, revenue, synthetic spend, ROAS |
| `cited` | the write-ups | measured DAG runs, build costs, the backtest's judgement, the tag QA suite: each with the document and section it comes from |

**Aggregates only.** The queries select no identifier. `tests/validate-snapshot.mjs` fails the snapshot if any key
looks like an id (`user_id`, `order_id`, ...), or if the text holds an email address, a GA4 measurement id, an
`analytics_<property_id>` dataset name, a 32-hex account id, a service account, or any value from `tagline/.env`.
Before writing, the exporter itself refuses a snapshot that contains the configured project or dataset names. The
validator also checks the size budget (200 KB) and that the numbers agree with each other: daily sums equal the
KPI totals, each model's shares sum to 1, funnel sessions equal KPI sessions, alert days add up, the site's orders
in sessions plus its cookieless orders equal its orders.

**Every number on the page comes from the snapshot or cites its source.** Figures BigQuery cannot know (run times,
Dataproc usage, the backtest's human judgement) sit in the exporter's `CITED` block, each with a link to the
document and section it comes from. `tests/test_export_snapshot.py` reads those sections and fails if a cited
figure no longer appears in them, or if an anchor no longer resolves.

## The page

`index.html`, `assets/dashboard.css` and `assets/dashboard.js`. It uses no framework and no chart library, and the
only request it makes is for its own files. The spec allowed Chart.js from a CDN. The charts are hand-built SVG
instead, for three reasons:

- the charts can use the page's colour tokens, so light and dark mode is plain CSS;
- every bar and every day can be reached from the keyboard;
- a CDN outage cannot break the page or its tests.

How the page is built:

- **Charts follow the dataviz method.** Two sources, two categorical slots (blue and orange), validated against
  the page's own surfaces in both modes (worst colour-blind ΔE 24.7 light, 26.8 dark). Sessions and revenue are two
  panels on one time axis, never two y-scales. The heatmap tint is capped so its ink keeps at least 4.5:1 contrast
  in both themes. Status colours (pass, expected, violation, alert severity) always come with an icon and a label.
- **Readable without the charts.** Every chart has a table view, and marks carry `aria-label`s. The daily chart
  takes the arrow keys, with a live region reading out the day.
- **Themes.** The page follows `prefers-color-scheme`. It also has the portfolio's Auto / Light / Dark toggle,
  stored under the same `localStorage` key (`theme-mode`), so a choice made on the portfolio carries over.
- **Narrow screens.** It lays out at 375 px with a 16 px gutter and no horizontal page scroll. Wide tables scroll
  inside their own wrapper, which becomes a focusable, labelled region while it does. At 375 px the sample's days
  are under 3 px apart, so the alert strip draws each alert day as a bar one day wide, tall for critical and short
  for warnings only, instead of a diamond or triangle that would cover its neighbours; the legend switches with it.
  Amber marks get a dark outline in light mode, since amber alone is 1.8:1 on white.

## Tests

- `tests/test_export_snapshot.py` (12, run with the pipeline's venv): the transforms on hand-built rows (channel
  folding, lookback split, rates from sums, ROAS, alert grouping, tag-health counts, strict JSON), and the cited
  figures against their documents.
- `tests/validate-snapshot.mjs`: the JSON Schema (`snapshot.schema.json`, draft 2020-12, Ajv strict mode), size,
  privacy and consistency, as above.
- `tests/dashboard.spec.mjs` (Playwright, the installed Chrome, 4 tests at 1280 and at 375 px, so 8): every
  section renders from the snapshot. The run fails on any console error or warning, any failed request, any
  request that leaves the local server, or a horizontal page scroll. The tests also check keyboard and pointer
  tooltips, the channel picker, the incident highlight, the theme toggle, system dark mode, labelled marks and
  captioned tables. Playwright starts `tests/serve.mjs` on 127.0.0.1 and stops it when the run ends.

## Measured

The run of 2026-09-30:

- **Snapshot:** 6 query jobs, 2.34 MiB processed, 60 MiB billed, 71,479 bytes written. A later fix to one cited
  sentence (not data) was written into the same snapshot with the exporter's own serializer: 71,584 bytes, same stamp.
- **Tests:** 12 of 12 pytest, the schema and the other checks, and 8 of 8 Playwright, against this folder and
  again against the published copy.
- **Validator,** a one-off check not kept as a test: six tampered copies each failed. They added an email, a
  `user_id` key, the project id, a measurement id, a share that breaks the sum to 1, and an extra top-level field.

## Limitations

- **A snapshot, not live.** Nothing refreshes it on a schedule; the stamp at the top says when it was read. A
  scheduled refresh would be one more DAG task and a push to the portfolio repo, which this project does not do on
  the owner's behalf.
- **The site has one day of synthetic traffic.** Its KPIs, funnel, campaigns and attribution are one simulated day,
  labelled as such everywhere on the page. No alert can fire on it yet.
- **The sample's revenue is obfuscated by Google.** Its numbers show the pipeline working, not how Google's store
  performed. ROAS on synthetic spend shows the join, not a business result.
- **The cited run figures are one measured run each**, as the write-ups say. The page shows no run-to-run spread.
