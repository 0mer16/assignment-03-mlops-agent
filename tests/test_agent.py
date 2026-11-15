"""Agent-level behaviour: tool selection, evidence, and refusing to invent results."""

from __future__ import annotations

from types import SimpleNamespace

from agent.orchestrator import MLOpsAgent
from agent.planner import Intent, RuleBasedPlanner, classify
from agent.registry import build_registry
from agent.schemas import ToolStatus
from agent.tools import kubernetes_tools, mlflow_tools


def test_questions_route_to_the_right_investigation():
    """Tool selection starts with reading the question correctly."""
    assert classify("Which dataset version was used for training?") is Intent.DATASET_PROVENANCE
    assert classify("Compare the last five runs by F1 score") is Intent.MODEL_SELECTION
    assert classify("Trigger the training pipeline") is Intent.PIPELINE
    assert classify("Why is my prediction API unavailable?") is Intent.DEPLOYMENT
    assert classify("") is Intent.CAPABILITIES


def test_agent_does_not_invent_results_when_every_tool_is_unavailable(monkeypatch):
    """The core honesty requirement.

    With no MLflow and no cluster, the agent must return zero facts, list the
    failures, and never produce a run id, metric or pod name of its own.
    """
    from agent.errors import ToolUnavailable

    def unavailable(*_args, **_kwargs):
        raise ToolUnavailable("service is not running")

    monkeypatch.setattr(mlflow_tools, "_client", unavailable)
    monkeypatch.setattr(kubernetes_tools, "_api", unavailable)

    agent = MLOpsAgent(registry=build_registry(), planner=RuleBasedPlanner())
    answer = agent.ask("Find the best recent model and check whether it is deployed")

    assert answer.facts == []
    assert answer.uncertainties, "failures must be reported as uncertainties"
    assert any("could not be reached" in u for u in answer.uncertainties)

    rendered = answer.render().lower()
    for invented in ("f1_score=0.", "run id ", "sha256", "replicas ready"):
        assert invented not in rendered

    # every evidence entry records a call that really happened and really failed
    assert answer.evidence
    assert all(item.status is not ToolStatus.OK for item in answer.evidence)


def test_answers_carry_evidence_for_every_claim(monkeypatch):
    """Facts come with the tool call that produced them."""
    client = SimpleNamespace(
        search_experiments=lambda: [SimpleNamespace(experiment_id="1")],
        search_runs=lambda **kwargs: [
            SimpleNamespace(
                info=SimpleNamespace(
                    run_id="abc123", run_name="winner", status="FINISHED", start_time=1, experiment_id="1"
                ),
                data=SimpleNamespace(
                    metrics={"f1_score": 0.88},
                    params={},
                    tags={"dataset.version": "dataset-v1", "dataset.dvc_hash": "deadbeef"},
                ),
            )
        ],
    )
    monkeypatch.setattr(mlflow_tools, "_client", lambda: client)

    agent = MLOpsAgent(registry=build_registry(), planner=RuleBasedPlanner())
    answer = agent.ask("Which run has the best f1 score?")

    assert any("winner" in fact for fact in answer.facts)
    assert any("dataset-v1" in fact for fact in answer.facts)
    tools_used = {item.tool for item in answer.evidence}
    assert "mlflow_best_run" in tools_used


def test_agent_never_triggers_training_without_confirmation(monkeypatch):
    """Asking to retrain must stop at a confirmation request, not start a run."""
    triggered = {"called": False}

    def fake_request(method, path, **kwargs):
        triggered["called"] = True
        raise AssertionError("the agent must not call Airflow before confirmation")

    monkeypatch.setattr("agent.tools.airflow_tools._request", fake_request)

    agent = MLOpsAgent(registry=build_registry(), planner=RuleBasedPlanner())
    answer = agent.ask("Trigger the model training pipeline")

    trigger_results = [e for e in answer.evidence if e.tool == "airflow_trigger_dag"]
    assert trigger_results, "the trigger tool should have been attempted"
    assert trigger_results[0].status is ToolStatus.CONFIRMATION_REQUIRED
    assert any("confirm" in action.lower() for action in answer.recommended_actions)


def test_full_review_recommends_rather_than_retrains(monkeypatch):
    """The mandatory integration scenario must not start training by itself."""
    from agent.errors import ToolUnavailable

    def unavailable(*_args, **_kwargs):
        raise ToolUnavailable("service is not running")

    monkeypatch.setattr(mlflow_tools, "_client", unavailable)
    monkeypatch.setattr(kubernetes_tools, "_api", unavailable)
    monkeypatch.setattr("agent.tools.airflow_tools._request", unavailable)

    agent = MLOpsAgent(registry=build_registry(), planner=RuleBasedPlanner())
    answer = agent.ask(
        "Find the best recent model, identify the dataset version used to train it, "
        "check the deployment status, and explain whether retraining is necessary"
    )

    assert "airflow_trigger_dag" not in {e.tool for e in answer.evidence}
    assert any("never trigger" in a.lower() or "confirmation" in a.lower() for a in answer.recommended_actions)


def test_logging_redacts_secrets_and_records_tool_metadata(caplog):
    """Structured logs carry tool, status, duration and request id, but no secrets."""
    import logging

    from agent.logging_utils import LOGGER_NAME, configure_logging, log_tool_call, redact

    configure_logging(None)
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)

    with log_tool_call("demo_tool", "req-9", {"api_key": "super-secret", "limit": 5}) as state:
        state["status"] = "ok"

    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert records, "tool calls must be logged"
    started, finished = records[0], records[-1]

    assert started.arguments["api_key"] == "***redacted***"
    assert started.arguments["limit"] == 5
    assert finished.tool == "demo_tool"
    assert finished.request_id == "req-9"
    assert finished.status == "ok"
    assert finished.duration_ms >= 0

    assert redact({"Authorization": "Bearer abcdefghijklmnop"})["Authorization"] == "***redacted***"
    assert "***redacted***" in redact("called with Bearer abcdefghijklmnopqrst")
