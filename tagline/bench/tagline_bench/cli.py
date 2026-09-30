"""Stage 4 measurement harness. Run from tagline/bench with the Spark venv (it has the BigQuery, Dataproc and
Storage clients) and tagline/spark on PYTHONPATH; the bench-* Make targets do that.

  build        run a build command N times (default `make build`); per job: bytes processed/billed, slot-ms,
               elapsed, from INFORMATION_SCHEMA.JOBS_BY_PROJECT by job id (the command's COSTS_JSON), plus the
               command's own wall time
  jobs         the same job record for a time window (e.g. an Airflow run), by labels
  spark        run the attribution batch N times, optionally with Spark property overrides
  batch        record a batch the harness did not start (the Airflow DAG's), like one run of `spark`
  storage      INFORMATION_SCHEMA.TABLE_STORAGE per table and dataset, and the monthly cost under both models
  fingerprint  row count + order-independent hashes of tables (the correctness gate's quick check)
  snapshot     copy tables into the baseline dataset (default tagline_s4_baseline) and fingerprint them
  diff         EXCEPT DISTINCT both ways (and row counts, multiset hashes) against the baseline dataset
  report       medians and ranges from the results log, as Markdown
  equivalence  the daily incremental build against a full build, in throwaway datasets (make stage4-equivalence)
  prices       the list prices every number uses

Every record goes to bench/results/<variant>.jsonl. Exit codes: 0 ok, 1 a command/batch failed or a diff found a
difference, 2 configuration or usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import prices, results
from .config import BASELINE_DATASET, LOG_DIR, MARTS, RAW, STAGING, TAGLINE_DIR, ConfigError, load_config, resolve_tables


def _scrub_line(line: str, secrets: dict[str, str]) -> str:
    return results.scrub(line, secrets)


def run_command(cmd: str, env: dict[str, str], log_path: Path, secrets: dict[str, str], heartbeat: float = 30) -> tuple[int, float, list[str]]:
    """Run a shell command in tagline/, output to log_path; print a heartbeat every `heartbeat` seconds with its
    latest line. Returns (exit code, wall seconds, last lines)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last: list[str] = []
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, shell=True, cwd=TAGLINE_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                log.flush()
                last.append(line.rstrip("\n"))
                del last[:-60]

        t = threading.Thread(target=pump, daemon=True)
        t.start()
        next_beat = started + heartbeat
        while proc.poll() is None:
            time.sleep(1)
            if time.monotonic() >= next_beat:
                tail = _scrub_line(last[-1], secrets) if last else ""
                print(f"    {time.monotonic() - started:5.0f} s  running: {tail[:140]}", flush=True)
                next_beat += heartbeat
        t.join(timeout=10)
    return proc.returncode, time.monotonic() - started, last


def _job_ids_from_costs_json(path: Path) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    # a script's statements are listed with their script's id (parent_job_id): record the script, which carries them
    ids = [j.get("parent_job_id") or j["job_id"] for j in json.loads(path.read_text(encoding="utf-8")) if j.get("job_id") and not j.get("dry_run")]
    return list(dict.fromkeys(ids))


def _fingerprints(bq, tables, float_digits=None) -> dict[str, Any]:
    out = {}
    for ds, t in tables:
        out[f"{ds}.{t}"] = bq.fingerprint(ds, t, float_digits)
    return out


def _print_jobs(jobs: list[dict[str, Any]]) -> None:
    print(f"    {'step':<34} {'kind':<7} {'billed MiB':>11} {'slot-ms':>10} {'elapsed s':>9}")
    for j in jobs:
        print(
            f"    {str(j['step'])[:34]:<34} {str(j['kind'])[:7]:<7} {(j['bytes_billed'] or 0) / 2**20:11.1f} "
            f"{j['slot_ms'] or 0:10,} {j['elapsed_seconds'] or 0:9.1f}"
        )
        for c in j.get("statements", []):
            name = f"  {(c.get('statement_type') or '').lower()} {c.get('destination') or ''}".rstrip()
            print(f"    {name[:42]:<42} {(c['bytes_billed'] or 0) / 2**20:11.1f} {c['slot_ms'] or 0:10,} {c['elapsed_seconds'] or 0:9.1f}")


