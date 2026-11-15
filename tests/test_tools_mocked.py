"""Tool behaviour against mocked backends, including unreachable ones."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.errors import PermissionDenied, ToolUnavailable
from agent.registry import build_registry
from agent.schemas import BestRunInput, NamespaceInput, ToolStatus
from agent.tools import dvc_tools, kubernetes_tools, mlflow_tools


def _fake_run(run_id: str, name: str, f1: float, dataset_version: str = "dataset-v1", status: str = "FINISHED"):
    """Minimal stand-in for an mlflow Run object."""
    return SimpleNamespace(
        info=SimpleNamespace(
            run_id=run_id, run_name=name, status=status, start_time=1, experiment_id="1"
        ),
        data=SimpleNamespace(
            metrics={"f1_score": f1, "accuracy": f1 - 0.01},
            params={"n_estimators": "100"},
            tags={"dataset.version": dataset_version, "dataset.dvc_hash": "abc123"},
        ),
    )


def test_best_run_ranks_by_metric_and_reports_caveats(monkeypatch):
    """The best run is chosen from recorded metrics, with its selection caveats."""
    client = SimpleNamespace(
        search_experiments=lambda: [SimpleNamespace(experiment_id="1")],
        search_runs=lambda **kwargs: [
            _fake_run("r1", "low", 0.70),
            _fake_run("r2", "best", 0.91),
            _fake_run("r3", "mid", 0.85),
        ],
    )
    monkeypatch.setattr(mlflow_tools, "_client", lambda: client)

    result = mlflow_tools.best_run(BestRunInput(metric="f1_score", limit=5))

    assert result.status is ToolStatus.OK
    assert result.data["best_run"]["run_id"] == "r2"
    assert result.data["best_score"] == 0.91
    assert result.data["margin_over_runner_up"] == pytest.approx(0.06)
    # A metric win is explicitly not a promotion decision.
    assert any("not a promotion decision" in c for c in result.data["caveats"])


def test_best_run_reports_missing_metric_rather_than_guessing(monkeypatch):
    """Asking for a metric nothing recorded is an error, not an invented number."""
    client = SimpleNamespace(
        search_experiments=lambda: [SimpleNamespace(experiment_id="1")],
        search_runs=lambda **kwargs: [_fake_run("r1", "only", 0.7)],
    )
    monkeypatch.setattr(mlflow_tools, "_client", lambda: client)

    result = mlflow_tools.best_run(BestRunInput(metric="roc_auc", limit=5))

    assert result.status is ToolStatus.ERROR
    assert "roc_auc" in result.error
    assert result.data == {}


def test_unreachable_mlflow_is_reported_as_unavailable(monkeypatch):
    """A connection failure becomes 'unavailable', which the agent must surface."""
    def explode():
        raise ToolUnavailable("cannot reach the MLflow tracking server at http://127.0.0.1:1")

    monkeypatch.setattr(mlflow_tools, "_client", explode)
    registry = build_registry()

    result = registry.execute("mlflow_list_experiments", {}, request_id="t-1")

    assert result.status is ToolStatus.UNAVAILABLE
    assert "cannot reach" in result.error


def test_unreachable_kubernetes_is_reported_as_unavailable(monkeypatch):
    """No kubeconfig means no evidence, and the tool says exactly that."""
    def explode():
        raise ToolUnavailable("no usable kubeconfig")

    monkeypatch.setattr(kubernetes_tools, "_api", explode)

    registry = build_registry()
    result = registry.execute("k8s_list_pods", {"namespace": "mlops"}, request_id="t-2")

    assert result.status is ToolStatus.UNAVAILABLE
    assert "kubeconfig" in result.error


def test_kubernetes_read_is_namespace_scoped(monkeypatch):
    """Reads are restricted to the allowlist too, not just writes."""
    monkeypatch.setattr(kubernetes_tools, "_api", lambda: (_ for _ in ()).throw(AssertionError("must not connect")))

    with pytest.raises(PermissionDenied):
        kubernetes_tools.list_pods(NamespaceInput(namespace="default"))


def test_dvc_refuses_commands_outside_the_read_only_allowlist():
    """The agent can inspect DVC state but cannot push, remove or garbage-collect."""
    with pytest.raises(PermissionDenied) as exc:
        dvc_tools._run("dvc", ["push"])
    assert "allowlist" in str(exc.value)

    with pytest.raises(PermissionDenied):
        dvc_tools._run("git", ["push"])


def test_dvc_reports_untracked_dataset_clearly(tmp_path, isolated_settings):
    """A path DVC does not track produces an explicit error, not a made-up hash."""
    isolated_settings.repo_root = tmp_path
    registry = build_registry()

    result = registry.execute("dvc_dataset_version", {"path": "data/missing.csv"}, request_id="t-3")

    assert result.status is ToolStatus.ERROR
    assert "not tracked by DVC" in result.error


def test_failed_runs_are_excluded_from_the_ranking(monkeypatch):
    """A crashed run can still have logged a metric; it must not be able to win."""
    client = SimpleNamespace(
        search_experiments=lambda: [SimpleNamespace(experiment_id="1")],
        search_runs=lambda **kwargs: [
            _fake_run("good", "finished-run", 0.80),
            _fake_run("bad", "crashed-run", 0.99, status="FAILED"),
        ],
    )
    monkeypatch.setattr(mlflow_tools, "_client", lambda: client)

    result = mlflow_tools.best_run(BestRunInput(metric="f1_score", limit=5))

    assert result.status is ToolStatus.OK
    assert result.data["best_run"]["run_id"] == "good"
    assert result.data["considered"] == 1
    excluded = result.data["excluded_unfinished_runs"]
    assert [r["run_id"] for r in excluded] == ["bad"]


def test_failed_tasks_separates_root_cause_from_blocked_tasks(monkeypatch):
    """upstream_failed tasks did not fail, they never ran.

    Reporting them as failures sends an operator to a log that only says the
    task was skipped, so the two are kept apart.
    """
    from agent.schemas import DagRunRefInput
    from agent.tools import airflow_tools

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "task_instances": [
                    {"task_id": "validate_dataset", "state": "failed", "try_number": 3, "start_date": "1"},
                    {"task_id": "preprocess_data", "state": "upstream_failed", "try_number": 1, "start_date": "2"},
                    {"task_id": "train_model", "state": "upstream_failed", "try_number": 1, "start_date": "3"},
                ]
            }

    monkeypatch.setattr(airflow_tools, "_request", lambda *a, **k: _Response())

    result = airflow_tools.failed_tasks(DagRunRefInput(dag_id="training_pipeline", dag_run_id="run-1"))

    assert result.status is ToolStatus.OK
    assert result.data["root_cause_task"] == "validate_dataset"
    assert [t["task_id"] for t in result.data["blocked_by_upstream"]] == ["preprocess_data", "train_model"]
    assert len(result.data["root_failures"]) == 1


def test_task_log_extracts_the_recorded_exception(monkeypatch):
    """Airflow 3 records the exception in error_detail; use it, don't scrape text."""
    from agent.schemas import TaskLogInput
    from agent.tools import airflow_tools

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "content": [
                    {"event": "validated 39 rows"},
                    {
                        "event": "Task failed with exception",
                        "error_detail": [
                            {
                                "exc_type": "ValueError",
                                "exc_value": "dataset has only 39 rows, below the minimum of 500",
                            }
                        ],
                    },
                ]
            }

    monkeypatch.setattr(airflow_tools, "_request", lambda *a, **k: _Response())

    result = airflow_tools.task_log(
        TaskLogInput(dag_id="training_pipeline", dag_run_id="run-1", task_id="validate_dataset", try_number=3)
    )

    assert result.status is ToolStatus.OK
    assert result.data["exceptions"] == ["ValueError: dataset has only 39 rows, below the minimum of 500"]
