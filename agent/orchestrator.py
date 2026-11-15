"""The agent itself: one question in, one evidence-backed answer out."""

from __future__ import annotations

from typing import Any

from agent.config import Settings, get_settings
from agent.logging_utils import configure_logging, default_log_file, get_logger, new_request_id
from agent.planner import PlannerContext, RuleBasedPlanner, select_planner
from agent.registry import ToolRegistry, build_registry
from agent.schemas import AgentAnswer
from agent.session import ToolSession


class MLOpsAgent:
    """Routes a natural-language request to tools and reports what they returned.

    The planner is swappable, but the contract is not: every answer is assembled
    from recorded tool results, and any tool that failed shows up as a stated
    uncertainty rather than being quietly dropped.
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        planner: Any | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        configure_logging(default_log_file())
        self.registry = registry or build_registry()
        self.planner = planner or select_planner(self.settings)

    def ask(self, question: str, confirm: bool = False) -> AgentAnswer:
        """Answer one question. ``confirm`` authorises state-changing tools."""
        request_id = new_request_id()
        logger = get_logger()
        logger.info(
            "request_started",
            extra={
                "request_id": request_id,
                "planner": getattr(self.planner, "name", type(self.planner).__name__),
                "confirm": confirm,
                "question_chars": len(question),
            },
        )

        session = ToolSession(self.registry, request_id)
        ctx = PlannerContext(
            question=question,
            session=session,
            request_id=request_id,
            confirm=confirm,
            settings=self.settings,
        )

        try:
            answer = self.planner.run(ctx)
        except Exception as exc:  # noqa: BLE001 - a planner failure must not lose the evidence
            logger.exception(
                "planner_failed",
                extra={"request_id": request_id, "error": str(exc), "status": "error"},
            )
            if isinstance(self.planner, RuleBasedPlanner):
                raise
            # Fall back to the deterministic planner so the question still gets
            # an answer built from real tool calls.
            fallback = RuleBasedPlanner()
            answer = fallback.run(ctx)
            answer.uncertainties.append(
                f"The {getattr(self.planner, 'name', 'configured')} planner failed ({type(exc).__name__}); "
                "the deterministic planner answered instead."
            )

        logger.info(
            "request_finished",
            extra={
                "request_id": request_id,
                "status": "ok",
                "tool_calls": len(session.results),
                "failed_tool_calls": len(session.failed),
                "planner": answer.planner,
            },
        )
        return answer

    def transcript(self) -> str:
        """The tool catalogue, for the CLI's --tools flag."""
        return self.registry.describe()
