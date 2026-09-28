"""Tagline Stage 3: multi-touch attribution of Stage 2's orders across each person's sessions.

Shipped to Dataproc Serverless as attribution.zip (see tagline_spark/batch.py). The transformation
modules take DataFrames and return DataFrames and never touch BigQuery, so they are unit tested on a
local SparkSession (tests/):

* journeys.py: which orders are attributed, and each order's touches (the journey rules);
* models.py: the six models' weights, the daily mart, and the invariants the job checks before writing;
* tables.py: the output tables' column order and descriptions.

job.py is the only module that reads or writes BigQuery.
"""
