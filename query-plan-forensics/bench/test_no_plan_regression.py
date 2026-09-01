"""The gate. Fails a pull request when a query plan changes shape.

What it asserts, and why each one is runner-independent:

  plan fingerprint     pure function of schema + statistics + GUCs
  root Total Cost      an estimate, not a measurement; only ANALYZE sampling
                       moves it
  Plan Rows            catches a statistics regression before it becomes a plan
                       regression
  required_indexes     catches a dropped or renamed index

What it never asserts on: wall-clock, execution time, planning time, workers
launched. A GitHub runner's IO is nothing like a laptop's, and asserting on
those guarantees a flapping gate.

total_buffers is compared only when ASSERT_BUFFERS is on. It is tempting --
"buffers touched is deterministic for a given plan" -- but the sum moves with
actual worker count and with bitmap-heap lossiness under work_mem pressure. It
is off by default and reported rather than enforced.

  QPF_STAGE=baseline python3 -m pytest bench/test_no_plan_regression.py -v
"""

from __future__ import annotations

import json
import os
import pathlib

import pytest

import plan as planlib

HERE = pathlib.Path(__file__).resolve().parent
BASELINE_DIR = HERE / "baselines"
RESULT_DIR = HERE / "results"

STAGE = os.environ.get("QPF_STAGE", "current")
ASSERT_BUFFERS = os.environ.get("QPF_ASSERT_BUFFERS", "0") == "1"


def _load_results():
    path = RESULT_DIR / f"{STAGE}.json"
    if not path.exists():
        pytest.skip(
            f"no results at {path}. Run: python3 bench/runner.py --stage {STAGE}"
        )
    return json.loads(path.read_text())


def _baselines():
    files = sorted(BASELINE_DIR.glob("Q*.json"))
    if not files:
        pytest.skip(
            "no baselines committed yet. Capture them once the schema is settled:\n"
            "  python3 bench/runner.py --stage baseline --write-baselines"
        )
    return files


@pytest.fixture(scope="module")
def results():
    return _load_results()


@pytest.mark.parametrize("baseline_path", _baselines(), ids=lambda p: p.stem)
def test_plan_did_not_regress(baseline_path, results):
    base = json.loads(baseline_path.read_text())
    qid = base["query_id"]

    assert qid in results["queries"], f"{qid} is baselined but was not run"
    cur = results["queries"][qid]

    # --- 1. plan shape ------------------------------------------------------
    base_fp = [list(x) for x in base["plan_fingerprint"]]
    cur_fp = [list(x) for x in cur["plan_fingerprint"]]
    if base_fp != cur_fp:
        pytest.fail(
            f"{qid} FAILED: plan fingerprint changed\n"
            + planlib.diff_fingerprints(base_fp, cur_fp)
            + f"\n  root_total_cost {base['root_total_cost']} -> {cur['root_total_cost']}"
        )

    # --- 2. estimated cost within budget ------------------------------------
    tol = base["cost_tolerance_pct"] / 100
    lo = base["root_total_cost"] * (1 - tol)
    hi = base["root_total_cost"] * (1 + tol)
    assert lo <= cur["root_total_cost"] <= hi, (
        f"{qid}: root_total_cost {base['root_total_cost']} -> {cur['root_total_cost']} "
        f"(budget +/-{base['cost_tolerance_pct']}%)"
    )

    # --- 3. row estimate has not drifted ------------------------------------
    factor = base["rows_tolerance_factor"]
    base_rows = max(base["plan_rows"], 1)
    cur_rows = max(cur["plan_rows"], 1)
    ratio = max(base_rows, cur_rows) / min(base_rows, cur_rows)
    assert ratio <= factor, (
        f"{qid}: plan_rows {base['plan_rows']} -> {cur['plan_rows']} "
        f"({ratio:.1f}x, budget {factor}x). Statistics have drifted; ANALYZE, "
        f"and if it persists the estimate itself is the bug."
    )

    # --- 4. the indexes it was supposed to use are still being used ---------
    missing = set(base["required_indexes"]) - set(cur["required_indexes"])
    assert not missing, (
        f"{qid}: plan no longer uses {sorted(missing)}. "
        f"Dropped, renamed, or the predicate stopped matching it."
    )

    # --- 5. buffers, opt-in only --------------------------------------------
    if ASSERT_BUFFERS and base.get("total_buffers"):
        btol = base["buffers_tolerance_pct"] / 100
        blo = base["total_buffers"] * (1 - btol)
        bhi = base["total_buffers"] * (1 + btol)
        assert blo <= cur["total_buffers"] <= bhi, (
            f"{qid}: total_buffers {base['total_buffers']} -> {cur['total_buffers']}"
        )


def test_dataset_matches_baseline_profile(results):
    """Plan shapes legitimately differ between the small and full profiles.

    Comparing a 500k-row run against 5M-row baselines produces failures that are
    correct planner behaviour, not regressions. Catch that here with a clear
    message rather than in ten confusing fingerprint diffs.
    """
    files = sorted(BASELINE_DIR.glob("Q*.json"))
    baselined_rows = json.loads(files[0].read_text()).get("movement_rows")
    if baselined_rows is None:
        pytest.skip("baselines predate row-count recording")
    actual = results["movement_rows"]
    assert abs(actual - baselined_rows) / max(baselined_rows, 1) < 0.05, (
        f"dataset mismatch: baselines captured at {baselined_rows:,} movements, "
        f"this run has {actual:,}. The gate proves the plan did not change "
        f"relative to the same-sized dataset it was baselined against -- it "
        f"cannot compare across profiles. Re-run with the matching PROFILE."
    )
