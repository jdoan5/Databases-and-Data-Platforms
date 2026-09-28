"""Where the attribution job is submitted from: `make spark-submit` and the Airflow DAG. Never shipped to
Dataproc. batch.py is the one definition of the Dataproc Serverless batch; submit.py uploads the code and
submits, watches and reports batches from the command line."""