def cmd_build(args, cfg, bq) -> int:
    from .bigquery import job_totals

    failures = 0
    for i in range(args.first_run, args.first_run + args.runs):
        run_id = uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory() as tmp:
            costs = Path(tmp) / "costs.json"
            env = {**os.environ, "COSTS_JSON": str(costs)}
            log_path = LOG_DIR / f"{args.variant}-build-{run_id}.log"
            print(f"[{args.variant}] build run {i} of {args.first_run + args.runs - 1}: {args.cmd}  (log {log_path.relative_to(TAGLINE_DIR)})", flush=True)
            started_at = results.now_utc()
            code, wall, last = run_command(args.cmd, env, log_path, cfg.secrets)
            ended_at = results.now_utc()
            ids = _job_ids_from_costs_json(costs)
        checks = [l.strip() for l in last if l.strip().startswith(("PASS", "FAIL")) or "checks passed" in l]
        if ids:
            jobs, collection = bq.jobs_by_ids(ids, started_at, ended_at), "job_ids"
        else:
            jobs = bq.jobs_by_window(started_at, ended_at, {"stage": "2"})
            collection = "window(app=tagline,stage=2)"
        totals = job_totals(jobs)
        record = {
            "type": "bq_run",
            "variant": args.variant,
            "run": i,
            "run_id": run_id,
            "command": args.cmd,
            "note": args.note,
            "site_export_configured": cfg.ga4_dataset is not None,
            "started_at": started_at,
            "ended_at": ended_at,
            "wall_seconds": round(wall, 2),
            "exit_code": code,
            "checks": [c for c in checks if c.startswith(("PASS", "FAIL"))],
            "checks_line": next((c for c in checks if "checks passed" in c), None),
            "collection": collection,
            "jobs": jobs,
            "totals": totals,
            "git": results.git_state(),
        }
        if args.fingerprint != "none" and code == 0:
            record["fingerprints"] = _fingerprints(bq, resolve_tables(args.fingerprint), args.float_digits)
        path = results.append(record, cfg.secrets)
        _print_jobs(jobs)
        print(
            f"    exit {code}; wall {wall:.1f} s; {totals['jobs']} jobs, {totals['bytes_billed'] / 2**30:.3f} GiB billed "
            f"(${totals['usd']:.4f}), {totals['slot_ms']:,} slot-ms, {totals['job_seconds']:.1f} s of job time; "
            f"{record['checks_line'] or 'no check line'}  -> {path.relative_to(TAGLINE_DIR)}",
            flush=True,
        )
        failures += code != 0
    return 1 if failures else 0


def cmd_jobs(args, cfg, bq) -> int:
    from datetime import datetime

    from .bigquery import job_totals
    from .spark import parse_kv

    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    until = datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    jobs = bq.jobs_by_window(since, until, parse_kv(args.label), tuple(args.job_type or ["QUERY"]))
    totals = job_totals(jobs)
    record = {
        "type": "bq_window",
        "variant": args.variant,
        "note": args.note,
        "since": since,
        "until": until,
        "label_filter": parse_kv(args.label),
        "wall_seconds": args.wall,
        "jobs": jobs,
        "totals": totals,
        "git": results.git_state(),
    }
    path = results.append(record, cfg.secrets)
    _print_jobs(jobs)
    print(f"    {totals['jobs']} jobs, {totals['bytes_billed'] / 2**30:.3f} GiB billed (${totals['usd']:.4f})  -> {path.relative_to(TAGLINE_DIR)}")
    return 0


