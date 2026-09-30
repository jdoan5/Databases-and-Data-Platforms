"""make stage4-equivalence: the daily incremental build must leave every table exactly as a full build would
(Stage 4, experiment 1). Everything runs in throwaway datasets (tagline_s4_eq_*, US, tables expire after a day as a
backstop; any left by an interrupted run are deleted first, and all are deleted at the end); tagline_staging and
tagline_marts are not touched.

Scenarios (each ends with the pipeline's 9 checks on the incremental tables and an exact diff of all 10 Stage 2
tables against a full build of the same export tables: EXCEPT DISTINCT both ways, row counts, multiset hashes):

  sample   the spec's test: a full build of the GA4 sample through 2021-01-30, then 2021-01-31 added incrementally
           (--since 20210131), against a full build through 2021-01-31
  site     then the site's own export day (TAGLINE_GA4_DATASET) added incrementally (the default daily window), against
           a full build of the sample and the site
  fixture  the paths neither of those has: the Stage 2 fixture's hand-built site rows (pipeline/tests/site_export_fixture.py)
           arriving day by day, with one sample day so builds are cheap, and a lookback of 1 so that only the rule
           under test can bring an earlier day into the window (the site window's first day is checked at every step).
           Each step names the conditional updates that must run (and no others) and the facts that show its path was
           taken, checked on the incremental tables before the diff:
             day 1           device A's anonymous session, in which A buys; full build
             day 2           A signs up (its day-1 session and order, outside the window, must become the person's), B
                             buys and sends the purchase twice, a shared laptop, cookieless purchases, a streaming copy of
                             the day next to its daily table (lookback 1)
             day 3           a day with only its streaming table (lookback 1, counted from day 2's daily table)
             days 3 and 4    day 3's daily table arrives (one event fewer, one late event more): day 3 was staged from
                             its streaming table, so the window must reach back to it; on day 4 another device reuses
                             B's transaction_id, so B's two day-2 purchase rows, outside the window, become a collision,
                             and a new device E has its only session (lookback 1)
             days 4 and 5    day 4 re-delivered without that purchase and without E's session (the window must reach
                             back to day 4, whose table changed): B's rows stop being a collision, E's session and E
                             vanish; on day 5 B sends its purchase once more, a repeat of a purchase before the window
                             (lookback 1)
             day 6           W signs in on A, which carried only U: A becomes a shared device, and its earlier anonymous
                             session and order go back to A's own person (lookback 1)
             day 1 again     day 1's table restated after it was staged (one more event): the window must reach back to
                             day 1 because the export table changed, though the lookback is 1
             no change       nothing new: the window is the lookback alone (so the step before recorded what it read)

Results: bench/results/<variant>.jsonl (default s4-e1-equivalence), one record per diff. Exit 1 on any difference or
failed check.
"""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import results
from .config import TAGLINE_DIR

PIPELINE_TESTS = TAGLINE_DIR / "pipeline" / "tests"
DATASETS = {
    "inc_staging": "tagline_s4_eq_inc_staging",
    "inc_marts": "tagline_s4_eq_inc_marts",
    "full_staging": "tagline_s4_eq_full_staging",
    "full_marts": "tagline_s4_eq_full_marts",
    "site": "tagline_s4_eq_site",  # the fixture's export tables, named as GA4 names them
}
LABELS = {"app": "tagline", "stage": "4", "purpose": "equivalence"}


def say(msg: str = "") -> None:
    print(msg, flush=True)


def _param(row: dict, key: str, value: Any) -> None:
    for p in row["event_params"]:
        if p["key"] == key:
            p["value"] = {"int_value": value} if isinstance(value, int) else {"string_value": str(value)}
            return
    raise KeyError(key)


@dataclass(frozen=True)
class Step:
    label: str
    tables: dict[str, list[dict]]  # export tables to load (replacing any of the same name) before the step
    opts: dict  # build_incremental options
    updates: frozenset[str] = frozenset()  # the conditional updates that must run, as "update <table>"; no others may
    since: str | None = None  # the site window's first day, when the step is about where it starts
    facts: tuple[tuple[str, str], ...] = ()  # (what it shows, SQL returning one row with a BOOL column ok)


def _session_rows(templates: list[dict], device: str, start: datetime, number: int, user_id: str | None = None) -> list[dict]:
    """Copies of template rows as one new session of `device`, a second apart from `start`; a template's own user_id is
    kept only on rows that had one and only when user_id is given."""
    out = []
    session_id = int(start.timestamp())
    for n, src in enumerate(templates):
        row = copy.deepcopy(src)
        row["user_pseudo_id"] = device
        row["user_id"] = user_id if src.get("user_id") else None
        row["event_date"] = f"{start:%Y%m%d}"
        row["event_timestamp"] = int((start + timedelta(seconds=n)).timestamp() * 1_000_000)
        _param(row, "ga_session_id", session_id)
        _param(row, "ga_session_number", number)
        out.append(row)
    return out


