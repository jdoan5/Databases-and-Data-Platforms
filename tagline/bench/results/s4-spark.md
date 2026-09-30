# Stage 4 results log (generated: `make bench-report`)

Prices read 2026-09-28: BigQuery on demand $6.25/TiB (US; first 1 TiB a month free); storage per GiB-month active_logical $0.02, long_term_logical $0.01, active_physical $0.04, long_term_physical $0.02 (first 10 GiB of each free); Serverless for Apache Spark standard tier us-central1 $0.06/DCU-hour and $0.000054795/GiB-hour shuffle storage. All money is list price before free tiers.

## Spark batches

| variant | run | batch | state | wall s | pending s | running s | DCU-hours | avg DCUs running | shuffle GB-h | compute s | write s | of which summary s | loads s | mode | list price |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| s4-e6-control | 1 | `tagline-attr-20260929-32a3de90-t1-7de513` | SUCCEEDED | 361 | 49 | 313 | 0.4174 | 4.8 | 26.09 | 164.1 | 106.3 | - | 2.2, 2.2 | local (local) | $0.0265 |
| s4-e6-control | 2 | `tagline-attr-20260929-78cd3ca2-t2-ec8f9d` | SUCCEEDED | 358 | 45 | 313 | 0.4140 | 4.8 | 25.88 | 159.2 | 109.4 | - | 2.1, 2.6 | local (local) | $0.0263 |
| s4-e6-local4 | 1 | `tagline-attr-20260929-d83a50a0-t1-c3eff7` | SUCCEEDED | 252 | 56 | 197 | 0.2643 | 4.8 | 16.52 | 93.1 | 60.1 | - | 2.5, 2.4 | local (local[4]) | $0.0168 |
| s4-e6-local4 | 2 | `tagline-attr-20260929-c76deafc-t2-20662f` | SUCCEEDED | 229 | 48 | 182 | 0.2412 | 4.8 | 15.08 | 81.5 | 56.5 | - | 2.5, 2.1 | local (local[4]) | $0.0153 |
| s4-e6-executors | 1 | `tagline-attr-20260929-937deb93-t1-6ea63f` | SUCCEEDED | 310 | 53 | 258 | 0.8489 | 11.9 | 53.05 | 92.3 | 62.4 | - | 2.3, 2.6 | executors (dataproc) | $0.0538 |
| s4-e6-executors | 2 | `tagline-attr-20260929-aa200e38-t2-a6d4b8` | SUCCEEDED | 263 | 41 | 222 | 0.7400 | 12.0 | 46.25 | 75.2 | 64.1 | - | 2.3, 2.5 | executors (dataproc) | $0.0469 |
| s4-e7-exactmart | 1 | `tagline-attr-20260929-651408d1-t1-ec2911` | SUCCEEDED | 315 | 73 | 242 | 0.3265 | 4.9 | 20.41 | 118.5 | 71.2 | 45.7 | 2.3, 2.6 | local (local[4]) | $0.0207 |
| s4-e7-exactmart | 2 | `tagline-attr-20260929-5020cd06-t2-db7a29` | SUCCEEDED | 258 | 51 | 207 | 0.2757 | 4.8 | 17.23 | 100.6 | 63.6 | 39.5 | 2.7, 2.1 | local (local[4]) | $0.0175 |
| s4-e7-shuffle4 | 1 | `tagline-attr-20260929-77066cd9-t1-855c78` | SUCCEEDED | 158 | 52 | 106 | 0.1406 | 4.8 | 8.79 | 30.5 | 32.1 | 3.3 | 2.7, 2.8 | local (local[4]) | $0.0089 |
| s4-e7-shuffle4 | 2 | `tagline-attr-20260929-5e123eb1-t2-41afa4` | SUCCEEDED | 147 | 46 | 101 | 0.1333 | 4.7 | 8.33 | 31.6 | 28.9 | 3.3 | 2.2, 2.0 | local (local[4]) | $0.0085 |
| s4-e7-cachedaqe | 1 | `tagline-attr-20260929-e2a6ff38-t1-dddc0f` | SUCCEEDED | 157 | 51 | 106 | 0.1394 | 4.7 | 8.71 | 31.2 | 30.8 | 2.4 | 2.5, 3.6 | local (local[4]) | $0.0088 |
| s4-e7-cachedaqe | 2 | `tagline-attr-20260929-6515c60f-t2-bd6045` | SUCCEEDED | 147 | 47 | 101 | 0.1330 | 4.7 | 8.31 | 30.8 | 28.5 | 2.6 | 2.2, 3.3 | local (local[4]) | $0.0084 |
| s4-e8-concurrent | 1 | `tagline-attr-20260929-3378e326-t1-dd4e76` | SUCCEEDED | 164 | 48 | 116 | 0.1557 | 4.8 | 9.73 | 33.9 | 37.1 | 4.0 | 2.3, 22.1 | local (local[4]) | $0.0099 |
| s4-e8-concurrent | 2 | `tagline-attr-20260929-0d886f81-t2-ad4ffe` | SUCCEEDED | 172 | 56 | 116 | 0.1545 | 4.8 | 9.65 | 38.6 | 27.8 | 5.4 | 2.3, 2.9 | local (local[4]) | $0.0098 |
| s4-e8-concurrent | 3 | `tagline-attr-20260929-b3993cff-t3-01c209` | SUCCEEDED | 151 | 60 | 91 | 0.1243 | 4.9 | 7.77 | 31.0 | 21.1 | 3.4 | 2.5, 2.9 | local (local[4]) | $0.0079 |
| s4-e9-driver4g | 1 | `tagline-attr-20260929-7b424025-t1-697dfa` | SUCCEEDED | 150 | 43 | 106 | 0.0888 | 3.0 | 8.95 | 32.2 | 31.2 | 4.2 | 2.6, 2.5 | local (local[4]) | $0.0058 |
| s4-e9-driver4g | 2 | `tagline-attr-20260929-12b91cc9-t2-18783c` | SUCCEEDED | 155 | 54 | 101 | 0.0849 | 3.0 | 8.56 | 30.5 | 30.3 | 3.5 | 2.5, 2.3 | local (local[4]) | $0.0056 |

