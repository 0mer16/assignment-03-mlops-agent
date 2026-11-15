"""Training pipeline DAG.

    validate_dataset -> preprocess_data -> train_model -> evaluate_model
                                                              |
                                                        quality_gate
                                                              |
                                                        promote_model

Two gates protect the registry. Validation stops a malformed dataset from ever
reaching training, and the quality gate stops a model that trains successfully
but scores below threshold from being promoted. Because promote_model sits
downstream of both, a failure at either point leaves the registry untouched.

Training runs in a separate virtual environment from Airflow, invoked as a
subprocess. That keeps Airflow's dependency set away from scikit-learn and
MLflow, which otherwise tend to fight over shared pins.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

try:  # Airflow 3
    from airflow.sdk import dag, task
except ImportError:  # pragma: no cover - Airflow 2 fallback
    from airflow.decorators import dag, task

log = logging.getLogger(__name__)

REPO = Path(os.environ.get("MLOPS_REPO", "/mnt/i/MLPAss/assignment-03-mlops-agent"))
MLOPS_PYTHON = os.environ.get("MLOPS_PYTHON", str(Path.home() / "mlops-venv" / "bin" / "python"))
TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://127.0.0.1:5001")
EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT", "failure-prediction")

DATASET = REPO / "data" / "dataset.csv"
PROCESSED = REPO / "data" / "processed" / "dataset_clean.csv"
METRICS = REPO / "metrics.json"

REQUIRED_COLUMNS = ["sensor_a", "sensor_b", "sensor_c", "load", "failure"]
MIN_ROWS = int(os.environ.get("MIN_ROWS", "500"))
F1_THRESHOLD = float(os.environ.get("QUALITY_GATE_F1", "0.75"))

SUBPROCESS_TIMEOUT = 900


def _run(command: list[str]) -> str:
    """Run a subprocess, surfacing its output in the task log either way."""
    log.info("running: %s", " ".join(command))
    completed = subprocess.run(
        command,
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
        check=False,
        env={**os.environ, "MLFLOW_TRACKING_URI": TRACKING_URI, "PYTHONIOENCODING": "utf-8"},
    )
    if completed.stdout:
        log.info("stdout:\n%s", completed.stdout[-4000:])
    if completed.stderr:
        log.warning("stderr:\n%s", completed.stderr[-4000:])
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")
    return completed.stdout


@dag(
    dag_id="training_pipeline",
    description="Validate, preprocess, train, evaluate and gate the failure-prediction model",
    schedule=None,
    start_date=datetime(2026, 1, 1),
    catchup=False,
    is_paused_upon_creation=False,
    max_active_runs=1,
    tags=["mlops", "training"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(seconds=30),
        "execution_timeout": timedelta(minutes=20),
    },
)
def training_pipeline():
    @task
    def validate_dataset() -> dict:
        """Fail fast on a missing, malformed or too-small dataset."""
        if not DATASET.exists():
            raise FileNotFoundError(f"dataset missing at {DATASET}; run 'dvc checkout' first")

        with DATASET.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                raise ValueError(f"dataset is missing required columns: {missing}")

            rows = 0
            empty_cells = 0
            bad_labels = 0
            for row in reader:
                rows += 1
                empty_cells += sum(1 for c in REQUIRED_COLUMNS if row.get(c) in (None, ""))
                if row.get("failure") not in {"0", "1"}:
                    bad_labels += 1

        log.info("validated %s rows, %s empty cells, %s bad labels", rows, empty_cells, bad_labels)
        if rows < MIN_ROWS:
            raise ValueError(f"dataset has only {rows} rows, below the minimum of {MIN_ROWS}")
        if empty_cells:
            raise ValueError(f"dataset has {empty_cells} empty cells in required columns")
        if bad_labels:
            raise ValueError(f"dataset has {bad_labels} rows whose 'failure' label is not 0 or 1")

        return {"rows": rows, "columns": header, "dataset": str(DATASET)}

    @task
    def preprocess_data(validation: dict) -> dict:
        """Drop duplicate rows and write the processed copy training reads."""
        with DATASET.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        seen: set[tuple] = set()
        unique = []
        for row in rows:
            key = tuple(row[c] for c in REQUIRED_COLUMNS)
            if key not in seen:
                seen.add(key)
                unique.append(row)

        PROCESSED.parent.mkdir(parents=True, exist_ok=True)
        with PROCESSED.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows({c: r[c] for c in REQUIRED_COLUMNS} for r in unique)

        duplicates = len(rows) - len(unique)
        log.info("preprocessing removed %s duplicate row(s), wrote %s", duplicates, PROCESSED)
        return {"input_rows": validation["rows"], "output_rows": len(unique), "duplicates_removed": duplicates}

    @task
    def train_model(preprocessing: dict) -> dict:
        """Train on the processed data and log the run to MLflow."""
        _run(
            [
                MLOPS_PYTHON,
                str(REPO / "training" / "train.py"),
                "--data",
                str(PROCESSED),
                "--experiment",
                EXPERIMENT,
                "--run-name",
                f"airflow-{datetime.utcnow():%Y%m%d-%H%M%S}",
                "--tracking-uri",
                TRACKING_URI,
            ]
        )
        summary = json.loads(METRICS.read_text(encoding="utf-8"))
        log.info("trained run %s on %s rows", summary["run_id"], preprocessing["output_rows"])
        return summary

    @task
    def evaluate_model(summary: dict) -> dict:
        """Report the recorded evaluation metrics for the run just trained."""
        metrics = summary["metrics"]
        for name, value in sorted(metrics.items()):
            log.info("metric %s = %.4f", name, value)
        return {
            "run_id": summary["run_id"],
            "metrics": metrics,
            "dataset_version": summary.get("dataset.version"),
            "dataset_hash": summary.get("dataset.dvc_hash"),
        }

    @task
    def quality_gate(evaluation: dict) -> dict:
        """Block promotion when the model is below the agreed F1 threshold."""
        f1 = float(evaluation["metrics"]["f1_score"])
        passed = f1 >= F1_THRESHOLD
        log.info("quality gate: f1_score=%.4f threshold=%.4f passed=%s", f1, F1_THRESHOLD, passed)
        if not passed:
            raise ValueError(
                f"quality gate failed: f1_score {f1:.4f} is below the threshold {F1_THRESHOLD:.4f}; "
                "the model will not be promoted"
            )
        return {**evaluation, "f1_score": f1, "threshold": F1_THRESHOLD}

    @task
    def promote_model(gated: dict) -> dict:
        """Register the model. Only reachable once validation and the gate pass."""
        output = _run(
            [
                MLOPS_PYTHON,
                str(REPO / "training" / "promote.py"),
                "--run-id",
                gated["run_id"],
                "--model-name",
                "failure-prediction",
                "--tracking-uri",
                TRACKING_URI,
            ]
        )
        log.info("promotion output: %s", output.strip()[-500:])
        return {"run_id": gated["run_id"], "f1_score": gated["f1_score"], "promoted": True}

    validated = validate_dataset()
    preprocessed = preprocess_data(validated)
    trained = train_model(preprocessed)
    evaluated = evaluate_model(trained)
    gated = quality_gate(evaluated)
    promote_model(gated)


training_pipeline()
