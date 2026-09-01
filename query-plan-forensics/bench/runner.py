#!/usr/bin/env python3
"""Run every query N times against the container and record what the planner did.

Two deliberate choices, both of which you will be asked about:

1. exec_ms comes out of the EXPLAIN JSON's "Execution Time" field, not from
   timing the client call. That removes psql startup, the wire round-trip and
   result formatting from every number -- none of which is being optimized.
   Wall-clock is recorded and never asserted on.

2. Iterations are ROUND-ROBIN, not blocked. Running 15xQ01 then 15xQ02 loads all
   of Q01's samples onto a cool CPU and all of Q10's onto a throttled one.
   Interleaving spreads thermal drift evenly across the whole set.

Standard library only. No psycopg, no driver wheels to fight with.

  python3 bench/runner.py --stage baseline
  python3 bench/runner.py --stage s3_covering --iterations 15 --cache prewarm
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import subprocess
import sys
import time

import plan as planlib

HERE = pathlib.Path(__file__).resolve().parent
QUERY_DIR = HERE / "queries"
RESULT_DIR = HERE / "results"

SERVICE = os.environ.get("QPF_SERVICE", "db")
DB = os.environ.get("QPF_DB", "inventory")
USER = os.environ.get("QPF_USER", "postgres")

EXPLAIN_PREFIX = "EXPLAIN (ANALYZE, BUFFERS, SERIALIZE TEXT, SETTINGS, VERBOSE, FORMAT JSON)"


def psql(sql: str, quiet: bool = True) -> str:
    """Execute SQL inside the container. Client version == server version, always."""
    cmd = [
        "docker", "compose", "exec", "-T", SERVICE,
        "psql", "-U", USER, "-d", DB, "-X", "-q",
        "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE.parent)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"psql failed (exit {proc.returncode})")
    return proc.stdout


def load_queries(only=None):
    out = []
    for path in sorted(QUERY_DIR.glob("Q*.sql")):
        qid = path.name.split("_")[0]
        if only and qid not in only:
            continue
        body = "\n".join(
            line for line in path.read_text().splitlines()
            if not line.strip().startswith("--")
        ).strip().rstrip(";")
        out.append((qid, path.name, body))
    if not out:
        raise SystemExit(f"no queries matched in {QUERY_DIR}")
    return out


def server_facts():
    version = psql("SELECT current_setting('server_version');").strip()
    checksums = psql("SHOW data_checksums;").strip()
    rows = psql("SELECT count(*) FROM stock_movements;").strip()
    return {"pg_version": version, "data_checksums": checksums, "movement_rows": int(rows)}


def git_sha():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=HERE.parent,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_once(qid, body, cache_mode):
    if cache_mode == "prewarm":
        # A deterministic known-warm state is more useful than chasing a cold one.
        psql("SELECT pg_prewarm('stock_movements');")
    wall_start = time.perf_counter()
    raw = psql(f"{EXPLAIN_PREFIX}\n{body};")
    wall_ms = (time.perf_counter() - wall_start) * 1000
    parsed = json.loads(raw)
    summary = planlib.summarize(parsed)
    summary["wall_ms"] = round(wall_ms, 2)  # recorded, never asserted on
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="label for this run, e.g. baseline, s3_brin")
    ap.add_argument("--iterations", type=int, default=15)
    ap.add_argument("--discard", type=int, default=2, help="warm-up rounds to drop")
    ap.add_argument("--cache", choices=["warm", "prewarm"], default="warm")
    ap.add_argument("--only", nargs="*", help="restrict to specific query ids")
    ap.add_argument("--write-baselines", action="store_true",
                    help="also write bench/baselines/QNN.json from this run")
    args = ap.parse_args()

    queries = load_queries(args.only)
    facts = server_facts()
    print(f"Postgres {facts['pg_version']}  checksums={facts['data_checksums']}  "
          f"movements={facts['movement_rows']:,}")
    print(f"stage={args.stage}  n={args.iterations} (discarding first {args.discard})  "
          f"cache={args.cache}\n")

    samples = {qid: [] for qid, _, _ in queries}

    # Round-robin: full pass over every query, then repeat.
    for i in range(args.iterations):
        for qid, _, body in queries:
            samples[qid].append(run_once(qid, body, args.cache))
        print(f"  round {i + 1}/{args.iterations} done", end="\r", flush=True)
    print(" " * 40, end="\r")

    results = {}
    for qid, filename, _ in queries:
        kept = samples[qid][args.discard:]
        times = sorted(s["exec_ms"] for s in kept)
        last = kept[-1]
        cv = (statistics.pstdev(times) / statistics.fmean(times) * 100) if len(times) > 1 else 0.0
        results[qid] = {
            "query_id": qid,
            "file": filename,
            "n": len(kept),
            "p50_ms": round(statistics.median(times), 3),
            "p95_ms": round(times[max(0, int(len(times) * 0.95) - 1)], 3),
            "cv_pct": round(cv, 1),
            "plan_fingerprint": last["plan_fingerprint"],
            "required_indexes": last["required_indexes"],
            "root_total_cost": last["root_total_cost"],
            "plan_rows": last["plan_rows"],
            "actual_rows": last["actual_rows"],
            "total_buffers": last["total_buffers"],
            "plan": last["plan"],
        }

    payload = {
        "stage": args.stage,
        "git_sha": git_sha(),
        "cache_mode": args.cache,
        "iterations": args.iterations,
        **facts,
        "queries": results,
    }

    RESULT_DIR.mkdir(exist_ok=True)
    out = RESULT_DIR / f"{args.stage}.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out.relative_to(HERE.parent)}")

    noisy = [q for q, r in results.items() if r["cv_pct"] > 10]
    if noisy:
        print(f"\n  WARNING: coefficient of variation over 10% for {', '.join(noisy)}.")
        print("  These numbers are not defensible under questioning. Fix the")
        print("  methodology -- close other apps, check thermal state, raise")
        print("  --iterations -- before tuning anything.")

    if args.write_baselines:
        bdir = HERE / "baselines"
        bdir.mkdir(exist_ok=True)
        for qid, r in results.items():
            (bdir / f"{qid}.json").write_text(
                json.dumps(
                    {
                        "query_id": qid,
                        "pg_major": int(facts["pg_version"].split(".")[0]),
                        "movement_rows": facts["movement_rows"],
                        "plan_fingerprint": r["plan_fingerprint"],
                        "required_indexes": r["required_indexes"],
                        "root_total_cost": r["root_total_cost"],
                        "cost_tolerance_pct": 15,
                        "plan_rows": r["plan_rows"],
                        "rows_tolerance_factor": 2.0,
                        "total_buffers": r["total_buffers"],
                        "buffers_tolerance_pct": 25,
                    },
                    indent=2,
                )
                + "\n"
            )
        print(f"wrote {len(results)} baselines to bench/baselines/")


if __name__ == "__main__":
    main()
