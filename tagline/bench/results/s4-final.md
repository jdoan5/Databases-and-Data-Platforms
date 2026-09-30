# Stage 4 results log (generated: `make bench-report`)

Prices read 2026-09-28: BigQuery on demand $6.25/TiB (US; first 1 TiB a month free); storage per GiB-month active_logical $0.02, long_term_logical $0.01, active_physical $0.04, long_term_physical $0.02 (first 10 GiB of each free); Serverless for Apache Spark standard tier us-central1 $0.06/DCU-hour and $0.000054795/GiB-hour shuffle storage. All money is list price before free tiers.

## BigQuery runs

Per run: the command's wall time, and the sums over its jobs (from `INFORMATION_SCHEMA.JOBS_BY_PROJECT`). Median (min–max) over the runs. Bytes billed repeat almost exactly (a job that prunes clustered blocks can move by a MiB between rebuilds); slot-ms and seconds do not.

| variant | runs | command wall s | jobs | bytes billed | slot-ms | job seconds | list price per run | x30 per month | checks |
|---|---|---|---|---|---|---|---|---|---|
| s4-end-full | 3 | 98.7 (85.7–109.8) | 19 | 8.052 GiB (8.045 GiB–8.057 GiB) | 1,793,407 (1,767,071–1,985,295) | 67.2 (58.5–81.2) | $0.0491 | $1.474 | 9/9 checks passed |
| s4-end-daily | 3 | 89.7 (86.6–98.8) | 10 | 2.123 GiB | 650,428 (578,980–653,110) | 71.0 (68.5–81.3) | $0.0130 | $0.389 | 9/9 checks passed |

### s4-end-full: per job, median (min–max) over 3 runs

| step | kind | bytes processed | bytes billed | slot-ms | elapsed s | slot-ms spread |
|---|---|---|---|---|---|---|
| stg_events | model | 3.340 GiB | 3.340 GiB | 956,834 (946,731–962,290) | 14.0 (13.1–17.6) | 2% |
| stg_items | model | 1.225 GiB | 1.226 GiB | 61,682 (51,501–62,785) | 3.0 (2.8–3.0) | 18% |
| int_purchases | model | 299.4 MiB (293.5 MiB–303.2 MiB) | 300.0 MiB (294.0 MiB–304.0 MiB) | 34,795 (31,764–37,300) | 2.6 (2.3–3.1) | 16% |
| int_device_days | model | 232.3 MiB | 233.0 MiB | 44,200 (40,746–49,534) | 5.7 (5.3–6.4) | 20% |
| int_identity | model | 20.0 MiB | 21.0 MiB | 17,327 (15,639–26,020) | 2.7 (2.1–2.9) | 60% |
| fct_sessions | model | 988.1 MiB | 989.0 MiB | 216,917 (214,111–233,861) | 9.2 (7.3–9.7) | 9% |
| fct_orders | model | 69.9 MiB | 70.0 MiB | 109,943 (99,156–136,376) | 4.6 (4.1–24.5) | 34% |
| fct_order_items | model | 377.3 MiB | 378.0 MiB | 88,926 (47,943–210,812) | 3.4 (2.8–4.9) | 183% |
| mart_campaign_daily | model | 21.0 MiB | 21.0 MiB | 41,518 (27,528–68,594) | 3.0 (2.9–3.1) | 99% |
| mart_funnel_daily | model | 8.2 MiB | 10.0 MiB | 25,779 (18,685–30,911) | 2.3 (2.3–2.6) | 47% |
| 01_keys_unique | check | 238.6 MiB | 239.0 MiB | 65,563 (55,228–67,739) | 1.6 (1.1–1.6) | 19% |
| 02_orders_have_session_and_person | check | 38.4 MiB | 39.0 MiB | 34,529 (31,650–58,653) | 0.8 (0.8–1.2) | 78% |
| 03_order_revenue_reconciles | check | 96.9 MiB (95.1 MiB–98.0 MiB) | 97.0 MiB (96.0 MiB–98.0 MiB) | 24,673 (14,893–28,865) | 0.9 (0.8–0.9) | 57% |
| 04_marts_reconcile | check | 10.5 MiB | 40.0 MiB | 35,629 (16,600–38,369) | 0.9 (0.8–1.2) | 61% |
| 05_sample_row_counts | check | 81.9 MiB | 82.0 MiB | 4,673 (4,583–5,204) | 3.7 (3.4–3.8) | 13% |
| 06_sessions_cover_events | check | 330.9 MiB | 331.0 MiB | 17,122 (13,657–31,110) | 1.0 (0.9–1.0) | 102% |
| 07_identity | check | 153.9 MiB | 154.0 MiB | 17,222 (14,415–24,570) | 1.3 (1.1–1.5) | 59% |
| 08_no_email_like_strings | check | 525.2 MiB | 526.0 MiB | 7,010 (6,500–7,432) | 0.5 (0.4–0.6) | 13% |
| 09_synthetic_is_labelled | check | 20.0 MiB | 40.0 MiB | 2,241 (1,787–5,648) | 0.6 (0.5–0.6) | 172% |

