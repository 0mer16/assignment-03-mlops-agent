"""The Gemini planner, driven by a stubbed client.

The LLM path has to be testable without a network call, otherwise the only way
to know it works is to try it in front of an examiner.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.errors import ToolUnavailable
from agent.planner import GeminiPlanner, PlannerContext, _schema_for_gemini
from agent.registry import build_registry
from agent.schemas import BestRunInput, ToolStatus
from agent.session import ToolSession
from agent.tools import mlflow_tools


class _FakeCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _FakeClient:
    """Returns a tool call first, then a JSON answer, like a real exchange."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls += 1
        return self._responses.pop(0)


def _response(*, calls=None, text=""):
    return SimpleNamespace(
        function_calls=calls or [],
        text=text,
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[]))],
    )


def test_tool_schemas_convert_to_gemini_declarations():
    """Every registered tool must produce a declaration Gemini will accept."""
    for spec in build_registry().specs():
        schema = _schema_for_gemini(spec.input_model)
        assert schema["type"] == "object"
        assert "properties" in schema
        # Gemini rejects these JSON Schema keys.
        assert "$defs" not in schema
        assert "anyOf" not in str(schema)


def test_gemini_planner_executes_tool_calls_and_parses_the_answer(monkeypatch):
    client = _FakeClient(
        [
            _response(calls=[_FakeCall("mlflow_best_run", {"metric": "f1_score", "limit": 5})]),
            _response(
                text='{"answer": "rf-depth6 is the leading candidate.", '
                '"facts": ["rf-depth6 scored 0.91"], "uncertainties": [], '
                '"recommended_actions": ["Validate against the quality gate."]}'
            ),
        ]
    )

    fake_run = SimpleNamespace(
        info=SimpleNamespace(run_id="r2", run_name="rf-depth6", status="FINISHED", start_time=1, experiment_id="1"),
        data=SimpleNamespace(metrics={"f1_score": 0.91}, params={}, tags={"dataset.version": "dataset-v1"}),
    )
    monkeypatch.setattr(
        mlflow_tools,
        "_client",
        lambda: SimpleNamespace(
            search_experiments=lambda: [SimpleNamespace(experiment_id="1")],
            search_runs=lambda **kwargs: [fake_run],
        ),
    )

    registry = build_registry()
    session = ToolSession(registry, "req-gem")
    planner = GeminiPlanner(client=client, model="gemini-3.8-flash")
    answer = planner.run(PlannerContext(question="best model?", session=session, request_id="req-gem"))

    assert client.calls == 2
    assert answer.planner == "gemini"
    assert "rf-depth6" in answer.answer
    assert answer.facts == ["rf-depth6 scored 0.91"]
    # the tool really ran, and its result is in the evidence
    assert [e.tool for e in answer.evidence] == ["mlflow_best_run"]
    assert answer.evidence[0].status is ToolStatus.OK


def test_gemini_planner_cannot_invent_results_when_tools_fail(monkeypatch):
    """Even if the model asserts facts, a total tool failure discards them."""
    client = _FakeClient(
        [
            _response(calls=[_FakeCall("mlflow_best_run", {"metric": "f1_score"})]),
            _response(text='{"answer": "The best model is v9 with F1 0.99.", "facts": ["F1 was 0.99"]}'),
        ]
    )
    monkeypatch.setattr(
        mlflow_tools, "_client", lambda: (_ for _ in ()).throw(ToolUnavailable("tracking server is down"))
    )

    session = ToolSession(build_registry(), "req-gem2")
    planner = GeminiPlanner(client=client, model="gemini-3.8-flash")
    answer = planner.run(PlannerContext(question="best model?", session=session, request_id="req-gem2"))

    assert answer.facts == []
    assert "0.99" not in answer.answer
    assert any("could not be reached" in u for u in answer.uncertainties)


def test_gemini_planner_retries_a_transient_overload(monkeypatch):
    """A 503 means try again; it should not fall straight back to the rules planner."""
    attempts = {"n": 0}

    class _Flaky(_FakeClient):
        def generate_content(self, model, contents, config):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("503 UNAVAILABLE. model is overloaded")
            return _response(text='{"answer": "ok", "facts": []}')

    monkeypatch.setattr("time.sleep", lambda *_: None)
    session = ToolSession(build_registry(), "req-gem3")
    planner = GeminiPlanner(client=_Flaky([]), model="gemini-3.8-flash")
    answer = planner.run(PlannerContext(question="hello", session=session, request_id="req-gem3"))

    assert attempts["n"] == 2
    assert answer.answer == "ok"


def test_best_run_input_schema_is_declarable():
    schema = _schema_for_gemini(BestRunInput)
    assert schema["properties"]["metric"]["type"] == "string"
    assert schema["properties"]["limit"]["type"] == "integer"


@pytest.mark.parametrize("planner_setting", ["rules", "auto"])
def test_planner_selection_falls_back_without_a_key(monkeypatch, planner_setting):
    from agent import config
    from agent.planner import RuleBasedPlanner, select_planner

    monkeypatch.setenv("AGENT_PLANNER", planner_setting)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    assert isinstance(select_planner(config.get_settings(refresh=True)), RuleBasedPlanner)
