"""Safety policy applied before any tool runs.

Two rules matter here:

* Operations that change cluster or pipeline state need explicit confirmation
  from the user. The agent never sets that flag by itself.
* Those operations are restricted to an approved namespace and an approved set
  of DAGs, so neither a typo nor a persuasive prompt can reach production.

The checks run before the call is made, so a refusal never touches the live
system.
"""

from __future__ import annotations

from agent.config import Settings, get_settings
from agent.errors import ConfirmationRequired, PermissionDenied


def resolve_namespace(namespace: str | None, settings: Settings | None = None) -> str:
    """Return the namespace to use, refusing anything outside the allowlist."""
    settings = settings or get_settings()
    target = namespace or settings.kube_namespace
    if target not in settings.approved_namespaces:
        raise PermissionDenied(
            f"namespace {target!r} is not approved; allowed namespaces are {settings.approved_namespaces}"
        )
    return target


def check_dag_approved(dag_id: str, settings: Settings | None = None) -> str:
    """Return the DAG id, refusing any DAG that is not approved for triggering."""
    settings = settings or get_settings()
    if dag_id not in settings.approved_dags:
        raise PermissionDenied(
            f"DAG {dag_id!r} is not approved for triggering; allowed DAGs are {settings.approved_dags}"
        )
    return dag_id


def require_confirmation(confirm: bool, action: str) -> None:
    """Stop a state-changing action the user has not explicitly confirmed."""
    if not confirm:
        raise ConfirmationRequired(
            f"{action} changes system state and needs explicit confirmation; "
            "re-run the tool with confirm=true to proceed"
        )