### s4-end-daily: per job, median (min–max) over 3 runs

| step | kind | bytes processed | bytes billed | slot-ms | elapsed s | slot-ms spread |
|---|---|---|---|---|---|---|
| incremental | incremental | 290.7 MiB | 627.0 MiB | 411,964 (313,134–462,163) | 59.1 (56.4–69.7) | 36% |
| &nbsp;&nbsp;- | begin_transaction | 0.0 MiB | 0.0 MiB | - | 0.1 (0.1–0.1) | - |
| &nbsp;&nbsp;_old_events | create_table_as_select | 0.1 MiB | 10.0 MiB | 1,526 (1,090–4,773) | 1.8 (1.3–14.0) | 241% |
| &nbsp;&nbsp;_new_events | create_table_as_select | 2.4 MiB | 30.0 MiB | 72,025 (33,385–79,341) | 2.2 (2.1–2.2) | 64% |
| &nbsp;&nbsp;stg_events | merge | 2.6 MiB | 20.0 MiB | 11,705 (6,302–14,355) | 1.7 (1.5–2.1) | 69% |
| &nbsp;&nbsp;_flips | create_table_as_select | 0.3 MiB | 20.0 MiB | 10,716 (8,410–18,247) | 1.6 (1.4–1.9) | 92% |
| &nbsp;&nbsp;- | select | 0.0 MiB | 0.0 MiB | 51 (51–88) | 0.2 (0.2–0.2) | 73% |
| &nbsp;&nbsp;stg_items | merge | 1.5 MiB | 20.0 MiB | 528 (287–557) | 1.3 (1.2–1.3) | 51% |
| &nbsp;&nbsp;int_purchases | merge | 0.1 MiB | 20.0 MiB | 437 (173–471) | 1.3 (1.1–1.8) | 68% |
| &nbsp;&nbsp;- (2) | select | 0.0 MiB | 0.0 MiB | 97 (37–101) | 0.2 (0.2–0.2) | 66% |
| &nbsp;&nbsp;int_device_days | merge | 0.1 MiB | 20.0 MiB | 504 (261–613) | 1.3 (1.3–1.3) | 70% |
| &nbsp;&nbsp;_identity | create_table_as_select | 20.0 MiB | 21.0 MiB | 33,037 (17,868–43,773) | 2.5 (2.0–2.7) | 78% |
| &nbsp;&nbsp;_identity_changed | create_table_as_select | 45.0 MiB | 46.0 MiB | 2,102 (1,623–3,490) | 1.6 (1.3–1.6) | 89% |
| &nbsp;&nbsp;int_identity | merge | 69.8 MiB | 70.0 MiB | 8,683 (6,939–9,801) | 3.9 (3.6–4.0) | 33% |
| &nbsp;&nbsp;_sessions_touched | create_table_as_select | 0.1 MiB | 20.0 MiB | 2,144 (992–2,850) | 1.2 (0.9–1.3) | 87% |
| &nbsp;&nbsp;- (3) | select | 17.4 MiB | 20.0 MiB | 2,152 (2,127–2,488) | 0.4 (0.4–0.6) | 17% |
| &nbsp;&nbsp;fct_sessions | delete | 0.0 MiB | 20.0 MiB | 10,401 (6,550–14,068) | 1.5 (1.4–1.6) | 72% |
| &nbsp;&nbsp;fct_sessions | insert | 22.9 MiB | 40.0 MiB | 13,131 (10,707–31,648) | 2.8 (2.3–3.3) | 159% |
| &nbsp;&nbsp;- (4) | select | 0.0 MiB | 0.0 MiB | 40 (27–40) | 0.2 (0.2–0.2) | 32% |
| &nbsp;&nbsp;_orders | create_table_as_select | 69.9 MiB | 70.0 MiB | 58,090 (54,248–73,375) | 2.3 (2.2–3.8) | 33% |
| &nbsp;&nbsp;_orders_changed | create_table_as_select | 2.6 MiB | 20.0 MiB | 27,641 (14,235–44,281) | 1.5 (1.5–1.9) | 109% |
| &nbsp;&nbsp;fct_orders | merge | 2.6 MiB | 20.0 MiB | 27,653 (24,387–39,288) | 2.5 (2.2–3.0) | 54% |
| &nbsp;&nbsp;- (5) | select | 0.2 MiB | 20.0 MiB | 4,052 (2,201–4,902) | 0.4 (0.3–0.7) | 67% |
| &nbsp;&nbsp;fct_order_items | delete | 3.1 MiB | 20.0 MiB | 12,679 (8,560–27,619) | 1.4 (1.4–2.0) | 150% |
| &nbsp;&nbsp;fct_order_items | insert | 0.6 MiB | 50.0 MiB | 8,689 (7,581–28,258) | 1.4 (1.3–2.4) | 238% |
| &nbsp;&nbsp;mart_campaign_daily | merge | 21.2 MiB | 30.0 MiB | 40,018 (39,042–47,803) | 3.8 (3.0–3.8) | 22% |
| &nbsp;&nbsp;mart_funnel_daily | merge | 8.3 MiB | 20.0 MiB | 29,342 (29,156–41,349) | 3.2 (2.9–3.2) | 42% |
| &nbsp;&nbsp;- | commit_transaction | 0.0 MiB | 0.0 MiB | - | 1.8 (1.3–2.0) | - |
| 01_keys_unique | check | 238.6 MiB | 239.0 MiB | 68,459 (50,859–74,457) | 1.5 (1.2–1.6) | 34% |
| 02_orders_have_session_and_person | check | 38.4 MiB | 39.0 MiB | 45,587 (39,260–61,568) | 0.9 (0.8–1.0) | 49% |
| 03_order_revenue_reconciles | check | 95.1 MiB | 96.0 MiB | 12,522 (11,834–28,967) | 1.0 (1.0–1.2) | 137% |
| 04_marts_reconcile | check | 10.5 MiB | 40.0 MiB | 26,295 (18,447–61,633) | 1.7 (1.0–1.8) | 164% |
| 05_sample_row_counts | check | 81.9 MiB | 82.0 MiB | 5,443 (4,641–5,792) | 3.2 (3.1–3.5) | 21% |
| 06_sessions_cover_events | check | 330.9 MiB | 331.0 MiB | 25,210 (18,539–25,457) | 1.0 (0.9–1.5) | 27% |
| 07_identity | check | 153.9 MiB | 154.0 MiB | 15,795 (15,110–48,008) | 1.2 (1.0–2.3) | 208% |
| 08_no_email_like_strings | check | 525.2 MiB | 526.0 MiB | 6,325 (6,183–7,238) | 0.4 (0.4–0.5) | 17% |
| 09_synthetic_is_labelled | check | 20.0 MiB | 40.0 MiB | 2,251 (1,954–7,423) | 0.5 (0.4–0.6) | 243% |