def cmd_spark(args, cfg, bq) -> int:
    from tagline_spark import submit

    from .spark import Logs, parse_kv, run_batch

    target = submit.load_target()
    logs = Logs(cfg.project)
    overrides = {
        "set_props": parse_kv(args.prop),
        "unset_props": args.unset_prop or [],
        "labels": parse_kv(args.label),
        "runtime": args.runtime,
        "job_args": parse_kv(args.job_arg),
    }
    failures = 0
    for i in range(args.first_run, args.first_run + args.runs):
        print(f"[{args.variant}] spark run {i} of {args.first_run + args.runs - 1}", flush=True)
        rec = run_batch(target, args.variant, i, overrides, bq, logs, poll_seconds=args.poll)
        record = {"type": "spark_run", "variant": args.variant, "run": i, "note": args.note, "overrides": overrides, **rec, "git": results.git_state()}
        if args.fingerprint != "none" and rec["state"] == "SUCCEEDED":
            record["fingerprints"] = _fingerprints(bq, resolve_tables(args.fingerprint), args.float_digits)
        path = results.append(record, cfg.secrets)
        print(
            f"    {rec['state']}: wall {rec['wall_seconds']:.0f} s (pending {rec['pending_seconds'] or 0:.0f}, running {rec['running_seconds'] or 0:.0f}); "
            f"{rec['dcu_hours']:.4f} DCU-hours (avg {rec['avg_dcu_while_running'] or 0:.1f} DCUs running), {rec['shuffle_gib_hours']:.2f} GB-h shuffle, "
            f"${rec['usd']:.4f}; compute {rec['compute_seconds']} s, write {rec['write_seconds']} s; app {rec['app_id']} ({rec['mode']}, "
            f"master {rec.get('spark_master')}, parallelism {rec.get('default_parallelism')}); "
            f"worker logged: {rec['worker_logged']}  -> {path.relative_to(TAGLINE_DIR)}",
            flush=True,
        )
        failures += rec["state"] != "SUCCEEDED"
    return 1 if failures else 0


def cmd_batch(args, cfg, bq) -> int:
    """Record a batch the harness did not start (the Airflow DAG's), as a spark_run record."""
    from tagline_spark import submit

    from .spark import Logs, describe_batch

    target = submit.load_target()
    batch_id = args.batch_id.rsplit("/", 1)[-1]
    rec = describe_batch(submit._client(target), f"{target.parent}/batches/{batch_id}", bq, Logs(cfg.project))
    record = {"type": "spark_run", "variant": args.variant, "run": args.run, "note": args.note, "overrides": None, "started_by": args.started_by,
              **rec, "git": results.git_state()}
    if args.fingerprint != "none" and rec["state"] == "SUCCEEDED":
        record["fingerprints"] = _fingerprints(bq, resolve_tables(args.fingerprint), args.float_digits)
    path = results.append(record, cfg.secrets)
    print(
        f"    {rec['state']}: wall {rec['wall_seconds'] or 0:.0f} s (pending {rec['pending_seconds'] or 0:.0f}, running {rec['running_seconds'] or 0:.0f}); "
        f"{rec['dcu_hours']:.4f} DCU-hours (avg {rec['avg_dcu_while_running'] or 0:.1f} DCUs running), ${rec['usd']:.4f}; "
        f"compute {rec['compute_seconds']} s, write {rec['write_seconds']} s; app {rec['app_id']} ({rec['mode']}, master {rec.get('spark_master')})"
        f"  -> {path.relative_to(TAGLINE_DIR)}",
        flush=True,
    )
    return 0 if rec["state"] == "SUCCEEDED" else 1


