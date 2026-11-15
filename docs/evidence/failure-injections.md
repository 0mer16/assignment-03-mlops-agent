# Injected failure diagnoses

Raw agent output captured while each failure was active. The analysis is in
[../technical-report.md](../technical-report.md#5-failure-investigations).

## Failure A - ConfigMap pointing at a missing model file

```
The deployment is not fully healthy. The evidence below points at the cause.

Facts:
  - Deployment inference-api in namespace mlops runs image tag 1.0.0 with 2/2 replicas ready.
  - A rollout is in progress and has not completed: 1 of 2 pod(s) are on the new version and 1 are unavailable. The previous version is still serving traffic, which is why the replica counts still look healthy.
  - The ConfigMap declares APP_VERSION 1.0.0, which matches the running image.
  - Pod inference-api-f988bc7d6-jmhnj is phase=Running ready=False restarts=0 states={'api': 'running'}.
  - Warning event on Pod/inference-api-6f787b46bc-cmfj4: Failed - Error: container has runAsNonRoot and image has non-numeric user (appuser), cannot verify user is non-root (pod: "inference-api-6f787b46bc-cmfj4_mlops(d71890e8-e732-49da-a7ee-1828f785fb4d)", container: api)
  - Warning event on Pod/inference-api-6f787b46bc-lgkx9: Failed - Error: container has runAsNonRoot and image has non-numeric user (appuser), cannot verify user is non-root (pod: "inference-api-6f787b46bc-lgkx9_mlops(889a74ea-eb15-4e77-b012-19ef013e8220)", container: api)
  - Warning event on Pod/inference-api-f988bc7d6-jmhnj: Unhealthy - Readiness probe failed: HTTP probe failed with statuscode: 503
  - Last log lines from inference-api-f988bc7d6-jmhnj: INFO:     10.244.0.1:37886 - "GET /health HTTP/1.1" 200 OK | INFO:     10.244.0.1:37890 - "GET /ready HTTP/1.1" 503 Service Unavailable | INFO:     10.244.0.1:43136 - "GET /ready HTTP/1.1" 503 Service Unavailable | INFO:     10.244.0.1:55396 - "GET /health HTTP/1.1" 200 OK | INFO:     10.244.0.1:55406 - "GET /ready HTTP/1.1" 503 Service Unavailable
  - Service inference-api (ClusterIP) has 2 endpoint(s), so traffic can reach a pod.

Recommended actions:
  - The new pods cannot become ready, so the rollout is stuck on the old version. Traffic is still served, but the deployed version is not the one you intended.

Evidence:
  - [ok] k8s_unhealthy_pods (CoreV1Api.list_namespaced_pod + list_namespaced_event(namespace=mlops))
  - [ok] k8s_deployed_version (AppsV1Api.read_namespaced_deployment(inference-api) + ConfigMap inference-api-config)
  - [ok] k8s_describe_service (CoreV1Api.read_namespaced_service(inference-api))
  - [ok] k8s_pod_logs (CoreV1Api.read_namespaced_pod_log(inference-api-f988bc7d6-jmhnj, tail_lines=30))
```

## Failure B - dataset truncated below the validation minimum

```
Workflow status for training_pipeline, read from the Airflow API.

Facts:
  - Airflow currently has 1 DAG(s): training_pipeline.
  - Latest state of training_pipeline is failed.
  -   manual__2026-11-15T18:26:10.683373+00:00: failed (manual)
  -   manual__2026-11-15T18:22:04.438442+00:00: success (manual)
  -   manual__2026-11-15T17:35:05.020950+00:00: success (manual)
  - The task that actually failed is 'validate_dataset'.
  - 5 downstream task(s) never ran because of it: preprocess_data, train_model, evaluate_model, quality_gate, promote_model.
  - validate_dataset raised: ValueError: dataset has only 39 rows, below the minimum of 500

Recommended actions:
  - Fix the cause reported by 'validate_dataset' and re-run; nothing downstream of it was promoted.

Evidence:
  - [ok] airflow_list_dags (GET http://127.0.0.1:8080/api/v2/dags)
  - [ok] airflow_dag_runs (GET http://127.0.0.1:8080/api/v2/dags/training_pipeline/dagRuns)
  - [ok] airflow_failed_tasks (GET http://127.0.0.1:8080/api/v2/dags/training_pipeline/dagRuns/manual__2026-11-15T18:26:10.683373+00:00/taskInstances)
  - [ok] airflow_task_log (GET http://127.0.0.1:8080/api/v2/dags/training_pipeline/dagRuns/manual__2026-11-15T18:26:10.683373+00:00/taskInstances/validate_dataset/logs/3)
```
