"""Shared fixtures.

Nothing in the suite touches a real service: every backend is either mocked or
deliberately unreachable, which is also what lets CI run the same tests.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from agent import config
from agent.registry import ToolRegistry
from agent.schemas import EmptyInput, ToolResult, ToolSpec, ToolStatus


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    """Give each test its own settings, pointing at nothing real."""
    monkeypatch.setenv("AGENT_PLANNER", "rules")
    monkeypatch.setenv("AGENT_DISABLE_FILE_LOG", "1")
    monkeypatch.setenv("APPROVED_NAMESPACES", "mlops")
    monkeypatch.setenv("APPROVED_DAGS", "training_pipeline")
    monkeypatch.setenv("K8S_NAMESPACE", "mlops")
    monkeypatch.setenv("TOOL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://127.0.0.1:1")
    monkeypatch.setenv("AIRFLOW_API_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    settings = config.get_settings(refresh=True)
    settings.repo_root = tmp_path
    yield settings
    config.get_settings(refresh=True)


class EchoInput(BaseModel):
    value: int = 1


@pytest.fixture
def registry() -> ToolRegistry:
    """A tiny registry whose tools have predictable, controllable behaviour."""
    reg = ToolRegistry()

    def echo(payload: EchoInput) -> ToolResult:
        return ToolResult(
            tool="echo", status=ToolStatus.OK, data={"value": payload.value}, source="test://echo"
        )

    def boom(_: EmptyInput) -> ToolResult:
        raise RuntimeError("backend exploded")

    def slow(_: EmptyInput) -> ToolResult:
        import time

        time.sleep(10)
        return ToolResult(tool="slow", status=ToolStatus.OK)

    reg.register(ToolSpec(name="echo", description="Echo a number.", input_model=EchoInput), echo)
    reg.register(ToolSpec(name="boom", description="Always raises.", input_model=EmptyInput), boom)
    reg.register(ToolSpec(name="slow", description="Never returns in time.", input_model=EmptyInput), slow)
    return reg