def fixture_days(fixture) -> list[Step]:
    """The fixture scenario's steps; the first is a full build."""
    fx = fixture.build_fixture()
    d1, d2, d3 = (f"{d:%Y%m%d}" for d in (fixture.DAY1, fixture.DAY2, fixture.DAY3))
    day = lambda n, h: datetime(fixture.DAY1.year, fixture.DAY1.month, fixture.DAY1.day, h, tzinfo=timezone.utc) + timedelta(days=n - 1)  # noqa: E731
    d4, d5, d6 = (f"{day(n, 0):%Y%m%d}" for n in (4, 5, 6))
    c_rows = fx.tables[f"events_intraday_{d3}"]
    c_by_name = {r["event_name"]: r for r in reversed(c_rows)}  # the first row of each name

    # day 1: device A's anonymous session also buys (so the sign-up on day 2, and A turning shared on day 6, must
    # re-person an order outside the window, and its lines)
    a1 = fixture.Session(fixture.DEVICE_A, 1, day(1, 14), None, fixture.FALL_LAUNCH, cross=fixture.cross_channel(fixture.FALL_LAUNCH, "Search Ads 360"))
    a_order = fixture.make_order((("TL-APP-003", 1),), a1.start + timedelta(seconds=90.1), "0ANONA1")
    confirmation = f"/order/{a_order.transaction_id}"
    day1 = copy.deepcopy(fx.tables[f"events_{d1}"]) + [
        fixture.event(a1, 90.0, "page_view", confirmation, "Order confirmed", referrer=fixture.HOST + "/checkout"),
        fixture.event(a1, 90.1, "purchase", confirmation, "Order confirmed", items=a_order.bought(), ecommerce=a_order.ecommerce(), params=a_order.params()),
    ]
    # day 1 restated: GA4 reprocessed the day and it gained an event
    day1_restated = copy.deepcopy(day1) + [fixture.event(a1, 150.0, "page_view", "/", "All products", referrer=fixture.HOST + confirmation)]

    # day 3's daily table: the streaming rows minus the last one, plus a late page_view 30 s after the last kept one
    daily3 = copy.deepcopy(c_rows[:-1])
    late = copy.deepcopy(c_rows[-2])
    late["event_timestamp"] += 30_000_000
    daily3.append(late)

    # day 4: device C comes back and buys, reusing device B's transaction_id from day 2; new device E's only session
    b_purchase = next(r for r in fx.tables[f"events_{d2}"] if r["event_name"] == "purchase" and r["user_pseudo_id"] == fixture.DEVICE_B)
    visit = [c_by_name["session_start"], c_by_name["page_view"]]
    device_e = "5555555555.1790812800"
    c_visit = _session_rows(visit, fixture.DEVICE_C, day(4, 9), 2)
    c_purchase = _session_rows([*visit, b_purchase], fixture.DEVICE_C, day(4, 9), 2)[-1]
    e_visit = _session_rows(visit, device_e, day(4, 11), 1)
    day4 = [*c_visit, c_purchase, *e_visit]
    day4_redelivered = copy.deepcopy(c_visit)

    # day 5: B sends its day-2 purchase again, in a new session: a repeat of a purchase before the window
    day5 = _session_rows([*visit, b_purchase], fixture.DEVICE_B, day(5, 10), 2, user_id=fixture.U)

    # day 6: W signs in on device A, which carried only U until now
    a3 = fixture.Session(fixture.DEVICE_A, 3, day(6, 12), fixture.DIRECT, fixture.FALL_LAUNCH)
    day6 = [
        fixture.event(a3, 0.000, "session_start", "/", "All products"),
        fixture.event(a3, 0.001, "page_view", "/", "All products"),
        fixture.event(a3, 10.0, "page_view", "/signin", "Sign in", referrer=fixture.HOST + "/"),
        fixture.event(a3, 25.0, "login", "/signin", "Sign in", user_id=fixture.W, params={"method": "email"}),
        fixture.event(a3, 26.0, "page_view", "/", "All products", user_id=fixture.W, referrer=fixture.HOST + "/signin"),
    ]

    a, b, e, tx_a, tx_b, u = fixture.DEVICE_A, fixture.DEVICE_B, device_e, a_order.transaction_id, fx.transaction_id, fixture.U
    anon_a = f"anon:tagline_site:{a}"
    b_rows = "FROM `{staging}.stg_events` WHERE source = 'tagline_site' AND event_name = 'purchase' AND user_pseudo_id = '%s' AND event_date = '%s'"
    order_person = ("(SELECT COUNT(*) = 1 AND LOGICAL_AND(person_id = '{p}') FROM `{marts}.fct_orders` WHERE order_id = '%s') AND "
                    "(SELECT COUNT(*) = 1 AND LOGICAL_AND(person_id = '{p}') FROM `{marts}.fct_order_items` WHERE order_id = '%s')") % (tx_a, tx_a)
    iso = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"  # noqa: E731
    return [
        Step("day 1: full build", {f"events_{d1}": day1}, {}),
        Step("day 2: sign-up reaching back to day 1's session and order, duplicate purchase, streaming copy (lookback 1)",
             {f"events_{d2}": fx.tables[f"events_{d2}"], f"events_intraday_{d2}": fx.tables[f"events_intraday_{d2}"]}, {"lookback": 1},
             updates=frozenset({"update fct_sessions"}), since=d2,
             facts=(("A's day-1 order and its line are now U's", "SELECT " + order_person.replace("{p}", u) + " AS ok"),)),
        Step("day 3: streaming table only (lookback 1)", {f"events_intraday_{d3}": c_rows}, {"lookback": 1}, since=d2),
        Step("days 3-4: day 3's daily table replaces the streaming one it was staged from; day 4 reuses B's transaction_id; new device E (lookback 1)",
             {f"events_{d3}": daily3, f"events_{d4}": day4}, {"lookback": 1},
             updates=frozenset({"update stg_events", "update int_purchases"}), since=d3,
             facts=(("B's two day-2 purchase rows became a collision", f"SELECT COUNT(*) = 2 AND LOGICAL_AND(is_transaction_id_collision) AS ok {b_rows % (b, iso(d2))}"),
                    ("device E is in int_identity", f"SELECT COUNT(*) = 1 AS ok FROM `{{staging}}.int_identity` WHERE user_pseudo_id = '{e}'"))),
        Step("days 4-5: day 4 re-delivered without C's purchase and E's session; day 5 repeats B's purchase (lookback 1)",
             {f"events_{d4}": day4_redelivered, f"events_{d5}": day5}, {"lookback": 1},
             updates=frozenset({"update stg_events", "update int_purchases"}), since=d4,
             facts=(("B's day-2 purchase rows are no longer a collision", f"SELECT COUNT(*) = 2 AND LOGICAL_AND(NOT is_transaction_id_collision) AS ok {b_rows % (b, iso(d2))}"),
                    ("B's day-5 purchase is a repeat of its day-2 one", f"SELECT COUNT(*) = 1 AND LOGICAL_AND(is_duplicate_purchase AND order_id = '{tx_b}') AS ok {b_rows % (b, iso(d5))}"),
                    ("E's session and E are gone", f"SELECT (SELECT COUNT(*) FROM `{{marts}}.fct_sessions` WHERE user_pseudo_id = '{e}') "
                     f"+ (SELECT COUNT(*) FROM `{{staging}}.int_identity` WHERE user_pseudo_id = '{e}') "
                     f"+ (SELECT COUNT(*) FROM `{{staging}}.int_device_days` WHERE user_pseudo_id = '{e}') = 0 AS ok"))),
        Step("day 6: W signs in on A, which turns shared: A's earlier anonymous session and order go back to A (lookback 1)",
             {f"events_{d6}": day6}, {"lookback": 1}, updates=frozenset({"update fct_sessions"}), since=d6,
             facts=(("A is a shared device and its day-1 order and line are A's own again",
                     f"SELECT (SELECT identity_rule = 'shared_device' FROM `{{staging}}.int_identity` WHERE user_pseudo_id = '{a}') AND "
                     + order_person.replace("{p}", anon_a) + " AS ok"),)),
        Step("day 1 again: day 1's table restated after it was staged; the window reaches back to it (lookback 1)",
             {f"events_{d1}": day1_restated}, {"lookback": 1}, since=d1),
        Step("no change: nothing new, so the window is the lookback alone (lookback 1)", {}, {"lookback": 1}, since=d6),
    ]


