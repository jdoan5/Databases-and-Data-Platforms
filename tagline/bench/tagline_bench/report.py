"""Markdown from the results log: medians and ranges per variant, so a write-up quotes numbers from one place."""

from __future__ import annotations

from collections import OrderedDict, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from . import prices
from .results import spread_pct, stats

GIB, MIB = 2**30, 2**20


def fmt_bytes(n: float | None) -> str:
    if n is None:
        return "-"
    if abs(n) >= GIB:
        return f"{n / GIB:.3f} GiB"
    return f"{n / MIB:.1f} MiB"


def fmt_num(n: float | None, digits: int = 1) -> str:
    if n is None:
        return "-"
    if isinstance(n, int) or float(n).is_integer() and abs(n) >= 1000:
        return f"{int(n):,}"
    return f"{n:,.{digits}f}"


def med_range(s: Mapping[str, Any], f=fmt_num) -> str:
    if not s["n"]:
        return "-"
    if s["n"] == 1 or s["min"] == s["max"]:
        return f(s["median"])
    return f"{f(s['median'])} ({f(s['min'])}–{f(s['max'])})"


def _by_variant(records: Iterable[Mapping[str, Any]], rtype: str) -> OrderedDict[str, list[Mapping[str, Any]]]:
    out: OrderedDict[str, list[Mapping[str, Any]]] = OrderedDict()
    for r in records:
        if r.get("type") == rtype:
            out.setdefault(r["variant"], []).append(r)
    return out


