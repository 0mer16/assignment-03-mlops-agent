"""Planners: deciding which tools to call and turning the results into an answer.

Two implementations share one interface.

``RuleBasedPlanner`` classifies the question and runs a fixed investigation for
each intent. It is deterministic, needs no API key, and is what CI and the test
suite exercise.

``GeminiPlanner`` hands the tool catalogue to Gemini as function declarations and
lets the model choose. The tools, the safety policy and the evidence trail are
identical either way: the planner only decides *which* calls to make, never what
the results were.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from agent.config import Settings, get_settings
from agent.errors import ToolUnavailable
from agent.schemas import AgentAnswer, ToolResult, ToolStatus
from agent.session import ToolSession

MAX_TOOL_STEPS = 8


class Intent(StrEnum):
    """What the user is asking about."""

    DATASET_PROVENANCE = "dataset_provenance"
    MODEL_SELECTION = "model_selection"
    PIPELINE = "pipeline"
    DEPLOYMENT = "deployment"
    FULL_REVIEW = "full_review"
    CAPABILITIES = "capabilities"


# Keyword weights per intent. Deliberately simple and inspectable: the rule
# planner is the fallback, so its behaviour should be obvious from reading it.
_KEYWORDS: dict[Intent, tuple[str, ...]] = {
    Intent.DATASET_PROVENANCE: ("dataset", "data version", "dvc", "provenance", "which data", "training data"),
    Intent.MODEL_SELECTION: (
        "best model", "best run", "f1", "accuracy", "compare", "experiment", "metric", "candidate",
        "registered model", "registry", "model version",
    ),
    Intent.PIPELINE: ("pipeline", "dag", "airflow", "trigger", "training run", "workflow", "retrain"),
    Intent.DEPLOYMENT: ("deploy", "kubernetes", "pod", "cluster", "unavailable", "down", "api", "health", "replica"),
    Intent.FULL_REVIEW: ("retraining necessary", "do we need to retrain", "overall", "full review", "end to end"),
}

_FULL_REVIEW_HINTS = ("best", "dataset")


def classify(question: str) -> Intent:
    """Pick the intent whose keywords best match the question."""
    text = question.lower().strip()
    if not text:
        return Intent.CAPABILITIES

    scores = {
        intent: sum(1 for word in words if word in text)
        for intent, words in _KEYWORDS.items()
    }

    # A question that spans model, data and deployment is the integration case,
    # even when no single phrase says so.
    breadth = sum(1 for intent in (Intent.MODEL_SELECTION, Intent.DATASET_PROVENANCE, Intent.DEPLOYMENT)
                  if scores.get(intent, 0) > 0)
    if scores.get(Intent.FULL_REVIEW, 0) or (breadth >= 2 and all(h in text for h in _FULL_REVIEW_HINTS)):
        return Intent.FULL_REVIEW

    best_intent, best_score = max(scores.items(), key=lambda pair: pair[1])
    return best_intent if best_score else Intent.CAPABILITIES


@dataclass
class PlannerContext:
    """Everything a planner needs for one question."""

    question: str
    session: ToolSession
    request_id: str
    confirm: bool = False
    settings: Settings | None = None

    def get_settings(self) -> Settings:
        return self.settings or get_settings()


# --- helpers shared by both planners ---------------------------------------


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _facts_from_best_run(result: ToolResult) -> list[str]:
    data = result.data
    best = data.get("best_run", {})
    facts = [
        f"Best run by {data.get('metric')} is {best.get('run_name') or best.get('run_id')} "
        f"(run id {best.get('run_id')}) scoring {_fmt(data.get('best_score'))}, "
        f"chosen from {data.get('considered')} recent runs."
    ]
    if data.get("margin_over_runner_up") is not None:
        facts.append(f"It leads the runner-up by {_fmt(data['margin_over_runner_up'])} on that metric.")
    return facts


def _facts_from_provenance(result: ToolResult) -> list[str]:
    data = result.data
    commit = data.get("git_commit") or {}
    outs = data.get("outs") or [{}]
    hash_value = outs[0].get("hash")
    facts = [
        f"Dataset {data.get('tracked_path')} is recorded with DVC hash {hash_value}."
    ]
    if commit.get("short_sha"):
        tags = commit.get("tags") or []
        tag_text = f", tagged {', '.join(tags)}" if tags else ""
        facts.append(
            f"That version was committed as {commit['short_sha']} on {commit.get('committed_at')}{tag_text}."
        )
    return facts


def _facts_from_deployment(result: ToolResult) -> list[str]:
    data = result.data
    facts = [
        f"Deployment {data.get('deployment')} in namespace {data.get('namespace')} runs image tag "
        f"{', '.join(data.get('image_tags') or ['unknown'])} with "
        f"{data.get('ready_replicas')}/{data.get('desired_replicas')} replicas ready."
    ]
    if not data.get("rollout_complete", True):
        facts.append(
            f"A rollout is in progress and has not completed: {data.get('updated_replicas')} of "
            f"{data.get('desired_replicas')} pod(s) are on the new version and "
            f"{data.get('unavailable_replicas')} are unavailable. The previous version is still "
            "serving traffic, which is why the replica counts still look healthy."
        )
    if data.get("configmap_app_version"):
        match = data.get("version_matches_configmap")
        facts.append(
            f"The ConfigMap declares APP_VERSION {data['configmap_app_version']}, which "
            + ("matches the running image." if match else "does NOT match the running image tag.")
        )
    return facts


# --- rule-based planner -----------------------------------------------------


class RuleBasedPlanner:
    """Deterministic investigation per intent. No LLM, no API key."""

    name = "rules"

    def run(self, ctx: PlannerContext) -> AgentAnswer:
        intent = classify(ctx.question)
        handler = {
            Intent.DATASET_PROVENANCE: self._dataset_provenance,
            Intent.MODEL_SELECTION: self._model_selection,
            Intent.PIPELINE: self._pipeline,
            Intent.DEPLOYMENT: self._deployment,
            Intent.FULL_REVIEW: self._full_review,
            Intent.CAPABILITIES: self._capabilities,
        }[intent]

        answer, facts, actions = handler(ctx)
        return AgentAnswer(
            request_id=ctx.request_id,
            question=ctx.question,
            answer=answer,
            facts=facts,
            uncertainties=ctx.session.uncertainties(),
            recommended_actions=actions,
            evidence=ctx.session.evidence(),
            planner=self.name,
        )

    # each handler returns (answer_text, facts, recommended_actions)

    def _capabilities(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        catalogue = ctx.session.registry.describe()
        return (
            "I did not recognise that as a question about datasets, experiments, pipelines or the "
            "deployment, so I have not called any tools. Here is what I can investigate:\n\n" + catalogue,
            [],
            ["Ask about a dataset version, experiment metrics, a DAG run, or the deployment's health."],
        )

    def _dataset_provenance(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        listing = ctx.session.call("dvc_list_tracked_datasets")
        path = "data/dataset.csv"
        if listing.ok and listing.data.get("datasets"):
            path = listing.data["datasets"][0]["tracked_path"]

        version = ctx.session.call("dvc_dataset_version", path=path)
        status = ctx.session.call("dvc_dataset_status")

        facts: list[str] = []
        actions: list[str] = []
        if listing.ok:
            facts.append(f"DVC tracks {listing.data.get('count')} dataset(s) in this repository.")
        if version.ok:
            facts.extend(_facts_from_provenance(version))
        if status.ok:
            if status.data.get("up_to_date"):
                facts.append("The working copy of the tracked data matches what DVC recorded.")
            else:
                changed = status.data.get("changed_entries") or []
                facts.append(f"Tracked data has local changes that DVC has not recorded: {changed}.")
                actions.append("Run 'dvc status' locally and commit or discard the data change before training.")

        if version.ok:
            repro = ctx.session.call("dvc_reproduce_instructions", rev="HEAD", path=path)
            if repro.ok:
                actions.append("To restore this exact version: " + "; ".join(repro.data.get("steps", [])[:2]))

        answer = (
            f"Dataset provenance for {path}, taken from the DVC pointer file and Git history."
            if version.ok
            else "I could not establish dataset provenance from DVC; see the uncertainties below."
        )
        return answer, facts, actions

    def _model_selection(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        if any(word in ctx.question.lower() for word in ("registr", "model version")):
            return self._registry(ctx)

        metric = _metric_from_question(ctx.question)
        best = ctx.session.call("mlflow_best_run", metric=metric, limit=5)

        facts: list[str] = []
        actions: list[str] = []
        if best.ok:
            facts.extend(_facts_from_best_run(best))
            ranking = best.data.get("ranking") or []
            for row in ranking[:5]:
                facts.append(f"  {row.get('run_name') or row.get('run_id')}: {metric}={_fmt(row.get(metric))}")

            provenance = (best.data.get("best_run") or {}).get("dataset_provenance") or {}
            if provenance:
                facts.append(
                    "That run recorded dataset version "
                    f"{provenance.get('dataset.version')} (DVC hash {provenance.get('dataset.dvc_hash')})."
                )
            actions.append(
                "Treat this as the leading candidate, not an approved release: confirm the dataset version, "
                "then validate it against the quality gate before promoting."
            )
            for caveat in best.data.get("caveats", []):
                actions.append(f"Before promoting, note: {caveat}")

        answer = (
            f"Ranked the most recent runs by {metric} using the MLflow tracking store."
            if best.ok
            else f"I could not rank runs by {metric}; see the uncertainties below."
        )
        return answer, facts, actions

    def _registry(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        """What has actually been promoted, as opposed to what scored well."""
        models = ctx.session.call("mlflow_registered_models")
        facts: list[str] = []
        actions: list[str] = []
        if models.ok:
            entries = models.data.get("models", [])
            facts.append(f"The registry holds {len(entries)} registered model(s).")
            for model in entries:
                for version in model.get("versions", []):
                    facts.append(
                        f"{model['name']} version {version['version']} "
                        f"(run {version['run_id']}, status {version['status']})."
                    )
            if not entries:
                actions.append("Nothing has been promoted yet; a successful pipeline run registers a version.")

        answer = (
            "Model registry contents, read from the MLflow tracking server."
            if models.ok
            else "I could not read the model registry; see the uncertainties below."
        )
        return answer, facts, actions

    def _pipeline(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        settings = ctx.get_settings()
        dag_id = settings.approved_dags[0] if settings.approved_dags else "training_pipeline"
        wants_trigger = any(word in ctx.question.lower() for word in ("trigger", "start", "run the", "kick off"))

        listing = ctx.session.call("airflow_list_dags")
        facts: list[str] = []
        actions: list[str] = []
        if listing.ok:
            names = [d["dag_id"] for d in listing.data.get("dags", [])]
            facts.append(f"Airflow currently has {len(names)} DAG(s): {', '.join(names) or 'none'}.")

        if wants_trigger:
            # confirm is never set by the agent itself; it comes from the user.
            triggered = ctx.session.call("airflow_trigger_dag", dag_id=dag_id, confirm=ctx.confirm,
                                         note="requested through the MLOps agent")
            if triggered.ok:
                facts.append(
                    f"Triggered {dag_id}; Airflow created run {triggered.data.get('dag_run_id')} "
                    f"in state {triggered.data.get('state')}."
                )
                actions.append("Ask me again in a few minutes for the final state of that run.")
            elif triggered.status is ToolStatus.CONFIRMATION_REQUIRED:
                actions.append(
                    f"Triggering {dag_id} changes system state. Confirm and I will run it "
                    "(re-ask with confirmation)."
                )

        runs = ctx.session.call("airflow_dag_runs", dag_id=dag_id, limit=5)
        if runs.ok:
            recent = runs.data.get("runs", [])
            facts.append(f"Latest state of {dag_id} is {runs.data.get('latest_state') or 'no runs recorded'}.")
            for run in recent[:5]:
                facts.append(f"  {run.get('dag_run_id')}: {run.get('state')} ({run.get('run_type')})")

            if runs.data.get("latest_state") == "failed":
                failed = ctx.session.call("airflow_failed_tasks", dag_id=dag_id)
                if failed.ok and failed.data.get("failed_tasks"):
                    names = [t["task_id"] for t in failed.data["failed_tasks"]]
                    facts.append(f"Failed task(s) in the latest run: {', '.join(names)}.")
                    actions.append(f"Inspect the log of '{names[0]}' with airflow_task_log for the root cause.")

        answer = (
            f"Workflow status for {dag_id}, read from the Airflow API."
            if listing.ok or runs.ok
            else "I could not reach Airflow, so I have no pipeline status to report."
        )
        return answer, facts, actions

    def _deployment(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        health = ctx.session.call("k8s_unhealthy_pods")
        version = ctx.session.call("k8s_deployed_version")
        service = ctx.session.call("k8s_describe_service")

        facts: list[str] = []
        actions: list[str] = []

        if version.ok:
            facts.extend(_facts_from_deployment(version))
            if not version.data.get("fully_available"):
                actions.append("Not all replicas are ready; the service is degraded rather than healthy.")
            elif not version.data.get("rollout_complete", True):
                actions.append(
                    "The new pods cannot become ready, so the rollout is stuck on the old version. "
                    "Traffic is still served, but the deployed version is not the one you intended."
                )

        if health.ok:
            if health.data.get("all_healthy"):
                facts.append(
                    f"All {health.data.get('total_pods')} pod(s) in {health.data.get('namespace')} are ready "
                    "with no restarts."
                )
            else:
                for pod in health.data.get("unhealthy_pods", []):
                    facts.append(
                        f"Pod {pod['name']} is phase={pod['phase']} ready={pod['ready']} "
                        f"restarts={pod['restarts']} states={pod['container_states']}."
                    )
                for event in (health.data.get("warning_events") or [])[-5:]:
                    facts.append(f"Warning event on {event['object']}: {event['reason']} - {event['message']}")

                # Pull the log of the first unhealthy pod: that is usually where the cause is.
                first_bad = health.data["unhealthy_pods"][0]["name"]
                logs = ctx.session.call("k8s_pod_logs", pod_name=first_bad, tail_lines=30)
                if logs.ok:
                    tail = (logs.data.get("log_tail") or "").strip().splitlines()[-5:]
                    if tail:
                        facts.append(f"Last log lines from {first_bad}: " + " | ".join(tail))

        if service.ok:
            if service.data.get("has_endpoints"):
                facts.append(
                    f"Service {service.data['name']} ({service.data['type']}) has "
                    f"{len(service.data['endpoint_addresses'])} endpoint(s), so traffic can reach a pod."
                )
            else:
                facts.append(
                    f"Service {service.data['name']} has no endpoints: its selector "
                    f"{service.data.get('selector')} currently matches no ready pod."
                )
                actions.append(
                    "No endpoints behind the Service is why requests fail. Check that pod labels match the "
                    "selector and that readiness probes are passing."
                )

        healthy = bool(
            health.ok
            and health.data.get("all_healthy")
            and version.ok
            and version.data.get("fully_available")
        )
        if healthy:
            answer = "The inference deployment looks healthy, based on live pod, deployment and service state."
        elif health.ok or version.ok or service.ok:
            answer = "The deployment is not fully healthy. The evidence below points at the cause."
        else:
            answer = "I could not reach the Kubernetes API, so I cannot say anything about the deployment."
        return answer, facts, actions

    def _full_review(self, ctx: PlannerContext) -> tuple[str, list[str], list[str]]:
        """The mandatory integration scenario, in evidence order."""
        settings = ctx.get_settings()
        metric = _metric_from_question(ctx.question)
        facts: list[str] = []
        actions: list[str] = []
        retrain_reasons: list[str] = []

        # 1. which model is the current best candidate
        best = ctx.session.call("mlflow_best_run", metric=metric, limit=5)
        best_version = None
        if best.ok:
            facts.extend(_facts_from_best_run(best))
            provenance = (best.data.get("best_run") or {}).get("dataset_provenance") or {}
            best_version = provenance.get("dataset.version")
            if provenance:
                facts.append(
                    f"The best run recorded dataset version {best_version} "
                    f"(DVC hash {provenance.get('dataset.dvc_hash')}, commit {provenance.get('dataset.git_commit')})."
                )

        # 2. the dataset behind it
        dataset = ctx.session.call("dvc_dataset_version", path="data/dataset.csv")
        if dataset.ok:
            facts.extend(_facts_from_provenance(dataset))
        status = ctx.session.call("dvc_dataset_status")
        if status.ok and not status.data.get("up_to_date"):
            retrain_reasons.append("tracked data has changed since the recorded version")
            facts.append(f"DVC reports uncommitted data changes: {status.data.get('changed_entries')}.")

        # 3. what is actually deployed
        deployed = ctx.session.call("k8s_deployed_version")
        if deployed.ok:
            facts.extend(_facts_from_deployment(deployed))
            if not deployed.data.get("fully_available"):
                retrain_reasons.append("the deployment is not serving all of its replicas")
        health = ctx.session.call("k8s_unhealthy_pods")
        if health.ok and not health.data.get("all_healthy"):
            facts.append(f"{len(health.data.get('unhealthy_pods', []))} pod(s) are unhealthy in the namespace.")

        # 4. recent training workflow state
        dag_id = settings.approved_dags[0] if settings.approved_dags else "training_pipeline"
        runs = ctx.session.call("airflow_dag_runs", dag_id=dag_id, limit=3)
        if runs.ok:
            facts.append(f"The most recent {dag_id} run is in state {runs.data.get('latest_state')}.")
            if runs.data.get("latest_state") == "failed":
                retrain_reasons.append("the most recent training run failed")

        # 5. correlate, and be explicit that retraining is a recommendation only
        if retrain_reasons:
            actions.append(
                "Retraining looks justified because " + "; ".join(retrain_reasons)
                + ". I have not triggered it: confirm and I will run the approved DAG."
            )
        else:
            actions.append(
                "I see no evidence that retraining is needed: no failed quality gate, no changed dataset and "
                "no model-degradation results were supplied. A freshness policy would be needed to decide on age alone."
            )
        actions.append("I never trigger training automatically; triggering always needs your explicit confirmation.")

        answer = (
            "Correlated the best recent model, the dataset that trained it, the live deployment and the "
            "recent training runs. Findings and their evidence are below."
        )
        return answer, facts, actions


def _metric_from_question(question: str) -> str:
    """Pick the evaluation metric the user asked about, defaulting to F1."""
    text = question.lower()
    for name, metric in (
        ("f1", "f1_score"),
        ("accuracy", "accuracy"),
        ("precision", "precision"),
        ("recall", "recall"),
        ("roc", "roc_auc"),
    ):
        if name in text:
            return metric
    return "f1_score"


# --- Gemini planner ---------------------------------------------------------

SYSTEM_PROMPT = """You are an MLOps operations agent with tools for DVC, MLflow, Airflow and Kubernetes.

