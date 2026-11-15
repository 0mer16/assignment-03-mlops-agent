"""Kubernetes operations tools.

Every call resolves its namespace through the safety policy first, so the agent
can only look at (and only act on) approved namespaces. Restarting a deployment
additionally needs confirm=true.

The read tools are deliberately shaped around diagnosis: pod phase, container
readiness, restart counts and warning events are the things that actually
explain why an inference API is not serving traffic.
"""

from __future__ import annotations

from typing import Any

from agent.config import get_settings
from agent.errors import ToolUnavailable
from agent.registry import ToolRegistry
from agent.safety import require_confirmation, resolve_namespace
from agent.schemas import (
    DeploymentInput,
    NamespaceInput,
    PodLogsInput,
    RestartDeploymentInput,
    ServiceInput,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

MODULE = "kubernetes"


def _api():
    """Load kube config and return (CoreV1Api, AppsV1Api, ApiException)."""
    try:
        from kubernetes import client, config
        from kubernetes.client.rest import ApiException
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ToolUnavailable(f"the kubernetes client library is not installed: {exc}") from exc

    try:
        config.load_kube_config()
    except Exception:  # noqa: BLE001 - fall back to in-cluster credentials
        try:
            config.load_incluster_config()
        except Exception as exc:  # noqa: BLE001
            raise ToolUnavailable(
                f"no usable kubeconfig: {exc}. Set KUBECONFIG to a context that can reach the cluster."
            ) from exc

    return client.CoreV1Api(), client.AppsV1Api(), ApiException


def _call(fn, *args, **kwargs):
    """Run a client call, separating 'cluster unreachable' from 'object missing'."""
    from urllib3.exceptions import HTTPError

    try:
        return fn(*args, **kwargs)
    except HTTPError as exc:
        raise ToolUnavailable(f"cannot reach the Kubernetes API server: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        status = getattr(exc, "status", None)
        if status in (401, 403):
            raise ToolUnavailable(f"the kubeconfig credentials were rejected (HTTP {status})") from exc
        if status is None:
            raise ToolUnavailable(f"Kubernetes API call failed: {type(exc).__name__}: {exc}") from exc
        raise


def _pod_summary(pod) -> dict[str, Any]:
    statuses = pod.status.container_statuses or []
    return {
        "name": pod.metadata.name,
        "phase": pod.status.phase,
        "ready": all(c.ready for c in statuses) if statuses else False,
        "restarts": sum(c.restart_count for c in statuses),
        "node": pod.spec.node_name,
        "start_time": str(pod.status.start_time) if pod.status.start_time else None,
        "images": [c.image for c in pod.spec.containers],
        "container_states": {
            c.name: (
                "running"
                if c.state and c.state.running
                else (c.state.waiting.reason if c.state and c.state.waiting else None)
                or (c.state.terminated.reason if c.state and c.state.terminated else None)
                or "unknown"
            )
            for c in statuses
        },
    }


def list_pods(payload: NamespaceInput) -> ToolResult:
    """List the pods in the application namespace."""
    namespace = resolve_namespace(payload.namespace)
    core, _, _ = _api()
    pods = _call(core.list_namespaced_pod, namespace=namespace)
    summaries = [_pod_summary(p) for p in pods.items]
    return ToolResult(
        tool="k8s_list_pods",
        status=ToolStatus.OK,
        data={
            "namespace": namespace,
            "pods": summaries,
            "count": len(summaries),
            "ready_count": sum(1 for p in summaries if p["ready"]),
        },
        source=f"CoreV1Api.list_namespaced_pod(namespace={namespace})",
    )


def list_deployments(payload: NamespaceInput) -> ToolResult:
    """List deployments and how many replicas are actually ready."""
    namespace = resolve_namespace(payload.namespace)
    _, apps, _ = _api()
    deployments = _call(apps.list_namespaced_deployment, namespace=namespace)
    entries = [
        {
            "name": d.metadata.name,
            "desired_replicas": d.spec.replicas,
            "ready_replicas": d.status.ready_replicas or 0,
            "available_replicas": d.status.available_replicas or 0,
            "images": [c.image for c in d.spec.template.spec.containers],
        }
        for d in deployments.items
    ]
    return ToolResult(
        tool="k8s_list_deployments",
        status=ToolStatus.OK,
        data={"namespace": namespace, "deployments": entries, "count": len(entries)},
        source=f"AppsV1Api.list_namespaced_deployment(namespace={namespace})",
    )


def pod_logs(payload: PodLogsInput) -> ToolResult:
    """Fetch the tail of a pod's log."""
    namespace = resolve_namespace(payload.namespace)
    core, _, ApiException = _api()
    try:
        logs = _call(
            core.read_namespaced_pod_log,
            name=payload.pod_name,
            namespace=namespace,
            tail_lines=payload.tail_lines,
        )
    except ApiException as exc:
        if exc.status == 404:
            return ToolResult(
                tool="k8s_pod_logs",
                status=ToolStatus.ERROR,
                error=f"pod {payload.pod_name!r} does not exist in namespace {namespace!r}",
                source=f"CoreV1Api.read_namespaced_pod_log({payload.pod_name})",
            )
        raise

    return ToolResult(
        tool="k8s_pod_logs",
        status=ToolStatus.OK,
        data={
            "namespace": namespace,
            "pod": payload.pod_name,
            "lines_requested": payload.tail_lines,
            "log_tail": logs[-4000:],
        },
        source=f"CoreV1Api.read_namespaced_pod_log({payload.pod_name}, tail_lines={payload.tail_lines})",
    )


def describe_service(payload: ServiceInput) -> ToolResult:
    """Inspect a Service and the endpoints currently behind it.

    A Service with no endpoints is the usual reason an API is unreachable while
    the deployment still looks present, so both are reported together.
    """
    namespace = resolve_namespace(payload.namespace)
    core, _, ApiException = _api()
    try:
        service = _call(core.read_namespaced_service, name=payload.service_name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            return ToolResult(
                tool="k8s_describe_service",
                status=ToolStatus.ERROR,
                error=f"service {payload.service_name!r} does not exist in namespace {namespace!r}",
                source=f"CoreV1Api.read_namespaced_service({payload.service_name})",
            )
        raise

    endpoint_addresses: list[str] = []
    try:
        endpoints = _call(core.read_namespaced_endpoints, name=payload.service_name, namespace=namespace)
        for subset in endpoints.subsets or []:
            endpoint_addresses.extend(a.ip for a in (subset.addresses or []))
    except ApiException:
        endpoint_addresses = []

    return ToolResult(
        tool="k8s_describe_service",
        status=ToolStatus.OK,
        data={
            "namespace": namespace,
            "name": service.metadata.name,
            "type": service.spec.type,
            "cluster_ip": service.spec.cluster_ip,
            "selector": service.spec.selector,
            "ports": [{"port": p.port, "target_port": str(p.target_port), "protocol": p.protocol} for p in service.spec.ports or []],
            "endpoint_addresses": endpoint_addresses,
            "has_endpoints": bool(endpoint_addresses),
        },
        source=f"CoreV1Api.read_namespaced_service({payload.service_name})",
    )


def unhealthy_pods(payload: NamespaceInput) -> ToolResult:
    """Find pods that are not ready or are restarting, with the warning events behind it."""
    namespace = resolve_namespace(payload.namespace)
    core, _, _ = _api()
    pods = _call(core.list_namespaced_pod, namespace=namespace)
    summaries = [_pod_summary(p) for p in pods.items]
    unhealthy = [p for p in summaries if not p["ready"] or p["restarts"] > 0]

    events = _call(core.list_namespaced_event, namespace=namespace)
    warnings = [
        {
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "reason": e.reason,
            "message": (e.message or "")[:300],
            "count": e.count,
            "last_seen": str(e.last_timestamp) if e.last_timestamp else None,
        }
        for e in events.items
        if e.type == "Warning"
    ]
    warnings = warnings[-15:]

    return ToolResult(
        tool="k8s_unhealthy_pods",
        status=ToolStatus.OK,
        data={
            "namespace": namespace,
            "unhealthy_pods": unhealthy,
            "healthy_count": len(summaries) - len(unhealthy),
            "total_pods": len(summaries),
            "warning_events": warnings,
            "all_healthy": not unhealthy,
        },
        source=f"CoreV1Api.list_namespaced_pod + list_namespaced_event(namespace={namespace})",
    )


def deployed_version(payload: DeploymentInput) -> ToolResult:
    """Report which application version is actually deployed.

    The image tag is the authoritative answer; the ConfigMap value is reported
    next to it so a mismatch between the two is visible rather than hidden.
    """
    settings = get_settings()
    namespace = resolve_namespace(payload.namespace)
    name = payload.deployment_name or settings.kube_deployment
    core, apps, ApiException = _api()

    try:
        deployment = _call(apps.read_namespaced_deployment, name=name, namespace=namespace)
    except ApiException as exc:
        if exc.status == 404:
            return ToolResult(
                tool="k8s_deployed_version",
                status=ToolStatus.ERROR,
                error=f"deployment {name!r} does not exist in namespace {namespace!r}",
                source=f"AppsV1Api.read_namespaced_deployment({name})",
            )
        raise

    images = [c.image for c in deployment.spec.template.spec.containers]
    image_tags = [img.rsplit(":", 1)[-1] if ":" in img else "latest" for img in images]

    configmap_version = None
    try:
        cm = _call(core.read_namespaced_config_map, name=f"{name}-config", namespace=namespace)
        configmap_version = (cm.data or {}).get("APP_VERSION")
    except ApiException:
        configmap_version = None

    return ToolResult(
        tool="k8s_deployed_version",
        status=ToolStatus.OK,
        data={
            "namespace": namespace,
            "deployment": name,
            "images": images,
            "image_tags": image_tags,
            "configmap_app_version": configmap_version,
            "version_matches_configmap": (configmap_version in image_tags) if configmap_version else None,
            "desired_replicas": deployment.spec.replicas,
            "ready_replicas": deployment.status.ready_replicas or 0,
            "fully_available": (deployment.status.ready_replicas or 0) == deployment.spec.replicas,
        },
        source=f"AppsV1Api.read_namespaced_deployment({name}) + ConfigMap {name}-config",
    )


def restart_deployment(payload: RestartDeploymentInput) -> ToolResult:
    """Roll-restart a deployment. Changes cluster state, so it is gated."""
    settings = get_settings()
    namespace = resolve_namespace(payload.namespace)
    name = payload.deployment_name or settings.kube_deployment
    require_confirmation(payload.confirm, f"restarting deployment {name!r} in namespace {namespace!r}")

    import datetime

    _, apps, ApiException = _api()
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    body = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": timestamp}}
            }
        }
    }
    try:
        _call(apps.patch_namespaced_deployment, name=name, namespace=namespace, body=body)
    except ApiException as exc:
        if exc.status == 404:
            return ToolResult(
                tool="k8s_restart_deployment",
                status=ToolStatus.ERROR,
                error=f"deployment {name!r} does not exist in namespace {namespace!r}",
                source=f"AppsV1Api.patch_namespaced_deployment({name})",
            )
        raise

    return ToolResult(
        tool="k8s_restart_deployment",
        status=ToolStatus.OK,
        data={
            "namespace": namespace,
            "deployment": name,
            "restarted_at": timestamp,
            "note": "rollout triggered; check k8s_list_pods for the new pods coming up",
        },
        source=f"AppsV1Api.patch_namespaced_deployment({name})",
    )


