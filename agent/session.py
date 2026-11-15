"""Tool session: the bookkeeping that keeps answers tied to real tool output.

Every call a planner makes goes through a ToolSession, which records the result.
The evidence list in the final answer is built from those records rather than
from anything the planner says, so a claim can always be traced back to a tool
call that actually happened.
"""

from __future__ import annotations

from typing import Any

from agent.registry import ToolRegistry
from agent.schemas import EvidenceItem, ToolResult, ToolStatus

# Statuses that mean "we got no evidence", as opposed to "the tool answered".
NO_EVIDENCE = {
    ToolStatus.UNAVAILABLE,
    ToolStatus.TIMEOUT,
    ToolStatus.ERROR,
    ToolStatus.DENIED,
    ToolStatus.CONFIRMATION_REQUIRED,
}


class ToolSession:
    """Runs tools for one user question and remembers what happened."""

    def __init__(self, registry: ToolRegistry, request_id: str) -> None:
        self.registry = registry
        self.request_id = request_id
        self.results: list[ToolResult] = []

    def call(self, tool: str, **arguments: Any) -> ToolResult:
        """Execute a tool and record its result."""
        result = self.registry.execute(tool, arguments, request_id=self.request_id)
        self.results.append(result)
        return result

    def call_with(self, tool: str, arguments: dict[str, Any]) -> ToolResult:
        """Same as ``call`` but with arguments already in a dict."""
        return self.call(tool, **arguments)

    # --- reporting ---------------------------------------------------------

    @property
    def succeeded(self) -> list[ToolResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[ToolResult]:
        return [r for r in self.results if r.status in NO_EVIDENCE]

    def evidence(self) -> list[EvidenceItem]:
        """One evidence entry per tool call, successful or not."""
        return [
            EvidenceItem(
                tool=r.tool,
                status=r.status,
                source=r.source,
                duration_ms=r.duration_ms,
                detail=r.error if not r.ok else None,
            )
            for r in self.results
        ]

    def uncertainties(self) -> list[str]:
        """Say plainly what could not be established, and why.

        This is what stops a failed tool turning into a confident guess: each
        failure becomes a stated gap in the answer.
        """
        gaps: list[str] = []
        for result in self.failed:
            reason = result.error or "no detail returned"
            if result.status is ToolStatus.UNAVAILABLE:
                gaps.append(f"{result.tool} could not be reached, so that evidence is missing ({reason})")
            elif result.status is ToolStatus.TIMEOUT:
                gaps.append(f"{result.tool} timed out, so its result is unknown ({reason})")
            elif result.status is ToolStatus.DENIED:
                gaps.append(f"{result.tool} was refused by the safety policy ({reason})")
            elif result.status is ToolStatus.CONFIRMATION_REQUIRED:
                gaps.append(f"{result.tool} needs your confirmation before it will run ({reason})")
            else:
                gaps.append(f"{result.tool} returned an error ({reason})")
        return gaps

    def transcript(self) -> list[dict[str, Any]]:
        """Machine-readable record of the calls, used by the CLI's --json mode."""
        return [
            {
                "tool": r.tool,
                "status": r.status.value,
                "duration_ms": r.duration_ms,
                "source": r.source,
                "error": r.error,
            }
            for r in self.results
        ]


def first_ok(session: ToolSession, tool: str) -> ToolResult | None:
    """Most recent successful result for a tool, if there is one."""
    for result in reversed(session.results):
        if result.tool == tool and result.ok:
            return result
    return None
