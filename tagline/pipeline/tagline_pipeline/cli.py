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

    c = sub.add_parser("check", help="run the data checks against the built tables")
    c.add_argument("--costs-json", metavar="PATH")

    sub.add_parser("numbers", help="print the reconciled numbers (events, sessions, orders, revenue, identity, cost)")

    args = parser.parse_args(argv)
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
        result = pipeline.build(cfg, bq, dry_run=args.dry_run, start_at=args.start_at, only=args.only, stats=stats)
        if args.dry_run or args.no_check:
            return EXIT_OK
        checks, _ = pipeline.run_checks(cfg, bq, result.site, stats=stats)
        print(pipeline.format_checks(checks))
        return EXIT_OK if all(not r.failures for r in checks) else EXIT_CHECK_FAILED

    if args.command == "check":
        checks, _ = pipeline.run_checks(cfg, bq, stats=stats)
        print(pipeline.format_checks(checks))
        return EXIT_OK if all(not r.failures for r in checks) else EXIT_CHECK_FAILED

    if args.command == "numbers":
        reports, report_stats = pipeline.run_reports(cfg, bq)
        stats += report_stats
        for name, title, rows in reports:
            print(f"\n## {name}: {title}\n")
            print(pipeline.format_rows(rows))
        return EXIT_OK

    return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())