## BigQuery jobs by time window

| variant | since | jobs | bytes billed | slot-ms | job seconds | wall s | note |
|---|---|---|---|---|---|---|---|
| s4-end-dag-daily | 2026-09-29T05:48:43Z | 14 | 2.211 GiB | 604,680 | 70.1 | 249.1 | airflow dags test tagline_daily 2026-09-28, run manual__2026-09-29T05:48:47, the default daily incremental run: sensor found events_20260927; 19 tasks succeeded, 11 skipped (the full-refresh branch); 249.1 s first task start to last task end |
| s4-end-dag-full | 2026-09-29T05:56:02Z | 22 | 8.131 GiB | 1,883,015 | 60.4 | 278.0 | airflow dags test tagline_daily 2026-09-28 --conf full_refresh=true, run manual__2026-09-29T05:56:05: every model rebuilt; 28 tasks succeeded, 2 skipped (no_ga4_export, stage2_incremental); 278.0 s first task start to last task end |

## Spark batches

| variant | run | batch | state | wall s | pending s | running s | DCU-hours | avg DCUs running | shuffle GB-h | compute s | write s | of which summary s | loads s | mode | list price |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s4-end-spark | 1 | `tagline-attr-20260929-e7bcd4be-t1-da2853` | SUCCEEDED | 154 | 48 | 106 | 0.0872 | 3.0 | 8.80 | 31.6 | 31.7 | 3.6 | 2.6, 2.5 | local (local[4]) | $0.0057 |
| s4-end-spark | 2 | `tagline-attr-20260929-7957f4dc-t2-8cf733` | SUCCEEDED | 134 | 43 | 91 | 0.0764 | 3.0 | 7.70 | 27.0 | 26.7 | 3.1 | 2.4, 2.4 | local (local[4]) | $0.0050 |
| s4-end-dag-daily | 1 | `tagline-attr-20260929-df0db414-t1-9b6f71` | SUCCEEDED | 145 | 44 | 101 | 0.0826 | 2.9 | 8.33 | 29.7 | 30.1 | 3.3 | 2.2, 2.3 | local (local[4]) | $0.0054 |
| s4-end-dag-full | 1 | `tagline-attr-20260929-61124d24-t1-71ff1c` | SUCCEEDED | 183 | 57 | 126 | 0.1036 | 3.0 | 10.45 | 53.6 | 29.8 | 3.9 | 2.2, 2.5 | local (local[4]) | $0.0068 |

