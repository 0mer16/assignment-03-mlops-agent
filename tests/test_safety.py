"""Safety policy: unapproved targets and unconfirmed state changes are refused."""

from __future__ import annotations

import pytest

from agent.errors import ConfirmationRequired, PermissionDenied
from agent.registry import build_registry
from agent.safety import check_dag_approved, require_confirmation, resolve_namespace
from agent.schemas import ToolStatus


def test_approved_namespace_resolves():
    assert resolve_namespace("mlops") == "mlops"
    assert resolve_namespace(None) == "mlops"  # falls back to the configured default


def test_unapproved_namespace_is_refused():
    with pytest.raises(PermissionDenied) as exc:
        resolve_namespace("kube-system")
    assert "not approved" in str(exc.value)


def test_unapproved_dag_cannot_be_triggered():
    with pytest.raises(PermissionDenied):
        check_dag_approved("some_other_dag")
    assert check_dag_approved("training_pipeline") == "training_pipeline"


def test_state_change_without_confirmation_is_refused():
    with pytest.raises(ConfirmationRequired):
        require_confirmation(False, "restarting a deployment")
    require_confirmation(True, "restarting a deployment")  # does not raise


def test_unauthorized_kubernetes_modification_is_rejected():
    """A restart aimed at a namespace outside the allowlist never reaches the cluster.

    The policy check runs before any API client is built, so this is refused
    even with no cluster configured at all.
    """
    registry = build_registry()

    result = registry.execute(
        "k8s_restart_deployment",
        {"deployment_name": "inference-api", "namespace": "kube-system", "confirm": True},
        request_id="sec-1",
    )

    assert result.status is ToolStatus.DENIED
    assert "kube-system" in result.error
    assert "not approved" in result.error


def test_restart_requires_explicit_confirmation():
    """Even in the approved namespace, a restart needs the user to confirm."""
    registry = build_registry()

    result = registry.execute(
        "k8s_restart_deployment",
        {"deployment_name": "inference-api", "namespace": "mlops", "confirm": False},
        request_id="sec-2",
    )

    assert result.status is ToolStatus.CONFIRMATION_REQUIRED
    assert "confirmation" in result.error


def test_dag_trigger_is_gated_on_both_approval_and_confirmation():
    registry = build_registry()

    denied = registry.execute("airflow_trigger_dag", {"dag_id": "prod_dag", "confirm": True}, request_id="sec-3")
    assert denied.status is ToolStatus.DENIED

    unconfirmed = registry.execute(
        "airflow_trigger_dag", {"dag_id": "training_pipeline", "confirm": False}, request_id="sec-4"
    )
    assert unconfirmed.status is ToolStatus.CONFIRMATION_REQUIRED
