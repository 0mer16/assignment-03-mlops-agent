# Technical report

MLOps AI agent over DVC, MLflow, Airflow and Kubernetes.

## 1. System architecture

The full design is in [architecture.md](../architecture.md). In short: a planner decides which
tools to call, a registry validates and bounds every call, a safety policy runs before the call
reaches the service, and a session records what came back. The answer is assembled from those
recorded results, so a claim can always be traced to a tool call that actually happened.

```
question → planner → registry (validate, timeout) → safety policy → tool → ToolResult
                                                                            ↓
                              answer + facts + uncertainties + evidence ← session
```

The deployed stack during this work:

| Component | Where it ran | Version |
|---|---|---|
| Agent, tests, DVC | Windows, project virtualenv | Python 3.13.14 |
| MLflow tracking + registry | WSL2 Debian 13, port 5001 | 3.18.0, SQLite backend |
| Airflow | WSL2 Debian 13, port 8080 | 3.4.0, API v2, LocalExecutor |
| Kubernetes | kind inside WSL2 Docker | kind 0.33.0, Kubernetes 1.34.0 |

Airflow does not run on Windows and Kubernetes needs a container runtime, so both live in WSL2.
Windows forwards localhost into WSL, so the agent reaches all four over `127.0.0.1`.

## 2. Tool definitions and permissions

