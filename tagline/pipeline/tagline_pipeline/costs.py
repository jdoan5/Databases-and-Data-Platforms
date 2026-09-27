"""What each BigQuery job cost: bytes processed and billed, slot-milliseconds, wall time.

These numbers are Stage 4's baseline, so every job the pipeline runs is recorded and
printed as a table at the end of a command.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_UNITS = ("B", "KiB", "MiB", "GiB", "TiB")


@dataclass
class JobStat:
    step: str
    kind: str  # model, check, report, load
    bytes_processed: int | None = None
    bytes_billed: int | None = None
    slot_ms: int | None = None
    seconds: float | None = None
    rows: int | None = None  # rows in the built table, or rows a check/report returned
    job_id: str | None = None
    dry_run: bool = False


def human_bytes(n: int | None) -> str:
    """1536 -> '1.5 KiB'. Binary units, as BigQuery bills (per TiB)."""
    if n is None:
        return "-"
    value = float(n)
    for unit in _UNITS:
        if abs(value) < 1024 or unit == _UNITS[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def _num(n: int | None) -> str:
    return "-" if n is None else f"{n:,}"


def format_cost_table(stats: list[JobStat]) -> str:
    """A fixed-width table, one line per job and a total line."""
    headers = ("step", "kind", "processed", "billed", "slot-ms", "seconds", "rows")
    lines: list[tuple[str, ...]] = []
    for s in stats:
        lines.append(
            (
                s.step,
                s.kind + (" (dry run)" if s.dry_run else ""),
                human_bytes(s.bytes_processed),
                human_bytes(s.bytes_billed),
                _num(s.slot_ms),
                "-" if s.seconds is None else f"{s.seconds:.1f}",
                _num(s.rows),
            )
        )

    def total(attr: str) -> int | None:
        values = [getattr(s, attr) for s in stats if getattr(s, attr) is not None]
        return sum(values) if values else None

    seconds = [s.seconds for s in stats if s.seconds is not None]
    lines.append(
        (
            "total",
            f"{len(stats)} jobs",
            human_bytes(total("bytes_processed")),
            human_bytes(total("bytes_billed")),
            _num(total("slot_ms")),
            f"{sum(seconds):.1f}" if seconds else "-",
            "",
        )
    )
    widths = [max(len(h), *(len(row[i]) for row in lines)) for i, h in enumerate(headers)]
    right = {2, 3, 4, 5, 6}

    def fmt(row: tuple[str, ...]) -> str:
        return "  ".join(c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(row, widths))).rstrip()

    rule = "  ".join("-" * w for w in widths)
    return "\n".join([fmt(headers), rule, *(fmt(r) for r in lines[:-1]), rule, fmt(lines[-1])])


def write_json(stats: list[JobStat], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(s) for s in stats], indent=2) + "\n", encoding="utf-8")