| variant | succeeded | wall s | pending s | DCU-hours | compute s | write s | list price | x30 per month |
|---|---|---|---|---|---|---|---|---|
| s4-end-spark | 2 of 2 | 144 (134–154) | 45 (43–48) | 0.0818 (0.0764–0.0872) | 29.3 (27.0–31.6) | 29.2 (26.7–31.7) | $0.0054 ($0.0050–$0.0057) | $0.161 |
| s4-end-dag-daily | 1 of 1 | 145 | 44 | 0.0826 | 29.7 | 30.1 | $0.0054 | $0.162 |
| s4-end-dag-full | 1 of 1 | 183 | 57 | 0.1036 | 53.6 | 29.8 | $0.0068 | $0.204 |

## Storage

### s4-end-storage, observed 2026-09-29T06:04:28.187522Z: end of Stage 4, after the final measurements and both DAG runs; before tagline_s4_baseline is deleted

Source: tables.get metadata (fail-safe bytes and dropped tables not visible there, so the physical cost is a lower bound). Physical = current + time travel.

| dataset | billing model | time travel h | tables | rows | logical | active logical | long-term logical | physical | current physical | time travel | fail-safe | $/month logical | $/month physical |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| analytics_<property_id> | LOGICAL (default) | 168 | 3 | 1,501 | 2.1 MiB | 2.1 MiB | 0.0 MiB | 0.1 MiB | 0.1 MiB | 0.0 MiB | n/a | $0.0000 | $0.0000 |
| tagline_marts | LOGICAL (default) | 168 | 7 | 462,893 | 163.5 MiB | 163.5 MiB | 0.0 MiB | 327.1 MiB | 27.2 MiB | 299.9 MiB | n/a | $0.0032 | $0.0128 |
| tagline_raw | LOGICAL (default) | 168 | 2 | 576 | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | n/a | $0.0000 | $0.0000 |
| tagline_s4_baseline | LOGICAL (default) | 168 | 10 | 9,015,981 | 3.562 GiB | 3.562 GiB | 0.0 MiB | 205.8 MiB | 205.8 MiB | 0.0 MiB | n/a | $0.0712 | $0.0080 |
| tagline_staging | LOGICAL (default) | 168 | 5 | 8,877,907 | 3.426 GiB | 3.426 GiB | 0.0 MiB | 1.614 GiB | 165.8 MiB | 1.452 GiB | n/a | $0.0685 | $0.0646 |