Rules you must follow:
1. Never state an operational fact unless a tool returned it in this conversation. If a tool fails or is
   unavailable, say that the information could not be retrieved. Never invent run ids, metrics, hashes,
   pod names or statuses.
2. Call tools to gather evidence before answering. Multi-part questions usually need several tools.
3. Never trigger training or change cluster state on your own initiative. Those tools need confirm=true,
   which only the user can authorise. If one is refused for lack of confirmation, report that and ask.
4. A high metric alone does not make a model production-ready. Mention the caveats a tool returns.
5. Finish with a JSON object and nothing else:
   {"answer": str, "facts": [str], "uncertainties": [str], "recommended_actions": [str]}
   facts must only contain things tools actually returned."""


def _schema_for_gemini(model: Any) -> dict[str, Any]:
    """Convert a pydantic model into the subset of JSON Schema Gemini accepts."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def clean(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        node = {k: v for k, v in node.items() if k not in {"title", "default", "$defs", "additionalProperties"}}
        if "$ref" in node:
            ref = node.pop("$ref").split("/")[-1]
            node.update(clean(defs.get(ref, {})))
        if "anyOf" in node:
            # Optional[X] becomes anyOf[X, null]; Gemini wants a single type.
            options = [o for o in node.pop("anyOf") if o.get("type") != "null"]
            if options:
                node.update(clean(options[0]))
        for key in ("properties", "items"):
            if key in node:
                if key == "properties":
                    node[key] = {k: clean(v) for k, v in node[key].items()}
                else:
                    node[key] = clean(node[key])
        return node

    cleaned = clean(schema)
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned


class GeminiPlanner:
    """Lets Gemini choose the tools, through the same registry and safety policy."""

    name = "gemini"

    def __init__(self, client: Any | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.gemini_model
        self._client = client
        if client is None:
            if not settings.gemini_api_key:
                raise ToolUnavailable("no Gemini API key configured; set GEMINI_API_KEY or use AGENT_PLANNER=rules")
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ToolUnavailable(f"google-genai is not installed: {exc}") from exc
            self._client = genai.Client(api_key=settings.gemini_api_key)

    def run(self, ctx: PlannerContext) -> AgentAnswer:
        from google.genai import types

        declarations = [
            types.FunctionDeclaration(
                name=spec.name,
                description=spec.description,
                parameters=_schema_for_gemini(spec.input_model),
            )
            for spec in ctx.session.registry.specs()
        ]
        config = types.GenerateContentConfig(
            tools=[types.Tool(function_declarations=declarations)],
            system_instruction=SYSTEM_PROMPT,
            temperature=0.0,
        )

        user_text = ctx.question
        if ctx.confirm:
            user_text += "\n\n[The user has explicitly confirmed state-changing operations for this request.]"
        contents: list[Any] = [types.Content(role="user", parts=[types.Part(text=user_text)])]

        final_text = ""
        for _ in range(MAX_TOOL_STEPS):
            response = self._client.models.generate_content(model=self.model, contents=contents, config=config)
            calls = getattr(response, "function_calls", None) or []
            if not calls:
                final_text = (getattr(response, "text", "") or "").strip()
                break

            contents.append(response.candidates[0].content)
            parts = []
            for call in calls:
                result = ctx.session.call_with(call.name, dict(call.args or {}))
                parts.append(
                    types.Part.from_function_response(
                        name=call.name,
                        response={"result": result.model_dump(mode="json")},
                    )
                )
            contents.append(types.Content(role="user", parts=parts))

        return self._to_answer(ctx, final_text)

    def _to_answer(self, ctx: PlannerContext, text: str) -> AgentAnswer:
        """Parse the model's JSON, then re-attach the real evidence and gaps."""
        payload: dict[str, Any] = {}
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = {}

        answer = str(payload.get("answer") or text or "No answer was produced.")
        facts = [str(f) for f in payload.get("facts", []) if str(f).strip()]
        actions = [str(a) for a in payload.get("recommended_actions", []) if str(a).strip()]

        # Whatever the model said, every failed call is reported as a gap. This
        # is the guard that keeps an unreachable service from becoming a guess.
        uncertainties = [str(u) for u in payload.get("uncertainties", []) if str(u).strip()]
        for gap in ctx.session.uncertainties():
            if gap not in uncertainties:
                uncertainties.append(gap)

        if not ctx.session.succeeded and ctx.session.results:
            facts = []
            answer = (
                "I could not retrieve any of the information needed to answer that: every tool call failed. "
                "The failures are listed below."
            )

        return AgentAnswer(
            request_id=ctx.request_id,
            question=ctx.question,
            answer=answer,
            facts=facts,
            uncertainties=uncertainties,
            recommended_actions=actions,
            evidence=ctx.session.evidence(),
            planner=self.name,
        )


def select_planner(settings: Settings | None = None) -> Any:
    """Choose a planner from configuration, falling back to rules without a key."""
    settings = settings or get_settings()
    choice = (settings.planner or "auto").lower()
    if choice == "rules":
        return RuleBasedPlanner()
    if choice == "gemini":
        return GeminiPlanner()
    return GeminiPlanner() if settings.gemini_api_key else RuleBasedPlanner()