def cmd_storage(args, cfg, bq) -> int:
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    if cfg.ga4_dataset and args.include_export and cfg.ga4_dataset not in datasets:
        datasets.append(cfg.ga4_dataset)
    rows, source, why = bq.table_storage(datasets)
    if why:
        print(f"  INFORMATION_SCHEMA.TABLE_STORAGE was denied ({results.scrub(why, cfg.secrets)[:160]}); using each table's metadata instead")
    options = {d: bq.dataset_options(d) for d in datasets}
    per_ds: dict[str, dict[str, int]] = {}
    tables = []
    for r in rows:
        t = {k: r[k] for k in r}
        t["monthly_usd"] = prices.storage_monthly_usd(
            active_logical=r["active_logical_bytes"] or 0,
            long_term_logical=r["long_term_logical_bytes"] or 0,
            active_physical=r["active_physical_bytes"] or 0,
            long_term_physical=r["long_term_physical_bytes"] or 0,
            fail_safe_physical=r["fail_safe_physical_bytes"] or 0,
        )
        tables.append(t)
        agg = per_ds.setdefault(r["table_schema"], {})
        for k in ("total_rows", "total_logical_bytes", "active_logical_bytes", "long_term_logical_bytes", "total_physical_bytes",
                  "active_physical_bytes", "long_term_physical_bytes", "time_travel_physical_bytes", "current_physical_bytes",
                  "fail_safe_physical_bytes"):
            agg[k] = agg.get(k, 0) + int(r[k] or 0)
        agg["tables"] = agg.get("tables", 0) + 1
        agg["deleted_tables"] = agg.get("deleted_tables", 0) + int(bool(r["deleted"]))
    for ds, agg in per_ds.items():
        agg["monthly_usd"] = prices.storage_monthly_usd(
            active_logical=agg["active_logical_bytes"], long_term_logical=agg["long_term_logical_bytes"],
            active_physical=agg["active_physical_bytes"], long_term_physical=agg["long_term_physical_bytes"],
            fail_safe_physical=agg["fail_safe_physical_bytes"],
        )
    record = {"type": "storage", "variant": args.variant, "note": args.note, "observed_at": results.now_utc(),
              "source": source, "view_denied": why, "fail_safe_included": source.startswith("INFORMATION_SCHEMA"),
              "datasets": {d: {"options": options.get(d), **per_ds.get(d, {})} for d in datasets}, "tables": tables,
              "prices": prices.as_dict()}
    path = results.append(record, cfg.secrets)
    gib = 2**30
    print(f"  source: {source}")
    print(f"  {'dataset':<24} {'tables':>6} {'logical GiB':>11} {'physical GiB':>12} {'current GiB':>11} {'time-travel GiB':>15} {'fail-safe GiB':>13} {'$/mo logical':>12} {'$/mo physical':>13}  model")
    for d in datasets:
        a = per_ds.get(d)
        if not a:
            print(f"  {results.scrub(d, cfg.secrets):<24} (no tables)")
            continue
        print(
            f"  {results.scrub(d, cfg.secrets):<24} {a['tables']:>6} {a['total_logical_bytes'] / gib:11.3f} {a['total_physical_bytes'] / gib:12.3f} "
            f"{a['current_physical_bytes'] / gib:11.3f} "
            f"{a['time_travel_physical_bytes'] / gib:15.3f} {a['fail_safe_physical_bytes'] / gib:13.3f} {a['monthly_usd']['logical']:12.4f} "
            f"{a['monthly_usd']['physical']:13.4f}  {(options.get(d) or {}).get('storage_billing_model')}"
        )
    print(f"  -> {path.relative_to(TAGLINE_DIR)}")
    return 0


def cmd_fingerprint(args, cfg, bq) -> int:
    fps = _fingerprints(bq, resolve_tables(args.tables), args.float_digits)
    record = {"type": "fingerprint", "variant": args.variant, "note": args.note, "fingerprints": fps, "git": results.git_state()}
    path = results.append(record, cfg.secrets)
    for k, v in fps.items():
        print(f"  {k:<40} {v['rows']:>10,} rows  {v['fingerprint']}")
    print(f"  -> {path.relative_to(TAGLINE_DIR)}")
    return 0


def cmd_snapshot(args, cfg, bq) -> int:
    tables = resolve_tables(args.tables)
    created = bq.ensure_dataset(
        args.dataset,
        "Tagline Stage 4: copies of the tables as the baseline code built them, for the correctness gate "
        "(tagline/bench, make bench-diff). Temporary: delete at the end of Stage 4.",
        {"app": "tagline", "stage": "4", "purpose": "baseline"},
    )
    expires = results.now_utc() + timedelta(days=args.expire_days) if args.expire_days else None
    copies = {}
    for ds, t in tables:
        copies[f"{ds}.{t}"] = bq.copy_table(ds, t, args.dataset, expires, {"app": "tagline", "stage": "4", "purpose": "baseline"})
        print(f"  copied {ds}.{t} -> {args.dataset}.{t}: {copies[f'{ds}.{t}']['rows']:,} rows", flush=True)
    fps = {}
    for ds, t in tables:
        if (ds, t) in {("tagline_staging", "stg_events"), ("tagline_staging", "stg_items")} and not args.fingerprint_staging:
            continue
        fps[f"{args.dataset}.{t}"] = bq.fingerprint(args.dataset, t, None)
    record = {"type": "snapshot", "variant": args.variant, "note": args.note, "dataset": args.dataset, "dataset_created": created,
              "expires": expires, "copies": copies, "fingerprints": fps, "git": results.git_state()}
    path = results.append(record, cfg.secrets)
    print(f"  {len(copies)} tables in {args.dataset} (expire {expires or 'never'})  -> {path.relative_to(TAGLINE_DIR)}")
    return 0