| table | rows | partitions | logical | physical | current physical | time travel | compression (logical / current) |
|---|---|---|---|---|---|---|---|
| tagline_raw.campaign_costs | 556 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 8.6x |
| tagline_raw.products | 20 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.5x |
| tagline_staging.int_device_days | 319,114 | 93 | 22.4 MiB | 94.3 MiB | 8.5 MiB | 85.7 MiB | 2.6x |
| tagline_staging.int_identity | 270,202 | - | 34.9 MiB | 430.6 MiB | 8.6 MiB | 422.0 MiB | 4.0x |
| tagline_staging.int_purchases | 5,705 | 93 | 0.8 MiB | 7.3 MiB | 0.7 MiB | 6.7 MiB | 1.2x |
| tagline_staging.stg_events | 4,297,017 | 93 | 2.579 GiB | 849.3 MiB | 120.9 MiB | 728.4 MiB | 21.8x |
| tagline_staging.stg_items | 3,985,869 | 93 | 809.2 MiB | 271.0 MiB | 27.0 MiB | 244.0 MiB | 29.9x |
| tagline_marts.fct_attribution | 73,962 | 92 | 18.7 MiB | 47.1 MiB | 1.4 MiB | 45.7 MiB | 13.1x |
| tagline_marts.fct_order_items | 15,085 | 93 | 3.1 MiB | 31.0 MiB | 0.9 MiB | 30.1 MiB | 3.4x |
| tagline_marts.fct_orders | 5,381 | 93 | 1.3 MiB | 44.0 MiB | 0.9 MiB | 43.2 MiB | 1.5x |
| tagline_marts.fct_sessions | 360,177 | 93 | 139.6 MiB | 161.0 MiB | 23.0 MiB | 138.0 MiB | 6.1x |
| tagline_marts.mart_attribution_daily | 5,556 | 92 | 0.5 MiB | 10.4 MiB | 0.3 MiB | 10.1 MiB | 1.4x |
| tagline_marts.mart_campaign_daily | 2,639 | 93 | 0.2 MiB | 18.2 MiB | 0.4 MiB | 17.8 MiB | 0.7x |
| tagline_marts.mart_funnel_daily | 93 | 93 | 0.0 MiB | 15.3 MiB | 0.3 MiB | 15.0 MiB | 0.0x |
| tagline_s4_baseline.fct_attribution | 73,962 | 92 | 18.7 MiB | 1.4 MiB | 1.4 MiB | 0.0 MiB | 13.1x |
| tagline_s4_baseline.fct_order_items | 15,085 | 93 | 3.1 MiB | 0.9 MiB | 0.9 MiB | 0.0 MiB | 3.4x |
| tagline_s4_baseline.fct_orders | 5,381 | 93 | 1.3 MiB | 0.9 MiB | 0.9 MiB | 0.0 MiB | 1.5x |
| tagline_s4_baseline.fct_sessions | 360,177 | 93 | 139.6 MiB | 23.0 MiB | 23.0 MiB | 0.0 MiB | 6.1x |
| tagline_s4_baseline.int_identity | 270,202 | - | 34.9 MiB | 8.6 MiB | 8.6 MiB | 0.0 MiB | 4.0x |
| tagline_s4_baseline.mart_attribution_daily | 5,556 | 92 | 0.5 MiB | 0.3 MiB | 0.3 MiB | 0.0 MiB | 1.4x |
| tagline_s4_baseline.mart_campaign_daily | 2,639 | 93 | 0.2 MiB | 0.4 MiB | 0.4 MiB | 0.0 MiB | 0.7x |
| tagline_s4_baseline.mart_funnel_daily | 93 | 93 | 0.0 MiB | 0.3 MiB | 0.3 MiB | 0.0 MiB | 0.0x |
| tagline_s4_baseline.stg_events | 4,297,017 | 93 | 2.579 GiB | 120.4 MiB | 120.4 MiB | 0.0 MiB | 21.9x |
| tagline_s4_baseline.stg_items | 3,985,869 | 93 | 809.2 MiB | 49.6 MiB | 49.6 MiB | 0.0 MiB | 16.3x |
| analytics_<property_id>.events_20260927 | 1,433 | - | 2.0 MiB | 0.1 MiB | 0.1 MiB | 0.0 MiB | 27.0x |
| analytics_<property_id>.pseudonymous_users_20260927 | 48 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 1.2x |
| analytics_<property_id>.users_20260927 | 20 | - | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.0 MiB | 0.6x |

