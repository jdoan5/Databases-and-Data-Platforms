"""Normalize an EXPLAIN JSON tree into something two machines can compare.

The whole regression gate rests on the distinction this module draws:

  KEEP    node type, relation, index, and the planner's *estimates*
          -- pure functions of schema + statistics + GUCs
  DISCARD anything measured -- actual times, worker counts, buffer hit/read split

A GitHub Actions runner has different CPU and IO than a laptop. Asserting on
measurements guarantees a flapping gate; asserting on plan shape and estimated
cost does not.
"""

from __future__ import annotations

# Fields that vary run to run even when the plan is identical. The gate must
# never assert on any of these -- see test_no_plan_regression.py.
VOLATILE_KEYS = {
    "Actual Startup Time",
    "Actual Total Time",
    "Actual Rows",
    "Actual Loops",
    "Workers Launched",
    "Workers",
    "Shared Hit Blocks",
    "Shared Read Blocks",
    "Shared Dirtied Blocks",
    "Shared Written Blocks",
    "Local Hit Blocks",
    "Local Read Blocks",
    "Temp Read Blocks",
    "Temp Written Blocks",
    "I/O Read Time",
    "I/O Write Time",
    "Subplans Removed",  # runtime partition pruning is legitimately variable
}

# What gets removed from the plan we STORE, which is a narrower set.
#
# These two lists were the same list once, and that was a mistake: a stored plan
# with its buffer counts stripped cannot be re-examined after the fact, so
# "Q07 gained 208k buffers with no plan change" became undiagnosable from the
# saved run. Nothing here is used for fingerprinting -- fingerprint() reads only
# Node Type, Relation Name and Index Name -- so keeping the counters costs
# nothing and buys every later question.
STORAGE_STRIP = {
    "Actual Startup Time",
    "Actual Total Time",
    "Actual Loops",
}


def fingerprint(plan_node, depth=0, out=None):
    """Flatten a plan tree to an ordered list of (depth, node, relation, index).

    Order matters: two plans with the same nodes in a different arrangement are
    different plans, and the gate should say so.
    """
    if out is None:
        out = []
    out.append(
        (
            depth,
            plan_node.get("Node Type"),
            plan_node.get("Relation Name"),
            plan_node.get("Index Name"),
        )
    )
    for child in plan_node.get("Plans", []) or []:
        fingerprint(child, depth + 1, out)
    return out


def indexes_used(plan_node, acc=None):
    """Every index the plan actually references, anywhere in the tree."""
    if acc is None:
        acc = set()
    name = plan_node.get("Index Name")
    if name:
        acc.add(name)
    for child in plan_node.get("Plans", []) or []:
        indexes_used(child, acc)
    return acc


def _walk(node, fn):
    fn(node)
    for child in node.get("Plans", []) or []:
        _walk(child, fn)


def total_buffers(plan_node):
    """Buffers touched by the whole query: the ROOT node's hit + read.

    Do not sum the tree. EXPLAIN's buffer counters are CUMULATIVE -- every node
    already includes everything its children touched -- so adding them up
    double-counts once per level of nesting. On Q07, a six-deep plan, summing
    gave 339,126 against a true 56,526: a 6x inflation that also manufactured a
    fake "+208,830 buffers with no plan change" between two runs, because the
    error scales with the numbers rather than being a constant offset.

    Recorded and reported, but still NOT asserted on by default: the true value
    moves with actual worker count and with bitmap-heap lossiness under work_mem
    pressure, both of which differ between a laptop and a CI runner. See
    ASSERT_BUFFERS in test_no_plan_regression.py.
    """
    return plan_node.get("Shared Hit Blocks", 0) + plan_node.get("Shared Read Blocks", 0)


def strip_volatile(plan_node):
    """Return a deep copy of the tree with only the noisiest timings removed.

    Buffer counters and worker counts are KEPT so a saved run stays diagnosable.
    They are still never asserted on -- that is the gate's job, not storage's.
    """
    clean = {k: v for k, v in plan_node.items() if k not in STORAGE_STRIP and k != "Plans"}
    children = plan_node.get("Plans")
    if children:
        clean["Plans"] = [strip_volatile(c) for c in children]
    return clean


def summarize(explain_json):
    """Reduce one EXPLAIN (FORMAT JSON) result to the row the harness stores."""
    root = explain_json[0] if isinstance(explain_json, list) else explain_json
    plan = root["Plan"]
    return {
        "plan_fingerprint": fingerprint(plan),
        "required_indexes": sorted(indexes_used(plan)),
        "root_total_cost": plan.get("Total Cost"),
        "plan_rows": plan.get("Plan Rows"),
        "actual_rows": plan.get("Actual Rows"),
        "total_buffers": total_buffers(plan),
        "exec_ms": root.get("Execution Time"),
        "plan_ms": root.get("Planning Time"),
        "settings": root.get("Settings", {}),
        "plan": strip_volatile(plan),
    }


def diff_fingerprints(baseline, current):
    """Human-readable first divergence between two fingerprints."""
    for i, (b, c) in enumerate(zip(baseline, current)):
        if list(b) != list(c):
            return f"  first divergence at node {i}:\n    baseline: {b}\n    current:  {c}"
    if len(baseline) != len(current):
        return f"  tree length {len(baseline)} -> {len(current)}"
    return "  (identical)"