def cmd_diff(args, cfg, bq) -> int:
    tables = resolve_tables(args.tables)
    out = {}
    for ds, t in tables:
        d = bq.diff(ds, t, args.against, args.float_digits, examples=args.examples)
        out[f"{ds}.{t}"] = d
        verdict = "IDENTICAL" if d["identical"] else "DIFFERENT"
        print(
            f"  {verdict:<9} {ds}.{t:<24} rows {d['current_rows']:,} vs {d['baseline_rows']:,}; only in current {d['only_in_current']:,}, "
            f"only in baseline {d['only_in_baseline']:,}"
            + (f"; columns +{d['columns_only_in_current']} -{d['columns_only_in_baseline']} ~{d['columns_type_changed']}"
               if d["columns_only_in_current"] or d["columns_only_in_baseline"] or d["columns_type_changed"] else ""),
            flush=True,
        )
        for ex in d.get("examples", []):
            print(f"      {ex['side']}: {results.scrub(ex['r'], cfg.secrets)[:300]}")
    record = {"type": "diff", "variant": args.variant, "note": args.note, "against": args.against, "float_digits": args.float_digits,
              "tables": out, "identical": all(d["identical"] for d in out.values()), "git": results.git_state()}
    path = results.append(record, cfg.secrets)
    print(f"  {'all identical' if record['identical'] else 'DIFFERENCES FOUND'}  -> {path.relative_to(TAGLINE_DIR)}")
    return 0 if record["identical"] else 1


def cmd_report(args, cfg, bq) -> int:
    from .report import render

    files = [results.variant_file(v) for v in args.variant] if args.variant else None
    text = render(results.load(files), with_jobs=not args.no_jobs)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def cmd_equivalence(args, cfg, bq) -> int:
    from .equivalence import run

    return run(args, cfg, bq)


