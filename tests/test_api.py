"""Inference API: the readiness split that Kubernetes probes depend on."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main as api_main


class _StubModel:
    def predict(self, rows):
        return [1]

    def predict_proba(self, rows):
        return [[0.12, 0.88]]


@pytest.fixture
def client(monkeypatch):
    # Stub the loader as well, otherwise startup would replace the stub model
    # with a real load attempt against a path that does not exist here.
    monkeypatch.setattr(api_main, "load_model", lambda: None)
    monkeypatch.setattr(api_main, "_model", _StubModel())
    monkeypatch.setattr(api_main, "_load_error", None)
    with TestClient(api_main.app) as test_client:
        yield test_client


def test_health_reports_liveness(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["application"] == "inference-api"


def test_ready_is_green_once_the_model_is_loaded(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_predict_returns_a_scored_result(client):
    response = client.post(
        "/predict",
        json={"sensor_a": 0.5, "sensor_b": 1.2, "sensor_c": 0.3, "load": 0.8},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["prediction"] == 1
    assert body["probability"] == pytest.approx(0.88)


def test_invalid_payload_is_rejected(client):
    """load is constrained to 0..1, so an out-of-range request is a 422."""
    response = client.post(
        "/predict",
        json={"sensor_a": 0.5, "sensor_b": 1.2, "sensor_c": 0.3, "load": 9.9},
    )
    assert response.status_code == 422


def test_pod_is_not_ready_when_the_model_failed_to_load(monkeypatch):
    """Readiness fails while liveness still passes.

    This is the behaviour that takes a broken pod out of the Service endpoints
    instead of restarting it forever, and it is what the agent diagnoses when
    the API is reachable but serving nothing.
    """
    monkeypatch.setattr(api_main, "load_model", lambda: None)
    monkeypatch.setattr(api_main, "_model", None)
    monkeypatch.setattr(api_main, "_load_error", "FileNotFoundError: /app/models/model.joblib")

    with TestClient(api_main.app, raise_server_exceptions=False) as test_client:
        ready = test_client.get("/ready")
        assert ready.status_code == 503
        assert ready.json()["status"] == "not_ready"

        health = test_client.get("/health")
        assert health.status_code == 200
        assert health.json()["model_loaded"] is False

        predict = test_client.post(
            "/predict", json={"sensor_a": 0.1, "sensor_b": 0.1, "sensor_c": 0.1, "load": 0.1}
        )
        assert predict.status_code == 503