def register(registry: ToolRegistry) -> None:
    """Add the Kubernetes tools to the registry."""
    registry.register(
        ToolSpec(
            name="k8s_list_pods",
            description="List pods in the approved application namespace with phase, readiness and restart counts.",
            input_model=NamespaceInput,
            module=MODULE,
        ),
        list_pods,
    )
    registry.register(
        ToolSpec(
            name="k8s_list_deployments",
            description="List deployments with desired and ready replica counts.",
            input_model=NamespaceInput,
            module=MODULE,
        ),
        list_deployments,
    )
    registry.register(
        ToolSpec(
            name="k8s_pod_logs",
            description="Fetch the tail of a pod's container log.",
            input_model=PodLogsInput,
            module=MODULE,
        ),
        pod_logs,
    )
    registry.register(
        ToolSpec(
            name="k8s_describe_service",
            description="Inspect a Service, its ports and whether it currently has any endpoints behind it.",
            input_model=ServiceInput,
            module=MODULE,
        ),
        describe_service,
    )
    registry.register(
        ToolSpec(
            name="k8s_unhealthy_pods",
            description="Identify pods that are not ready or are restarting, together with recent warning events.",
            input_model=NamespaceInput,
            module=MODULE,
        ),
        unhealthy_pods,
    )
    registry.register(
        ToolSpec(
            name="k8s_deployed_version",
            description="Report the application version currently deployed, from the image tag and the ConfigMap.",
            input_model=DeploymentInput,
            module=MODULE,
        ),
        deployed_version,
    )
    registry.register(
        ToolSpec(
            name="k8s_restart_deployment",
            description=(
                "Restart a deployment. Changes cluster state: restricted to the approved namespace and "
                "requires confirm=true, which must come from the user."
            ),
            input_model=RestartDeploymentInput,
            mutating=True,
            module=MODULE,
        ),
        restart_deployment,
    )
