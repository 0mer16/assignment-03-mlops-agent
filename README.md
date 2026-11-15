# MLOps AI Agent

A conversational agent that answers questions about datasets, experiments, pipelines and
deployments by calling DVC, MLflow, Airflow and Kubernetes, rather than answering from model
knowledge.

The rule it is built around: **every operational claim must come from a tool response.** If a
service cannot be reached, the agent says so instead of filling the gap. That behaviour is
enforced by the code, not just the prompt, and it is covered by tests.

```
                        ┌──────────────────────┐
  "which dataset        │     MLOpsAgent       │
   trained the best ───▶│  planner + registry  │
   model, and is it     │  safety + logging    │
   deployed?"           └──────────┬───────────┘
                                   │ structured tool calls
          ┌────────────┬───────────┼────────────┬─────────────┐
          ▼            ▼           ▼            ▼             ▼
       DVC (5)    MLflow (6)   Airflow (5)  Kubernetes (7)  evidence
     provenance   experiments   workflows    diagnosis       trail
```

See [architecture.md](architecture.md) for the full design, tool definitions and permissions,
and [docs/technical-report.md](docs/technical-report.md) for the evidence, tested conversations
and failure investigations.

## What the agent can do

| Module | Tools |
|---|---|
| DVC | list tracked datasets, dataset version + Git commit, change detection, compare two revisions, reproduction steps |
| MLflow | list experiments, list runs, run details, best run by metric, compare runs, model registry |
| Airflow | list DAGs, run status, trigger an approved DAG, find failed tasks, task logs |
| Kubernetes | list pods, list deployments, pod logs, describe service, unhealthy pods + events, deployed version, restart deployment |

