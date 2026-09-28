"""Dataproc main file for the Stage 3 attribution job. The code is in attribution.zip, which the batch
passes as a Python file (python_file_uris), so `attribution` imports from it. See tagline_spark/batch.py."""

import sys

from attribution.job import main

if __name__ == "__main__":
    sys.exit(main())