| variant | succeeded | wall s | pending s | DCU-hours | compute s | write s | list price | x30 per month |
|---|---|---|---|---|---|---|---|---|
| s4-e6-control | 2 of 2 | 360 (358–361) | 47 (45–49) | 0.4157 (0.4140–0.4174) | 161.6 (159.2–164.1) | 107.8 (106.3–109.4) | $0.0264 ($0.0263–$0.0265) | $0.791 |
| s4-e6-local4 | 2 of 2 | 241 (229–252) | 52 (48–56) | 0.2528 (0.2412–0.2643) | 87.3 (81.5–93.1) | 58.3 (56.5–60.1) | $0.0160 ($0.0153–$0.0168) | $0.481 |
| s4-e6-executors | 2 of 2 | 287 (263–310) | 47 (41–53) | 0.7944 (0.7400–0.8489) | 83.8 (75.2–92.3) | 63.2 (62.4–64.1) | $0.0504 ($0.0469–$0.0538) | $1.512 |
| s4-e7-exactmart | 2 of 2 | 286 (258–315) | 62 (51–73) | 0.3011 (0.2757–0.3265) | 109.5 (100.6–118.5) | 67.4 (63.6–71.2) | $0.0191 ($0.0175–$0.0207) | $0.573 |
| s4-e7-shuffle4 | 2 of 2 | 152 (147–158) | 49 (46–52) | 0.1369 (0.1333–0.1406) | 31.1 (30.5–31.6) | 30.5 (28.9–32.1) | $0.0087 ($0.0085–$0.0089) | $0.261 |
| s4-e7-cachedaqe | 2 of 2 | 152 (147–157) | 49 (47–51) | 0.1362 (0.1330–0.1394) | 31.0 (30.8–31.2) | 29.6 (28.5–30.8) | $0.0086 ($0.0084–$0.0088) | $0.259 |
| s4-e8-concurrent | 3 of 3 | 164 (151–172) | 56 (48–60) | 0.1545 (0.1243–0.1557) | 33.9 (31.0–38.6) | 27.8 (21.1–37.1) | $0.0098 ($0.0079–$0.0099) | $0.294 |
| s4-e9-driver4g | 2 of 2 | 152 (150–155) | 49 (43–54) | 0.0868 (0.0849–0.0888) | 31.4 (30.5–32.2) | 30.8 (30.3–31.2) | $0.0057 ($0.0056–$0.0058) | $0.171 |

## Fingerprints