23 tools in total. Two of them change state — triggering a DAG and restarting a deployment —
and both are gated (see [Safety](#safety)).

## Requirements

- Python 3.11 or newer (developed on 3.13)
- Docker, for the container image and the local Kubernetes cluster
- [kind](https://kind.sigs.k8s.io/) and kubectl
- Linux, WSL2 or macOS for Airflow (Airflow does not run natively on Windows)

## Installation

```bash
git clone https://github.com/0mer16/assignment-03-mlops-agent.git
cd assignment-03-mlops-agent

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Configuration

All endpoints, credentials and policy come from the environment. Nothing is hard-coded.

```bash
cp .env.example .env
# then edit .env
```

`.env` is gitignored. No credential is ever committed, and the logger redacts anything whose
key looks like a secret.

| Variable | Purpose |
|---|---|
| `MLFLOW_TRACKING_URI` | MLflow tracking server |
| `AIRFLOW_API_URL`, `AIRFLOW_USERNAME`, `AIRFLOW_PASSWORD` | Airflow API and credentials |
| `KUBECONFIG`, `K8S_NAMESPACE`, `K8S_DEPLOYMENT` | Cluster access and the application being watched |
| `APPROVED_NAMESPACES`, `APPROVED_DAGS` | The safety allowlists |
| `TOOL_TIMEOUT_SECONDS` | Per-tool budget, default 30 |
| `AGENT_PLANNER` | `auto`, `rules` or `gemini` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini planner, optional |

## Launching the services

Each service is independent. The agent reports whichever ones it cannot reach, so you can start
with one and add the rest.

### 1. Dataset (DVC)

```bash
python training/generate_data.py --variant v1
dvc add data/dataset.csv
dvc push
git add data/dataset.csv.dvc .gitignore && git commit -m "data: track dataset v1"
git tag dataset-v1
```

The remote is a local directory configured in `.dvc/config`, so no cloud credentials are needed.

### 2. MLflow

```bash
mlflow server \
  --backend-store-uri sqlite:///mlflow.db \
  --default-artifact-root ./mlartifacts \
  --host 127.0.0.1 --port 5001
```

SQLite rather than a file store, because the model registry needs a database backend.

### 3. Train some runs

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
python training/train.py --n-estimators 60 --max-depth 3 --run-name baseline-depth3
python training/train.py --n-estimators 120 --max-depth 6 --run-name rf-depth6
```

Each run records the dataset's DVC hash and Git commit as tags, which is what makes
"which dataset trained this model?" answerable from provenance rather than guesswork.

### 4. Airflow

```bash
export AIRFLOW_HOME=~/airflow
export AIRFLOW__CORE__DAGS_FOLDER=$PWD/dags
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export MLOPS_REPO=$PWD
export MLOPS_PYTHON=$PWD/.venv/bin/python
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001

airflow db migrate
airflow standalone
```

The generated admin password is written to
`$AIRFLOW_HOME/simple_auth_manager_passwords.json.generated`. Copy it into `.env`.

### 5. Kubernetes

```bash
kind create cluster --name mlops --image kindest/node:v1.34.0

docker build -t mlops-inference-api:1.0.0 .
kind load docker-image mlops-inference-api:1.0.0 --name mlops

kubectl apply -f kubernetes/namespace.yaml
kubectl apply -f kubernetes/configmap.yaml
kubectl apply -f kubernetes/deployment.yaml
kubectl apply -f kubernetes/service.yaml
kubectl -n mlops rollout status deployment/inference-api

kind get kubeconfig --name mlops > .kube/config
```

The deployment runs two replicas behind a ClusterIP service, with readiness and liveness
probes, CPU and memory requests and limits, and configuration from a ConfigMap.

## Talking to the agent

```bash
mlops-agent "Which dataset version was used to train the best model?"
mlops-agent --json "Why is my prediction API unavailable?"
mlops-agent --tools                      # list the tool catalogue
mlops-agent                              # interactive session
```

State-changing operations need confirmation, which the agent never grants itself:

```bash
mlops-agent --confirm "Trigger the model training pipeline"
```

In an interactive session, prefix the question with `confirm:` to do the same.

### Example

```
$ mlops-agent "Compare the last five runs and recommend the best candidate by F1"

Ranked the most recent runs by f1_score using the MLflow tracking store.

Facts:
  - Best run by f1_score is baseline-depth3 (run id 5f798c82...) scoring 0.819, chosen from 4 recent runs.
  - It leads the runner-up by 0.0047 on that metric.
  - That run recorded dataset version dataset-v1 (DVC hash 9c8da426...).

Recommended actions:
  - Treat this as the leading candidate, not an approved release: confirm the dataset
    version, then validate it against the quality gate before promoting.

Evidence:
  - [ok] mlflow_best_run (MlflowClient.search_runs(experiment_ids=['1']) ranked by f1_score)
```

## Safety

| Control | Behaviour |
|---|---|
| Confirmation | `airflow_trigger_dag` and `k8s_restart_deployment` refuse unless `confirm=true`, which only the user supplies |
| Namespace allowlist | Every Kubernetes call, read or write, resolves through `APPROVED_NAMESPACES` first |
| DAG allowlist | Only DAGs in `APPROVED_DAGS` can be triggered |
| Command allowlist | DVC tools accept only read-only `git` and `dvc` subcommands |
| Timeouts | Every tool runs on a worker thread with a budget, so a hung service cannot hang the agent |
| Redaction | Logs replace any value whose key looks like a credential, and bearer tokens inside free text |

The checks run before the call is made, so a refusal never reaches the live system.

## Tests

```bash
pytest -v
ruff check .
```

32 tests, none of which touch a real service: DVC, MLflow, Airflow and Kubernetes are mocked or
deliberately unreachable. That is also what lets CI run the same suite with no cluster and no
credentials.

The suite covers successful tool selection, invalid arguments, timeouts, unavailable services,
the refusal to invent results when a tool is down, and rejection of unauthorised Kubernetes
changes.

## Logs

Every tool call writes one JSON object per line to `logs/agent.jsonl`:

```json
{"timestamp":"2026-11-15T17:35:05.020Z","level":"INFO","event":"tool_call_finished",
 "tool":"mlflow_best_run","request_id":"a1b2c3d4e5f6","status":"ok","duration_ms":84.2}
```

A `request_id` ties one question to every tool call it caused.

## Repository layout

```
agent/          the agent: registry, planners, safety, logging, tools/
api/            FastAPI inference service
dags/           Airflow training pipeline
training/       dataset generation, training, promotion
kubernetes/     namespace, configmap, deployment, service
tests/          pytest suite
docs/           technical report
```
