# Stage 4 results log (generated: `make bench-report`)

Prices read 2026-09-28: BigQuery on demand $6.25/TiB (US; first 1 TiB a month free); storage per GiB-month active_logical $0.02, long_term_logical $0.01, active_physical $0.04, long_term_physical $0.02 (first 10 GiB of each free); Serverless for Apache Spark standard tier us-central1 $0.06/DCU-hour and $0.000054795/GiB-hour shuffle storage. All money is list price before free tiers.

## A daily run (median build + median batch)

List price. The BigQuery part is the variant's build command (for the baseline, `make build`: 8 models, 9 checks); the DAG also runs the 3 attribution checks (about 80 MiB, $0.0005) and has per-task overhead. Compare the last column with BigQuery's free 1 TiB of queries a month: below it, the BigQuery part is list price, not what the project pays.

| variant | build wall s | batch wall s | BigQuery per run | Spark per run | per run | x30 per month | BigQuery TiB per month |
|---|---|---|---|---|---|---|---|
| baseline | 85.3 | 390 | $0.0526 | $0.0287 | $0.0814 | $2.44 | 0.253 |

## BigQuery runs

Per run: the command's wall time, and the sums over its jobs (from `INFORMATION_SCHEMA.JOBS_BY_PROJECT`). Median (min–max) over the runs. Bytes billed repeat almost exactly (a job that prunes clustered blocks can move by a MiB between rebuilds); slot-ms and seconds do not.

| variant | runs | command wall s | jobs | bytes billed | slot-ms | job seconds | list price per run | x30 per month | checks |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 3 | 85.3 (81.3–91.4) | 17 | 8.624 GiB (8.623 GiB–8.625 GiB) | 1,706,455 (1,623,238–1,735,924) | 60.6 (55.8–65.2) | $0.0526 | $1.579 | 9/9 checks passed |

### baseline: per job, median (min–max) over 3 runs

| step | kind | bytes processed | bytes billed | slot-ms | elapsed s | slot-ms spread |
|---|---|---|---|---|---|---|
| stg_events | model | 3.340 GiB | 3.340 GiB | 875,877 (832,771–961,150) | 15.0 (13.2–15.3) | 15% |
| stg_items | model | 1.363 GiB | 1.363 GiB | 97,886 (93,720–103,435) | 3.7 (3.2–3.7) | 10% |
| int_identity | model | 341.1 MiB | 342.0 MiB | 52,224 (34,305–52,707) | 6.5 (5.8–6.6) | 35% |
| fct_sessions | model | 1.135 GiB | 1.136 GiB | 211,936 (196,748–233,715) | 10.9 (7.4–11.1) | 17% |
| fct_orders | model | 562.8 MiB | 563.0 MiB | 96,980 (91,168–125,778) | 3.9 (3.7–5.2) | 36% |
| fct_order_items | model | 377.3 MiB | 378.0 MiB | 61,545 (36,497–63,171) | 3.1 (2.6–3.9) | 43% |
| mart_campaign_daily | model | 21.0 MiB | 21.0 MiB | 35,816 (34,860–36,736) | 3.2 (2.9–3.9) | 5% |
| mart_funnel_daily | model | 8.2 MiB | 10.0 MiB | 21,830 (14,720–23,213) | 3.0 (2.4–3.1) | 39% |
| 01_keys_unique | check | 226.1 MiB | 227.0 MiB | 58,315 (41,481–91,707) | 1.9 (1.4–3.6) | 86% |
| 02_orders_have_session_and_person | check | 38.4 MiB | 39.0 MiB | 47,882 (31,422–61,362) | 0.9 (0.8–1.5) | 63% |
| 03_order_revenue_reconciles | check | 98.7 MiB (97.9 MiB–99.4 MiB) | 99.0 MiB (98.0 MiB–100.0 MiB) | 24,968 (14,894–25,201) | 1.0 (0.9–1.3) | 41% |
| 04_marts_reconcile | check | 10.5 MiB | 40.0 MiB | 41,923 (18,208–48,347) | 1.2 (1.0–1.2) | 72% |
| 05_sample_row_counts | check | 81.9 MiB | 82.0 MiB | 5,173 (4,534–6,036) | 3.3 (3.2–3.4) | 29% |
| 06_sessions_cover_events | check | 330.9 MiB | 331.0 MiB | 15,419 (13,082–23,024) | 1.0 (0.9–1.7) | 64% |
| 07_identity | check | 153.9 MiB | 154.0 MiB | 25,084 (16,178–25,850) | 1.4 (1.3–2.1) | 39% |
| 08_no_email_like_strings | check | 525.2 MiB | 526.0 MiB | 6,780 (6,315–7,441) | 0.5 (0.4–0.7) | 17% |
| 09_synthetic_is_labelled | check | 20.0 MiB | 40.0 MiB | 6,019 (1,959–8,225) | 0.5 (0.5–0.7) | 104% |