def run(args, cfg, bench_bq) -> int:
    if str(PIPELINE_TESTS) not in sys.path:
        sys.path.insert(0, str(PIPELINE_TESTS))
    from google.api_core.exceptions import NotFound
    from google.cloud import bigquery

    from tagline_pipeline import pipeline
    from tagline_pipeline.bq import BigQuery
    from tagline_pipeline.config import load_config
    from tagline_pipeline.costs import JobStat

    base = load_config()
    client = bigquery.Client(project=base.project, location=base.location)
    stats: list[JobStat] = []
    failures: list[str] = []
    wanted = set(args.scenario or ["sample", "site", "fixture"])
    if "site" in wanted and not base.has_site:
        say("TAGLINE_GA4_DATASET is not set: skipping the site scenario")
        wanted.discard("site")
    if "site" in wanted:
        wanted.add("sample")  # the site day is added on top of the sample scenario's tables

    def make_datasets() -> None:
        expire = 24 * 3600 * 1000
        for key, name in DATASETS.items():
            ds = bigquery.Dataset(f"{base.project}.{name}")
            ds.location = base.location
            ds.default_table_expiration_ms = expire
            ds.labels = dict(LABELS)
            ds.description = "Tagline Stage 4 equivalence test (tagline/bench, make stage4-equivalence): temporary, deleted at the end."
            client.create_dataset(ds, exists_ok=True)

    def drop_datasets() -> None:
        for name in DATASETS.values():
            client.delete_dataset(f"{base.project}.{name}", delete_contents=True, not_found_ok=True)

    def cfg_for(side: str, **over) -> Any:
        return replace(base, staging_dataset=DATASETS[f"{side}_staging"], marts_dataset=DATASETS[f"{side}_marts"], **over)

    def full(side: str, c) -> None:
        bq = BigQuery(c, client=client)
        say(f"  full build ({side}): sample {c.sample_start}..{c.sample_end}, site {c.ga4_dataset and c.ga4_table_prefix + '*' or 'none'}")
        pipeline.build(c, bq, stats=stats)

    last_statements: list[dict] = []

    def incremental(c, **opts):
        """The incremental build, then the checks on its tables. Returns the build's result and the conditional
        updates that ran."""
        bq = BigQuery(c, client=client)
        result = pipeline.build_incremental(c, bq, stats=stats, **opts)
        say(f"  incremental: {result.window.describe()}")
        st = bq.last_script_statements
        billed = sum(s.bytes_billed or 0 for s in st)
        say(f"  incremental script: {len(st)} statements, {billed / 2**20:,.0f} MiB billed")
        last_statements[:] = [{"statement": s.kind, "table": s.step, "bytes_billed": s.bytes_billed, "slot_ms": s.slot_ms} for s in st]
        # the conditional updates show which reach-back paths ran
        updates = [f"{s.kind} {s.step}" for s in st if s.kind == "update"]
        say(f"  conditional updates that ran: {', '.join(updates) or 'none'}")
        checks, _ = pipeline.run_checks(c, bq, result.site, stats=stats)
        bad = [x.name for x in checks if x.failures]
        say(f"  checks on the incremental tables: {len(checks) - len(bad)}/{len(checks)} passed" + (f" (FAILED: {', '.join(bad)})" if bad else ""))
        if bad:
            failures.extend(f"{step_label}: check {b}" for b in bad)
        return result, updates

    def diff(label: str, window_note: str, extra: dict | None = None) -> None:
        from tagline_pipeline.config import SQL_DIR
        from tagline_pipeline.sqlfiles import list_models

        out = {}
        for m in list_models(SQL_DIR):
            layer = "staging" if m.layer == "staging" else "marts"
            d = bench_bq.diff(DATASETS[f"inc_{layer}"], m.name, DATASETS[f"full_{layer}"], None, examples=3)
            out[f"{layer}.{m.name}"] = d
            verdict = "IDENTICAL" if d["identical"] else "DIFFERENT"
            say(f"    {verdict:<9} {m.name:<22} rows {d['current_rows']:,} (incremental) vs {d['baseline_rows']:,} (full); "
                f"only in incremental {d['only_in_current']:,}, only in full {d['only_in_baseline']:,}")
            for ex in d.get("examples", []):
                say(f"      {ex['side']}: {results.scrub(ex['r'], cfg.secrets)[:300]}")
        identical = all(d["identical"] for d in out.values())
        if not identical:
            failures.append(f"{label}: tables differ")
        results.append(
            {"type": "equivalence", "variant": args.variant, "note": args.note, "scenario": label, "window": window_note,
             "statements": list(last_statements), "tables": out, "identical": identical, **(extra or {}), "git": results.git_state()},
            cfg.secrets,
        )

    def expectations(step: Step, result, updates: list[str], inc_bq) -> list[str]:
        """What shows the step took its path: exactly the conditional updates it names, the window's first day, and its
        facts on the incremental tables. Returns the problems (none when all hold)."""
        problems = []
        if set(updates) != set(step.updates):
            problems.append(f"conditional updates {sorted(updates) or 'none'}, expected {sorted(step.updates) or 'none'}")
        site = result.window.get("tagline_site") if result.window is not None else None
        if step.since is not None and (site is None or site.since != step.since):
            problems.append(f"the site window starts at {site.since if site else 'nothing'}, expected {step.since}")
        names = {"staging": f"{base.project}.{DATASETS['inc_staging']}", "marts": f"{base.project}.{DATASETS['inc_marts']}"}
        for what, sql in step.facts:
            rows, stat = inc_bq.query("eq_fact", "bench", sql.format(**names))
            stats.append(stat)
            ok = len(rows) == 1 and rows[0]["ok"] is True
            say(f"  {'holds' if ok else 'DOES NOT HOLD'}: {what}")
            if not ok:
                problems.append(f"does not hold: {what}")
        say(f"  expectations: {'met' if not problems else 'NOT MET: ' + '; '.join(problems)}")
        return problems

    drop_datasets()  # whatever an interrupted run left: its export tables would change what the fixture reads
    make_datasets()
    try:
        if "sample" in wanted:
            step_label = "sample"
            say(f"\n== sample: the GA4 sample through {args.sample_start}..20210130, then 2021-01-31 incrementally")
            full("inc", cfg_for("inc", ga4_dataset=None, ga4_project=None, sample_start=args.sample_start, sample_end="20210130"))
            c = cfg_for("inc", ga4_dataset=None, ga4_project=None, sample_start=args.sample_start, sample_end="20210131")
            incremental(c, since="20210131")
            full("full", cfg_for("full", ga4_dataset=None, ga4_project=None, sample_start=args.sample_start, sample_end="20210131"))
            diff("sample: +20210131", "ga4_sample 20210131 (--since)")
        if "site" in wanted:
            step_label = "site"
            say("\n== site: the site's own export added incrementally (default window)")
            c = cfg_for("inc", sample_start=args.sample_start, sample_end="20210131")
            incremental(c)
            full("full", cfg_for("full", sample_start=args.sample_start, sample_end="20210131"))
            diff("site: + the site's export", "tagline_site (default window)")
        if "fixture" in wanted:
            import site_export_fixture as fixture

            say("\n== fixture: the Stage 2 fixture's site rows day by day, one sample day")
            site_ds = DATASETS["site"]
            site_bq = BigQuery(replace(base, raw_dataset=site_ds), client=client)
            schema = fixture.export_schema(client.get_table(fixture.SAMPLE_SCHEMA_TABLE).schema)
            over = dict(ga4_dataset=site_ds, ga4_project=None, ga4_table_prefix="events_", sample_start="20210131", sample_end="20210131")
            for i, step in enumerate(fixture_days(fixture)):
                label, tables, opts = step.label, step.tables, step.opts
                step_label = f"fixture {label}"
                say(f"\n-- fixture {label}")
                for name, rows in tables.items():
                    stats.append(site_bq.load_json(name, f"{base.project}.{site_ds}.{name}", rows, schema,
                                                   "TEMPORARY SYNTHETIC fixture rows shaped like a GA4 export table (make stage4-equivalence)."))
                    say(f"  loaded {name}: {len(rows)} rows")
                if i == 0:
                    full("inc", cfg_for("inc", **over))
                    continue
                c = cfg_for("inc", **over)
                result, updates = incremental(c, **opts)
                problems = expectations(step, result, updates, BigQuery(c, client=client))
                failures.extend(f"{step_label}: {p}" for p in problems)
                full("full", cfg_for("full", **over))
                diff(f"fixture: {label}", json.dumps(opts), {
                    "expected": {"updates": sorted(step.updates), "since": step.since, "facts": [d for d, _ in step.facts]},
                    "expectations_met": not problems, "problems": problems})
    finally:
        if args.keep:
            say(f"\n--keep: datasets {', '.join(DATASETS.values())} are still there (tables expire in a day)")
        else:
            drop_datasets()
            say(f"\ndeleted {', '.join(DATASETS.values())}")
        billed = sum(s.bytes_billed or 0 for s in stats if s.bytes_billed)
        say(f"pipeline jobs: {len(stats)} records, {billed / 2**30:.2f} GiB billed (${billed / 2**40 * 6.25:.3f})")

    say(f"\n{'EQUIVALENT: every table identical to a full build in every step, and every fixture step took its path' if not failures else 'FAILED: ' + '; '.join(failures)}")
    return 0 if not failures else 1
