#!/usr/bin/env bash
# Injected failure 1: point the ConfigMap at a model file that does not exist.
# The pods still start (liveness passes) but never become ready, so the Service
# loses its endpoints and the API stops answering.
set -uo pipefail

REPO=/mnt/i/MLPAss/assignment-03-mlops-agent
export KUBECONFIG=$REPO/.kube/config
CTX=kind-mlops
NS=mlops

action=${1:-inject}

case "$action" in
  inject)
    echo "### pointing MODEL_PATH at a file that does not exist"
    kubectl --context "$CTX" -n "$NS" patch configmap inference-api-config \
      --type merge -p '{"data":{"MODEL_PATH":"/app/models/does-not-exist.joblib"}}'
    kubectl --context "$CTX" -n "$NS" rollout restart deployment/inference-api
    echo "### waiting for the new pods to be scheduled"
    sleep 45
    kubectl --context "$CTX" -n "$NS" get pods
    echo "### endpoints behind the service"
    kubectl --context "$CTX" -n "$NS" get endpoints inference-api
    ;;
  restore)
    echo "### restoring MODEL_PATH"
    kubectl --context "$CTX" -n "$NS" patch configmap inference-api-config \
      --type merge -p '{"data":{"MODEL_PATH":"/app/models/model.joblib"}}'
    kubectl --context "$CTX" -n "$NS" rollout restart deployment/inference-api
    kubectl --context "$CTX" -n "$NS" rollout status deployment/inference-api --timeout=180s
    kubectl --context "$CTX" -n "$NS" get pods
    ;;
  *)
    echo "usage: $0 [inject|restore]" >&2
    exit 2
    ;;
esac
