# Airflow evidence

Exported 2026-11-15 18:33 UTC from http://127.0.0.1:8080 (API v2)

## DAGs
```json
{
    "dags": [
        {
            "dag_id": "training_pipeline",
            "dag_display_name": "training_pipeline",
            "is_paused": false,
            "scheduling_state": "active",
            "is_stale": false,
            "last_parsed_time": "2026-11-15T17:58:24.035295Z",
            "last_parse_duration": 3.6868384860001697,
            "last_expired": null,
            "bundle_name": "dags-folder",
            "bundle_version": null,
            "relative_fileloc": "training_pipeline.py",
            "fileloc": "/mnt/i/MLPAss/assignment-03-mlops-agent/dags/training_pipeline.py",
            "description": "Validate, preprocess, train, evaluate and gate the failure-prediction model",
            "timetable_summary": null,
            "timetable_description": "Never, external triggers only",
            "timetable_partitioned": false,
            "timetable_periodic": false,
            "tags": [
                {
                    "name": "mlops",
                    "dag_id": "training_pipeline",
                    "dag_display_name": "training_pipeline"
                },
                {
                    "name": "training",
                    "dag_id": "training_pipeline",
                    "dag_display_name": "training_pipeline"
                }
            ],
            "max_active_tasks": 16,
            "max_active_runs": 1,
            "max_consecutive_failed_dag_runs": 0,
            "has_task_concurrency_limits": false,
            "has_import_errors": false,
            "next_dagrun_logical_date": null,
            "next_dagrun_data_interval_start": null,
            "next_dagrun_data_interval_end": null,
            "next_dagrun_run_after": null,
            "allowed_run_types": null,
            "owners": [
                "airflow"
            ],
            "is_backfillable": false,
            "file_token": "eyJidW5kbGVfbmFtZSI6ImRhZ3MtZm9sZGVyIiwicmVsYXRpdmVfZmlsZWxvYyI6InRyYWluaW5nX3BpcGVsaW5lLnB5In0.ySZhS452WcpvQ5hLJRliR0v5TPU"
        }
    ],
    "total_entries": 1
}
```

## Recent runs of training_pipeline
```json
{
    "dag_runs": [
        {
            "dag_run_id": "manual__2026-11-15T18:31:58.177125+00:00",
            "dag_id": "training_pipeline",
            "logical_date": null,
            "queued_at": "2026-11-15T18:31:58.183548Z",
            "start_date": "2026-11-15T18:31:59.656992Z",
            "end_date": "2026-11-15T18:32:21.086397Z",
            "duration": 21.429405,
            "data_interval_start": null,
            "data_interval_end": null,
            "run_after": "2026-11-15T18:31:58.177125Z",
            "last_scheduling_decision": "2026-11-15T18:32:21.084739Z",
            "run_type": "manual",
            "state": "success",
            "triggered_by": "rest_api",
            "triggering_user_name": "admin",
            "conf": {},
            "note": "requested through the MLOps agent",
            "dag_versions": [
                {
                    "id": "01a1e045-b954-7967-b175-2a207605cb7b",
                    "version_number": 1,
                    "dag_id": "training_pipeline",
                    "bundle_name": "dags-folder",
                    "bundle_version": null,
                    "created_at": "2026-11-15T17:33:48.244500Z",
                    "dag_display_name": "training_pipeline",
                    "bundle_url": null
                }
            ],
            "bundle_version": null,
            "dag_display_name": "training_pipeline",
            "partition_key": null,
            "partition_date": null,
            "team_name": null
        },
        {
            "dag_run_id": "manual__2026-11-15T18:26:10.683373+00:00",
            "dag_id": "training_pipeline",
            "logical_date": null,
            "queued_at": "2026-11-15T18:26:10.688020Z",
            "start_date": "2026-11-15T18:26:11.280278Z",
            "end_date": "2026-11-15T18:27:33.509708Z",
            "duration": 82.22943,
            "data_interval_start": null,
            "data_interval_end": null,
            "run_after": "2026-11-15T18:26:10.683373Z",
            "last_scheduling_decision": "2026-11-15T18:27:33.508183Z",
            "run_type": "manual",
            "state": "failed",
            "triggered_by": "rest_api",
            "triggering_user_name": "admin",
            "conf": {},
            "note": "injected failure: truncated dataset",
            "dag_versions": [
                {
                    "id": "01a1e045-b954-7967-b175-2a207605cb7b",
                    "version_number": 1,
                    "dag_id": "training_pipeline",
                    "bundle_name": "dags-folder",
                    "bundle_version": null,
                    "created_at": "2026-11-15T17:33:48.244500Z",
                    "dag_display_name": "training_pipeline",
                    "bundle_url": null
                }
            ],
            "bundle_version": null,
            "dag_display_name": "training_pipeline",
```

## Task instances of the latest run
Run: `manual__2026-11-15T18:31:58.177125+00:00`

```
task_id             state       tries  duration_s
validate_dataset    success     1      0.6
preprocess_data     success     1      2.5
train_model         success     1      10.1
evaluate_model      success     1      1.5
quality_gate        success     1      1.1
promote_model       success     1      2.5
```