Distinct fingerprints per table across every record of the variant (1 = identical every time).

| variant | table | fingerprints taken | distinct | rows |
|---|---|---|---|---|
| s4-e6-control | fct_attribution | 2 | 1 | 73,962 |
| s4-e6-control | mart_attribution_daily | 2 | 1 | 5,556 |
| s4-e6-local4 | fct_attribution | 2 | 1 | 73,962 |
| s4-e6-local4 | mart_attribution_daily | 2 | 1 | 5,556 |
| s4-e6-executors | fct_attribution | 2 | 1 | 73,962 |
| s4-e6-executors | mart_attribution_daily | 2 | 2 | 5,556 |
| s4-e7-exactmart | fct_attribution | 2 | 1 | 73,962 |
| s4-e7-exactmart | mart_attribution_daily | 2 | 1 | 5,556 |
| s4-e7-shuffle4 | fct_attribution | 2 | 1 | 73,962 |
| s4-e7-shuffle4 | mart_attribution_daily | 2 | 1 | 5,556 |
| s4-e7-cachedaqe | fct_attribution | 2 | 1 | 73,962 |
| s4-e7-cachedaqe | mart_attribution_daily | 2 | 1 | 5,556 |
| s4-e8-concurrent | fct_attribution | 3 | 1 | 73,962 |
| s4-e8-concurrent | mart_attribution_daily | 3 | 1 | 5,556 |
| s4-e9-driver4g | fct_attribution | 2 | 1 | 73,962 |
| s4-e9-driver4g | mart_attribution_daily | 2 | 1 | 5,556 |

## Diffs against the baseline dataset

| variant | recorded | table | identical | rows (current / baseline) | only in current | only in baseline |
|---|---|---|---|---|---|---|
| s4-e6-local4 | 2026-09-29T04:14:13.169876Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e6-local4 | 2026-09-29T04:14:13.169876Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e6-executors | 2026-09-29T04:21:17.493473Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e6-executors | 2026-09-29T04:21:17.493473Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 823 | 823 |
| s4-e6-executors | 2026-09-29T04:21:20.461780Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e6-executors | 2026-09-29T04:26:52.081550Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e6-executors | 2026-09-29T04:26:52.081550Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 783 | 783 |
| s4-e6-executors | 2026-09-29T04:26:54.923701Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e7-exactmart | 2026-09-29T04:40:18.063770Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e7-exactmart | 2026-09-29T04:40:18.063770Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-e7-exactmart | 2026-09-29T04:40:24.105348Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e7-shuffle4 | 2026-09-29T04:48:36.754298Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e7-shuffle4 | 2026-09-29T04:48:36.754298Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-e7-shuffle4 | 2026-09-29T04:48:40.442095Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e7-cachedaqe | 2026-09-29T04:56:55.609566Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e7-cachedaqe | 2026-09-29T04:56:55.609566Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-e7-cachedaqe | 2026-09-29T04:56:58.381195Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e8-concurrent | 2026-09-29T05:06:35.330165Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e8-concurrent | 2026-09-29T05:06:35.330165Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-e8-concurrent | 2026-09-29T05:06:39.918501Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e8-concurrent | 2026-09-29T05:12:05.459350Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e8-concurrent | 2026-09-29T05:12:05.459350Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-e8-concurrent | 2026-09-29T05:12:09.228863Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |
| s4-e9-driver4g | 2026-09-29T05:20:39.437753Z | tagline_marts.fct_attribution | True | 73,962 / 73,962 | 0 | 0 |
| s4-e9-driver4g | 2026-09-29T05:20:39.437753Z | tagline_marts.mart_attribution_daily | False | 5,556 / 5,556 | 1,030 | 1,030 |
| s4-e9-driver4g | 2026-09-29T05:20:42.330964Z | tagline_marts.mart_attribution_daily | True | 5,556 / 5,556 | 0 | 0 |

## Load-job probes

| variant | table and layout | runs | load seconds |
|---|---|---|---|
| s4-e8-loads | fct_attribution.partitioned | 3 | 2.75 (2.74–15.02) |
| s4-e8-loads | fct_attribution.unpartitioned | 3 | 2.91 (1.98–6.12) |
| s4-e8-loads | mart_attribution_daily.partitioned | 3 | 2.47 (2.46–2.77) |
| s4-e8-loads | mart_attribution_daily.unpartitioned | 3 | 1.61 (1.26–1.67) |
