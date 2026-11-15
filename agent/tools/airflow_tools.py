"""Apache Airflow workflow tools.

Airflow 3 replaced the old experimental endpoints with ``/api/v2`` behind JWT
auth, so these tools exchange the configured credentials for a short-lived token
and reuse it for the session.

Triggering is the only state-changing operation exposed here. It is gated twice:
the DAG must be on the approved list, and the caller must pass confirm=true,
which the agent only sets after the user says so.
"""

from __future__ import annotations

from typing import Any

import requests

from agent.config import get_settings
from agent.errors import ToolUnavailable
from agent.registry import ToolRegistry
from agent.safety import check_dag_approved, require_confirmation
from agent.schemas import (
    DagRunRefInput,
    DagRunsInput,
    EmptyInput,
    TaskLogInput,
    ToolResult,
    ToolSpec,
    ToolStatus,
    TriggerDagInput,
)

MODULE = "airflow"
API_PREFIX = "/api/v2"
_HTTP_TIMEOUT = 20

# Cached per process; Airflow tokens are short-lived but outlive one conversation.
_token_cache: dict[str, str] = {}


def _base_url() -> str:
    return get_settings().airflow_api_url.rstrip("/")


def _get_token(force_refresh: bool = False) -> str:
    """Exchange username and password for a JWT access token."""
    settings = get_settings()
    base = _base_url()
    if not force_refresh and _token_cache.get(base):
        return _token_cache[base]

    if not settings.airflow_password:
        raise ToolUnavailable(
            "no Airflow password configured; set AIRFLOW_PASSWORD in the environment (see README)"
        )

    try:
        response = requests.post(
            f"{base}/auth/token",
            json={"username": settings.airflow_username, "password": settings.airflow_password},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ToolUnavailable(f"cannot reach the Airflow API at {base}: {exc}") from exc

    if response.status_code != 200:
        raise ToolUnavailable(
            f"Airflow rejected the credentials for user {settings.airflow_username!r} "
            f"(HTTP {response.status_code})"
        )

    token = response.json().get("access_token")
    if not token:
        raise ToolUnavailable("Airflow returned no access_token in its auth response")
    _token_cache[base] = token
    return token


def _request(method: str, path: str, **kwargs: Any) -> requests.Response:
    """Call the Airflow API with a bearer token, refreshing it once if expired."""
    url = f"{_base_url()}{API_PREFIX}{path}"
    for attempt in (1, 2):
        token = _get_token(force_refresh=attempt == 2)
        try:
            response = requests.request(
                method,
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=_HTTP_TIMEOUT,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ToolUnavailable(f"cannot reach the Airflow API at {url}: {exc}") from exc

        if response.status_code == 401 and attempt == 1:
            continue  # token probably expired, try once with a fresh one
        return response
    raise ToolUnavailable("Airflow authentication kept failing after a token refresh")


def _json(response: requests.Response, context: str) -> dict[str, Any]:
    if response.status_code >= 500:
        raise ToolUnavailable(f"Airflow returned HTTP {response.status_code} for {context}")
    try:
        return response.json()
    except ValueError as exc:
        raise ToolUnavailable(f"Airflow returned a non-JSON response for {context}") from exc


def list_dags(_: EmptyInput) -> ToolResult:
    """List the DAGs Airflow knows about."""
    response = _request("GET", "/dags", params={"limit": 100})
    payload = _json(response, "GET /dags")
    if response.status_code != 200:
        return ToolResult(
            tool="airflow_list_dags",
            status=ToolStatus.ERROR,
            error=f"HTTP {response.status_code}: {payload}",
            source=f"GET {_base_url()}{API_PREFIX}/dags",
        )

    dags = [
        {
            "dag_id": d.get("dag_id"),
            "is_paused": d.get("is_paused"),
            "description": d.get("description"),
            "tags": [t.get("name") if isinstance(t, dict) else t for t in (d.get("tags") or [])],
            "last_parsed_time": d.get("last_parsed_time"),
        }
        for d in payload.get("dags", [])
    ]
    return ToolResult(
        tool="airflow_list_dags",
        status=ToolStatus.OK,
        data={
            "dags": dags,
            "count": len(dags),
            "approved_for_trigger": get_settings().approved_dags,
        },
        source=f"GET {_base_url()}{API_PREFIX}/dags",
    )


def dag_runs(payload: DagRunsInput) -> ToolResult:
    """Recent runs of one DAG, newest first."""
    path = f"/dags/{payload.dag_id}/dagRuns"
    response = _request("GET", path, params={"limit": payload.limit, "order_by": "-run_after"})
    body = _json(response, f"GET {path}")
    if response.status_code == 404:
        return ToolResult(
            tool="airflow_dag_runs",
            status=ToolStatus.ERROR,
            error=f"DAG {payload.dag_id!r} does not exist in Airflow",
            source=f"GET {_base_url()}{API_PREFIX}{path}",
        )
    if response.status_code != 200:
        return ToolResult(
            tool="airflow_dag_runs",
            status=ToolStatus.ERROR,
            error=f"HTTP {response.status_code}: {body}",
            source=f"GET {_base_url()}{API_PREFIX}{path}",
        )

    runs = [
        {
            "dag_run_id": r.get("dag_run_id"),
            "state": r.get("state"),
            "run_type": r.get("run_type"),
            "logical_date": r.get("logical_date"),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "note": r.get("note"),
        }
        for r in body.get("dag_runs", [])
    ]
    return ToolResult(
        tool="airflow_dag_runs",
        status=ToolStatus.OK,
        data={
            "dag_id": payload.dag_id,
            "runs": runs,
            "count": len(runs),
            "latest_state": runs[0]["state"] if runs else None,
        },
        source=f"GET {_base_url()}{API_PREFIX}{path}",
    )


def trigger_dag(payload: TriggerDagInput) -> ToolResult:
    """Trigger an approved training DAG, only with explicit confirmation."""
    check_dag_approved(payload.dag_id)
    require_confirmation(payload.confirm, f"triggering DAG {payload.dag_id!r}")

    path = f"/dags/{payload.dag_id}/dagRuns"
    body: dict[str, Any] = {"logical_date": None, "conf": {}}
    if payload.note:
        body["note"] = payload.note

    response = _request("POST", path, json=body)
    data = _json(response, f"POST {path}")
    if response.status_code not in (200, 201):
        return ToolResult(
            tool="airflow_trigger_dag",
            status=ToolStatus.ERROR,
            error=f"HTTP {response.status_code}: {data}",
            source=f"POST {_base_url()}{API_PREFIX}{path}",
        )

    return ToolResult(
        tool="airflow_trigger_dag",
        status=ToolStatus.OK,
        data={
            "dag_id": payload.dag_id,
            "dag_run_id": data.get("dag_run_id"),
            "state": data.get("state"),
            "logical_date": data.get("logical_date"),
            "note": "the run was queued; poll airflow_dag_runs for the outcome",
        },
        source=f"POST {_base_url()}{API_PREFIX}{path}",
    )


def _latest_run_id(dag_id: str) -> str | None:
    response = _request("GET", f"/dags/{dag_id}/dagRuns", params={"limit": 1, "order_by": "-run_after"})
    body = _json(response, "GET dagRuns")
    runs = body.get("dag_runs", [])
    return runs[0].get("dag_run_id") if runs else None


def failed_tasks(payload: DagRunRefInput) -> ToolResult:
    """Find which tasks failed in a DAG run, and why they stopped."""
    run_id = payload.dag_run_id or _latest_run_id(payload.dag_id)
    if run_id is None:
        return ToolResult(
            tool="airflow_failed_tasks",
            status=ToolStatus.ERROR,
            error=f"DAG {payload.dag_id!r} has no recorded runs",
            source=f"{_base_url()}{API_PREFIX}/dags/{payload.dag_id}/dagRuns",
        )

    path = f"/dags/{payload.dag_id}/dagRuns/{run_id}/taskInstances"
    response = _request("GET", path, params={"limit": 200})
    body = _json(response, f"GET {path}")
    if response.status_code != 200:
        return ToolResult(
            tool="airflow_failed_tasks",
            status=ToolStatus.ERROR,
            error=f"HTTP {response.status_code}: {body}",
            source=f"GET {_base_url()}{API_PREFIX}{path}",
        )

    instances = body.get("task_instances", [])
    summary = [
        {
            "task_id": t.get("task_id"),
            "state": t.get("state"),
            "try_number": t.get("try_number"),
            "duration": t.get("duration"),
            "start_date": t.get("start_date"),
        }
        for t in instances
    ]
    failed = [t for t in summary if t["state"] in {"failed", "upstream_failed"}]
    return ToolResult(
        tool="airflow_failed_tasks",
        status=ToolStatus.OK,
        data={
            "dag_id": payload.dag_id,
            "dag_run_id": run_id,
            "failed_tasks": failed,
            "all_tasks": summary,
            "has_failures": bool(failed),
        },
        source=f"GET {_base_url()}{API_PREFIX}{path}",
    )


def task_log(payload: TaskLogInput) -> ToolResult:
    """Fetch the log of one task attempt, for diagnosing a failure."""
    path = (
        f"/dags/{payload.dag_id}/dagRuns/{payload.dag_run_id}"
        f"/taskInstances/{payload.task_id}/logs/{payload.try_number}"
    )
    response = _request("GET", path, headers={"Accept": "application/json"})
    if response.status_code != 200:
        return ToolResult(
            tool="airflow_task_log",
            status=ToolStatus.ERROR,
            error=f"HTTP {response.status_code} fetching the task log",
            source=f"GET {_base_url()}{API_PREFIX}{path}",
        )

    try:
        content = response.json().get("content", "")
    except ValueError:
        content = response.text
    if isinstance(content, list):
        # Airflow 3 returns structured log chunks rather than one blob of text.
        content = "\n".join(
            str(chunk.get("event", chunk)) if isinstance(chunk, dict) else str(chunk)
            for chunk in content
        )

    text = str(content)
    tail = text[-4000:]
    return ToolResult(
        tool="airflow_task_log",
        status=ToolStatus.OK,
        data={
            "dag_id": payload.dag_id,
            "dag_run_id": payload.dag_run_id,
            "task_id": payload.task_id,
            "try_number": payload.try_number,
            "truncated": len(text) > len(tail),
            "log_tail": tail,
        },
        source=f"GET {_base_url()}{API_PREFIX}{path}",
    )


def register(registry: ToolRegistry) -> None:
    """Add the Airflow tools to the registry."""
    registry.register(
        ToolSpec(
            name="airflow_list_dags",
            description="List the DAGs registered in Airflow, with their paused state.",
            input_model=EmptyInput,
            module=MODULE,
        ),
        list_dags,
    )
    registry.register(
        ToolSpec(
            name="airflow_dag_runs",
            description="Get the status of recent runs of a DAG, newest first.",
            input_model=DagRunsInput,
            module=MODULE,
        ),
        dag_runs,
    )
    registry.register(
        ToolSpec(
            name="airflow_trigger_dag",
            description=(
                "Trigger an approved training DAG. Changes system state: requires confirm=true, "
                "which must come from the user, and the DAG must be on the approved list."
            ),
            input_model=TriggerDagInput,
            mutating=True,
            module=MODULE,
        ),
        trigger_dag,
    )
    registry.register(
        ToolSpec(
            name="airflow_failed_tasks",
            description="List the task instances of a DAG run and identify which ones failed.",
            input_model=DagRunRefInput,
            module=MODULE,
        ),
        failed_tasks,
    )
    registry.register(
        ToolSpec(
            name="airflow_task_log",
            description="Fetch the execution log of a single task attempt for failure diagnosis.",
            input_model=TaskLogInput,
            module=MODULE,
        ),
        task_log,
    )
