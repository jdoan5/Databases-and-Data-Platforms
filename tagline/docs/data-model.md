# Data model: Tagline Stage 2

Stage 2 turns raw GA4 BigQuery export rows into stitched, enriched tables. Python
(`tagline/pipeline`) loads reference data, builds the SQL models in order and runs the
checks; the modelling is plain SQL in `pipeline/sql/models/`, one file per table. Every table
is `CREATE OR REPLACE`d on every build, carries a table description and a description on
every column (the runner refuses to finish a model with an undocumented column), and is
labelled `app:tagline, stage:2`. Everything lives in one project, in the US multi-region,
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
                                             tagline_staging.stg_events ──► tagline_staging.stg_items
                                                          │                                     │
                                                          ├──► tagline_staging.int_identity     │
                                                          ▼            │                        │
                                             tagline_marts.fct_sessions ◄┘                      │
                                                          │                                     │
                                                          ▼                                     ▼
                                             tagline_marts.fct_orders ──► tagline_marts.fct_order_items
                                                          │                       ▲
tagline/site/src/catalog/products.json ──► tagline_raw.products ──────────────────┘
  + SYNTHETIC unit_cost (reference.py)

reference.py (seeded) ──► tagline_raw.campaign_costs (SYNTHETIC) ──┐
                                                                   ▼
                        fct_sessions ──► tagline_marts.mart_campaign_daily
                        fct_sessions ──► tagline_marts.mart_funnel_daily
```

Build order is the numeric prefix of the model files: `10_stg_events`, `20_stg_items`,
`30_int_identity`, `40_fct_sessions`, `50_fct_orders`, `60_fct_order_items`,
`70_mart_campaign_daily`, `80_mart_funnel_daily`. `stg_`/`int_` models build into
`tagline_staging`, `fct_`/`mart_` into `tagline_marts`. `fct_orders` also reads `stg_events`
(the purchase events) and `int_identity` (the person of an order placed in no session).
`fct_order_items` reads three tables: the order from `fct_orders`, its lines from `stg_items`
(the kept purchase event's items) and the catalog cost from `tagline_raw.products`.

---

## Tables: grain, key, layout

| Table | One row per | Key | Partition / cluster | Rows (sample) | Rows (site) |
|---|---|---|---|---|---|
| `tagline_raw.products` | catalog SKU | `item_id` | none (20 rows) | 20 (the site's catalog) | |
| `tagline_raw.campaign_costs` | day × data source × source / medium / campaign | `cost_date, source, session_source, session_medium, session_campaign` | none (556 rows) | 190 | 366 |
| `tagline_staging.stg_events` | GA4 event (exact export duplicates collapsed) | `source, event_key` | `event_date` / `source, event_name` | 4,295,584 | 1,433 |
| `tagline_staging.stg_items` | item in an ecommerce event's `items` array | `source, event_key, item_index` | `event_date` / `source, event_name, item_id` | 3,982,732 | 3,137 |
| `tagline_staging.int_identity` | device (`user_pseudo_id`) | `source, user_pseudo_id` | none: no date grain, always read whole | 270,154 | 48 |
| `tagline_marts.fct_sessions` | session | `source, user_pseudo_id, ga_session_id` (also `session_key`) | `session_date` / `source` | 360,129 | 48 |
| `tagline_marts.fct_orders` | order | `source, order_id` | `order_date` | 5,368 | 13 |
| `tagline_marts.fct_order_items` | order line | `source, order_id, line_number` | `order_date` | 15,063 | 22 |
| `tagline_marts.mart_campaign_daily` | date × source × session source / medium / campaign | all five | `date` | 2,634 | 5 |
| `tagline_marts.mart_funnel_daily` | date × source | `date, source` | `date` | 92 | 1 |

Partitioning is by the table's date because the spec asks for it and because analysts'
reads will filter on dates; clustering is on the columns such reads are expected to filter on
(`source`, `event_name`, `item_id`). **At this volume none of it prunes anything, measured:**
no model, check or report filters on a date, nothing filters `stg_items` on `event_name` or
`item_id`, and the daily partitions are far below the 64 MB Google gives as the size where
clustering starts to help (`stg_events` averages 28.7 MiB a day, `stg_items` 8.8 MiB, the marts
under 2 MiB; `fct_orders` is 1.3 MiB in 92 partitions). A test query, `COUNT(DISTINCT
session_key)` over `stg_events` `WHERE event_name = 'purchase'`, processed 207,929,957 bytes,
exactly its dry-run upper bound. So the layout is a placeholder: Stage 4 decides it, and its
first hypothesis is already measured (in a scratch copy, the same `stg_events` columns
unpartitioned and clustered by `event_name` processed 9.4 times fewer bytes for that query).

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
| 01 keys unique | every table's key is unique and never NULL (all ten tables above) |
| 02 orders have session and person | every order has a person; an order with a session is in `fct_sessions` and, unless its own purchase carried a `user_id`, has the session's person and rule; an order with no session is in the documented no-session rules (`signed_in_purchase`, `device_without_session`, `cookieless`); every order line has an order |
| 03 order revenue reconciles | per source: order count, dropped duplicates, zero-value orders, distinct transaction ids, revenue, tax and shipping in `fct_orders` equal the deduplicated purchase events in `stg_events`, and `fct_sessions` `orders + zero_value_orders` and revenue equal the `fct_orders` that have a session |
| 04 marts reconcile | per source: `mart_campaign_daily` sessions, engaged sessions, orders, revenue equal `fct_sessions`, and its cost equals `campaign_costs` on covered dates; funnel sessions and conversions equal `fct_sessions`, and each step is no larger than the one before |
| 05 sample row counts | sample export rows = staged rows + exact duplicates removed, and duplicates removed = the documented 0 |
| 06 sessions cover events | per source: every event with a session id is in exactly one session, every device is in `int_identity`, every session has a person |
| 07 identity | no person has two `user_id`s; each device's person and rule follow from its `user_id`s and each session's from its own `user_id` and device (NULL-safe); every `user_id` is 32-hex |
| 08 no email-like strings | no email (raw or `%40`) in `user_id`, `person_id`, page URL, title, referrer, search term or landing page; reports counts, never values |
| 09 synthetic is labelled | every cost row is flagged synthetic and both reference tables' descriptions say SYNTHETIC |

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
prices; `stg_events` is 3.34 GiB of it. The cost table in the README has the breakdown.

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
