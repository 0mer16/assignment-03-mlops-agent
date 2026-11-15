"""Tool registry: the one place where a tool call is validated, bounded and logged."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Any

from pydantic import BaseModel, ValidationError

from agent.config import get_settings
from agent.errors import ConfirmationRequired, PermissionDenied, ToolUnavailable
from agent.logging_utils import log_tool_call
from agent.schemas import ToolResult, ToolSpec, ToolStatus

ToolFunc = Callable[[BaseModel], ToolResult]


class ToolRegistry:
    """Holds the callable tools and runs them under a uniform contract."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._funcs: dict[str, ToolFunc] = {}

    def register(self, spec: ToolSpec, func: ToolFunc) -> None:
        if spec.name in self._specs:
            raise ValueError(f"tool {spec.name!r} is already registered")
        self._specs[spec.name] = spec
        self._funcs[spec.name] = func

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in self.names()]

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def describe(self) -> str:
        """Plain-text catalogue, used in planner prompts and by the CLI."""
        lines = []
        for spec in self.specs():
            flag = " [changes state]" if spec.mutating else ""
            lines.append(f"- {spec.name}{flag}: {spec.description}")
        return "\n".join(lines)

    def execute(self, name: str, arguments: dict[str, Any] | None = None, request_id: str = "") -> ToolResult:
        """Validate, run and time one tool call. Never raises because a tool failed."""
        arguments = arguments or {}
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                tool=name,
                status=ToolStatus.ERROR,
                error=f"unknown tool {name!r}; available tools are {self.names()}",
                request_id=request_id,
            )

        try:
            payload = spec.input_model(**arguments)
        except ValidationError as exc:
            return ToolResult(
                tool=name,
                status=ToolStatus.ERROR,
                error=f"invalid arguments: {exc.errors(include_url=False)}",
                request_id=request_id,
            )

        timeout = get_settings().tool_timeout_seconds
        with log_tool_call(name, request_id, arguments) as state:
            result = self._run_bounded(spec, payload, timeout, request_id)
            state["status"] = result.status.value
        result.request_id = request_id
        if not result.duration_ms:
            result.duration_ms = float(state.get("duration_ms", 0.0))
        return result

    def _run_bounded(self, spec: ToolSpec, payload: BaseModel, timeout: float, request_id: str) -> ToolResult:
        """Run the tool on a worker thread so a stuck service cannot block the agent."""
        func = self._funcs[spec.name]
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(func, payload)
            try:
                return future.result(timeout=timeout)
            except FuturesTimeout:
                return ToolResult(
                    tool=spec.name,
                    status=ToolStatus.TIMEOUT,
                    error=f"tool timed out after {timeout:g}s",
                    request_id=request_id,
                )
            except ConfirmationRequired as exc:
                return ToolResult(
                    tool=spec.name,
                    status=ToolStatus.CONFIRMATION_REQUIRED,
                    error=str(exc),
                    request_id=request_id,
                )
            except PermissionDenied as exc:
                return ToolResult(tool=spec.name, status=ToolStatus.DENIED, error=str(exc), request_id=request_id)
            except ToolUnavailable as exc:
                return ToolResult(
                    tool=spec.name, status=ToolStatus.UNAVAILABLE, error=str(exc), request_id=request_id
                )
            except Exception as exc:  # noqa: BLE001 - a tool bug is reported, it never crashes the agent
                return ToolResult(
                    tool=spec.name,
                    status=ToolStatus.ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                    request_id=request_id,
                )
        finally:
            # A timed-out worker is abandoned on purpose: waiting for it would
            # reintroduce exactly the hang the timeout exists to prevent.
            pool.shutdown(wait=False, cancel_futures=True)


def build_registry() -> ToolRegistry:
    """Register every tool module.

    Imports happen here rather than at module import time so that a missing
    optional dependency in one backend cannot stop the other three loading.
    """
    from agent.tools import airflow_tools, dvc_tools, kubernetes_tools, mlflow_tools

    registry = ToolRegistry()
    for module in (dvc_tools, mlflow_tools, airflow_tools, kubernetes_tools):
        module.register(registry)
    return registry
