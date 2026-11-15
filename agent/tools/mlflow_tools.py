"""MLflow experiment-intelligence tools.

These use MlflowClient rather than scraping the MLflow web interface, so results
come from the tracking store itself.

Dataset provenance matters as much as the metrics here: the training job records
the DVC hash and Git commit of the data it used as run tags, which is what lets
the agent answer "which dataset version trained this model?" without guessing.
"""

from __future__ import annotations

from typing import Any

from agent.config import get_settings
from agent.errors import ToolUnavailable
from agent.registry import ToolRegistry
from agent.schemas import (
    BestRunInput,
    CompareRunsInput,
    EmptyInput,
    ListRunsInput,
    RunIdInput,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

MODULE = "mlflow"

# Tags the training job writes so a run can be traced back to its data.
PROVENANCE_TAGS = ("dataset.path", "dataset.dvc_hash", "dataset.git_commit", "dataset.version")


def _client():
    """Build an MlflowClient, turning any connection problem into ToolUnavailable."""
    settings = get_settings()
    try:
        from mlflow.tracking import MlflowClient
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ToolUnavailable(f"mlflow is not installed: {exc}") from exc

    try:
        return MlflowClient(tracking_uri=settings.mlflow_tracking_uri)
    except Exception as exc:  # noqa: BLE001 - any client construction failure means no evidence
        raise ToolUnavailable(
            f"cannot reach the MLflow tracking server at {settings.mlflow_tracking_uri}: {exc}"
        ) from exc


def _call(fn, *args, **kwargs):
    """Run an MlflowClient call and report an unreachable server honestly."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - mlflow raises many connection error types
        raise ToolUnavailable(
            f"MLflow request failed against {get_settings().mlflow_tracking_uri}: {type(exc).__name__}: {exc}"
        ) from exc


def _experiment_ids(client, experiment_name: str | None) -> list[str]:
    if experiment_name:
        experiment = _call(client.get_experiment_by_name, experiment_name)
        if experiment is None:
            return []
        return [experiment.experiment_id]
    return [e.experiment_id for e in _call(client.search_experiments)]


def _run_summary(run) -> dict[str, Any]:
    return {
        "run_id": run.info.run_id,
        "run_name": run.info.run_name,
        "status": run.info.status,
        "start_time": run.info.start_time,
        "experiment_id": run.info.experiment_id,
        "metrics": dict(run.data.metrics),
        "params": dict(run.data.params),
        "dataset_provenance": {tag: run.data.tags.get(tag) for tag in PROVENANCE_TAGS if run.data.tags.get(tag)},
    }


def list_experiments(_: EmptyInput) -> ToolResult:
    """List experiments on the tracking server."""
    client = _client()
    experiments = _call(client.search_experiments)
    return ToolResult(
        tool="mlflow_list_experiments",
        status=ToolStatus.OK,
        data={
            "experiments": [
                {
                    "experiment_id": e.experiment_id,
                    "name": e.name,
                    "lifecycle_stage": e.lifecycle_stage,
                    "artifact_location": e.artifact_location,
                }
                for e in experiments
            ],
            "count": len(experiments),
        },
        source=f"MlflowClient.search_experiments @ {get_settings().mlflow_tracking_uri}",
    )


def list_runs(payload: ListRunsInput) -> ToolResult:
    """List the most recent training runs, newest first."""
    client = _client()
    experiment_ids = _experiment_ids(client, payload.experiment_name)
    if not experiment_ids:
        return ToolResult(
            tool="mlflow_list_runs",
            status=ToolStatus.ERROR,
            error=f"experiment {payload.experiment_name!r} does not exist on the tracking server",
            source=get_settings().mlflow_tracking_uri,
        )

    runs = _call(
        client.search_runs,
        experiment_ids=experiment_ids,
        order_by=["attributes.start_time DESC"],
        max_results=payload.limit,
    )
    return ToolResult(
        tool="mlflow_list_runs",
        status=ToolStatus.OK,
        data={"runs": [_run_summary(r) for r in runs], "count": len(runs)},
        source=f"MlflowClient.search_runs(experiment_ids={experiment_ids})",
    )


def run_details(payload: RunIdInput) -> ToolResult:
    """Full metrics, parameters, tags and artifacts for one run."""
    client = _client()
    run = _call(client.get_run, payload.run_id)
    artifacts = [
        {"path": a.path, "is_dir": a.is_dir, "size_bytes": a.file_size}
        for a in _call(client.list_artifacts, payload.run_id)
    ]
    return ToolResult(
        tool="mlflow_run_details",
        status=ToolStatus.OK,
        data={**_run_summary(run), "tags": dict(run.data.tags), "artifacts": artifacts},
        source=f"MlflowClient.get_run({payload.run_id})",
    )


def best_run(payload: BestRunInput) -> ToolResult:
    """Rank recent runs by one metric and return the leader with its runners-up.

    The leader is the best *recorded* score. Whether it should be promoted is a
    separate decision, so the caveats travel with the answer.
    """
    client = _client()
    experiment_ids = _experiment_ids(client, payload.experiment_name)
    if not experiment_ids:
        return ToolResult(
            tool="mlflow_best_run",
            status=ToolStatus.ERROR,
            error=f"experiment {payload.experiment_name!r} does not exist on the tracking server",
            source=get_settings().mlflow_tracking_uri,
        )

    runs = _call(
        client.search_runs,
        experiment_ids=experiment_ids,
        order_by=["attributes.start_time DESC"],
        max_results=payload.limit,
    )
    # A run that crashed can still have logged a metric before it died. Ranking
    # those alongside completed runs would let a failed run win, so they are
    # excluded and counted rather than silently dropped.
    with_metric = [r for r in runs if payload.metric in r.data.metrics]
    excluded = [r for r in with_metric if r.info.status != "FINISHED"]
    scored = [(r.data.metrics[payload.metric], r) for r in with_metric if r.info.status == "FINISHED"]

    if not scored:
        detail = f" ({len(excluded)} run(s) recorded it but did not finish)" if excluded else ""
        return ToolResult(
            tool="mlflow_best_run",
            status=ToolStatus.ERROR,
            error=(
                f"none of the {len(runs)} most recent runs completed with a metric named "
                f"{payload.metric!r}{detail}"
            ),
            source=f"MlflowClient.search_runs(experiment_ids={experiment_ids})",
        )

    scored.sort(key=lambda pair: pair[0], reverse=payload.larger_is_better)
    winner_score, winner = scored[0]

    ranking = [
        {"run_id": r.info.run_id, "run_name": r.info.run_name, payload.metric: score, "status": r.info.status}
        for score, r in scored
    ]
    margin = None
    if len(scored) > 1:
        margin = round(abs(winner_score - scored[1][0]), 6)

    return ToolResult(
        tool="mlflow_best_run",
        status=ToolStatus.OK,
        data={
            "metric": payload.metric,
            "best_run": _run_summary(winner),
            "best_score": winner_score,
            "margin_over_runner_up": margin,
            "considered": len(scored),
            "excluded_unfinished_runs": [
                {"run_id": r.info.run_id, "run_name": r.info.run_name, "status": r.info.status} for r in excluded
            ],
            "ranking": ranking,
            "caveats": [
                "ranked only on the recorded metric; a single metric is not a promotion decision",
                "runs may have used different dataset versions, so check provenance before comparing",
                "no production traffic or latency evidence is included in this ranking",
            ],
        },
        source=f"MlflowClient.search_runs(experiment_ids={experiment_ids}) ranked by {payload.metric}",
    )


def compare_runs(payload: CompareRunsInput) -> ToolResult:
    """Put several runs side by side, including which dataset each used."""
    client = _client()
    summaries = [_run_summary(_call(client.get_run, run_id)) for run_id in payload.run_ids]

    metric_names = sorted({name for s in summaries for name in s["metrics"]})
    table = {
        name: {s["run_id"]: s["metrics"].get(name) for s in summaries}
        for name in metric_names
    }
    datasets = {s["run_id"]: s["dataset_provenance"].get("dataset.version") for s in summaries}
    return ToolResult(
        tool="mlflow_compare_runs",
        status=ToolStatus.OK,
        data={
            "runs": summaries,
            "metric_comparison": table,
            "dataset_versions": datasets,
            "same_dataset": len(set(datasets.values())) == 1,
        },
        source=f"MlflowClient.get_run x{len(payload.run_ids)}",
    )


def registered_models(_: EmptyInput) -> ToolResult:
    """Read the model registry, when the tracking backend provides one."""
    client = _client()
    try:
        models = _call(client.search_registered_models)
    except ToolUnavailable:
        # A file-backed tracking store has no registry; that is a fact, not a failure.
        return ToolResult(
            tool="mlflow_registered_models",
            status=ToolStatus.UNAVAILABLE,
            error="the configured MLflow backend does not expose a model registry",
            source=get_settings().mlflow_tracking_uri,
        )

    entries = []
    for model in models:
        entries.append(
            {
                "name": model.name,
                "versions": [
                    {"version": v.version, "stage": v.current_stage, "run_id": v.run_id, "status": v.status}
                    for v in (model.latest_versions or [])
                ],
            }
        )
    return ToolResult(
        tool="mlflow_registered_models",
        status=ToolStatus.OK,
        data={"models": entries, "count": len(entries)},
        source="MlflowClient.search_registered_models",
    )


def register(registry: ToolRegistry) -> None:
    """Add the MLflow tools to the registry."""
    registry.register(
        ToolSpec(
            name="mlflow_list_experiments",
            description="List MLflow experiments on the tracking server.",
            input_model=EmptyInput,
            module=MODULE,
        ),
        list_experiments,
    )
    registry.register(
        ToolSpec(
            name="mlflow_list_runs",
            description="List the most recent MLflow training runs with their metrics and dataset provenance.",
            input_model=ListRunsInput,
            module=MODULE,
        ),
        list_runs,
    )
    registry.register(
        ToolSpec(
            name="mlflow_run_details",
            description="Get the metrics, parameters, tags and artifacts recorded for one MLflow run.",
            input_model=RunIdInput,
            module=MODULE,
        ),
        run_details,
    )
    registry.register(
        ToolSpec(
            name="mlflow_best_run",
            description=(
                "Rank recent runs by an evaluation metric such as f1_score and return the best candidate "
                "with its ranking and the caveats on that choice."
            ),
            input_model=BestRunInput,
            module=MODULE,
        ),
        best_run,
    )
    registry.register(
        ToolSpec(
            name="mlflow_compare_runs",
            description="Compare two or more MLflow runs metric by metric, including the dataset version each used.",
            input_model=CompareRunsInput,
            module=MODULE,
        ),
        compare_runs,
    )
    registry.register(
        ToolSpec(
            name="mlflow_registered_models",
            description="List registered models and their versions from the MLflow model registry.",
            input_model=EmptyInput,
            module=MODULE,
        ),
        registered_models,
    )
