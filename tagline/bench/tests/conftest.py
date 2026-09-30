import sys
from pathlib import Path

# Run from source: tagline/bench (tagline_bench) and tagline/spark (tagline_spark) on the path.
BENCH = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BENCH), str(BENCH.parent / "spark")]
