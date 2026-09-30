# Data model: Tagline Stage 2

Stage 2 turns raw GA4 BigQuery export rows into stitched, enriched tables. Python
(`tagline/pipeline`) loads reference data, builds the SQL models in order and runs the
checks; the modelling is plain SQL in `pipeline/sql/models/`, one file per table. A full build
(`make build`) `CREATE OR REPLACE`s every table; the daily incremental build (`make
build-incremental`, Stage 4) updates them in place from the same SQL, only for the export days that
are new or may have changed ([below](#incremental-builds-stage-4)). Every table carries a table
description and a description on every column (the runner refuses to finish a model with an
undocumented column), and is labelled `app:tagline, stage:2`. Everything lives in one project, in the US multi-region,
because the public sample is in the US and BigQuery cannot join across locations.

Numbers for the GA4 sample are from the build of 2026-09-27; the build of 2026-09-28 added the
site's own export (one day, 2026-09-27) and left every sample number unchanged. The site's
numbers are [below](#reconciled-numbers-site-export). `make numbers` prints both again.

---

## Lineage

```
bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*        (source: ga4_sample)
  _TABLE_SUFFIX 20201101..20210131                                     │
                                                                       │
<project>.analytics_<property_id>.events_YYYYMMDD                      │   (source: tagline_site,
<project>.analytics_<property_id>.events_intraday_YYYYMMDD             │    only when TAGLINE_GA4_DATASET
  daily tables, plus streaming tables for days with no daily table ────┤    is set and has tables)
                                                                       ▼
                                             tagline_staging.stg_events ─────────────► tagline_staging.stg_items
                                                          │                                             │
                            ┌─────────────────────────────┼──────────────────────────────┐              │
                            ▼                             │                              ▼              │
          tagline_staging.int_device_days                 │               tagline_staging.int_purchases │
                            │                             │                              │              │
                            ▼                             ▼                              │              │
          tagline_staging.int_identity ──────► tagline_marts.fct_sessions                │              │
                            │                             │                              │              │
                            └─────────────────────────────┴──► tagline_marts.fct_orders ◄┘              │
                                                                          │                             ▼
                                                                          └──────────► tagline_marts.fct_order_items
                                                                                                        ▲
tagline/site/src/catalog/products.json ──► tagline_raw.products ────────────────────────────────────────┘
  + SYNTHETIC unit_cost (reference.py)

reference.py (seeded) ──► tagline_raw.campaign_costs (SYNTHETIC) ──┐
                                                                   ▼
                        fct_sessions ──► tagline_marts.mart_campaign_daily
                        fct_sessions ──► tagline_marts.mart_funnel_daily

Stage 5 (docs/monitoring.md):
  stg_events, fct_sessions, fct_orders ──► tagline_marts.mart_kpi_daily
  stg_events, fct_sessions ──► tagline_marts.mart_tag_health_daily ◄── tagging/events.schema.json (generated checks)
  both marts ──► anomaly rules (pipeline/monitoring.toml) ──► tagline_marts.kpi_alerts (written by Python, not a model)
```

Build order is the numeric prefix of the model files: `10_stg_events`, `20_stg_items`,
`25_int_purchases`, `27_int_device_days`, `30_int_identity`, `40_fct_sessions`, `50_fct_orders`,
`60_fct_order_items`, `70_mart_campaign_daily`, `80_mart_funnel_daily`, and since Stage 5 `85_mart_kpi_daily` and
`90_mart_tag_health_daily`. `stg_`/`int_` models build
into `tagline_staging`, `fct_`/`mart_` into `tagline_marts`. `fct_orders` reads the purchase events
from `int_purchases` (stg_events' 5,705 purchase rows, narrow) rather than scanning all of
`stg_events`, plus `fct_sessions` and `int_identity` (the person of an order placed in no session).
`int_identity` adds up `int_device_days` (stg_events grouped by device and day). Both narrow tables
were added in Stage 4 for the daily incremental build ([below](#incremental-builds-stage-4)).
`fct_order_items` reads three tables: the order from `fct_orders`, its lines from `stg_items`
(the kept purchase event's items) and the catalog cost from `tagline_raw.products`.

---

## Tables: grain, key, layout

| Table | One row per | Key | Partition / cluster | Rows (sample) | Rows (site) |
|---|---|---|---|---|---|
| `tagline_raw.products` | catalog SKU | `item_id` | none (20 rows) | 20 (the site's catalog) | |
| `tagline_raw.campaign_costs` | day × data source × source / medium / campaign | `cost_date, source, session_source, session_medium, session_campaign` | none (556 rows) | 190 | 366 |
| `tagline_staging.stg_events` | GA4 event (exact export duplicates collapsed) | `source, event_key` | `event_date` / `source, event_name` | 4,295,584 | 1,433 |
| `tagline_staging.stg_items` | item in an ecommerce event's `items` array | `source, event_key, item_index` | `event_date` (not clustered since Stage 4) | 3,982,732 | 3,137 |
| `tagline_staging.int_purchases` | purchase event (repeats included), the columns `fct_orders` needs | `source, event_key` | `event_date` | 5,692 | 13 |
| `tagline_staging.int_device_days` | device × day it was seen | `source, user_pseudo_id, event_date` | `event_date` | 319,066 | 48 |
| `tagline_staging.int_identity` | device (`user_pseudo_id`) | `source, user_pseudo_id` | none: no date grain, always read whole | 270,154 | 48 |
| `tagline_staging.staged_export_days` | site export day in `stg_events`: the export table read for it and that table's metadata ([below](#incremental-builds-stage-4)) | `source, export_day` | none (one row a day) | | 1 |
| `tagline_marts.fct_sessions` | session | `source, user_pseudo_id, ga_session_id` (also `session_key`) | `session_date` / `source` | 360,129 | 48 |
| `tagline_marts.fct_orders` | order | `source, order_id` | `order_date` | 5,368 | 13 |
| `tagline_marts.fct_order_items` | order line | `source, order_id, line_number` | `order_date` | 15,063 | 22 |
| `tagline_marts.mart_campaign_daily` | date × source × session source / medium / campaign | all five | `date` | 2,634 | 5 |
| `tagline_marts.mart_funnel_daily` | date × source | `date, source` | `date` | 92 | 1 |
| `tagline_marts.mart_kpi_daily` (Stage 5) | date × source: the KPIs the alerts watch | `date, source` | `date` | 92 | 1 |
| `tagline_marts.mart_tag_health_daily` (Stage 5) | date × source × event name × check | `date, source, event_name, check_name` | `date` / `source, event_name` | 8,416 | 239 |
| `tagline_marts.kpi_alerts` (Stage 5) | alert: date × source × metric × rule, replaced by `make alerts` / the DAG with a load job | `date, source, metric, rule` | `date` | 114 | 0 |

Partitioning is by the table's date. Stage 4 measured the layout (experiment 4) and kept it with one change:

- **Daily partitions stay**, because the daily incremental build replaces a day's partitions and reads only
  the partitions it needs. On an unpartitioned copy of `stg_events`, the statement that replaces one day
  would bill the whole table, 2.58 GiB (dry run), against 16 MiB with daily partitions.
- **`stg_events` stays clustered by `source, event_name`.** Within daily partitions of about 28 MiB it
  prunes little but not nothing: reads filtered on `event_name = 'purchase'` billed about 15% less clustered
  than not (`int_purchases` 299 against 353 MiB, check 03 97 against 113 MiB; medians of the final full builds
  against experiment 4's), and building it cost no measurable extra slot time. On an unpartitioned copy clustered the same way, the same purchase read billed 12 MiB
  instead of 303: clustering needs bigger blocks than a day of this data, which is what the daily build
  gives up.
- **`stg_items` is no longer clustered**: nothing reads it by `event_name` or `item_id`, and without the
  clustering its build took about half the slot-ms, for the same bytes. Experiment 4 removed three tables'
  clustering in the same builds (50–53k slot-ms, with `stg_events`, which `stg_items` reads, unclustered too);
  the six final builds, with `stg_events` clustered and `stg_items` not, isolate it: 62k (49–83k) against
  94–168k clustered (baseline and experiment 2, three builds each).
- `fct_sessions` stays clustered by `source`: with and without, the builds were within noise (not isolated:
  its input `stg_events` was unclustered in the same builds). The marts'
  date partitions are tiny (`fct_orders` is 1.3 MiB in 93 partitions), below BigQuery's 10 MiB minimum per
  table read, so they cost nothing either way.
- A layout change needs the table dropped first: `CREATE OR REPLACE` refuses to change a table's clustering
  ("Cannot replace a table with a different partitioning spec").

`session_key` is `source:user_pseudo_id:ga_session_id`. In live exports `ga_session_id` is the
session's start time in seconds; in the obfuscated sample it is not (0 of 360,129 sample
sessions have it within an hour of their first event). Either way it is unique only together
with `user_pseudo_id` (349,545 distinct ids against 360,129 sessions in the sample), so it never
identifies a session alone. `event_key` is `FARM_FINGERPRINT(TO_JSON_STRING(row))` of the whole
export row.

---

## Sources

**`ga4_sample`**: `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`, the Google
Merchandise Store, 2020-11-01 to 2021-01-31. Obfuscated: no `user_id` at all; many values are
`<Other>`, `(data deleted)`, `(not set)` or `<obfuscated>`; `privacy_info` is NULL throughout;
`shipping` is never populated. These values are kept as GA4 wrote them (they are real
categories in the sample's reports), except in `transaction_id` and item text fields, where
`(not set)` and `''` become NULL. The export predates `collected_traffic_source` and
`session_traffic_source_last_click`; its campaign data is in the `source`, `medium`,
`campaign` and `term` event parameters.

**`tagline_site`**: the site's own export, `analytics_<property_id>` in the same project,
created when GA4 is linked to BigQuery. It holds one day so far, the simulator's live run:
`events_20260927` (1,433 rows) and `pseudonymous_users_20260927` (48 rows, not read), and no
streaming table. With `TAGLINE_GA4_DATASET=analytics_<property_id>` in `tagline/.env`, every
build lists the dataset's tables (`sources.py`, unit tested):

- every daily table `events_YYYYMMDD` is read, through the wildcard `events_*` with
  `_TABLE_SUFFIX BETWEEN '<first day>' AND '<last day>'` (an eight-digit range cannot match
  `intraday_…`, so the streaming tables are excluded by the same filter);
- a streaming table `events_intraday_YYYYMMDD` is read only for a day that has no daily table
  yet. Google deletes the streaming table once the day's daily table is complete, but both
  exist for a while, and reading both would count the day twice;
- anything else (`users_*`, `pseudonymous_users_*`) is ignored.

With the variable unset, or set to a dataset with no event tables, the model unions only
the sample and says so on stderr and in every table's description. The site block reads
the two records current exports add: `collected_traffic_source` (the utm_* values collected
with each event) and `session_traffic_source_last_click` (GA4's session attribution,
repeated on every event of the session, with `manual_campaign`, `google_ads_campaign` and
`cross_channel_campaign` subrecords). `stream_id` and `privacy_info.*` are STRING in
current exports and INTEGER in the obfuscated sample; both blocks cast them to STRING.

**Cost guard.** Every query job sets `maximum_bytes_billed` (10 GB unless
`TAGLINE_MAX_BYTES_BILLED` says otherwise), turns the query cache off so recorded costs are
real, and is labelled with its step so Stage 4 can find it in `INFORMATION_SCHEMA.JOBS`.
Every read of an `events_*` wildcard is filtered on `_TABLE_SUFFIX` (a unit test renders
every SQL file and fails if one is not).

---

## Deduplication

1. **Exact duplicate export rows.** GA4's export can contain the same row twice. Rows with
   the same `event_key` (a fingerprint of the entire row) in the same source are collapsed
   to one; `export_row_count` records how many there were. The sample has none: 4,295,584
   export rows, 4,295,584 staged events (check 05 enforces this and the documented count of
   0). The site-export fixture plants one and sees it collapsed. The site's export has none
   either: 1,433 export rows, 1,433 staged events.
2. **Which `transaction_id`.** `ecommerce.transaction_id`, else the `transaction_id` event
   parameter, each cleaned first (`''` and `(not set)` become NULL), so a `(not set)` in
   `ecommerce` falls back to the parameter. In the sample, 883 purchases say `(not set)` in
   `ecommerce`; 433 of them carry the real id in the parameter, and 23 more have a NULL
   `ecommerce.transaction_id` and the id in the parameter.
3. **Purchases by `transaction_id`, per device.** The tagging plan (§12) asks Stage 2 to dedupe
   again in SQL. `is_duplicate_purchase` is TRUE for every purchase after the first (by
   `event_timestamp`, then `event_key`) with the same `transaction_id` **from the same device**
   in the same source; `fct_orders` keeps only the first. In the sample every such repeat is a
   real duplicate (same revenue, within the hour): 5,692 purchase events become 5,368 orders,
   and 324 repeat purchases in 318 orders are dropped (at most 3 repeats of one order).
   Counting every purchase event would overstate revenue by $22,020 (6.5%).
4. **The same `transaction_id` on two devices is two orders.** The sample reuses 15 ids across
   devices, weeks apart, with other items and revenue: id collisions, not duplicates (Google:
   the same transaction ID should not be used across different users, and GA4 deduplicates a
   user's purchases). Those 30 orders ($2,152) are all kept, flagged
   `is_transaction_id_collision`, with `order_id = '<transaction_id>@<user_pseudo_id>'` so the
   key stays unique. Deduplicating across devices would drop 15 real orders ($1,190).
5. **Purchases without a usable `transaction_id`** cannot be deduplicated, so each is its own
   order with `order_id = 'no-transaction-id:<event_key>'` and `has_transaction_id = FALSE`.
   The sample has 450, and every one also has no revenue (`purchase_revenue` NULL, the USD
   value 0). They are flagged `is_zero_value_without_id` (no id and no revenue): they stay in
   `fct_orders` so it reconciles with the purchase events, but `fct_sessions`
   (`zero_value_orders`, not `orders`), `converted`, `mart_campaign_daily` and
   `mart_funnel_daily` do not count them. Counted as orders they would add 402 converted
   sessions (9%) that bought nothing verifiable. A site purchase without an id but with
   revenue still counts.

---

## Identity

`int_identity` maps every device (`source, user_pseudo_id`) to a person:

| Device carried | `identity_rule` | `person_id` |
|---|---|---|
| no `user_id` ever | `anonymous_device` | `anon:<source>:<user_pseudo_id>` |
| exactly one `user_id` | `user_id` | that `user_id` |
| two or more `user_id`s | `shared_device` | `anon:<source>:<user_pseudo_id>` (not merged into anyone) |

- A device that signed in once belongs to that person for all of its events, including its
  anonymous events before sign-in and after sign-out (tagging plan §12: events earlier in
  the session belong to the user who signs in later, as GA4 itself treats them).
- Several devices with the same `user_id` share one `person_id`: that is the cross-device
  stitch. `person_device_count` says how many devices a person has.
- **Ambiguous case, a shared device** (two accounts signed in on one browser): the device
  is not given to either person, because guessing would attribute one person's browsing to
  another. Its signed-in sessions still go to whoever signed in (below); its anonymous
  sessions stay with the device's anonymous id.
- The `user_id` the site sends is an opaque 32-hex account id, never an email (tagging plan
  §7); check 07 fails on anything else.

`fct_sessions` resolves each session's person:

| Session | `identity_rule` | `person_id` |
|---|---|---|
| carried a `user_id` (the last one seen, if someone switched accounts mid-session) | `signed_in_session` | that `user_id` |
| anonymous, on a `user_id` device | `device_user_id` | the device's `user_id` (stitched) |
| anonymous, on a shared device | `shared_device_anonymous` | the device's anonymous id |
| anonymous, on a device that never signed in | `anonymous_device` | the device's anonymous id |

`fct_orders` resolves each order's person, starting from the purchase itself:

| Order | `identity_rule` | `person_id` |
|---|---|---|
| the purchase event carried a `user_id` | `signed_in_purchase` | that `user_id` (an account switch mid-session cannot move the order to the other account) |
| otherwise, placed in a session | the session's rule | the session's person |
| no session, device known (no `ga_session_id`) | `device_without_session` | the device's person from `int_identity` |
| no session, no device, no `user_id` (a cookieless purchase) | `cookieless` | `cookieless:<source>:<order_id>`, a per-order anonymous id |

**The sample has no `user_id`**, so for it every person is a single device: 270,154 devices,
270,154 people, all `anonymous_device`, nobody on two devices. Stitching is shown on
site-shaped data by the fixture (`make fixture`): a device's anonymous session a day before
sign-up joins the person (`device_user_id`), a second device logging in to the same account
joins the same person (`person_device_count = 2`), a laptop used by two accounts is kept
apart (`shared_device`), and two consent-denied purchases with no device and no session are
kept as orders, one with its `user_id`'s person, one with a per-order anonymous id.

**On the site's export** stitching happens on real GA4 rows: 48 devices, 29 of them signed in
(`user_id`, 20 people) and 19 never (`anonymous_device`). 8 people are on two or more devices
(17 devices; one person on 3). Each of the 29 signed-in devices has the simulator's account id for
its person, and no account is split. The simulator planned 11 people who sign in on two or more
devices; the other 3 signed in on a second device that denied consent, whose rows carry the
account's `user_id` but no device id, so that device is not counted (details and the list per
person in the [README](../README.md#the-sites-own-export)). Every session on a signed-in device
is `signed_in_session`, because the simulator signs in during the visit's only session: the
export has no `device_user_id` or `shared_device` case.

**Consent-denied (cookieless) rows.** With Consent Mode's analytics storage denied, gtag.js
sends cookieless pings, and Google's export includes the `user_id` the site set. Several
write-ups report that such rows have no `user_pseudo_id` and no `ga_session_id`; Google's
2023 developer blog says instead that each such session gets a different `user_pseudo_id`.
The site's export shows the first: all 415 rows with `analytics_storage = No` (from the 20
devices that rejected or ignored the banner) have neither id, and 75 of them carry a `user_id`
(8 accounts). Devices that accepted are in the export with their `user_pseudo_id` and
`analytics_storage = Yes`, even the 3 whose every recorded hit was sent before they accepted. The
model handles both cases:

- NULL ids (what the site's export has): the event has no `session_key`, so it is in no session
  and not in `int_identity`; `events_without_session` in `make numbers` counts them (415). A
  purchase among them is still an order in `fct_orders`, with the person from the table above, and
  checks 02 and 03 count it as its own documented bucket. It is not in `mart_campaign_daily` or
  `mart_funnel_daily`, which are built from sessions. On the site: 5 orders, $378.98, 3
  `cookieless` and 2 `signed_in_purchase`.
- A new `user_pseudo_id` per session: each denied session becomes its own device and, unless
  it carries a `user_id`, its own anonymous person, so person counts go up with nothing to
  flag them. `fct_sessions.analytics_storage` says which sessions were denied.
- Streaming tables: for a day read from `events_intraday_*` (no daily table yet), pre-consent
  hits reportedly have a NULL `user_pseudo_id` until the daily table fills it in, so on such
  a day an accepting visitor's landing `page_view` can be missing from its session.

**Stitching consent-denied activity depends on a pending decision.** The tagging plan (§8)
sends `user_id` whatever the consent choice, accepted for this demo and marked **Needs
sign-off**. So a denied session or purchase that carries a `user_id` joins that person's
profile here (`signed_in_session`, `signed_in_purchase`). That is only as settled as §8: if
the sign-off says denied activity must stay out of cross-device profiles, the rules above need
a `consent_denied` rule keyed on `analytics_storage`.

Other limits: no stitching without a sign-in (no probabilistic or IP matching). A person is
only as good as the site's account id (the Stage 1 stand-in keeps its directory per browser;
the simulator plays the backend so the same person gets the same id on every device).

---

## Session attribution

Each session gets one `session_source` / `session_medium` / `session_campaign`;
`traffic_source_basis` says which rule gave it:

1. `session_traffic_source_last_click`: GA4's own session attribution (current exports,
   so the site), from one subrecord at a time so a triple never mixes records:
   `cross_channel_campaign` when it has values (it combines every integration: manual,
   Google Ads, Search Ads 360, Display & Video 360, Campaign Manager 360), else
   `manual_campaign` (utm_*), else a Google Ads auto-tagged click with no utm_*, which becomes
   `google / cpc / <Ads campaign name>`. The first event in the session that carries the
   record wins (Google repeats it on every event of the session). Third-party write-ups
   (Adswerve, tanelytics) report that `cross_channel_campaign` is the one that matches the
   session source / medium in GA4's reports; that is not verified here against a GA4 report. In
   the site's export it has a value on all 1,018 consented rows and equals `manual_campaign`
   except on the 13 direct sessions (259 rows), where `manual_campaign` is `(not set)` and
   `cross_channel_campaign` is `(direct) / (none) / (direct)`; `google_ads_campaign` is empty
   (no Ads clicks). All 48 site sessions are attributed by this rule, each to its device's
   planned landing.
2. `collected_at_landing`: the traffic source collected at the session's landing:
   `collected_traffic_source.manual_*` when present, else the `source` / `medium` /
   `campaign` event parameters, on the session's first `page_view`; else on an event before
   it; else, for a session with no `page_view`, on its first event that has them. A source
   first seen later in the session is not used: GA4 does not re-attribute a session mid-way,
   and in the sample most such later sources are the store's own checkout host
   (`shop.googlemerchandisestore.com / referral`, 22,545 of 32,340 sessions). This is how the
   sample is attributed: 233,236 sessions.
3. `first_user_traffic_source`: nothing collected at landing, and it is the device's first
   session (`ga_session_number = 1`): `traffic_source.*`, the source that first acquired the
   device, which for a first session is that session's own acquisition (96,912 sample
   sessions; 5,195 of them `google / cpc`).
4. `no_source_collected`: nothing to go on: `(not set) / (not set) / (not set)` (29,981
   sample sessions, 834 of them with an order). Unknown is kept apart from an explicit
   `(direct) / (none)`, which the sample records on 59,181 sessions.

Why the sample needs rules 2 to 4: it never carries a traffic source on `session_start` or
`first_visit` (0 of 354,970 and 257,462 events), only on some `page_view`s (483,491 of
1,350,428) and other events, so a missing source means unknown, not direct. 66,366 sessions
collect more than one distinct source, which is why the landing rule matters.

`first_user_source` / `medium` / `campaign` on `stg_events` are `traffic_source.*` (user
scope), used only by rule 3. The rule differs by source because the fields differ, so site
and sample sessions are not attributed identically: GA4's reports attribute a direct visit
to an earlier campaign (last non-direct click), and whether the export's session record does
the same is still not observed (the simulator never makes a second visit on a device), while
the sample's rules only look inside the session.

Orders take the attribution of the session they were placed in; `mart_campaign_daily`
groups sessions, orders and revenue by that triple and joins spend from
`tagline_raw.campaign_costs` on the same triple and date. Spend is restricted to the dates
each source has sessions (so the simulator's autumn-2026 budgets do not appear next to
the 2020 sample), and a day with spend and no sessions still appears (cost, zero visits).

---

## Funnel

`mart_funnel_daily` is a closed funnel inside one session: a session reaches `add_to_cart`
only if it also has `view_item`, `begin_checkout` only if it has both, and
`purchase_sessions` only if it has all three and an order (after the dedupe). Order within
the session is not enforced. `converted_sessions` counts every session with an order by any
path, so it reconciles with `fct_sessions`. A zero-value purchase without a `transaction_id`
does not count as an order here. Sample, whole period: 360,129 sessions → 77,020 view_item →
15,173 add_to_cart → 5,959 begin_checkout → 2,573 purchase; 4,446 sessions converted by any
path.

---

## What is synthetic

| Data | Where | How it is labelled |
|---|---|---|
| `unit_cost_usd`, `gross_margin_rate` for the 20 site products | `tagline_raw.products`, `fct_order_items` | margin = a rate per category (Apparel 0.55, Drinkware 0.60, Bags 0.50, Office 0.62, Stickers 0.80) ± a seeded jitter of up to 0.03; `cost_is_synthetic`, table and column descriptions; check 09 |
| daily ad spend | `tagline_raw.campaign_costs`, `mart_campaign_daily.cost_usd`, `roas`, `cost_per_order` | seeded generator in `reference.py`; `is_synthetic`, descriptions say SYNTHETIC; check 09 fails otherwise |
| every simulator visit, order and account | the site's GA4 property and its export (`events_20260927`, from the live run), then every `source = 'tagline_site'` row in `stg_events`, `fct_*`, `mart_*` and the Stage 3 tables | docs only (this file, the README, the simulator's live-mode banner): no column flags it and check 09 does not cover it |
| the site-export fixture | temporary `fake_ga4_events_*` tables in `tagline_raw`, deleted at the end of `make fixture` | table descriptions, `purpose:fixture` label, 24-hour table expiry |

Campaign spend for the sample is sized from the sample's own traffic, as the model
attributes it, at an invented price per session: `google / cpc` about 137 sessions a day
(12,581 in 92 days) at about $0.75; Partners / affiliate about 14 a day (1,250) at about
$0.50; about $120 per newsletter variant, two variants per send, on the send date only (so
$222 to $246 per send). The simulator's three campaigns get flat invented budgets from
2026-09-01 to 2026-12-31. The GA4 sample's revenue is itself obfuscated, so ROAS here
demonstrates the join, not anything about Google's campaigns.

---

## Checks

Each check is a query in `pipeline/sql/checks/` that returns no rows when it passes;
`make build` runs them after the models and fails if any returns rows.

| Check | What it enforces |
|---|---|
| 01 keys unique | every table's key is unique and never NULL (all twelve tables above) |
| 02 orders have session and person | every order has a person; an order with a session is in `fct_sessions` and, unless its own purchase carried a `user_id`, has the session's person and rule; an order with no session is in the documented no-session rules (`signed_in_purchase`, `device_without_session`, `cookieless`); every order line has an order |
| 03 order revenue reconciles | per source: order count, dropped duplicates, zero-value orders, distinct transaction ids, revenue, tax and shipping in `fct_orders` equal the deduplicated purchase events in `stg_events`, and `fct_sessions` `orders + zero_value_orders` and revenue equal the `fct_orders` that have a session |
| 04 marts reconcile | per source: `mart_campaign_daily` sessions, engaged sessions, orders, revenue equal `fct_sessions`, and its cost equals `campaign_costs` on covered dates; funnel sessions and conversions equal `fct_sessions`, and each step is no larger than the one before |
| 05 sample row counts | sample export rows = staged rows + exact duplicates removed, and duplicates removed = the documented 0 |
| 06 sessions cover events | per source: every event with a session id is in exactly one session, every device is in `int_identity`, every session has a person |
| 07 identity | no person has two `user_id`s; each device's person and rule follow from its `user_id`s and each session's from its own `user_id` and device (NULL-safe); every `user_id` is 32-hex |
| 08 no email-like strings | no email (raw or `%40`) in `user_id`, `person_id`, page URL, title, referrer, search term or landing page; reports counts, never values |
| 09 synthetic is labelled | every cost row is flagged synthetic and both reference tables' descriptions say SYNTHETIC |
| 10 KPI reconciles (Stage 5) | `mart_kpi_daily`: one row per date and source for every day with a session, an order or a consent-recording event; every count equals `fct_sessions`, `fct_orders` or `stg_events` on that day; every rate within 0 and 1 |
| 11 tag health reconciles (Stage 5) | `mart_tag_health_daily`: key unique; every event name on every day covered with its exact event count, every session day with its session count; every contract event holds every check the contract generates; status consistent with violations and expectations |

---

## Incremental builds (Stage 4)

`make build` rebuilds every table from every export day (about 8 GiB billed). `make build-incremental`,
and the DAG's default run, processes only the export days that are new or may have changed, and applies
them to every table in **one BigQuery script, in one transaction**
([`incremental.py`](../pipeline/tagline_pipeline/incremental.py)): if any statement fails, every table
stays as it was. The rules below rely on the tables being one consistent build, since each run reads the
previous run's tables as its history. A daily run keeps them so; a full build does not guarantee it (each
model is its own job, see [when to run a full build](#when-to-run-a-full-build)).

**What was staged.** Every build that writes `stg_events` also writes `tagline_staging.staged_export_days`:
one row per site export day, naming the export table the build read for it (the daily table, else the
streaming one) and that table's last modification time and row count, from the export dataset's `__TABLES__`
(metadata, 0 bytes billed) taken **before** the build read the table. A full build replaces the whole table
after `stg_events` (a DDL job that reads nothing; the DAG's `stg_events` task does the same); the daily build
replaces its window's days inside its own transaction, so the record always describes what `stg_events` holds.
Taking the metadata before the read errs one way only: a change that lands while a run reads the day makes the
day look changed next time, never unchanged.

**Which days (the window).** Per source, from the earliest day that needs processing to that source's
newest export day. A day needs processing when:

- it has an export table but no rows in `stg_events` yet (a new day, or a gap after missed runs);
- **its export no longer matches what was staged**: the table a build would read for the day now is another
  one (the day was read from its streaming table and its daily table has landed since), or it was modified
  since, or it has another row count. That covers GA4 adding late events or reprocessing a day (which Google
  says can happen, occasionally, after its usual update period too), a day backfilled or replaced by hand, and
  runs that did not happen: the next run compares with what was staged, whenever that was. A loaded day with
  no record (tables built before the record existed) is read once more, and recorded;
- for the site, it is one of the **4 days up to the newest day that has a daily table**, a floor under the
  rule above. Google updates a daily table "for up to 2 calendar days, plus today" after its date (table
  20220101 through 20220104), so the run on the fourth day after a date is the first to read its final table.
  Counting from the newest *daily* table keeps a streaming table for today (streaming export) from shortening
  that. A streaming table is always in the window anyway, being newer than every daily table, which matters
  because the metadata does not count a streaming table's buffer;
- the sample: static, so only days not loaded yet;
- `SINCE=YYYYMMDD` (`--since`) starts every source's window on that day, and refuses if an earlier export
  day is missing or changed; `LOOKBACK=n` (`--lookback`) replaces the 4, and a lookback below 1 is refused.

This is the third version of the rule. The first re-read the site's newest 3 *export* days and nothing
else: with the DAG reading the previous day's table once it lands (13:36 UTC for this property's first day,
whose time zone is about UTC−7), it stopped reading a day about 17 hours before GA4 stopped updating it (a day
longer with streaming on, whose table for today counted as the newest day), and it never noticed a change to a
day outside those 3. A review found both. The second compared each export table's modification time with the
time the day's `stg_events` partition was last written, which misses a change that lands between a run's read
and its commit (the partition is written after the read, so it looks newer than the change); the record taken
before the read replaced it. The measured runs are not affected: the site export has one day, which every
version reads.

The days already loaded are read from `stg_events`' date partitions (`INFORMATION_SCHEMA.PARTITIONS`, a
metadata query, 10 MiB), the record through the table-data API (no query), and the export's metadata from
`__TABLES__` (0 bytes); both queries are in the run's cost table. A window always runs to the newest day, so
every row it replaces is newer than every row it keeps. The purchase dedupe relies on that.

What is left uncovered: a day deleted from the export, or expired. The run reports it and keeps its rows unless
the window starts on or before it; a full build drops them. No check compares the site's staged rows per day
with its export tables, as check 05 does for the sample: the window re-reads a changed day instead, and a count
check would add a billed query to every run and could not be exact for a streaming table, whose buffer the
metadata does not count (not done).

**What each table does**, with the model files' own SQL (each has an `incremental_filter` hook that a full
build leaves empty):

| table | in the daily build | why |
|---|---|---|
| `stg_events` | the window's date partitions are replaced; earlier purchase rows whose `transaction_id` the window now also shows on another device (or no longer does) get the new collision flag and `order_id` | rows are per export row; the window's purchases are deduplicated against the earlier ones in `int_purchases` |
| `stg_items`, `int_purchases`, `int_device_days` | the window's date partitions are replaced (`int_purchases` also takes the collision updates) | rows are per event, or per device and day |
| `int_identity` | recomputed whole, from `int_device_days` (about 20 MiB) | a sign-in today gives the device's earlier events to the person, and `person_device_count` spans devices |
| `fct_sessions` | every session with an event in the window, before or after this run, is recomputed from all its events (from the first day of the earliest such session); every other session on a device whose person, `user_id` or rule changed gets them again | sessions cross midnight; identity reaches back |
| `fct_orders` | recomputed whole, from `int_purchases`, `fct_sessions` and `int_identity` (about 70 MiB) | an order takes its session's attribution and person |
| `fct_order_items` | the lines of every order that is new, gone or changed, read from the `stg_items` partitions of those orders' dates | lines carry the order's id, person and session |
| `mart_campaign_daily`, `mart_funnel_daily` | recomputed whole | a few MiB of `fct_sessions` |
| `mart_kpi_daily`, `mart_tag_health_daily` (Stage 5) | the days the run can change are deleted and re-inserted: each window source's days from the first day of the earliest recomputed session, plus the days of changed orders and of flipped collisions | one row per day; tag health reads `stg_events`, so only those partitions (711 KiB on the site's day) |
| `staged_export_days` | the site window's days replaced by what this run read (the metadata taken before the read) | the next run compares the export with it |

The three expressions that give a session its person (`-- identity: begin` to `-- identity: end` in
`40_fct_sessions.sql`) are read out of the model file for the re-resolution, so that rule is written once.
Money totals (`fct_sessions.revenue_usd`, `mart_campaign_daily.revenue_usd` and `cost_usd`) are summed as
NUMERIC and turned back into FLOAT64, so a total does not depend on the order BigQuery adds rows in: as a
FLOAT64 sum, one mart row came out as 278.96 in one build and 278.96000000000004 in another, which would
have made an incremental and a full build differ in the last bit.

**Identical to a full build on every step tested.** `make stage4-equivalence` runs in throwaway datasets
(`tagline_s4_eq_*`, deleted at the end, and first if an interrupted run left them) and diffs all ten Stage 2
tables exactly (EXCEPT DISTINCT both ways, row counts, multiset hashes) against a full build of the same export
tables, after the pipeline's nine checks pass on the incremental tables: the GA4 sample built through
2021-01-30 with 2021-01-31 added incrementally; then the site's export day added incrementally; then the
Stage 2 fixture's site rows arriving day by day with a lookback of 1, so that only the rule under test can
bring an earlier day into the window. Each fixture step names the conditional updates that must run (and no
others), the day the site's window must start on, and facts on the incremental tables that show its path was
taken: a sign-up reaching back to a session **and an order** outside the window, a streaming-only day, that
day's daily table arriving (one event fewer, one late event more) after the day was staged from the streaming
one, another device reusing a `transaction_id` from outside the window and the collision going away again on
re-delivery, a session and a device vanishing, a repeat purchase across the window's edge, a device turning
shared (its earlier session and order going back to the device's own person), a day restated after it was
staged, and a run with nothing new. Results: every table identical in all five steps of the first run and of
the rerun on the final Stage 4 code (`bench/results/s4-e1-equivalence.jsonl`, `s4-end-equivalence.jsonl`: the
sample day, the site day and the first three fixture days, each also listing its statements); after the review,
the sample and site scenarios identical again (`s4-review-equivalence.jsonl`), and the seven fixture steps above
identical, every expectation met, on the code as it stands (`s4-fix-equivalence.jsonl`, 19 minutes, 9.0 GiB,
about $0.055).

### When to run a full build

`make build`, or the DAG with `{"full_refresh": true}`:

- the first build;
- after changing a model's SQL: the daily build only redoes the window, so older rows would keep the old logic;
- after `make reference` changes `tagline_raw.products`: `fct_order_items` takes each line's unit cost and
  catalog flag from it, and the daily build recomputes only the lines of changed orders, so older lines would
  keep the old costs (the campaign mart is recomputed whole and takes new campaign costs on its own);
- **after a full build that failed part way** (`make build`, or a full-refresh DAG run, stopping at a model
  that fails): each model is its own `CREATE OR REPLACE` job, with no transaction around them, so the tables
  are left part new and part old. The next daily run would build on that mix and never repair it:
  rerun the full build until it succeeds;
- after an export day is deleted or expires (the daily run reports it and keeps its rows), or if the GA4
  property's time zone changes (the window assumes a later `event_date` means a later event).

A day replaced, restated or backfilled in the export no longer needs one: its table no longer matches
`staged_export_days`, which puts it back in the window. A partial build that does not write `stg_events`
(`--from`, `--only` another model) leaves that record alone, as it leaves `stg_events`. Nothing checks that the
tables are one consistent build; a build id stamped on every table by the full build, which the daily build
would refuse to run past when the stamps differ, would, and is not done.

---

## Reconciled numbers (GA4 sample)

| | |
|---|---|
| export rows read | 4,295,584 (2020-11-01 to 2021-01-31) |
| events staged | 4,295,584 (0 exact duplicates) |
| devices = people | 270,154 |
| sessions | 360,129 (320,096 engaged) |
| purchase events | 5,692 |
| repeat purchases dropped | 324 (in 318 orders) |
| orders | 5,368 (450 without a transaction_id, all zero-value; 30 on 15 transaction ids used on two devices) |
| sessions with an order | 4,446 (zero-value orders without an id not counted) |
| revenue | $340,145.00 (tax $28,164.00; shipping not in the sample) |
| order lines | 15,063 (21,416 units, $340,100.00 of item revenue) |

Order revenue and item revenue differ by $45 net, in the sample's own rows: in 937 orders
the items do not add up to the purchase revenue (448 lines have no revenue, 2 orders have no
items). `fct_orders` reports purchase revenue; `fct_order_items` reports what the lines say.

A full build plus checks processes 8.57 GiB and bills 8.62 GiB, about $0.05 at on-demand
prices; `stg_events` is 3.34 GiB of it. The cost table in the README has the breakdown. (Those are Stage 2's
numbers. After Stage 4 a full build bills 8.05 GiB, and the daily incremental build 2.12 GiB, 1.51 GiB of it the
nine checks: `bench/results/`.)

## Reconciled numbers (site export)

The site's own export, `events_20260927`, after the build of 2026-09-28 (`make numbers`); checks 02,
03, 04 and 06 reconcile these per source. The simulator's record of the same run is in
`simulator/out/live/`, and the README sets the two side by side.

| | |
|---|---|
| export rows read | 1,433 (one daily table; no streaming table) |
| events staged | 1,433 (0 exact duplicates); 1,018 consented, 415 consent-denied with no device and no session id |
| devices | 48 (29 signed in, 19 anonymous) |
| people | 39 with a session (20 by `user_id`, 8 of them on two or more devices; 19 anonymous devices); 5 more with only an order in no session (2 by `user_id`, 3 `cookieless`) |
| sessions | 48 (45 engaged) |
| purchase events → orders | 13 → 13 (0 repeats; 13 distinct `transaction_id`s, none on two devices, none missing) |
| orders in a session / in none | 8 ($461.95) / 5 ($378.98) |
| sessions with an order | 8 |
| revenue | $840.93 (tax $67.28, shipping $93.00) |
| order lines | 22 (33 units, $840.93; all matched to the catalog, synthetic cost $371.01, margin 56%) |

The same build with both sources processed 8.57 GiB and billed 8.62 GiB, as without the site.

---

## Stage 3 tables

Built by the attribution job in `tagline/spark/` (PySpark on Dataproc Serverless, not SQL), which
reads `fct_orders` and `fct_sessions` and overwrites both tables on every run; the rules are in
[spark/README.md](../spark/README.md#the-rules), the checks on them in
`airflow/dags/tagline_airflow/sql/attribution_checks/`. Like Stage 2's tables, both carry a table
description and a description on every column (the job refuses to finish otherwise), labelled
`app:tagline, stage:3`.

```
tagline_marts.fct_orders ───┬──► tagline_marts.fct_attribution ──► tagline_marts.mart_attribution_daily
tagline_marts.fct_sessions ─┘
```

| Table | One row per | Key | Partition | Rows (sample) | Rows (site) |
|---|---|---|---|---|---|
| `tagline_marts.fct_attribution` | order × attribution model × touch (session) | `source, order_id, model, session_key` | `order_date` | 73,890 (4,918 orders, 12,315 touches, 6 models) | 72 (8 orders, 12 touches) |
| `tagline_marts.mart_attribution_daily` | order date × source × model × session source / medium / campaign × `lookback_complete` | all seven | `order_date` | 5,532 (4,982 with credit) | 24 (all with credit) |

Every touch appears under every model, weight 0 included, so the mart has rows with 0 attributed
orders. As with Stage 2's facts, the date partitions prune nothing at this volume.
