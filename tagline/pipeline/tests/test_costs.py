from tagline_pipeline.costs import JobStat, format_cost_table, human_bytes, write_json


def test_human_bytes_uses_binary_units():
    assert human_bytes(None) == "-"
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1536) == "1.50 KiB"
    assert human_bytes(3 * 1024**3) == "3.00 GiB"
    assert human_bytes(5 * 1024**5) == "5120.00 TiB"


def test_cost_table_has_a_line_per_job_and_totals():
    stats = [
        JobStat("stg_events", "model", bytes_processed=2 * 1024**3, bytes_billed=2 * 1024**3, slot_ms=1_000_000, seconds=12.34, rows=10),
        JobStat("01_keys_unique", "check", bytes_processed=1024, bytes_billed=10 * 1024**2, slot_ms=500, seconds=1.0, rows=0),
        JobStat("products", "load", seconds=2.0, rows=20),
    ]
    lines = format_cost_table(stats).splitlines()
    assert lines[0].split() == ["step", "kind", "processed", "billed", "slot-ms", "seconds", "rows"]
    assert len(lines) == 2 + 3 + 2  # header, rule, jobs, rule, total
    assert "1,000,000" in lines[2] and "12.3" in lines[2]
    assert lines[4].split()[:2] == ["products", "load"] and lines[4].split()[2:4] == ["-", "-"]
    total = lines[-1].split()
    assert total[:3] == ["total", "3", "jobs"]
    assert "1,000,500" in lines[-1]
    assert "15.3" in lines[-1]


def test_dry_run_is_labelled():
    table = format_cost_table([JobStat("stg_events", "model", bytes_processed=100, dry_run=True)])
    assert "model (dry run)" in table


def test_write_json(tmp_path):
    path = tmp_path / "runs" / "costs.json"
    write_json([JobStat("x", "model", bytes_processed=1)], path)
    assert '"bytes_processed": 1' in path.read_text()