## BigQuery jobs by time window

| variant | since | jobs | bytes billed | slot-ms | job seconds | wall s | note |
|---|---|---|---|---|---|---|---|
| baseline | 2026-09-29T00:15:00Z | 6 | 0.0 MiB | 143,068 | 21.0 | - | the connector load jobs of baseline Spark runs 1 to 3: run 1 waited 71 s and 38 s in the shared load pool before starting |

## Spark batches

| variant | run | batch | state | wall s | pending s | running s | DCU-hours | avg DCUs running | shuffle GB-h | compute s | write s | loads s | mode | list price |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 1 | `tagline-attr-20260929-4d7ba0f7-t1-d362fe` | SUCCEEDED | 517 | 53 | 464 | 0.6195 | 4.8 | 38.72 | 180.0 | 236.5 | 2.5, 4.3 | local | $0.0393 |
| baseline | 2 | `tagline-attr-20260929-e2c6780e-t2-7bdb47` | SUCCEEDED | 380 | 47 | 333 | 0.4472 | 4.8 | 27.95 | 167.6 | 122.6 | 6.3, 3.2 | local | $0.0284 |
| baseline | 3 | `tagline-attr-20260929-8337086e-t3-f1077c` | SUCCEEDED | 390 | 52 | 338 | 0.4527 | 4.8 | 28.30 | 170.6 | 123.2 | 2.4, 2.4 | local | $0.0287 |

| variant | succeeded | wall s | pending s | DCU-hours | compute s | write s | list price | x30 per month |
|---|---|---|---|---|---|---|---|---|
| baseline | 3 of 3 | 390 (380–517) | 52 (47–53) | 0.4527 (0.4472–0.6195) | 170.6 (167.6–180.0) | 123.2 (122.6–236.5) | $0.0287 ($0.0284–$0.0393) | $0.861 |

## Storage

### baseline, observed 2026-09-29T00:10:58.342436Z: before the baseline builds: the tables as the Stage 3 runs of 2026-09-28 left them (many rebuilds that day)

Source: tables.get metadata (fail-safe bytes and dropped tables not visible there, so the physical cost is a lower bound). Physical = current + time travel.

| dataset | billing model | time travel h | tables | rows | logical | active logical | long-term logical | physical | current physical | time travel | fail-safe | $/month logical | $/month physical |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytics_<property_id> | LOGICAL (default) | 168 | 3 | 1,501 | 2.1 MiB | 2.1 MiB | 0.0 MiB | 0.1 MiB | 0.1 MiB | 0.0 MiB | n/a | $0.0000 | $0.0000 |
| tagline_marts | LOGICAL (default) | 168 | 7 | 462,893 | 163.5 MiB | 163.5 MiB | 0.0 MiB | 446.2 MiB | 27.2 MiB | 419.0 MiB | n/a | $0.0032 | $0.0174 |
| tagline_raw | LOGICAL (default) | 168 | 2 | 576 | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | n/a | $0.0000 | $0.0000 |
| tagline_staging | LOGICAL (default) | 168 | 3 | 8,553,088 | 3.403 GiB | 3.403 GiB | 0.0 MiB | 2.976 GiB | 178.5 MiB | 2.801 GiB | n/a | $0.0681 | $0.1190 |

