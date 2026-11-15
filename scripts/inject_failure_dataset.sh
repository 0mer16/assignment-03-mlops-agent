#!/usr/bin/env bash
# Injected failure 2: corrupt the dataset so the DAG's validation gate rejects
# it. Training must never start, and nothing may reach the model registry.
set -uo pipefail

REPO=/mnt/i/MLPAss/assignment-03-mlops-agent
AIRFLOW=http://127.0.0.1:8080
AF_PASS=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/airflow/simple_auth_manager_passwords.json.generated')))['admin'])")

token() {
  curl -s -X POST "$AIRFLOW/auth/token" -H 'Content-Type: application/json' \
    -d "{\"username\":\"admin\",\"password\":\"$AF_PASS\"}" \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])"
}

action=${1:-inject}
cd "$REPO"

case "$action" in
  inject)
    echo "### backing up and truncating the dataset below the minimum row count"
    cp data/dataset.csv /tmp/dataset.csv.backup
    head -40 /tmp/dataset.csv.backup > data/dataset.csv
    wc -l data/dataset.csv

    TOKEN=$(token)
    echo "### triggering the pipeline"
    RUN=$(curl -s -X POST "$AIRFLOW/api/v2/dags/training_pipeline/dagRuns" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      -d '{"logical_date": null, "conf": {}, "note": "injected failure: truncated dataset"}' \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['dag_run_id'])")
    echo "run: $RUN"

    for i in $(seq 1 40); do
      STATE=$(curl -s -H "Authorization: Bearer $(token)" \
        "$AIRFLOW/api/v2/dags/training_pipeline/dagRuns/$RUN" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['state'])")
      case "$STATE" in success|failed) break;; esac
      sleep 10
    done
    echo "final state: $STATE"

    echo "### task instances"
    curl -s -H "Authorization: Bearer $(token)" \
      "$AIRFLOW/api/v2/dags/training_pipeline/dagRuns/$RUN/taskInstances?limit=50" \
      | python3 -c "
import sys, json
for t in sorted(json.load(sys.stdin)['task_instances'], key=lambda x: x.get('start_date') or 'z'):
    print(f\"  {t['task_id']:<20}{str(t['state'])}\")"
    ;;
  restore)
    echo "### restoring the dataset from the DVC cache"
    # dvc checkout restores the exact recorded content; the resulting hash
    # should match the md5 in data/dataset.csv.dvc
    dvc checkout data/dataset.csv.dvc --force
    wc -l data/dataset.csv
    md5sum data/dataset.csv
    grep md5 data/dataset.csv.dvc | head -1
    ;;
  *)
    echo "usage: $0 [inject|restore]" >&2
    exit 2
    ;;
esac
