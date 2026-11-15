"""Register a trained run's model in the MLflow model registry.

Called only by the DAG's promote_model task, which sits downstream of the
quality gate, so reaching this script already means the model passed.
"""

from __future__ import annotations

import argparse
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def promote(run_id: str, model_name: str, tracking_uri: str | None) -> dict:
    import mlflow
    from mlflow.tracking import MlflowClient

    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)

    client = MlflowClient()
    try:
        client.create_registered_model(model_name)
    except Exception:  # noqa: BLE001 - already exists is the normal case after the first run
        pass

    version = client.create_model_version(
        name=model_name,
        source=f"runs:/{run_id}/model",
        run_id=run_id,
    )
    client.set_model_version_tag(model_name, version.version, "promoted_by", "training_pipeline")
    return {"model": model_name, "version": version.version, "run_id": run_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Register a model version in MLflow.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-name", default="failure-prediction")
    parser.add_argument("--tracking-uri", default=None)
    args = parser.parse_args(argv)

    result = promote(args.run_id, args.model_name, args.tracking_uri)
    print(f"registered {result['model']} version {result['version']} from run {result['run_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