| table | rows | partitions | logical | physical | current physical | time travel | compression (logical / current) |
|---|---|---|---|---|---|---|---|
| tagline_raw.campaign_costs | 556 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 8.6x |
| tagline_raw.products | 20 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.5x |
| tagline_staging.int_identity | 270,202 | - | 34.9 MiB | 146.4 MiB | 8.6 MiB | 137.7 MiB | 4.0x |
| tagline_staging.stg_events | 4,297,017 | 93 | 2.579 GiB | 2.009 GiB | 120.1 MiB | 1.892 GiB | 22.0x |
| tagline_staging.stg_items | 3,985,869 | 93 | 809.2 MiB | 843.3 MiB | 49.7 MiB | 793.6 MiB | 16.3x |
| tagline_marts.fct_attribution | 73,962 | 92 | 18.7 MiB | 12.8 MiB | 1.4 MiB | 11.4 MiB | 13.1x |
| tagline_marts.fct_order_items | 15,085 | 93 | 3.1 MiB | 15.5 MiB | 0.9 MiB | 14.5 MiB | 3.4x |
| tagline_marts.fct_orders | 5,381 | 93 | 1.3 MiB | 14.4 MiB | 0.9 MiB | 13.6 MiB | 1.5x |
| tagline_marts.fct_sessions | 360,177 | 93 | 139.6 MiB | 389.8 MiB | 23.0 MiB | 366.8 MiB | 6.1x |
| tagline_marts.mart_attribution_daily | 5,556 | 92 | 0.5 MiB | 2.6 MiB | 0.3 MiB | 2.3 MiB | 1.4x |
| tagline_marts.mart_campaign_daily | 2,639 | 93 | 0.2 MiB | 6.0 MiB | 0.4 MiB | 5.7 MiB | 0.7x |
| tagline_marts.mart_funnel_daily | 93 | 93 | 0.0 MiB | 5.1 MiB | 0.3 MiB | 4.8 MiB | 0.0x |
| analytics_<property_id>.events_20260927 | 1,433 | - | 2.0 MiB | 0.1 MiB | 0.1 MiB | 0.0 MiB | 27.0x |
| analytics_<property_id>.pseudonymous_users_20260927 | 48 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 1.2x |
| analytics_<property_id>.users_20260927 | 20 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.6x |

### baseline, observed 2026-09-29T00:44:29.333325Z: after the 3 baseline builds and 3 baseline batches

Source: tables.get metadata (fail-safe bytes and dropped tables not visible there, so the physical cost is a lower bound). Physical = current + time travel.

| dataset | billing model | time travel h | tables | rows | logical | active logical | long-term logical | physical | current physical | time travel | fail-safe | $/month logical | $/month physical |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytics_<property_id> | LOGICAL (default) | 168 | 3 | 1,501 | 2.1 MiB | 2.1 MiB | 0.0 MiB | 0.1 MiB | 0.1 MiB | 0.0 MiB | n/a | $0.0000 | $0.0000 |
| tagline_marts | LOGICAL (default) | 168 | 7 | 462,893 | 163.5 MiB | 163.5 MiB | 0.0 MiB | 527.7 MiB | 27.2 MiB | 500.5 MiB | n/a | $0.0032 | $0.0206 |
| tagline_raw | LOGICAL (default) | 168 | 2 | 576 | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | n/a | $0.0000 | $0.0000 |
| tagline_staging | LOGICAL (default) | 168 | 3 | 8,553,088 | 3.403 GiB | 3.403 GiB | 0.0 MiB | 3.501 GiB | 178.6 MiB | 3.326 GiB | n/a | $0.0681 | $0.1400 |

