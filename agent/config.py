"""Runtime settings.

Everything is read from the environment so no endpoint, credential or namespace is
baked into the source. Defaults point at the local development stack described in
the README.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or default


@dataclass
class Settings:
    """Configuration for the agent and its four tool backends."""

    repo_root: Path = REPO_ROOT

    # MLflow
    mlflow_tracking_uri: str = field(default_factory=lambda: os.getenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:5000"))

    # Airflow
    airflow_api_url: str = field(default_factory=lambda: os.getenv("AIRFLOW_API_URL", "http://127.0.0.1:8080"))
    airflow_username: str = field(default_factory=lambda: os.getenv("AIRFLOW_USERNAME", "admin"))
    airflow_password: str = field(default_factory=lambda: os.getenv("AIRFLOW_PASSWORD", ""))

    # Kubernetes
    kube_namespace: str = field(default_factory=lambda: os.getenv("K8S_NAMESPACE", "mlops"))
    kube_deployment: str = field(default_factory=lambda: os.getenv("K8S_DEPLOYMENT", "inference-api"))

    # Safety policy. Anything outside these lists is refused before a call is made.
    approved_namespaces: list[str] = field(default_factory=lambda: _env_list("APPROVED_NAMESPACES", ["mlops"]))
    approved_dags: list[str] = field(default_factory=lambda: _env_list("APPROVED_DAGS", ["training_pipeline"]))

    # Every tool call is bounded so a hanging service cannot hang the agent.
    tool_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("TOOL_TIMEOUT_SECONDS", "30")))

    # Planner
    planner: str = field(default_factory=lambda: os.getenv("AGENT_PLANNER", "auto"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", "")))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-2.5-flash"))

    log_file: Path = field(default_factory=lambda: REPO_ROOT / "logs" / "agent.jsonl")


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    """Return process-wide settings, re-reading the environment when asked."""
    global _settings
    if _settings is None or refresh:
        _settings = Settings()
    return _settings
