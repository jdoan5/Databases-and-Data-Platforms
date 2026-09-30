"""Command line: python -m tagline_pipeline <command>, run from tagline/pipeline (or `make` in tagline/,
whose `make help` lists the same commands).

Exit codes: 0 done (and every check passed); 1 a data check failed; 2 bad configuration or usage;
3 Google Cloud credentials missing or expired, or a BigQuery error. The cost table of the jobs that
ran is printed (and written with --costs-json) whatever happened, so a failed build still records
what it billed.

The site-export fixture test (temporary fake GA4 export tables, built, verified and removed) is a
script of its own: tests/site_export_fixture.py, run by `make fixture`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .costs import JobStat, format_cost_table, write_json
from .incremental import DEFAULT_LOOKBACK_DAYS

EXIT_OK, EXIT_CHECK_FAILED, EXIT_CONFIG, EXIT_GOOGLE = 0, 1, 2, 3


def _print_costs(stats: list[JobStat], costs_json: str | None) -> None:
    if not stats:
        return
    print("\nBigQuery jobs (bytes processed / billed, slot-ms, wall seconds):")
    print(format_cost_table(stats))
    if costs_json:
        write_json(stats, Path(costs_json))
        print(f"\ncost table written to {costs_json}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tagline_pipeline", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("reference", help="load products (with synthetic unit_cost) and synthetic campaign_costs into tagline_raw")

    b = sub.add_parser("build", help="build every model in order, then run the checks")
    b.add_argument(
        "--dry-run",
        action="store_true",
        help="validate each model and print the bytes it would scan; creates nothing. Needs a previous build: "
        "later models are estimated against the tables as last built",
    )
    b.add_argument("--from", dest="start_at", metavar="MODEL", help="start at this model (the earlier tables must exist)")
    b.add_argument("--only", metavar="MODEL", help="build just this model")
    b.add_argument("--no-check", action="store_true", help="skip the checks after building")
    b.add_argument("--site-dataset", help="override TAGLINE_GA4_DATASET for this run")
    b.add_argument("--site-table-prefix", help="table prefix in the site dataset (default events_)")
    b.add_argument("--costs-json", metavar="PATH", help="also write the cost table as JSON")
    b.add_argument(
        "--incremental",
        action="store_true",
        help="the daily path: only the export days that are new or may have changed (the site's --lookback days, any day "
        "whose export table no longer matches what was recorded when it was staged, and any day not loaded yet), in one "
        "BigQuery script; needs an earlier full build",
    )
    b.add_argument("--since", metavar="YYYYMMDD", help="with --incremental: reprocess every source from this day on")
    b.add_argument(
        "--lookback",
        type=int,
        default=None,
        help=f"with --incremental: how many of the site's days to re-read at least, counted back from its newest daily "
        f"table (default {DEFAULT_LOOKBACK_DAYS}, at least 1; days whose export table changed since they were staged are "
        "re-read too)",
    )
    b.add_argument("--print-script", action="store_true", help="with --incremental: print the script instead of running it")

    c = sub.add_parser("check", help="run the data checks against the built tables")
    c.add_argument("--costs-json", metavar="PATH")
    c.add_argument(
        "--dir",
        metavar="DIR",
        help="run the checks in DIR instead (same convention: no rows = pass), e.g. the DAG's attribution checks",
    )

    sub.add_parser("numbers", help="print the reconciled numbers (events, sessions, orders, revenue, identity, cost)")

    a = sub.add_parser(
        "alerts",
        help="Stage 5: the anomaly rules on mart_kpi_daily and mart_tag_health_daily (detect: replace kpi_alerts; notify: "
        "send what is new; run: both; backtest: print every alert over every day, write nothing). Reads the marts through "
        "the table-data API and writes with a load job: no query is billed",
    )
    a.add_argument("action", choices=["detect", "notify", "run", "backtest"])
    a.add_argument(
        "--as-of",
        metavar="YYYY-MM-DD",
        help="notify / run: the day alerts are news on (default: today, UTC). detect / run: also the last day the site's "
        "export is due, so days after its newest data up to it are no_data alerts (without it: only gaps between days "
        "with data)",
    )
    a.add_argument("--source", help="backtest: only this source")
    a.add_argument("--fail-on-alert", action="store_true", help="notify / run: exit 1 when this run sends any alert")

    sub.add_parser("contract-sql", help="print the tag health checks generated from tagging/events.schema.json (no BigQuery)")

    args = parser.parse_args(argv)
    if args.command == "contract-sql":  # needs no project or credentials
        from . import contract

        c = contract.load_contract()
        print(f"-- generated from {contract.CONTRACT_FILE.name}, contract version {c.version}: {len(c.rules)} required fields "
              f"in {len(c.events)} events; email pattern {c.email_pattern}\n-- checks per event:")
        print(contract.checks_sql(c, indent=""))
        print("-- pii (every event):\n" + contract.pii_sql(c))
        return EXIT_OK
    try:
        cfg = load_config(
            ga4_dataset=getattr(args, "site_dataset", None),
            ga4_table_prefix=getattr(args, "site_table_prefix", None),
        )
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    # Imported late so `--help` and configuration errors work without the Google client.
    from google.api_core.exceptions import GoogleAPICallError
    from google.auth.exceptions import DefaultCredentialsError, RefreshError

    stats: list[JobStat] = []
    try:
        return _run(args, cfg, stats)
    except (DefaultCredentialsError, RefreshError) as e:
        print(
            f"Google Cloud credentials are missing or expired ({type(e).__name__}). "
            "Run `gcloud auth application-default login` and try again.",
            file=sys.stderr,
        )
        return EXIT_GOOGLE
    except GoogleAPICallError as e:
        print(f"BigQuery error: {e.message or e}", file=sys.stderr)
        return EXIT_GOOGLE
    finally:
        _print_costs(stats, getattr(args, "costs_json", None))


def _run(args: argparse.Namespace, cfg, stats: list[JobStat]) -> int:
    from . import pipeline
    from .bq import BigQuery

    bq = BigQuery(cfg)

    if args.command == "reference":
        created = bq.ensure_datasets()
        if created:
            print(f"created dataset(s): {', '.join(created)}")
        from .reference import load_reference

        for s in load_reference(cfg, bq):
            print(f"loaded {cfg.project}.{cfg.raw_dataset}.{s.step}: {s.rows} rows")
        return EXIT_OK

    if args.command == "build":
        if args.incremental:
            if args.dry_run or args.start_at or args.only:
                print("--incremental cannot be combined with --dry-run, --from or --only (use --print-script)", file=sys.stderr)
                return EXIT_CONFIG
            from .incremental import WindowError

            try:
                result = pipeline.build_incremental(
                    cfg,
                    bq,
                    since=args.since,
                    # `is None`, not `or`: --lookback 0 must reach plan_window and be refused, not become the default
                    lookback=DEFAULT_LOOKBACK_DAYS if args.lookback is None else args.lookback,
                    stats=stats,
                    print_script=args.print_script,
                )
            except WindowError as e:
                print(f"incremental build: {e}", file=sys.stderr)
                return EXIT_CONFIG
            if args.print_script or args.no_check or (result.window is not None and result.window.empty):
                return EXIT_OK
        elif args.since or args.lookback is not None or args.print_script:
            print("--since, --lookback and --print-script need --incremental", file=sys.stderr)
            return EXIT_CONFIG
        else:
            result = pipeline.build(cfg, bq, dry_run=args.dry_run, start_at=args.start_at, only=args.only, stats=stats)
        if args.dry_run or args.no_check:
            return EXIT_OK
        checks, _ = pipeline.run_checks(cfg, bq, result.site, stats=stats)
        print(pipeline.format_checks(checks))
        return EXIT_OK if all(not r.failures for r in checks) else EXIT_CHECK_FAILED

    if args.command == "check":
        checks, _ = pipeline.run_checks(cfg, bq, stats=stats, directory=Path(args.dir).resolve() if args.dir else None)
        print(pipeline.format_checks(checks))
        return EXIT_OK if all(not r.failures for r in checks) else EXIT_CHECK_FAILED

    if args.command == "alerts":
        return _alerts(args, cfg, bq)

    if args.command == "numbers":
        reports, report_stats = pipeline.run_reports(cfg, bq)
        stats += report_stats
        for name, title, rows in reports:
            print(f"\n## {name}: {title}\n")
            print(pipeline.format_rows(rows))
        return EXIT_OK

    return EXIT_CONFIG


def _alerts(args: argparse.Namespace, cfg, bq) -> int:
    from datetime import date, datetime, timezone

    from . import alerts, anomaly
    from .pipeline import format_rows

    config = anomaly.load_config()
    if args.action == "backtest":
        kpi, health = alerts.read_marts(cfg, bq)
        found = [a for a in anomaly.detect(kpi, health, config) if args.source in (None, a.source)]
        print(format_rows([
            {"date": a.date.isoformat(), "source": a.source, "metric": a.metric, "rule": a.rule, "severity": a.severity,
             "value": round(a.value, 4), "expected": None if a.expected is None else round(a.expected, 4),
             "score": a.score} for a in found
        ]))
        from collections import Counter

        print(f"\n{len(found)} alert(s) on {len({(a.date, a.source) for a in found})} source-day(s); "
              f"by rule {dict(Counter(a.rule for a in found))}, by severity {dict(Counter(a.severity for a in found))}")
        return EXIT_OK
    due = date.fromisoformat(args.as_of) if args.as_of else None
    if args.action in ("detect", "run"):
        alerts.detect_and_store(cfg, bq, config, as_of=due)
    if args.action == "detect":
        return EXIT_OK
    as_of = due or datetime.now(timezone.utc).date()
    result = alerts.notify(cfg, bq, as_of, cfg.alert_webhook_url, config)
    print(f"alerts as of {as_of}: {result.fresh} news ({result.new} sent now, delivery {result.delivery}), {result.critical} critical")
    return EXIT_CHECK_FAILED if args.fail_on_alert and result.new else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
