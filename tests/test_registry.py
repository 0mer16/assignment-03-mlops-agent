"""The registry contract: validate, bound, and never crash the agent."""

from __future__ import annotations

from agent.schemas import ToolStatus


def test_successful_tool_selection_and_execution(registry):
    """A valid call runs the right tool and returns its data."""
    result = registry.execute("echo", {"value": 42}, request_id="req-1")

    assert result.status is ToolStatus.OK
    assert result.data == {"value": 42}
    assert result.tool == "echo"
    assert result.request_id == "req-1"
    assert result.source == "test://echo"


def test_unknown_tool_is_reported_not_raised(registry):
    """Asking for a tool that does not exist is an error result, not an exception."""
    result = registry.execute("does_not_exist", {}, request_id="req-2")

    assert result.status is ToolStatus.ERROR
    assert "unknown tool" in result.error
    # The caller is told what it could have used instead.
    assert "echo" in result.error


def test_invalid_arguments_are_rejected_before_the_tool_runs(registry):
    """Schema validation failures are reported without touching the backend."""
    result = registry.execute("echo", {"value": "not-a-number"}, request_id="req-3")

    assert result.status is ToolStatus.ERROR
    assert "invalid arguments" in result.error


def test_tool_exception_becomes_an_error_result(registry):
    """A crashing backend degrades to an error, it does not propagate."""
    result = registry.execute("boom", {}, request_id="req-4")

    assert result.status is ToolStatus.ERROR
    assert "RuntimeError" in result.error
    assert "backend exploded" in result.error


def test_tool_timeout_is_bounded(registry, monkeypatch):
    """A hanging service is cut off at the configured timeout."""
    from agent import config

    monkeypatch.setenv("TOOL_TIMEOUT_SECONDS", "0.3")
    config.get_settings(refresh=True)

    result = registry.execute("slow", {}, request_id="req-5")

    assert result.status is ToolStatus.TIMEOUT
    assert "timed out" in result.error


def test_registry_describes_its_tools(registry):
    """The catalogue is what both the planner and the CLI advertise."""
    description = registry.describe()

    assert "echo" in description
    assert registry.names() == ["boom", "echo", "slow"]