def bq_section(records: list[Mapping[str, Any]], with_jobs: bool) -> list[str]:
    runs = _by_variant(records, "bq_run")
    if not runs:
        return []
    lines = [
        "## BigQuery runs",
        "",
        "Per run: the command's wall time, and the sums over its jobs (from `INFORMATION_SCHEMA.JOBS_BY_PROJECT`). "
        "Median (min–max) over the runs. Bytes billed repeat almost exactly (a job that prunes clustered blocks can move "
        "by a MiB between rebuilds); slot-ms and seconds do not.",
        "",
        "| variant | runs | command wall s | jobs | bytes billed | slot-ms | job seconds | list price per run | x30 per month | checks |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for variant, rs in runs.items():
        ok = [r for r in rs if r.get("exit_code") == 0]
        use = ok or rs
        billed = stats(r["totals"]["bytes_billed"] for r in use)
        checks = sorted({r.get("checks_line") or "-" for r in use})
        lines.append(
            f"| {variant} | {len(use)}{'' if len(ok) == len(rs) else f' ({len(rs) - len(ok)} failed)'} "
            f"| {med_range(stats(r['wall_seconds'] for r in use))} "
            f"| {med_range(stats(r['totals']['jobs'] for r in use), lambda x: f'{x:.0f}')} "
            f"| {med_range(billed, fmt_bytes)} "
            f"| {med_range(stats(r['totals']['slot_ms'] for r in use), lambda x: f'{x:,.0f}')} "
            f"| {med_range(stats(r['totals']['job_seconds'] for r in use))} "
            f"| ${prices.bq_usd(billed['median']):.4f} | ${prices.bq_usd(billed['median']) * prices.DAYS_PER_MONTH:.3f} "
            f"| {'; '.join(checks)} |"
        )
    lines.append("")
    if with_jobs:
        for variant, rs in runs.items():
            use = [r for r in rs if r.get("exit_code") == 0] or rs
            steps: OrderedDict[tuple[str, str], list[Mapping[str, Any]]] = OrderedDict()
            for r in use:
                for j in r["jobs"]:
                    steps.setdefault((str(j.get("step")), str(j.get("kind"))), []).append(j)
                    # a script's statements, as indented rows under it (the nth statement of a type on a table)
                    seen: dict[tuple[str, str], int] = {}
                    for c in j.get("statements", []):
                        table = (c.get("destination") or "-").split(".")[-1]
                        table = "-" if table.startswith("anon") else table
                        kind = (c.get("statement_type") or "-").lower()
                        n = seen[(kind, table)] = seen.get((kind, table), 0) + 1
                        steps.setdefault((f"&nbsp;&nbsp;{table}" + (f" ({n})" if n > 1 else ""), kind), []).append(c)
            lines += [
                f"### {variant}: per job, median (min–max) over {len(use)} runs",
                "",
                "| step | kind | bytes processed | bytes billed | slot-ms | elapsed s | slot-ms spread |",
                "|---|---|---|---|---|---|---|",
            ]
            for (step, kind), js in steps.items():
                slot = stats(j.get("slot_ms") for j in js)
                sp = spread_pct(slot)
                lines.append(
                    f"| {step} | {kind} | {med_range(stats(j.get('bytes_processed') for j in js), fmt_bytes)} "
                    f"| {med_range(stats(j.get('bytes_billed') for j in js), fmt_bytes)} "
                    f"| {med_range(slot, lambda x: f'{x:,.0f}')} | {med_range(stats(j.get('elapsed_seconds') for j in js))} "
                    f"| {'-' if sp is None else f'{sp:.0f}%'} |"
                )
            lines.append("")
    return lines


def window_section(records: list[Mapping[str, Any]]) -> list[str]:
    ws = _by_variant(records, "bq_window")
    if not ws:
        return []
    lines = ["## BigQuery jobs by time window", "", "| variant | since | jobs | bytes billed | slot-ms | job seconds | wall s | note |", "|---|---|---|---|---|---|---|---|"]
    for variant, rs in ws.items():
        for r in rs:
            t = r["totals"]
            lines.append(f"| {variant} | {r['since']} | {t['jobs']} | {fmt_bytes(t['bytes_billed'])} | {t['slot_ms']:,} | {t['job_seconds']:.1f} | {fmt_num(r.get('wall_seconds'))} | {r.get('note') or ''} |")
    return lines + [""]


def _mode(r: Mapping[str, Any]) -> str:
    """local / executors from the application id, with the master when the job reported it (Stage 4 code on)."""
    mode, master = r.get("mode") or "-", r.get("spark_master")
    return f"{mode} ({master})" if master else mode


def spark_section(records: list[Mapping[str, Any]]) -> list[str]:
    runs = _by_variant(records, "spark_run")
    if not runs:
        return []
    lines = [
        "## Spark batches",
        "",
        "| variant | run | batch | state | wall s | pending s | running s | DCU-hours | avg DCUs running | shuffle GB-h | compute s | write s | of which summary s | loads s | mode | list price |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    summary_rows = []
    for variant, rs in runs.items():
        for r in rs:
            loads = ", ".join(
                f"{j['elapsed_seconds']:.1f}" + (f" (+{j['queued_seconds']:.0f} queued)" if (j.get("queued_seconds") or 0) >= 5 else "")
                for j in r.get("load_jobs", [])
                if j.get("elapsed_seconds") is not None
            )
            lines.append(
                f"| {variant} | {r['run']} | `{r['batch_id']}` | {r['state']} | {fmt_num(r['wall_seconds'], 0)} | {fmt_num(r['pending_seconds'], 0)} "
                f"| {fmt_num(r['running_seconds'], 0)} | {r['dcu_hours']:.4f} | {fmt_num(r.get('avg_dcu_while_running'))} | {r['shuffle_gib_hours']:.2f} "
                f"| {fmt_num(r.get('compute_seconds'))} | {fmt_num(r.get('write_seconds'))} | {fmt_num((r.get('summary') or {}).get('summary_seconds'))} "
                f"| {loads or '-'} | {_mode(r)} | ${r['usd']:.4f} |"
            )
        ok = [r for r in rs if r["state"] == "SUCCEEDED"]
        usd = stats(r["usd"] for r in ok)
        monthly = "-" if usd["median"] is None else f"${usd['median'] * prices.DAYS_PER_MONTH:.3f}"
        summary_rows.append(
            f"| {variant} | {len(ok)} of {len(rs)} | {med_range(stats(r['wall_seconds'] for r in ok), lambda x: f'{x:.0f}')} "
            f"| {med_range(stats(r['pending_seconds'] for r in ok), lambda x: f'{x:.0f}')} "
            f"| {med_range(stats(r['dcu_hours'] for r in ok), lambda x: f'{x:.4f}')} "
            f"| {med_range(stats(r.get('compute_seconds') for r in ok))} | {med_range(stats(r.get('write_seconds') for r in ok))} "
            f"| {med_range(usd, lambda x: f'${x:.4f}')} | {monthly} |"
        )
    lines += [
        "",
        "| variant | succeeded | wall s | pending s | DCU-hours | compute s | write s | list price | x30 per month |",
        "|---|---|---|---|---|---|---|---|---|",
        *summary_rows,
        "",
    ]
    return lines


def storage_section(records: list[Mapping[str, Any]]) -> list[str]:
    st = _by_variant(records, "storage")
    if not st:
        return []
    lines = ["## Storage", ""]
    for variant, rs in st.items():
        for r in rs:
            lines += [
                f"### {variant}, observed {r['observed_at']}" + (f": {r['note']}" if r.get("note") else ""),
                "",
                f"Source: {r.get('source', 'INFORMATION_SCHEMA.TABLE_STORAGE')}"
                + ("" if r.get("fail_safe_included", True) else " (fail-safe bytes and dropped tables not visible there, so the physical cost is a lower bound)")
                + ". Physical = current + time travel.",
                "",
                "| dataset | billing model | time travel h | tables | rows | logical | active logical | long-term logical | physical | current physical | time travel | fail-safe | $/month logical | $/month physical |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
            ]
            for ds, a in r["datasets"].items():
                if "tables" not in a:
                    continue
                o = a.get("options") or {}
                lines.append(
                    f"| {ds} | {o.get('storage_billing_model', '-')} | {o.get('max_time_travel_hours', '-')} | {a['tables']} | {a['total_rows']:,} "
                    f"| {fmt_bytes(a['total_logical_bytes'])} | {fmt_bytes(a['active_logical_bytes'])} | {fmt_bytes(a['long_term_logical_bytes'])} "
                    f"| {fmt_bytes(a['total_physical_bytes'])} | {fmt_bytes(a.get('current_physical_bytes'))} | {fmt_bytes(a['time_travel_physical_bytes'])} "
                    f"| {fmt_bytes(a['fail_safe_physical_bytes']) if r.get('fail_safe_included', True) else 'n/a'} "
                    f"| ${a['monthly_usd']['logical']:.4f} | ${a['monthly_usd']['physical']:.4f} |"
                )
            lines += ["", "| table | rows | partitions | logical | physical | current physical | time travel | compression (logical / current) |", "|---|---|---|---|---|---|---|---|"]
            for t in r["tables"]:
                cur = t.get("current_physical_bytes")
                ratio = f"{t['total_logical_bytes'] / cur:.1f}x" if cur and t.get("total_logical_bytes") else "-"
                lines.append(
                    f"| {t['table_schema']}.{t['table_name']} | {t['total_rows'] or 0:,} | {t.get('total_partitions') or '-'} | {fmt_bytes(t['total_logical_bytes'])} "
                    f"| {fmt_bytes(t['total_physical_bytes'])} | {fmt_bytes(cur)} | {fmt_bytes(t['time_travel_physical_bytes'])} | {ratio} |"
                )
            lines.append("")
    return lines


def fingerprint_section(records: list[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in records:
        for key, fp in (r.get("fingerprints") or {}).items():
            table = key.split(".", 1)[1]
            tag = f"{fp['fingerprint']}" + (f"@{fp['float_digits']}" if fp.get("float_digits") is not None else "")
            seen[r["variant"]][table].add(tag)
            counts[r["variant"]][table] += 1
    if not seen:
        return []
    lines = ["## Fingerprints", "", "Distinct fingerprints per table across every record of the variant (1 = identical every time).", "",
             "| variant | table | fingerprints taken | distinct | rows |", "|---|---|---|---|---|"]
    for variant, tables in seen.items():
        for table, fps in sorted(tables.items()):
            rows = sorted({f.split(':', 1)[0] for f in fps})
            lines.append(f"| {variant} | {table} | {counts[variant][table]} | {len(fps)} | {', '.join(f'{int(x):,}' for x in rows)} |")
    return lines + [""]


def diff_section(records: list[Mapping[str, Any]]) -> list[str]:
    ds = [r for r in records if r.get("type") == "diff"]
    if not ds:
        return []
    lines = ["## Diffs against the baseline dataset", "", "| variant | recorded | table | identical | rows (current / baseline) | only in current | only in baseline |", "|---|---|---|---|---|---|---|"]
    for r in ds:
        for t, d in r["tables"].items():
            lines.append(f"| {r['variant']} | {r['recorded_at']} | {t} | {d['identical']} | {d['current_rows']:,} / {d['baseline_rows']:,} | {d['only_in_current']:,} | {d['only_in_baseline']:,} |")
    return lines + [""]


def load_probe_section(records: list[Mapping[str, Any]]) -> list[str]:
    """Load jobs of the same file into differently laid-out scratch tables (Stage 4, experiment 8)."""
    probes = [r for r in records if r.get("type") == "load_probe"]
    if not probes:
        return []
    lines = ["## Load-job probes", "", "| variant | table and layout | runs | load seconds |", "|---|---|---|---|"]
    for r in probes:
        for name, st in r["summary"].items():
            lines.append(f"| {r['variant']} | {name} | {st['runs']} | {st['median_s']:.2f} ({st['min_s']:.2f}–{st['max_s']:.2f}) |")
    return lines + [""]


def daily_section(records: list[Mapping[str, Any]]) -> list[str]:
    """For a variant with both builds and batches: one daily run = the median build + the median batch, x30."""
    bq_runs, sp_runs = _by_variant(records, "bq_run"), _by_variant(records, "spark_run")
    both = [v for v in bq_runs if v in sp_runs]
    if not both:
        return []
    lines = [
        "## A daily run (median build + median batch)",
        "",
        "List price. The BigQuery part is the variant's build command (for the baseline, `make build`: 8 models, 9 checks); "
        "the DAG also runs the 3 attribution checks (about 80 MiB, $0.0005) and has per-task overhead. Compare the last "
        "column with BigQuery's free 1 TiB of queries a month: below it, the BigQuery part is list price, not what the "
        "project pays.",
        "",
        "| variant | build wall s | batch wall s | BigQuery per run | Spark per run | per run | x30 per month | BigQuery TiB per month |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for v in both:
        b = [r for r in bq_runs[v] if r.get("exit_code") == 0]
        s = [r for r in sp_runs[v] if r["state"] == "SUCCEEDED"]
        if not b or not s:
            continue
        billed = stats(r["totals"]["bytes_billed"] for r in b)["median"]
        bq_usd = prices.bq_usd(billed)
        sp_usd = stats(r["usd"] for r in s)["median"]
        lines.append(
            f"| {v} | {stats(r['wall_seconds'] for r in b)['median']:.1f} | {stats(r['wall_seconds'] for r in s)['median']:.0f} "
            f"| ${bq_usd:.4f} | ${sp_usd:.4f} | ${bq_usd + sp_usd:.4f} | ${(bq_usd + sp_usd) * prices.DAYS_PER_MONTH:.2f} "
            f"| {billed * prices.DAYS_PER_MONTH / prices.TIB:.3f} |"
        )
    return lines + [""]


def render(records: list[Mapping[str, Any]], with_jobs: bool = True) -> str:
    p = prices.as_dict()
    head = [
        "# Stage 4 results log (generated: `make bench-report`)",
        "",
        f"Prices read {p['read_on']}: BigQuery on demand ${p['bq_on_demand_usd_per_tib']}/TiB (US; first 1 TiB a month free); storage per GiB-month "
        f"{', '.join(f'{k} ${v}' for k, v in p['storage_usd_per_gib_month'].items())} (first 10 GiB of each free); Serverless for Apache Spark "
        f"standard tier us-central1 ${p['dcu_usd_per_hour']}/DCU-hour and ${p['shuffle_usd_per_gib_hour']:.9f}/GiB-hour shuffle storage. "
        "All money is list price before free tiers.",
        "",
    ]
    body = daily_section(list(records)) + bq_section(list(records), with_jobs) + window_section(list(records)) + spark_section(list(records)) + storage_section(list(records)) + fingerprint_section(list(records)) + diff_section(list(records)) + load_probe_section(list(records))
    return "\n".join(head + body).rstrip() + "\n"