23 tools across four modules. The full table, including which ones change state and what gates
them, is in [architecture.md](../architecture.md#tool-definitions-and-permissions).

Two tools change state, and both are gated twice:

| Tool | Gate 1 | Gate 2 |
|---|---|---|
| `airflow_trigger_dag` | DAG must be in `APPROVED_DAGS` | `confirm=true`, which only the user supplies |
| `k8s_restart_deployment` | namespace must be in `APPROVED_NAMESPACES` | `confirm=true` |

Both checks run before any client is constructed, so a refused call never touches the live
system. The Kubernetes namespace allowlist applies to reads as well as writes.

## 3. Exported evidence

| System | File |
|---|---|
| DVC | [evidence/dvc.md](evidence/dvc.md) — pointer file, remote, status, pointer history, tags |
| MLflow | [evidence/mlflow.md](evidence/mlflow.md) — experiments, runs with provenance, registry |
| Airflow | [evidence/airflow.md](evidence/airflow.md) — DAGs, runs, task instances with retries |
| Kubernetes | [evidence/kubernetes.md](evidence/kubernetes.md) — nodes, deployment, pods, service, events, probes, logs |

Highlights:

- The dataset is tracked as `9c8da426164432cf5dd9a9333f560bd1`, recorded in commit `1c01f0c`
  and tagged `dataset-v1`.
- Every training run carries `dataset.version`, `dataset.dvc_hash` and `dataset.git_commit` as
  MLflow tags, which is what makes provenance answerable rather than guessable.
- `failure-prediction` version 1 is in the model registry, tagged `promoted_by=training_pipeline`,
  so the promotion is attributable to a pipeline run rather than a person's laptop.
- The deployment runs two replicas behind a ClusterIP service with readiness and liveness
  probes, CPU and memory requests and limits, and configuration from a ConfigMap.

## 4. Tested agent conversations

Five scenarios, captured from real invocations with their evidence tables, are in
[evidence/conversations.md](evidence/conversations.md): dataset provenance, model selection,
pipeline execution, deployment diagnosis, and the mandatory multi-tool question.

The multi-tool scenario is the one worth reading. Asked to find the best model, identify its
dataset, check the deployment and say whether retraining is needed, the agent called six tools
across all four systems and concluded:

> I see no evidence that retraining is needed: no failed quality gate, no changed dataset and
> no model-degradation results were supplied. A freshness policy would be needed to decide on
> age alone.
>
> I never trigger training automatically; triggering always needs your explicit confirmation.

That is the required behaviour: correlate the evidence, recommend, and do not act.

## 5. Failure investigations

### Failure A — deployment that looks healthy but serves the old version

**Injection.** Patch the ConfigMap so `MODEL_PATH` points at a file that does not exist, then
restart the deployment:

```bash
kubectl -n mlops patch configmap inference-api-config \
  --type merge -p '{"data":{"MODEL_PATH":"/app/models/does-not-exist.joblib"}}'
kubectl -n mlops rollout restart deployment/inference-api
```

**Symptom.** No error anywhere obvious. `kubectl get deploy` shows `2/2` ready and the Service
still has two endpoints, because the rolling update keeps the old pods serving while the new one
cannot start.

**What the agent found.**

```
- A rollout is in progress and has not completed: 1 of 2 pod(s) are on the new version and
  1 are unavailable. The previous version is still serving traffic, which is why the replica
  counts still look healthy.
- Pod inference-api-f988bc7d6-jmhnj is phase=Running ready=False restarts=0
- Warning event: Unhealthy - Readiness probe failed: HTTP probe failed with statuscode: 503
- Last log lines: "GET /health HTTP/1.1" 200 OK | "GET /ready HTTP/1.1" 503 Service Unavailable
```

**Root cause.** The container starts and stays alive, so the liveness probe passes. The model
file is missing, so `/ready` returns 503 and the pod is never added to the Service endpoints.
Kubernetes therefore holds the rollout rather than failing it.

**Resolution.** Restore `MODEL_PATH` and restart; the rollout completes. The separation of
liveness from readiness is what makes this diagnosable: had both probes hit `/health`, the pod
would have been marked healthy and silently served nothing.

This failure also improved the agent. The first diagnosis reported "2/2 replicas ready" because
that is what the deployment status says. `k8s_deployed_version` now also compares
`updatedReplicas` against `desired`, which is what exposes a stuck rollout hiding behind healthy
replica counts.

### Failure B — corrupt dataset reaching the training pipeline

**Injection.** Truncate the dataset to 39 rows, well below the pipeline's 500-row minimum, and
trigger the DAG.

**Symptom.** The run fails. All five downstream tasks show `upstream_failed`.

**What the agent found.**

```
- Latest state of training_pipeline is failed.
- The task that actually failed is 'validate_dataset'.
- 5 downstream task(s) never ran because of it: preprocess_data, train_model,
  evaluate_model, quality_gate, promote_model.
- validate_dataset raised: ValueError: dataset has only 39 rows, below the minimum of 500
```

**Root cause.** The validation gate did exactly what it exists for. Training never started, so
no run was logged and nothing reached the model registry.

**Resolution.** Restore the data from the DVC cache:

```bash
dvc checkout data/dataset.csv.dvc --force
```

The restored file hashes to `9c8da426164432cf5dd9a9333f560bd1`, matching the pointer exactly.
That is the reproducibility claim being demonstrated rather than asserted: the recorded version
came back byte for byte.

This failure also improved the agent twice. It originally reported all six tasks as "failed" and
recommended inspecting `preprocess_data`, which had never run. `airflow_failed_tasks` now
separates `root_failures` from `blocked_by_upstream`, and the planner fetches the failing task's
log and reports the exception from Airflow's structured `error_detail` field rather than
scraping text.

### Unplanned failures found while building

Four bugs surfaced during development and are worth recording, because each one would have
produced a wrong answer rather than an obvious crash.

| Symptom | Root cause | Fix |
|---|---|---|
| Every MLflow run stuck in `RUNNING` | MLflow prints an emoji when a run ends; the Windows cp1252 console raised `UnicodeEncodeError` inside `end_run()` | Force UTF-8 on the process streams in `train.py` |
| A crashed run with F1 0.99 would have won "best model" | `best_run` ranked on the metric alone; a run that died after logging still had one | Rank only `FINISHED` runs, and report the excluded ones |
| Airflow login rejected with valid credentials | Airflow 3 answers `/auth/token` with **201 Created**; the code checked for 200 | Accept both |
| Pipeline runs showed `dataset=untracked` | The DAG trains on a preprocessed copy, which DVC does not track, so provenance was looked up against the wrong file | Added `--provenance-from`, so lineage is recorded against the versioned source |

The common thread is that each failure was silent. That is the argument for the `ToolResult`
envelope: a tool that cannot answer has to say so, because the failures that matter in an MLOps
system rarely announce themselves.

## 6. Known limitations and proposed improvements

**Limitations**

- The rule-based planner matches keywords. It is predictable and inspectable, but an unusually
  phrased question falls through to the capability listing. The Gemini planner covers that case,
  and needs an API key.
- `mlflow_best_run` ranks on a single metric. It reports its caveats but knows nothing about
  fairness, latency or calibration.
- Retraining necessity is inferred from changed data, a failed run or a degraded deployment.
  There is no freshness or drift policy, and the agent says so rather than inventing a threshold.
- The tool timeout abandons the worker thread rather than cancelling the underlying request,
  because Python cannot safely interrupt a blocking socket read. The agent returns promptly; the
  orphaned request finishes in the background.
- Single-node kind cluster with SQLite behind both MLflow and Airflow. Appropriate for this
  scale, not a production topology.
- The agent reads Kubernetes through a kubeconfig with full cluster rights. The allowlist is
  enforced in the agent, not by RBAC.

**Proposed improvements, roughly in order of value**

1. Give the agent a dedicated Kubernetes ServiceAccount with a Role scoped to the `mlops`
   namespace and only the verbs it uses. Defence in depth: today the allowlist is the only thing
   between the agent and the rest of the cluster.
2. Add a drift and freshness policy so "does this need retraining?" has a defensible answer
   rather than an honest refusal.
3. Record the image digest alongside the tag in `k8s_deployed_version`. A tag can be overwritten;
   a digest cannot, so the current answer to "what is deployed?" is weaker than it looks.
4. Promote models through registry stages rather than tags alone, so the agent can distinguish a
   candidate from what is actually serving.
5. Cache read-only tool results for a few seconds within one request. The multi-tool scenario
   currently calls Kubernetes twice for overlapping information.
6. Replace SQLite with Postgres for Airflow and MLflow before any concurrent use.
