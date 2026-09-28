"""Helpers for the tagline_daily DAG (tagline/airflow/dags/tagline_daily.py).

Kept out of the DAG file so the DAG reads as a graph, and so the pure parts (model lineage,
batch ids, export table names) are unit tested by tests/test_dag.py. `.airflowignore` stops the
DAG processor from parsing this package as DAG files.
"""
