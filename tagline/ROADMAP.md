# Tagline roadmap

Stages 1 to 5 built the pipeline: site tags → BigQuery → Spark → Airflow, with tag QA and alerts on top. This page
covers what comes next and how it gets chosen: who the data serves, how a request comes in, how requests are
scored, the backlog scored and sequenced, what would change for a real store, and the decisions made so far.

Every backlog item cites the part of the repo that motivates it. None is a new idea without a trace. Scores were
set on 2026-09-30, with Stage 5 done.

---

## Who the data serves

Tagline has no real users. The four groups below are the people a store's data serves, and the decisions this
project's tables were built to answer. The IDs D1 to D7 are used for Reach in the backlog.

| Group | What they use |
|---|---|
| Analyst | KPI definitions, reconciliation with GA4's own reports, the choice of attribution model: `mart_kpi_daily`, the `fct_*` tables, `fct_attribution` |
| Marketing | spend and return by campaign, credit by channel: `mart_campaign_daily`, `mart_attribution_daily` |
| Product | where shoppers drop out of the funnel: `mart_funnel_daily`, the add-to-cart and checkout-to-purchase rates in `mart_kpi_daily`; signs the tagging plan |
| Engineering (site and data) | whether a tag change can ship, whether a KPI move is a broken tag, what the pipeline costs: tag QA, `mart_tag_health_daily`, `kpi_alerts`, [`bench/results/`](bench/results/) |

