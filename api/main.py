"""FastAPI inference service deployed to Kubernetes.

Health and readiness are deliberately different checks. Liveness only asks
whether the process is alive; readiness also requires the model to be loaded.
That split is what lets Kubernetes keep a pod running while taking it out of
the Service endpoints when it cannot actually serve predictions, and it is the
behaviour the agent diagnoses when the API appears unavailable.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("inference-api")

APP_VERSION = os.getenv("APP_VERSION", "0.0.0-dev")
MODEL_PATH = Path(os.getenv("MODEL_PATH", "/app/models/model.joblib"))
MODEL_NAME = os.getenv("MODEL_NAME", "failure-prediction")

FEATURES = ["sensor_a", "sensor_b", "sensor_c", "load"]

_model: Any | None = None
_load_error: str | None = None


def load_model() -> None:
    """Load the model once at startup, remembering why it failed if it did."""
    global _model, _load_error
    try:
        import joblib

        _model = joblib.load(MODEL_PATH)
        _load_error = None
        log.info("loaded model from %s", MODEL_PATH)
    except Exception as exc:  # noqa: BLE001 - any load failure means not ready
        _model = None
        _load_error = f"{type(exc).__name__}: {exc}"
        log.error("could not load model from %s: %s", MODEL_PATH, _load_error)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Load the model once at startup.

    A load failure is recorded rather than raised: the pod should start and
    report itself not-ready, which is far easier to diagnose than a crash loop.
    """
    if _model is None:
        load_model()
    yield


app = FastAPI(title="MLOps inference API", version=APP_VERSION, lifespan=lifespan)


class PredictRequest(BaseModel):
    sensor_a: float = Field(description="Primary sensor reading")
    sensor_b: float = Field(description="Secondary sensor reading")
    sensor_c: float = Field(description="Tertiary sensor reading")
    load: float = Field(ge=0.0, le=1.0, description="Normalised load between 0 and 1")


class PredictResponse(BaseModel):
    prediction: int
    probability: float
    model_version: str
    app_version: str


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness: the process is up and serving. Used by the liveness probe."""
    return {
        "status": "healthy",
        "application": "inference-api",
        "app_version": APP_VERSION,
        "model_loaded": _model is not None,
    }


@app.get("/ready")
def ready() -> JSONResponse:
    """Readiness: the model is loaded, so this pod can take traffic."""
    if _model is None:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": _load_error or "model not loaded", "model_path": str(MODEL_PATH)},
        )
    return JSONResponse(status_code=200, content={"status": "ready", "model_path": str(MODEL_PATH)})


@app.get("/version")
def version() -> dict[str, str]:
    """Which application and model this pod is serving."""
    return {"app_version": APP_VERSION, "model_name": MODEL_NAME, "model_path": str(MODEL_PATH)}


@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest) -> PredictResponse:
    """Score one record."""
    if _model is None:
        raise HTTPException(status_code=503, detail=f"model is not loaded: {_load_error or 'unknown reason'}")

    row = [[getattr(request, name) for name in FEATURES]]
    prediction = int(_model.predict(row)[0])
    probability = float(_model.predict_proba(row)[0][1])
    log.info("prediction=%s probability=%.4f", prediction, probability)
    return PredictResponse(
        prediction=prediction,
        probability=round(probability, 6),
        model_version=MODEL_NAME,
        app_version=APP_VERSION,
    )
