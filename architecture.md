# Architecture

## The problem this solves

An ML engineer answering "is our best model actually deployed, and does it need retraining?"
has to visit four systems: MLflow for metrics, DVC for the dataset behind a run, Airflow for
whether training succeeded, and Kubernetes for what is actually serving. Each answer is cheap;
the cost is in the switching and in correlating what comes back.

The agent collapses that into one conversation. What it must not do is make the answer *sound*
complete when a system was unreachable, because an operator who cannot tell a real status from
a plausible guess is worse off than one who had to check manually.

## Layers

```
   user question
        │
        ▼
┌─────────────────┐   classifies the question, decides which tools to call
│     Planner     │   RuleBasedPlanner (deterministic) | GeminiPlanner (LLM function calling)
└────────┬────────┘
         │ tool name + arguments
         ▼
┌─────────────────┐   validates arguments against a pydantic schema
│  ToolRegistry   │   applies the timeout, catches every failure mode
└────────┬────────┘   turns exceptions into typed statuses
         │
         ▼
┌─────────────────┐   namespace allowlist, DAG allowlist, confirmation
│  Safety policy  │   checked *before* the call reaches the service
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│  dvc_tools  mlflow_tools  airflow_tools  k8s     │
└────────┬────────────────────────────────────────┘
         │ ToolResult(status, data, source, duration)
         ▼
┌─────────────────┐   records every call, successful or not
│   ToolSession   │   builds the evidence list and the uncertainties
└────────┬────────┘
         ▼
     AgentAnswer: answer, facts, uncertainties, recommended_actions, evidence
```

## Why the result envelope is the important part

Every tool returns a `ToolResult` carrying a status, not just data:

| Status | Meaning |
|---|---|
| `ok` | the service answered |
| `unavailable` | unreachable, unauthenticated or not configured — **no evidence at all** |
| `error` | the service answered, and the answer was an error |
| `timeout` | exceeded the per-tool budget |
| `denied` | refused by the safety policy before the call |
| `confirmation_required` | state-changing, and the user has not confirmed |

`unavailable` is deliberately distinct from `error`. It is the status that means the agent knows
nothing, and `ToolSession.uncertainties()` turns each one into a sentence in the answer. The
agent cannot quietly drop a failed call, because the evidence list is assembled from the
recorded results rather than from anything the planner claims.

The `GeminiPlanner` gets the same treatment: whatever the model writes, every failed call is
appended to the uncertainties, and if *no* call succeeded the facts are discarded and the answer
is replaced with an explicit "I could not retrieve any of this". A persuasive model cannot talk
its way past an unreachable service.

## Planners

Both implement one method, `run(ctx) -> AgentAnswer`, and share the registry, the safety policy
and the evidence trail. They differ only in choosing which tools to call.

**RuleBasedPlanner** classifies the question into an intent and runs a fixed investigation. It
is deterministic, needs no API key, and is what the tests and CI exercise. It is also the
fallback: if the LLM planner raises, the orchestrator re-runs the question through it rather
than losing the request.

**GeminiPlanner** converts each tool's pydantic input model into a Gemini function declaration
and lets the model choose, looping until it stops requesting calls (bounded at 8 steps).

The split matters because an LLM planner that cannot be run offline makes the whole system
untestable, and an agent whose safety depends on prompt wording is not safe. Here the planner
chooses *which* calls to make; it never decides what the results were, and it cannot widen its
own permissions.

## Tool definitions and permissions

| Tool | Reads | Changes state | Gate |
|---|---|---|---|
| `dvc_list_tracked_datasets` | `.dvc` pointers, `dvc.yaml` | no | read-only command allowlist |
| `dvc_dataset_version` | pointer + `git log` | no | read-only command allowlist |
| `dvc_dataset_status` | `dvc status` | no | read-only command allowlist |
| `dvc_compare_versions` | `dvc diff` | no | read-only command allowlist |
| `dvc_reproduce_instructions` | `git show` | no | read-only command allowlist |
| `mlflow_list_experiments` | tracking store | no | — |
| `mlflow_list_runs` | tracking store | no | — |
| `mlflow_run_details` | tracking store | no | — |
| `mlflow_best_run` | tracking store | no | excludes unfinished runs |
| `mlflow_compare_runs` | tracking store | no | — |
| `mlflow_registered_models` | model registry | no | — |
| `airflow_list_dags` | Airflow API | no | — |
| `airflow_dag_runs` | Airflow API | no | — |
| `airflow_trigger_dag` | — | **yes** | approved DAG list **and** `confirm=true` |
| `airflow_failed_tasks` | Airflow API | no | — |
| `airflow_task_log` | Airflow API | no | — |
| `k8s_list_pods` | cluster | no | approved namespace |
| `k8s_list_deployments` | cluster | no | approved namespace |
| `k8s_pod_logs` | cluster | no | approved namespace |
| `k8s_describe_service` | cluster | no | approved namespace |
| `k8s_unhealthy_pods` | cluster | no | approved namespace |
| `k8s_deployed_version` | cluster | no | approved namespace |
| `k8s_restart_deployment` | — | **yes** | approved namespace **and** `confirm=true` |

The namespace allowlist applies to reads as well as writes. Diagnosis only ever needs the
application namespace, and a single rule is easier to reason about than two.

## Deliberate design decisions

**Shelling out for DVC, a client library for the others.** DVC has no stable Python API for
provenance lookups, so those tools run `dvc` and `git` as subprocesses behind an allowlist of
read-only subcommands. MLflow and Kubernetes have proper clients, and Airflow has a documented
REST API, so those are used directly rather than scraping any web interface.

**The dataset version is a pair, not a string.** A DVC content hash says what the bytes were; a
Git commit says when it was recorded and by whom. The training job writes both as MLflow run
tags, so a run can be traced to its data even after the working tree has moved on.

**Training runs in its own environment.** The DAG invokes a separate virtual environment as a
subprocess instead of importing scikit-learn into Airflow. Airflow pins a large dependency set
and so does MLflow; keeping them apart avoids a resolution conflict that would otherwise appear
at the worst moment.

**Readiness and liveness check different things.** Liveness asks whether the process is alive.
Readiness also requires the model to be loaded. A pod that cannot serve is removed from the
Service endpoints rather than restarted forever, and "Service has no endpoints" is a far more
useful symptom than a crash loop.

**Two gates in the pipeline.** Validation blocks a malformed dataset before training spends any
time on it; the quality gate blocks a model that trained fine but scored below threshold.
Because `promote_model` sits downstream of both, a failure at either point leaves the registry
untouched.

## Known limitations

- The rule-based planner matches keywords. It is predictable and inspectable, but a question
  phrased unusually falls through to the capability listing rather than being understood. The
  Gemini planner exists for exactly that case.
- `mlflow_best_run` ranks on one metric. It reports its caveats, but it does not know about
  fairness, latency or calibration.
- Retraining necessity is judged from changed data, a failed run or a degraded deployment. A
  real freshness policy (model age, drift metrics) is not implemented, and the agent says so
  rather than inventing a threshold.
- The timeout abandons the worker thread rather than killing the underlying request, because
  Python cannot safely interrupt a blocking socket read. The agent returns promptly; the
  orphaned request finishes in the background.
- Single-node kind cluster, SQLite behind both MLflow and Airflow. Fine for this scale, not a
  production topology.