Legal is not a user of the data. It approves what may be collected and kept (D7), and the tagging plan has a point
waiting for that approval ([§8](docs/tagging-plan.md#8-consent-consent-mode-v2)).

| ID | Decision | Who makes it | Answered by | What limits the answer today |
|---|---|---|---|---|
| D1 | Move spend between campaigns | Marketing | `mart_campaign_daily`: sessions, orders, revenue, cost, ROAS | Spend is synthetic, the sample's revenue is obfuscated, and the site's traffic is simulated. ROAS shows that the join works, and tells nothing about real campaigns ([README](README.md#what-is-synthetic)) |
| D2 | Which attribution model to report, and how much credit a channel gets | Analyst with Marketing | `fct_attribution`, `mart_attribution_daily`: six models, with `lookback_complete` | A sample journey is one device. 34.8% of the sample's orders have a short lookback. The site's cross-device journeys are seconds apart ([Stage 3 limitations](README.md#stage-3-limitations)) |
| D3 | Which funnel step to fix first | Product | `mart_funnel_daily`, `mart_kpi_daily` | The site has one exported day of 48 sessions |
| D4 | Whether a KPI move is real or a broken tag | Analyst, Engineering | `mart_tag_health_daily`, `kpi_alerts`, the alert message | For the site, only `contract_violation` and `no_data` can fire. On the sample, orders and revenue only catch near-total drops ([monitoring.md](docs/monitoring.md#limitations)) |
| D5 | Whether a tag change can ship | Engineering, with Analytics signing off | the tag QA suite (46 tests, in CI), the contract, the golden snapshots | Scripted journeys in one browser at desktop size, and no GTM container ([tag-qa.md](docs/tag-qa.md#limitations)) |
| D6 | Whether the daily run is worth what it costs | Engineering | `run_summary`, [STAGE4-RESULTS.md](STAGE4-RESULTS.md), [`bench/`](bench/README.md) | Measured at this volume only: a daily DAG run is 383 s and about $0.022 at list price ([What Stage 5 costs](README.md#what-stage-5-costs)) |
| D7 | What may be collected about a person, and for how long it is kept | Product and Engineering; Legal signs off | the [tagging plan §7 to §9](docs/tagging-plan.md#7-identity-user_id), check 08, tag QA's `no-pii` rule | §8 is not signed off, and there is no deletion or retention process |

---

## Intake

### How requests arrive

A request comes in through one of three GitHub issue forms. Other projects share this repository, so the forms are
named "Tagline: …" and add the label `tagline`:

| Form | For | File |
|---|---|---|
| Tagline: data request | a number, a table, a dashboard tile or an alert | [`tagline-data-request.yml`](../.github/ISSUE_TEMPLATE/tagline-data-request.yml) |
| Tagline: tag or contract change | an event or parameter added, changed or removed, or a change to consent or identity behaviour | [`tagline-tag-change.yml`](../.github/ISSUE_TEMPLATE/tagline-tag-change.yml) |
| Tagline: data bug | a number, table, alert or tag that looks wrong | [`tagline-data-bug.yml`](../.github/ISSUE_TEMPLATE/tagline-data-bug.yml) |

GitHub adds a form's labels to an issue only if they already exist in the repository
([issue-form syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)).
All four labels the forms add exist: `tagline`, `data-request` and `tag-change` (created 2026-10-01) and
GitHub's default `bug`. Blank issues stay enabled for the rest of the repository.

### What a request must state

| Every request | Why it is asked | Form field |
|---|---|---|
| The decision it informs | A number that informs no decision is not worth building. A request without one is parked and asked for it; it is not refused | `decision` (required) |
| The metric definition: numerator, denominator, grain, filters, which orders count | The repo already has two conversion rates (`conversion_rate` per session and `checkout_to_purchase_rate`), and orders with no `transaction_id` do not count as conversions. A definition that is written down can be checked | `metric` (required) |
| The deadline, and what happens if it is missed | This sets the sequence when two scores are close, and shows which dates are real | `deadline` (required) |
| Who is affected | This is Reach | `consumers` (required) |
| For a tag change: the event, its parameters, why, the tagging-plan sections and golden snapshots it touches, and whether it breaks readers of the data | This is the pull request [§14](docs/tagging-plan.md#14-changing-the-contract) requires, planned before anyone writes it | tag change form |
| For a bug: expected and observed values with their sources, where the bug shows, the dates, the severity | Enough to reproduce it without asking | bug form |

The repository is public, so every form ends with a required box: nothing in the issue identifies a person or
gives access to a system (no emails, account ids, webhook URLs, keys or cloud project ids).

### Triage cadence

- **Weekly, Mondays, 30 minutes.** For each new issue: check the required fields (anything missing is asked for in
  the issue); score it with RICE as defined below, with the four numbers posted in a comment; give it a row in the
  backlog on this page under Now, Next or Later; reply with where it landed and why. Every new issue gets a reply
  within two working days of the triage. This page is the board. There is no GitHub Projects board, so the
  roadmap is versioned with the code it describes.
- **The same day, outside the cadence: critical data bugs.** Personal data in any table, a wrong number someone is
  already using (orders, revenue, a KPI in a report), or a data check failing. This follows the alert rules, where
  `pii` is always critical ([monitoring.md](docs/monitoring.md#anomaly-alerts)).
- **Tag changes** never merge without the §14 pull request (the plan, `events.schema.json` with its version bumped,
  the builder and its unit test), `npm run tagqa` green, and the golden diff reviewed
  ([tag-qa.md](docs/tag-qa.md#golden-snapshots)).
- **Monthly.** Re-score Now and Next, move items whose gate or trigger has changed, and add any decision taken to
  the [decision log](#decision-log).

---

## Prioritisation: RICE

**Score = Reach × Impact × Confidence ÷ Effort.** Scores rank items against each other within this backlog. They
have no meaning outside it.

| Factor | Scale |
|---|---|
| **Reach** | The number of decisions D1 to D7 (above) whose input the item changes, 1 to 7. With no real users, Reach cannot be counted in people, so it is counted in decisions. The decisions are listed on every row, so each count can be checked |
| **Impact** | **3**: a decision gets an answer it cannot get today, or a wrong or synthetic number stops being read as real. **2**: closes a blind spot a doc names (a failure of a kind the data has, which the numbers or alerts cannot see). **1**: removes a failure that has already happened here at least once, or a manual step on every run or change. **0.5**: a failure that has not happened here yet, or saves a person's time, or $1 a month or more, without changing a number. **0.25**: hygiene, or a saving of under $1 a month or of machine time only (when two clauses apply, the lower score wins) |
| **Confidence** | **100%**: the problem and the size of the fix are measured in this repo. **80%**: the problem is measured and the fix's effect is estimated. **50%**: reasoned from the docs or a vendor's page, not measured here. Anything below 50% is not scored |
| **Effort** | Person-days for the one maintainer, 0.5 at least. Includes the tests, the measurement and the write-up this repo does for every change. Waiting on someone else (Legal, 14 days of data, a backend team) is a gate, not effort |

**Sequencing rules.** Horizons: **Now** is the next two weeks (October 2026). **Next** runs to the end of January
2027. **Later** means when its trigger fires, with no date. Within that:

1. **A hard date overrides the score.** An item with a deadline is placed so that it finishes before the deadline.
   Item 9 has one: the Spark runtime reaches end of support on 2027-01-31.
2. **An item waits for its gate, and whatever another item depends on goes with it or before it.** Items 1 and 4 go
   before 8. Item 11 ships with 8. Item 8 goes before 15, and 2 before 6.
3. **Money outside the formula counts.** Effort is person-days, so a recurring bill is not in the score. That is
   why the always-on scheduler (16) is Later despite scoring 1.07.
4. **Otherwise, the higher score goes first; on a tie, the smaller effort.** Later is listed the same way, but its
   trigger decides when.

---

## Backlog

### Now

| # | Item | Motivated by | Decisions | R | I | C | E | RICE |
|---|---|---|---|--:|--:|--:|--:|--:|
| 1 | **Get the §8 decision signed off:** push `user_id` whatever the consent state, or only after analytics consent; fill in the plan's sign-off table | [tagging plan §8](docs/tagging-plan.md#8-consent-consent-mode-v2) "Needs sign-off"; [Sign-off](docs/tagging-plan.md#sign-off), empty; [Stage 2 limitations](README.md#stage-2-limitations): 75 consent-denied rows from 8 accounts, 2 of the 5 denied orders ($79.00) | D7, D2 | 2 | 3 | 80% | 1 | **4.80** |
| 2 | **Send alerts to a real Slack or Discord channel** | [monitoring.md, Delivery](docs/monitoring.md#delivery): "Tested against a local sink only, never an external service" | D4 | 1 | 2 | 80% | 0.5 | **3.20** |
| 3 | **A reconciliation note: GA4's report against the export** | [README, GA4's own report against the export](README.md#the-sites-own-export): the report's 662 events and 41 users against 1,018 consented rows and 48 devices; revenue equal at $461.95 (consented purchases only); 138 more rows than recorded hits, "not investigated here" | D1, D4 | 2 | 2 | 80% | 1 | **3.20** |
| 4 | **Flag synthetic site rows in the tables**, not only in the docs | [data-model.md, What is synthetic](docs/data-model.md#what-is-synthetic): simulator rows are labelled in the docs only, "no column flags it and check 09 does not cover it" | D1, D2, D3 | 3 | 2 | 80% | 1.5 | **3.20** |
| 5 | **Rerun `make stage4-equivalence` and `make fixture`** on Stage 5's code | [Stage 5 limitations](README.md#stage-5-limitations), "Not rerun" (about 45 GiB and 17 GiB): the equivalence harness now diffs the two monitoring marts, and checks 10 and 11 should pass on the fixture, but neither has been run | D3, D4 | 2 | 0.5 | 80% | 0.5 | **1.60** |
| 6 | **Post failures that come after the alert step** (the Spark batch, the attribution checks) | [monitoring.md, Limitations](docs/monitoring.md#limitations), [orchestration.md, Limitations](docs/orchestration.md#limitations): they are red in the UI and nowhere else. That is what the two capacity failures of 2026-09-30 did ([In the DAG](README.md#in-the-dag)) | D1, D2 | 2 | 1 | 80% | 1 | **1.60** |
| 7 | **Contract versioning:** define what a major, minor and patch bump mean, and add a CI check that a schema change bumps the version | [tagging plan §14](docs/tagging-plan.md#14-changing-the-contract) says only "bump the version"; the contract is 1.0.0; Stage 2 relies on names staying unchanged ([§12](docs/tagging-plan.md#12-how-this-data-is-used-later)); a new required parameter with no warehouse column fails the tag-health generator ([Collected-data QA](README.md#collected-data-qa)) | D5, D4 | 2 | 1 | 80% | 1 | **1.60** |

Total effort for Now: 6.5 person-days. BigQuery: about 62 GiB for item 5 (about $0.38 at list price, inside the
free 1 TiB of queries a month). No Dataproc.

- **1. Done when** the question has gone to whoever signs for Legal, with the measured consequence of each answer.
  If `user_id` stays as it is, nothing changes. If it waits for consent, the site's identity push waits for
  analytics consent, tag QA gets a journey for it, and consent-denied sessions need their own identity rule. That
  follow-up becomes an item of its own. The sign-off table gets names and a date either way. Gate: the answer.
- **2. Done when** `make alerts AS_OF=2021-01-31` has posted once to the channel (then reset to unsent, as the local
  test was) and a DAG run's message has arrived. The URL goes in `tagline/.env` only.
- **3. Done when** the owner has re-read GA4's report for 2026-09-27 (it was read soon after the day, and reports
  can take 24 to 48 hours to finalise), the report's users are worked out from the export or bounded, the 138-row
  gap is explained or bounded, and a short section says which number to use for which question.
- **4. Done when** every `tagline_site` row can be told apart as synthetic or real in SQL, and check 09 fails if it
  cannot. One option is a reference table of the simulator's live runs, taken from its own record of each run,
  joined by time. A marker parameter on the hits would be a contract change (every object in the contract is
  closed), so it would go through the tag-change form.
- **5. Done when** both pass on the current code and the README's "Not rerun" line is replaced with the results.
- **6. Done when** an `on_failure_callback` on `spark_attribution` and the attribution checks posts through the same
  delivery code, and the DagBag test covers it. It needs item 2's channel to be worth anything.
- **7. Done when** the tagging plan defines the three kinds of bump (major: removes, renames or tightens something
  Stage 2, the tag-health checks or GA4 reports read; minor: additive; patch: docs and examples), and CI fails a
  pull request that changes `events.schema.json` without changing its version. The tag-change form's "breaking?"
  question proposes the bump.

### Next

| # | Item | Motivated by | Decisions | R | I | C | E | RICE |
|---|---|---|---|--:|--:|--:|--:|--:|
| 8 | **Real traffic:** deploy the storefront with its GA4 tag, in place of simulated visits only | [README, what the simulator does not produce](README.md#the-sites-own-export): one machine, one user agent, `localhost`, no second visit, no returning device, no Google Ads click; [Stage 2 limitations](README.md#stage-2-limitations) | D1, D2, D3, D4 | 4 | 3 | 50% | 3 | **2.00** |
| 9 | **Move the attribution batch off runtime 3.0 before its end of support, 2027-01-31** | [spark/README.md, Runtime](spark/README.md#runtime): "3.0 is not LTS, end of support 2027-01-31 (2.3 LTS: 2027-11-26)" | D1, D2 | 2 | 0.5 | 80% | 1 | 0.80 |
| 10 | **Dataproc capacity:** a delayed retry and a daily cap on batches | [In the DAG](README.md#in-the-dag): four batches refused in `us-central1` on 2026-09-30 ("does not have enough resources available"); [What Stage 5 costs](README.md#what-stage-5-costs): six batches against a cap of one, because the DAG was rerun | D1, D2 | 2 | 1 | 50% | 1 | **1.00** |
| 11 | **Deletion and retention requests keyed on `user_id`** | [README, Traffic simulator](README.md#traffic-simulator): "rows already exported stay in BigQuery until deleted there"; [STAGE4 E5](STAGE4-RESULTS.md#e5--storage-measured-recommended-nothing-switched): staging tables "cannot expire"; [data-model.md](docs/data-model.md#when-to-run-a-full-build): a deleted export day needs a full build; [tagging plan §7](docs/tagging-plan.md#7-identity-user_id) | D7 | 1 | 3 | 80% | 3 | 0.80 |
| 12 | **Stamp a build id on every table**, and have the daily build refuse to run on mixed stamps | [data-model.md, When to run a full build](docs/data-model.md#when-to-run-a-full-build): a failed full build leaves the tables "part new and part old", and nothing checks that the tables are one consistent build; a build id stamped on every table would, "and is not done" | D1, D2, D3 | 3 | 0.5 | 80% | 1.5 | 0.80 |
| 13 | **Replace both attribution tables in one transaction** | [orchestration.md, Limitations](docs/orchestration.md#limitations): two load jobs about 20 s apart; a batch stopped between them once (the TTL-stopped batch) | D1, D2 | 2 | 1 | 80% | 2 | 0.80 |
| 14 | **Simulator: second visits and returning devices** | [README](README.md#the-sites-own-export): with no second visit, "whether the export's session record credits a direct return to an earlier campaign is still not observed" | D2 | 1 | 2 | 80% | 2 | 0.80 |
| 15 | **Site KPI alerts, ready after 14 days of data** | [monitoring.md, Limitations](docs/monitoring.md#limitations) and [Stage 5 limitations](README.md#stage-5-limitations): the band needs 14 judged days and the floor a day of 100 sessions (the site's day had 48); the thresholds are fitted to the sample | D4 | 1 | 3 | 50% | 2 | 0.75 |

Total effort for Next: 15.5 person-days, plus the waits on items 1 and 15.

- **8.** What becomes real: sessions, devices, sources, consent choices, and the funnel up to checkout. What stays
  synthetic: orders and revenue, since checkout takes no payment. The README's GA4 setup steps
  ([How it was set up](README.md#the-sites-own-export)) already cover the property, the stream settings and the
  export link. Gates: items 1 and 4. **Done when** 14 days of real visits have been exported and built, and every
  check passes.
- **9. Date-bound**, so it is placed by its date despite its score. Move to a runtime still in support (2.3 LTS is
  supported to 2027-11-26, but its Java 17 does not match the JDK the tests run on here). Then rerun
  `make spark-test`, two measured batches and the three attribution checks, and diff the output against the
  current tables. **Target: done by 2027-01-10**, three weeks before the end of support.
- **10.** Capacity is Google's to give, and the refusals came on one afternoon, hence 50% confidence. A cap on
  batches per logical date would have stopped the reruns that made it six batches against a cap of one. A retry
  after a delay of tens of minutes gives capacity time to come back; that part is untested. Another region would
  need a subnet and a bucket of its own: new cloud resources, and the owner's call.
- **11.** The order matters. Delete in the site's export first: the daily window sees the changed row count in
  `staged_export_days` and re-reads the day ([STAGE4, the daily window](STAGE4-RESULTS.md#the-daily-window-after-review)).
  Deleting only downstream would bring the rows back on the next full build. The sample is Google's, not ours to
  delete. A deleted row stays recoverable for the 7-day time-travel window plus 7 days of fail-safe. Retention means
  expiring old export days, then running a full build. Gate: Legal sets a retention period (D7), a question that
  can go with item 1's. **Done when** a `make forget` target has removed a synthetic person from every table and a
  rebuild has not brought them back. This ships with item 8, not after it.
- **13.** Staging tables, then one multi-statement transaction, with capped query jobs. The connector's direct write
  was rejected in Stage 4 because it runs an uncapped MERGE ([STAGE4 E8](STAGE4-RESULTS.md#e8--the-write-path-nothing-changed)).
  It matters once the site's inputs change every day, which is item 8.
- **14.** A deterministic way to see the paths that item 8 may take months to produce. **Done when** the export has
  rows with `ga_session_number` 2 and the campaign-carry question is answered.
- **15.** With about 50 sessions a day, the rate rules judge nothing (their `min_volume` is 100 sessions, or 30
  checkout sessions). Readiness means either that volume, or site-specific volumes in `pipeline/monitoring.toml`
  backtested on the site's own days. Gate: 14 judged days, after item 8. **Done when** a backtest on the site's days
  is written up the way the sample's was ([the backtest](docs/monitoring.md#the-backtest-the-samples-92-days)).

### Later

| # | Item | Motivated by | Decisions | R | I | C | E | RICE | Trigger or gate |
|---|---|---|---|--:|--:|--:|--:|--:|---|
| 16 | An always-on scheduler: Cloud Composer on the [listed path](docs/orchestration.md#what-would-change-on-cloud-composer), or another | [Why not Cloud Composer](docs/orchestration.md#why-not-cloud-composer): $0.72 an hour in Google's example, about $525 a month, against about $0.022 a daily run; [Stage 3 limitations](README.md#stage-3-limitations): local Airflow runs only while the machine is on | D4, D6 | 2 | 2 | 80% | 3 | 1.07 | Someone depends on the daily alert (after items 2, 8 and 15) |
| 17 | Validate in CI and ship without Ajv; load it only with `?debug=1` | [Honest limitations](README.md#honest-limitations): +155 KB (44 KB gzipped), and `new Function` conflicts with a strict Content-Security-Policy | D5 | 1 | 0.5 | 100% | 1 | 0.50 | Real visitors (item 8) |
| 18 | Orders and revenue: a rule that catches partial drops, and a holiday calendar | [How big a drop each KPI sees](docs/monitoring.md#how-big-a-drop-each-kpi-sees): the lower edge of the band sits at 16% and 9% of expected; a ratio test is "not built, and would need its own backtest"; [Limitations](docs/monitoring.md#limitations): no holiday calendar | D4 | 1 | 2 | 50% | 2 | 0.50 | A year of the store's own history |
| 19 | Tag QA at phone size and in a second browser | [tag-qa.md, Limitations](docs/tag-qa.md#limitations): one browser, desktop size | D5 | 1 | 0.5 | 80% | 1 | 0.40 | Real traffic shows which devices matter |
| 20 | A real account service for cross-device identity | [Honest limitations](README.md#honest-limitations): account ids are per browser; [tagging plan §13](docs/tagging-plan.md#13-deviations-from-google), last row; [tag-qa.md](docs/tag-qa.md#limitations): the second browser is simulated | D2, D1 | 2 | 2 | 50% | 5 | 0.40 | A backend with sign-in |
| 21 | Attribute consent-denied purchases that carry a `user_id` to the person's consented sessions | [Stage 3 limitations](README.md#stage-3-limitations): "the rule would change nothing today" | D2, D1 | 2 | 0.5 | 50% | 1.5 | 0.33 | Item 1's answer |
| 22 | Storage billing: switch from logical to physical | [STAGE4 E5](STAGE4-RESULTS.md#e5--storage-measured-recommended-nothing-switched): $0.072 against about $0.014 a month on the incremental path, $0 today (inside the free 10 GiB), and a switch that cannot be undone for 14 days; fail-safe bytes are invisible without `TABLE_STORAGE` access, an IAM change ([Not done](STAGE4-RESULTS.md#not-done-and-why)) | D6 | 1 | 0.25 | 80% | 1 | 0.20 | Storage passes the free 10 GiB |
| 23 | Narrower checks on daily runs | [STAGE4, Not done](STAGE4-RESULTS.md#not-done-and-why): the checks are 71% of a daily run's bytes; row-local checks would read about 0.6 GiB a day less (about $0.11 a month at list price, $0 inside the free tier), and would narrow the correctness gate | D6, D4 | 2 | 0.25 | 80% | 2 | 0.20 | The BigQuery bill leaves the free tier, or volume grows |
| 24 | Per-purpose consent toggles | [tagging plan §8](docs/tagging-plan.md#8-consent-consent-mode-v2), a known simplification; [tag-qa.md](docs/tag-qa.md#limitations): only `G100` and `G111` are tested | D7 | 1 | 0.5 | 80% | 2 | 0.20 | The store runs ads |
| 25 | Server-side tagging, as a consideration, after a GTM container | [Honest limitations](README.md#honest-limitations): no GTM container, validation runs in the page; [tag-qa.md](docs/tag-qa.md#limitations): gtag.js is unpinned, and the GA4 layer needs the network; [tagging plan §9](docs/tagging-plan.md#9-personal-data) and [§11](docs/tagging-plan.md#11-optional-ga4-forwarding): the page URL is cleaned in the browser before gtag.js reads it | D5, D7 | 2 | 1 | 50% | 5 | 0.20 | A GTM container exists, and there is real traffic |
| 26 | A faster daily script | [STAGE4, Not done](STAGE4-RESULTS.md#not-done-and-why): perhaps 10 to 20 s of its roughly 60 s, not tried | D6 | 1 | 0.25 | 50% | 1 | 0.13 | The daily run gets a deadline |

- **16** scores above most of Next and sits here because of rule 3. At $0.72 an hour, it would pass the project's $10
  monthly budget alert within its first 14 hours. The case for it is people depending on the alert, and until item
  8 there are none.
- **25** has not been tried anywhere in this repo. What it could address here: a second place to strip personal data
  and check the contract, under the store's control rather than in the visitor's browser, and less exposure to
  changes in gtag.js. What it costs: a server to run and pay for (not priced here), and a second place the contract
  has to hold. Confidence stays at 50% until someone measures it.

### Housekeeping (the owner's calls, not scored)

These change no decision's input, so RICE does not fit them. Each takes minutes.

- **Firewall.** Done 2026-10-01: `default-allow-ssh`, `-rdp` and `-icmp` (open to `0.0.0.0/0`, used by nothing)
  were deleted; only `default-allow-internal`, which Dataproc needs, remains. A Spark run afterwards succeeded
  ([orchestration.md, Cost guards](docs/orchestration.md#cost-guards-and-retries)).
- **Cloud Storage leftovers.** Done 2026-10-01: the TTL-stopped batch's staging prefix and the two `dataproc-*`
  buckets a runtime 2.3 batch created were deleted; the Spark bucket holds only `code/`. The bucket now has a
  lifecycle rule that deletes objects under `.spark-bigquery-` once they are a day old, so a future stopped batch
  can't leave files behind for long ([orchestration.md, Limitations](docs/orchestration.md#limitations)).

### Closed since the Stage 5 write-up

- **The CI workflows have run on GitHub's runners.** This was an open point in [tag-qa.md](docs/tag-qa.md#ci) and
  the Stage 5 limitations. The push of 2026-09-30 16:30 UTC ran all three, green: `tagline-tagqa` passed 46 of 46
  on Playwright's Chromium 153 (2 workers, 1.2 min), `tagline-pipeline` passed 134 with 1
  skipped, and `tagline-site` passed 67 unit tests. So the Chromium path is no longer unverified. The README's
  Stage 1 and Stage 5 limitations and [tag-qa.md](docs/tag-qa.md#ci), which still said otherwise, were corrected in
  the Stage 6 wrap-up.

---

## What would change for a real store

- **Volume.** The sample is about 47,000 events a day. The site's one day is 1,433 rows. At this size BigQuery's
  10 MiB minimum per table decides the daily bill, and the checks are 71% of it
  ([STAGE4](STAGE4-RESULTS.md#what-the-measurements-taught)). At a store's volume, bytes would decide it, and the
  checks that read whole tables would grow with history. Narrower checks (item 23) would move up. The Stage 4
  figures describe this size only.
- **People and the law.** Real visitors bring the §8 decision, a privacy notice, deletion requests and a retention
  period (items 1 and 11), and per-purpose consent once there are ads (item 24). The plan's sign-off table would
  carry names.
- **Identity.** A real account service returns one `user_id` on every device. Here the simulator writes the account
  directory into each browser to stand in for it (item 20).
- **Money.** Revenue would come from a payment backend, spend from the ad platforms instead of the seeded
  `campaign_costs`, and paid clicks would carry a gclid, which only the Stage 2 fixture's hand-built rows exercise
  (the live export has none). ROAS would then mean something, and D1 would get a real answer.
- **Tags.** A GTM container with its own release process, Ajv out of the production bundle, and tag QA on the
  devices visitors actually use (items 17, 19, 25).
- **Operations.** A scheduler that runs whether or not a laptop is awake, alerts in a channel someone watches,
  failures after the alert step included, and thresholds fitted to the store's own year, holidays included (items
  2, 6, 16, 18).
- **What stays the same.** One contract as the single source of truth, checks that fail the build, a byte cap on
  every job, and changes made one at a time and measured.

---

## Decision log

The key decisions of Stages 1 to 5, and the one for Stage 6's dashboard. A new decision gets a row with its stage
or date, the alternative considered, and why.

| # | Stage | Decision | Alternative considered | Why | Where |
|---|---|---|---|---|---|
| 1 | 1 | `user_id` is an opaque random account id, never the email and never derived from it | A hash of the email, which would give the same id on every device with no backend | Anyone who has the email can compute its hash, and Google's User-ID rule forbids an id a third party could use to identify someone. The price is ids per browser until there is a real account service (item 20). Tag QA fails if the id is a SHA-256 prefix of the email (mutation 9) | [tagging plan §7](docs/tagging-plan.md#7-identity-user_id), [tag-qa.md](docs/tag-qa.md#does-it-catch-regressions) |
| 2 | 1 | The validator reports invalid pushes and still pushes them: it monitors and does not block | Dropping invalid pushes | Dropping data silently would hide the bug the validator exists to show. The price is Ajv in the production bundle (item 17) | [README, What gets tagged](README.md#what-gets-tagged) |
| 3 | 2 | A browser used by two accounts is given to neither | Giving it to the last or the most frequent account | A guess would attribute one person's browsing to another. The device's signed-in sessions still go to whoever signed in | [data-model.md, Identity](docs/data-model.md#identity) |
| 4 | 2 | Consent-denied purchases are kept as orders with no session (`cookieless`, or the account's person when the hit carries a `user_id`) | Dropping them, or inventing sessions for them | The export settled it: every denied row has no device id and no session id. Kept, they reconcile revenue to the export ($840.93). Left out of the channel marts and attribution, they are $378.98 of the site's day, which is stated wherever it matters | [README, The site's own export](README.md#the-sites-own-export) |
| 5 | 3 | Attribution runs in PySpark on Dataproc, checked row by row against an independent BigQuery rebuild | BigQuery SQL alone (the rebuild is about the same size) | Unit tests on hand-built journeys, and Spark on Dataproc under Airflow, were part of the brief. At this volume BigQuery could do it all, and the README says so. The price was 6.5 of Stage 3's 8 minutes a run, cut to 144 s in Stage 4 | [README, Stage 3](README.md#stage-3-attribution-in-spark-orchestrated-by-airflow), [spark/README.md](spark/README.md) |
| 6 | 3 | A journey ends at the order's session, not at the purchase | Ending it at the purchase (the first version) | That version credited 15 sample orders ($978) to a session opened after the order's session, and moved 11 of them to another channel. Ending at the order's session makes last click equal Stage 2 to the cent | [spark/README.md, The rules](spark/README.md#the-rules) |
| 7 | 3 | Airflow runs locally in Docker | Cloud Composer | An environment bills every hour it exists: $0.72 an hour in Google's example, about $525 a month, against about $0.022 a daily run. The price is a schedule that runs only while the laptop is on (item 16) | [orchestration.md, Why not Cloud Composer](docs/orchestration.md#why-not-cloud-composer) |
| 8 | 4 | The daily incremental build: one script, one transaction, with the window taken from `staged_export_days` | A full rebuild every day (8.05 GiB); a fixed window of the site's newest 3 days (the first version) | −75% bytes, and identical to a full build on every step tested, including seven fixture steps with a lookback of 1. The fixed window was too short for GA4's three days of updates and could not see changes outside it. The price: it is not faster (+12% wall time), and some cases still need a full build | [STAGE4 E1](STAGE4-RESULTS.md#e1--incremental-daily-processing-kept), [the daily window](STAGE4-RESULTS.md#the-daily-window-after-review) |
| 9 | 4 | Spark runs in `local[4]` on the smallest driver, with 4 shuffle partitions | The runtime's default one-thread `local`; executors (a driver and 2 executors) | In E6, `local[4]` cost $0.0160 a batch against $0.0264 for one-thread `local` and $0.0504 for executors (3.2×), which also ran slower and gave results that depended on network order. With 4 shuffle partitions (E7) and the smallest driver (E9), $0.0054 a batch | [STAGE4 E6](STAGE4-RESULTS.md#e6--why-runtime-30-runs-in-local-mode-local4-kept-executors-reverted), [E9](STAGE4-RESULTS.md#e9--right-sizing-the-minimum-driver-memory-kept) |
| 10 | 4 | Money is summed exactly (NUMERIC in BigQuery, DECIMAL in Spark), then cast back | Float sums, as before | A float sum depends on the order rows arrive in. Three builds gave two fingerprints for one mart, and executors changed the attribution sums. Exact sums are the same in any order; they changed 1 + 1,030 rows in the last bits | [STAGE4 E2b](STAGE4-RESULTS.md#e2b--exact-money-sums-kept-a-correctness-change), [E7](STAGE4-RESULTS.md#e7--partitions-exact-mart-sums-first-then-4-shuffle-partitions-kept) |
| 11 | 5 | Alerts judge each day against the median of the same weekday over 21 days, with a leave-one-out MAD spread, k = 4, only in the bad direction | Dropping the weekday centre, or k = 3.5 | Both alternatives catch more injected halvings (30 or 31 of 72, against 22), and both raise 7 or 8 noise incidents on the sample, against the final settings' 1. The owner asked for few false alarms. The price is orders and revenue that only fire on near-total drops (item 18) | [monitoring.md, the backtest](docs/monitoring.md#the-backtest-the-samples-92-days) |
| 12 | 6 | The dashboard is a static page reading a JSON snapshot of the marts (aggregates only), with a live link on the owner's portfolio site: Import-style, not a live query | A live connection that queries BigQuery on every view | A public page cannot hold credentials. A live connection would run queries on the project for every viewer, outside the pipeline's byte cap. And the data changes at most once a day (the sample never), so a snapshot stamped with its time loses nothing. The price: the numbers are as of the snapshot until it is refreshed and pushed again | [`dashboard/`](dashboard/) |
