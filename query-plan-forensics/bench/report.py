#!/usr/bin/env python3
"""Render a benchmark run as markdown -- and, given two runs, the before/after.

  python3 bench/report.py --stage baseline
  python3 bench/report.py --before baseline --after s3_covering
  python3 bench/report.py --stage baseline --github-summary
"""

from __future__ import annotations

import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
RESULT_DIR = HERE / "results"


def load(stage):
    path = RESULT_DIR / f"{stage}.json"
    if not path.exists():
        raise SystemExit(f"no results at {path}")
    return json.loads(path.read_text())


def leaf_scan(fp):
    """The deepest scan node -- the one line of a plan people actually read."""
    for depth, node, rel, idx in reversed(fp):
        if "Scan" in (node or ""):
            return f"{node}" + (f" on {idx}" if idx else (f" on {rel}" if rel else ""))
    return fp[0][1] if fp else "?"


def single(data):
    lines = [
        f"### `{data['stage']}` — {data['movement_rows']:,} movements, "
        f"PostgreSQL {data['pg_version']}, n={data['iterations']}, cache={data['cache_mode']}",
        "",
        "| Query | p50 ms | p95 ms | CV% | Buffers | Est. rows | Actual | Scan |",
        "|---|--:|--:|--:|--:|--:|--:|---|",
    ]
    for qid in sorted(data["queries"]):
        r = data["queries"][qid]
        est, act = r["plan_rows"] or 0, r["actual_rows"] or 0
        flag = " ⚠" if est and act and max(est, act) / max(min(est, act), 1) > 10 else ""
        lines.append(
            f"| {qid} | {r['p50_ms']:.1f} | {r['p95_ms']:.1f} | {r['cv_pct']:.1f} | "
            f"{r['total_buffers']:,} | {est:,} | {act:,}{flag} | `{leaf_scan(r['plan_fingerprint'])}` |"
        )
    lines += [
        "",
        "Est./Actual are the **root node's** rows — for an aggregate that is the "
        "group count, not the rows scanned. ⚠ marks a gap over 10x: the planner is "
        "guessing badly about cardinality, which is what Stage 4 is for.",
        "",
        "p50/p95 come from the EXPLAIN JSON's Execution Time, not from timing the "
        "client call. CV% over 10 means the measurement is not defensible.",
    ]
    return "\n".join(lines)


def compare(before, after):
    lines = [
        f"### `{before['stage']}` → `{after['stage']}`",
        "",
        "| Query | p50 before | p50 after | Δ | Buffers Δ | Plan changed |",
        "|---|--:|--:|--:|--:|---|",
    ]
    regressions = []
    for qid in sorted(before["queries"]):
        if qid not in after["queries"]:
            continue
        b, a = before["queries"][qid], after["queries"][qid]
        delta = (a["p50_ms"] - b["p50_ms"]) / max(b["p50_ms"], 0.001) * 100
        bufd = a["total_buffers"] - b["total_buffers"]
        changed = [list(x) for x in b["plan_fingerprint"]] != [list(x) for x in a["plan_fingerprint"]]
        mark = "yes" if changed else "—"
        if delta > 5:
            regressions.append((qid, delta))
        lines.append(
            f"| {qid} | {b['p50_ms']:.1f} | {a['p50_ms']:.1f} | {delta:+.1f}% | "
            f"{bufd:+,} | {mark} |"
        )
    if regressions:
        lines += ["", "**Slower after this change:**", ""]
        for qid, d in sorted(regressions, key=lambda x: -x[1]):
            lines.append(f"- `{qid}` {d:+.1f}% — publish this. An optimization that only "
                         f"ever wins was not measured.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage")
    ap.add_argument("--before")
    ap.add_argument("--after")
    ap.add_argument("--github-summary", action="store_true")
    args = ap.parse_args()

    if args.before and args.after:
        out = compare(load(args.before), load(args.after))
    elif args.stage:
        out = single(load(args.stage))
    else:
        raise SystemExit("pass --stage, or --before and --after")

    print(out)


if __name__ == "__main__":
    main()