def cmd_prices(args, cfg, bq) -> int:
    rec = prices.as_dict()
    print(json.dumps(rec, indent=2))
    if args.record:
        path = results.append({"type": "prices", "variant": args.variant, **rec}, cfg.secrets)
        print(f"-> {path.relative_to(TAGLINE_DIR)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tagline_bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, variant_required=True):
        sp.add_argument("--variant", required=variant_required, help="results go to bench/results/<variant>.jsonl")
        sp.add_argument("--note", default=None, help="free text kept with the record")

    def fp_opts(sp, default="none"):
        sp.add_argument("--fingerprint", default=default, help="table set to fingerprint afterwards: none, gate, stage2, stage2-marts, stage3, all, or a list")
        sp.add_argument("--float-digits", type=int, default=None, help="round top-level FLOAT64 columns to N digits in fingerprints")

    b = sub.add_parser("build", help="run a build command N times and record every job")
    common(b)
    b.add_argument("--runs", type=int, default=3)
    b.add_argument("--first-run", type=int, default=1, help="number of the first run (to add runs to a variant later)")
    b.add_argument("--cmd", default="make build", help="shell command run in tagline/ (COSTS_JSON is set in its environment)")
    fp_opts(b)
    b.set_defaults(func=cmd_build)

    j = sub.add_parser("jobs", help="record the tagline jobs of a time window (e.g. an Airflow run)")
    common(j)
    j.add_argument("--since", required=True, help="ISO timestamp, UTC")
    j.add_argument("--until", required=True)
    j.add_argument("--label", action="append", help="extra label filter key=value (repeatable), e.g. orchestrator=airflow")
    j.add_argument("--job-type", action="append", choices=["QUERY", "LOAD", "COPY", "EXTRACT"])
    j.add_argument("--wall", type=float, default=None, help="the run's own wall seconds, if known")
    j.set_defaults(func=cmd_jobs)

    s = sub.add_parser("spark", help="run the attribution batch N times")
    common(s)
    s.add_argument("--runs", type=int, default=2)
    s.add_argument("--first-run", type=int, default=1, help="number of the first run (to add runs to a variant later)")
    s.add_argument("--prop", action="append", help="Spark property override key=value (repeatable)")
    s.add_argument("--unset-prop", action="append", help="remove one of batch.py's properties (repeatable)")
    s.add_argument("--label", action="append", help="extra batch label key=value")
    s.add_argument("--job-arg", action="append", help="job argument name=value: sets --name=value (empty value removes it), e.g. spark-master=local[4]")
    s.add_argument("--runtime", default=None, help="another runtime version (default: batch.py's)")
    s.add_argument("--poll", type=float, default=30)
    fp_opts(s)
    s.set_defaults(func=cmd_spark)

    ba = sub.add_parser("batch", help="record a batch the harness did not start (e.g. the Airflow DAG's)")
    common(ba)
    ba.add_argument("--batch-id", required=True, help="the batch id (or its full resource name)")
    ba.add_argument("--run", type=int, default=1)
    ba.add_argument("--started-by", default="airflow", help="who started it, kept with the record")
    fp_opts(ba)
    ba.set_defaults(func=cmd_batch)

    st = sub.add_parser("storage", help="table storage and its monthly cost under both billing models")
    common(st)
    st.add_argument("--datasets", default=f"{RAW},{STAGING},{MARTS},{BASELINE_DATASET}")
    st.add_argument("--no-export", dest="include_export", action="store_false", help="leave out the GA4 export dataset")
    st.set_defaults(func=cmd_storage)

    f = sub.add_parser("fingerprint", help="fingerprint tables")
    common(f)
    f.add_argument("--tables", default="gate")
    f.add_argument("--float-digits", type=int, default=None)
    f.set_defaults(func=cmd_fingerprint)

    sn = sub.add_parser("snapshot", help="copy tables into the baseline dataset")
    common(sn)
    sn.add_argument("--dataset", default=BASELINE_DATASET)
    sn.add_argument("--tables", default="all")
    sn.add_argument("--expire-days", type=int, default=14, help="expiration set on each copy (0: none)")
    sn.add_argument("--fingerprint-staging", action="store_true", help="also fingerprint stg_events/stg_items (~3.4 GiB scanned)")
    sn.set_defaults(func=cmd_snapshot)

    d = sub.add_parser("diff", help="compare tables with the baseline dataset")
    common(d)
    d.add_argument("--against", default=BASELINE_DATASET)
    d.add_argument("--tables", default="gate")
    d.add_argument("--float-digits", type=int, default=None, help="round top-level FLOAT64 columns to N digits before comparing")
    d.add_argument("--examples", type=int, default=3, help="show up to N differing rows per side")
    d.set_defaults(func=cmd_diff)

    r = sub.add_parser("report", help="Markdown summary of the results log")
    r.add_argument("--variant", action="append", help="only these variants (repeatable); default all")
    r.add_argument("--out", default=None)
    r.add_argument("--no-jobs", action="store_true", help="leave out the per-job tables")
    r.set_defaults(func=cmd_report)

    eq = sub.add_parser("equivalence", help="incremental build vs full build, in throwaway datasets (make stage4-equivalence)")
    common(eq, variant_required=False)
    eq.add_argument("--scenario", action="append", choices=["sample", "site", "fixture"], help="default: all three")
    eq.add_argument("--sample-start", default="20201101", help="first sample day of the sample and site scenarios (a later day is cheaper)")
    eq.add_argument("--keep", action="store_true", help="leave the tagline_s4_eq_* datasets (their tables expire in a day)")
    eq.set_defaults(func=cmd_equivalence, variant="s4-e1-equivalence")

    pr = sub.add_parser("prices", help="print the prices; --record also logs them")
    common(pr, variant_required=False)
    pr.add_argument("--record", action="store_true")
    pr.set_defaults(func=cmd_prices)

    args = p.parse_args(argv)
    if getattr(args, "record", False) and not args.variant:
        p.error("--record needs --variant")
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2
    bq = None
    if args.command not in {"report", "prices"}:
        from .bigquery import BQ

        bq = BQ(cfg)
    try:
        return args.func(args, cfg, bq)
    except (ConfigError, ValueError) as e:
        print(f"error: {results.scrub(str(e), cfg.secrets)}", file=sys.stderr)
        return 2
    finally:
        if bq is not None and bq.jobs:
            print(f"(harness queries: {len(bq.jobs)} jobs, {bq.billed / 2**20:,.0f} MiB billed, ${prices.bq_usd(bq.billed):.4f})", file=sys.stderr)
