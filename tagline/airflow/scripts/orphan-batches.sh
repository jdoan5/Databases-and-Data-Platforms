#!/usr/bin/env bash
# Dataproc batches that tagline_daily started and that are still pending or running.
#
# The Spark task cancels its batch whenever a try ends before the batch does: an Airflow timeout or
# stop, any error while it waits, a Ctrl-C (AttributionBatchOperator). A worker that dies outright (the
# scheduler container killed, Docker Desktop quit in the middle of a run) never gets that far, and a
# cancel request can itself fail (it is then logged): the batch runs on until it finishes or reaches its
# 30-minute TTL, and when Airflow comes back the task's retry starts a second batch beside it, both
# writing the same tables. After such a crash, run this before `make airflow-up`:
#
#   make airflow-orphans            # list them (read-only)
#   make airflow-orphans CANCEL=1   # list them and cancel them
#
# A batch counts when it is PENDING or RUNNING and carries the labels app=tagline (the DAG's) and
# airflow-dag-id=tagline-daily (added by the Google provider's operator, which lowercases the DAG id and
# turns every character a label value may not hold, `_` included, into `-`), so a batch started by
# hand, e.g. by `make spark-submit`, is never touched. Project and region: TAGLINE_GCP_PROJECT and
# TAGLINE_GCP_REGION from the environment, else from tagline/.env. Uses the gcloud CLI's login.

set -euo pipefail

tagline_dir="$(cd "$(dirname "$0")/../.." && pwd)"
env_file="$tagline_dir/.env"
cancel="${CANCEL:-}"

# KEY=value from tagline/.env (optional `export`, optional quotes, trailing " # comment"), unless the
# environment already sets it, which is the precedence the Stage 2 pipeline uses too.
setting() {
  local key="$1" value="${!1:-}"
  if [[ -z "$value" && -f "$env_file" ]]; then
    value="$(sed -nE "s/^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=(.*)$/\2/p" "$env_file" | tail -n 1 \
      | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//; s/^"(.*)"$/\1/; s/^'\''(.*)'\''$/\1/')"
  fi
  if [[ -z "$value" ]]; then
    echo "$key is not set (tagline/.env; see .env.example)" >&2
    exit 2
  fi
  printf '%s' "$value"
}

project="$(setting TAGLINE_GCP_PROJECT)"
region="$(setting TAGLINE_GCP_REGION)"

# The label value the provider writes for the DAG id tagline_daily (seen on a real batch: tagline-daily).
dag_label="tagline-daily"

# The server-side filter is on state only (Dataproc rejects a list of states, and an OR filter
# hung when tried); the labels are matched here, on gcloud's `key=value;key=value` rendering.
orphans=()
for state in PENDING RUNNING; do
  while IFS=$'\t' read -r batch batch_state created labels; do
    [[ -n "$batch" ]] || continue
    [[ ";$labels;" == *";app=tagline;"* && ";$labels;" == *";airflow-dag-id=${dag_label};"* ]] || continue
    orphans+=("$batch")
    printf '%s\t%s\tcreated %s\n' "$batch" "$batch_state" "$created"
  done < <(gcloud dataproc batches list --project="$project" --region="$region" \
             --filter="state=$state" --format='value(name.basename(),state,createTime,labels)')
done

if [[ ${#orphans[@]} -eq 0 ]]; then
  echo "no pending or running tagline_daily batches in $region"
  exit 0
fi

if [[ "$cancel" != "1" ]]; then
  echo "${#orphans[@]} batch(es) still pending or running; if Airflow is not running them, cancel with: make airflow-orphans CANCEL=1"
  exit 0
fi

for batch in "${orphans[@]}"; do
  gcloud dataproc batches cancel "$batch" --project="$project" --region="$region" --quiet
done
echo "cancellation requested for ${#orphans[@]} batch(es)"