## Fingerprints

Distinct fingerprints per table across every record of the variant (1 = identical every time).

| variant | table | fingerprints taken | distinct | rows |
|---|---|---|---|---|
| s4-end-full | fct_order_items | 3 | 1 | 15,085 |
| s4-end-full | fct_orders | 3 | 1 | 5,381 |
| s4-end-full | fct_sessions | 3 | 1 | 360,177 |
| s4-end-full | mart_campaign_daily | 3 | 1 | 2,639 |
| s4-end-full | mart_funnel_daily | 3 | 1 | 93 |
| s4-end-daily | fct_order_items | 3 | 1 | 15,085 |
| s4-end-daily | fct_orders | 3 | 1 | 5,381 |
| s4-end-daily | fct_sessions | 3 | 1 | 360,177 |
| s4-end-daily | mart_campaign_daily | 3 | 1 | 2,639 |
| s4-end-daily | mart_funnel_daily | 3 | 1 | 93 |
| s4-end-spark | fct_attribution | 2 | 1 | 73,962 |
| s4-end-spark | mart_attribution_daily | 2 | 1 | 5,556 |
| s4-end-dag-daily | fct_attribution | 1 | 1 | 73,962 |
| s4-end-dag-daily | mart_attribution_daily | 1 | 1 | 5,556 |
| s4-end-dag-full | fct_attribution | 1 | 1 | 73,962 |
| s4-end-dag-full | mart_attribution_daily | 1 | 1 | 5,556 |

## Diffs against the baseline dataset

| variant | recorded | table | identical | rows (current / baseline) | only in current | only in baseline |
|---|---|---|---|---|---|---|
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.fct_order_items | True | 15,085 / 15,085 | 0 | 0 |
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.fct_orders | True | 5,381 / 5,381 | 0 | 0 |
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.fct_sessions | True | 360,177 / 360,177 | 0 | 0 |
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.mart_campaign_daily | False | 2,639 / 2,639 | 1 | 1 |
| s4-end-gate | 2026-09-29T05:47:44.536389Z | tagline_marts.mart_funnel_daily | True | 93 / 93 | 0 | 0 |
| s4-end-gate | 2026-09-29T05:47:49.287560Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-end-gate | 2026-09-29T05:47:49.287560Z | tagline_marts.mart_campaign_daily | True | 2,639 / 2,639 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.fct_order_items | True | 15,085 / 15,085 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.fct_orders | True | 5,381 / 5,381 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.fct_sessions | True | 360,177 / 360,177 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.mart_campaign_daily | False | 2,639 / 2,639 | 1 | 1 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_marts.mart_funnel_daily | True | 93 / 93 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_staging.int_identity | True | 270,202 / 270,202 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_staging.stg_events | True | 4,297,017 / 4,297,017 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:45.004479Z | tagline_staging.stg_items | True | 3,985,869 / 3,985,869 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:50.689550Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-end-gate | 2026-09-29T06:03:50.689550Z | tagline_marts.mart_campaign_daily | True | 2,639 / 2,639 | 0 | 0 |
