"""Train the model and record the run in MLflow.

The important part for this assignment is not the estimator, it is that each run
carries the identity of the data that produced it. The DVC content hash, the Git
commit of the pointer file and the human-readable dataset version are written as
run tags, which is what lets the agent answer "which dataset version trained
this model?" from recorded provenance instead of guessing.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split

# MLflow writes a "View run" line containing an emoji to stdout when a run ends.
# On a Windows console that defaults to cp1252 this raises UnicodeEncodeError
# from inside end_run(), which leaves the run stuck in RUNNING on the server.
# Forcing UTF-8 on our own streams keeps run status correct on every platform.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO_ROOT / "data" / "dataset.csv"
METRICS_PATH = REPO_ROOT / "metrics.json"
MODEL_DIR = REPO_ROOT / "models"


def _git(args: list[str]) -> str:
    """Run a read-only git command, returning empty string if it is not possible."""
    try:
        done = subprocess.run(  # noqa: S603 - fixed executable, fixed read-only arguments
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _repo_relative(path: Path) -> str:
    """Path as the repository sees it, with forward slashes.

    Callers pass anything from an absolute path to 'data/dataset.csv', and dvc
    repro uses the relative form, so resolve before comparing.
    """
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        # Outside the repository, for example the DAG's processed copy.
        return str(resolved).replace("\\", "/")


def dataset_provenance(data_path: Path) -> dict[str, str]:
    """Collect the identity of the dataset being trained on."""
    data_path = Path(data_path).resolve()
    provenance: dict[str, str] = {"dataset.path": _repo_relative(data_path)}

    pointer = Path(f"{data_path}.dvc")
    if pointer.exists():
        content = yaml.safe_load(pointer.read_text(encoding="utf-8")) or {}
        outs = content.get("outs") or [{}]
        if outs[0].get("md5"):
            provenance["dataset.dvc_hash"] = outs[0]["md5"]
        rel_pointer = _repo_relative(pointer)
        commit = _git(["log", "-1", "--format=%H", "--", rel_pointer])
        if commit:
            provenance["dataset.git_commit"] = commit
            tag = _git(["tag", "--points-at", commit])
            provenance["dataset.version"] = tag.split()[0] if tag else commit[:7]

    provenance.setdefault("dataset.version", "untracked")
    return provenance


def evaluate(model, x_test, y_test) -> dict[str, float]:
    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)[:, 1]
    return {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "precision": float(precision_score(y_test, predictions, zero_division=0)),
        "recall": float(recall_score(y_test, predictions, zero_division=0)),
        "f1_score": float(f1_score(y_test, predictions, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_test, probabilities)),
    }


def train(
    data_path: Path,
    experiment: str,
    n_estimators: int,
    max_depth: int | None,
    test_size: float,
    random_state: int,
    tracking_uri: str | None,
    run_name: str | None = None,
) -> dict[str, Any]:
    """Train, evaluate, and log everything to MLflow."""
    import mlflow
    import mlflow.sklearn

    if not data_path.exists():
        raise FileNotFoundError(f"dataset not found at {data_path}; run training/generate_data.py or dvc checkout")

    frame = pd.read_csv(data_path)
    features = frame.drop(columns=["failure"])
    target = frame["failure"]
    x_train, x_test, y_train, y_test = train_test_split(
        features, target, test_size=test_size, random_state=random_state, stratify=target
    )

    model = RandomForestClassifier(n_estimators=n_estimators, max_depth=max_depth, random_state=random_state)
    model.fit(x_train, y_train)
    metrics = evaluate(model, x_test, y_test)
    provenance = dataset_provenance(data_path)

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment)

    params = {
        "n_estimators": n_estimators,
        "max_depth": max_depth,
        "test_size": test_size,
        "random_state": random_state,
        "rows": len(frame),
        "features": features.shape[1],
    }

    with mlflow.start_run(run_name=run_name) as run:
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        mlflow.set_tags({**provenance, "git.commit": _git(["rev-parse", "HEAD"]), "trained_by": "training/train.py"})
        # Pin the serialisation format. When skops is present MLflow prefers it,
        # and skops refuses to save sklearn tree internals unless they are
        # explicitly trusted, so the same code would work on one machine and
        # fail on another. cloudpickle behaves the same way everywhere.
        mlflow.sklearn.log_model(model, name="model", serialization_format="cloudpickle")
        run_id = run.info.run_id

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump(model, MODEL_DIR / "model.joblib")

    summary = {"run_id": run_id, "experiment": experiment, "metrics": metrics, "params": params, **provenance}
    METRICS_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train the failure-prediction model and log it to MLflow.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--experiment", default="failure-prediction")
    parser.add_argument("--n-estimators", type=int, default=120)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--tracking-uri", default=None, help="Defaults to MLFLOW_TRACKING_URI")
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args(argv)

    summary = train(
        data_path=args.data,
        experiment=args.experiment,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        test_size=args.test_size,
        random_state=args.random_state,
        tracking_uri=args.tracking_uri,
        run_name=args.run_name,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
