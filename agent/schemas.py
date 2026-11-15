"""Structured inputs and outputs shared by the agent and every tool.

Each tool takes a pydantic input model and returns a ToolResult. The envelope is
what lets the agent tell the difference between "the service said X" and "the
service could not be reached", which is the whole point of not inventing answers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolStatus(StrEnum):
    """Outcome of a single tool call."""

    OK = "ok"
    ERROR = "error"
    UNAVAILABLE = "unavailable"  # service unreachable or not configured
    DENIED = "denied"  # blocked by the safety policy
    TIMEOUT = "timeout"
    CONFIRMATION_REQUIRED = "confirmation_required"


class ToolResult(BaseModel):
    """What every tool returns, successful or not."""

    tool: str
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    source: str | None = Field(default=None, description="Where the evidence came from, e.g. the command or endpoint")
    duration_ms: float = 0.0
    request_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ToolStatus.OK

    def summary(self) -> str:
        """One line suitable for a transcript or a log."""
        if self.ok:
            return f"{self.tool}: ok ({self.duration_ms:.0f} ms)"
        return f"{self.tool}: {self.status.value} - {self.error or 'no detail'}"


class ToolSpec(BaseModel):
    """Declaration of a callable tool, used for routing and for LLM function calling."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_model: type[BaseModel]
    mutating: bool = False
    module: str = ""


# --- DVC -------------------------------------------------------------------


class EmptyInput(BaseModel):
    """No arguments."""


class DatasetPathInput(BaseModel):
    path: str = Field(default="data/dataset.csv", description="Repository-relative path of the tracked dataset")


class DatasetCompareInput(BaseModel):
    rev_a: str = Field(description="Older git revision, branch or tag")
    rev_b: str = Field(default="HEAD", description="Newer git revision, branch or tag")
    path: str = Field(default="data", description="Repository-relative path to compare")


class DatasetReproduceInput(BaseModel):
    rev: str = Field(description="Git revision, branch or tag identifying the dataset version")
    path: str = Field(default="data/dataset.csv", description="Repository-relative path of the tracked dataset")


# --- MLflow ----------------------------------------------------------------


class ListRunsInput(BaseModel):
    experiment_name: str | None = Field(default=None, description="Experiment to read, defaults to all experiments")
    limit: int = Field(default=5, ge=1, le=100, description="How many of the most recent runs to return")


class RunIdInput(BaseModel):
    run_id: str = Field(description="MLflow run id")


class BestRunInput(BaseModel):
    metric: str = Field(default="f1_score", description="Metric to rank by, for example f1_score or accuracy")
    experiment_name: str | None = Field(default=None, description="Experiment to search, defaults to all")
    limit: int = Field(default=5, ge=1, le=100, description="How many recent runs to consider")
    larger_is_better: bool = Field(default=True)


class CompareRunsInput(BaseModel):
    run_ids: list[str] = Field(min_length=2, description="Two or more MLflow run ids to compare")


# --- Airflow ---------------------------------------------------------------


class DagIdInput(BaseModel):
    dag_id: str = Field(description="Airflow DAG id")


class DagRunsInput(BaseModel):
    dag_id: str = Field(description="Airflow DAG id")
    limit: int = Field(default=5, ge=1, le=50)


class TriggerDagInput(BaseModel):
    dag_id: str = Field(description="DAG to trigger, must be on the approved list")
    confirm: bool = Field(default=False, description="Must be true; the agent asks the user before setting it")
    note: str | None = Field(default=None, description="Why the run was triggered")


class DagRunRefInput(BaseModel):
    dag_id: str
    dag_run_id: str | None = Field(default=None, description="Defaults to the most recent run")


class TaskLogInput(BaseModel):
    dag_id: str
    dag_run_id: str
    task_id: str
    try_number: int = Field(default=1, ge=1)


# --- Kubernetes ------------------------------------------------------------


class NamespaceInput(BaseModel):
    namespace: str | None = Field(default=None, description="Defaults to the configured application namespace")


class PodLogsInput(BaseModel):
    pod_name: str
    namespace: str | None = None
    tail_lines: int = Field(default=50, ge=1, le=500)


class ServiceInput(BaseModel):
    service_name: str = Field(default="inference-api")
    namespace: str | None = None


class DeploymentInput(BaseModel):
    deployment_name: str | None = Field(default=None, description="Defaults to the configured deployment")
    namespace: str | None = None


class RestartDeploymentInput(BaseModel):
    deployment_name: str | None = None
    namespace: str | None = None
    confirm: bool = Field(default=False, description="Must be true; changes cluster state")


# --- Agent output ----------------------------------------------------------


class EvidenceItem(BaseModel):
    """One tool call behind an answer, kept so claims can be traced."""

    tool: str
    status: ToolStatus
    source: str | None = None
    duration_ms: float = 0.0
    detail: str | None = None


class AgentAnswer(BaseModel):
    """Final response, separating what is known from what is not."""

    request_id: str
    question: str
    answer: str
    facts: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    planner: str = "rules"

    def render(self) -> str:
        """Human readable transcript form."""
        lines = [self.answer.strip(), ""]
        for title, items in (
            ("Facts", self.facts),
            ("Uncertainties", self.uncertainties),
            ("Recommended actions", self.recommended_actions),
        ):
            if items:
                lines.append(f"{title}:")
                lines.extend(f"  - {item}" for item in items)
                lines.append("")
        if self.evidence:
            lines.append("Evidence:")
            for item in self.evidence:
                detail = f" - {item.detail}" if item.detail else ""
                lines.append(f"  - [{item.status.value}] {item.tool} ({item.source or 'n/a'}){detail}")
        return "\n".join(lines).strip()