| table | rows | partitions | logical | physical | current physical | time travel | compression (logical / current) |
|---|---|---|---|---|---|---|---|
| tagline_raw.campaign_costs | 556 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 8.6x |
| tagline_raw.products | 20 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.5x |
| tagline_staging.int_identity | 270,202 | - | 34.9 MiB | 172.2 MiB | 8.6 MiB | 163.6 MiB | 4.0x |
| tagline_staging.stg_events | 4,297,017 | 93 | 2.579 GiB | 2.364 GiB | 120.4 MiB | 2.246 GiB | 21.9x |
| tagline_staging.stg_items | 3,985,869 | 93 | 809.2 MiB | 992.1 MiB | 49.6 MiB | 942.5 MiB | 16.3x |
| tagline_marts.fct_attribution | 73,962 | 92 | 18.7 MiB | 17.1 MiB | 1.4 MiB | 15.7 MiB | 13.1x |
| tagline_marts.fct_order_items | 15,085 | 93 | 3.1 MiB | 18.2 MiB | 0.9 MiB | 17.3 MiB | 3.4x |
| tagline_marts.fct_orders | 5,381 | 93 | 1.3 MiB | 17.0 MiB | 0.9 MiB | 16.2 MiB | 1.5x |
| tagline_marts.fct_sessions | 360,177 | 93 | 139.6 MiB | 458.7 MiB | 23.0 MiB | 435.7 MiB | 6.1x |
| tagline_marts.mart_attribution_daily | 5,556 | 92 | 0.5 MiB | 3.6 MiB | 0.3 MiB | 3.3 MiB | 1.4x |
| tagline_marts.mart_campaign_daily | 2,639 | 93 | 0.2 MiB | 7.1 MiB | 0.4 MiB | 6.7 MiB | 0.7x |
| tagline_marts.mart_funnel_daily | 93 | 93 | 0.0 MiB | 6.0 MiB | 0.3 MiB | 5.7 MiB | 0.0x |
| analytics_<property_id>.events_20260927 | 1,433 | - | 2.0 MiB | 0.1 MiB | 0.1 MiB | 0.0 MiB | 27.0x |
| analytics_<property_id>.pseudonymous_users_20260927 | 48 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 1.2x |
| analytics_<property_id>.users_20260927 | 20 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.6x |

## Fingerprints

Distinct fingerprints per table across every record of the variant (1 = identical every time).

| variant | table | fingerprints taken | distinct | rows |
|---|---|---|---|---|
| baseline | fct_attribution | 5 | 1 | 73,962 |
| baseline | fct_order_items | 5 | 1 | 15,085 |
| baseline | fct_orders | 5 | 1 | 5,381 |
| baseline | fct_sessions | 5 | 1 | 360,177 |
| baseline | int_identity | 1 | 1 | 270,202 |
| baseline | mart_attribution_daily | 5 | 1 | 5,556 |
| baseline | mart_campaign_daily | 5 | 1 | 2,639 |
| baseline | mart_funnel_daily | 5 | 1 | 93 |

## Diffs against the baseline dataset

| variant | recorded | table | identical | rows (current / baseline) | only in current | only in baseline |
|---|---|---|---|---|---|---|
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.fct_order_items | True | 15,085 / 15,085 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.fct_orders | True | 5,381 / 5,381 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.fct_sessions | True | 360,177 / 360,177 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.mart_campaign_daily | True | 2,639 / 2,639 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_marts.mart_funnel_daily | True | 93 / 93 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_staging.int_identity | True | 270,202 / 270,202 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_staging.stg_events | True | 4,297,017 / 4,297,017 | 0 | 0 |
| baseline | 2026-09-29T00:46:08.064705Z | tagline_staging.stg_items | True | 3,985,869 / 3,985,869 | 0 | 0 |
