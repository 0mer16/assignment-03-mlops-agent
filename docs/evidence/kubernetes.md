# Kubernetes evidence

Exported 2026-11-15 18:33 UTC from the kind cluster `kind-mlops`

## `kubectl get nodes`
```
NAME                  STATUS   ROLES           AGE   VERSION
mlops-control-plane   Ready    control-plane   35m   v1.34.0
```

## `kubectl -n mlops get deploy,pods,svc,configmap -o wide`
```
NAME                            READY   UP-TO-DATE   AVAILABLE   AGE   CONTAINERS   IMAGES                      SELECTOR
deployment.apps/inference-api   2/2     2            2           24m   api          mlops-inference-api:1.0.0   app=inference-api

NAME                                 READY   STATUS    RESTARTS   AGE     IP            NODE                  NOMINATED NODE   READINESS GATES
pod/inference-api-7d8669fd97-bxlrk   1/1     Running   0          7m47s   10.244.0.11   mlops-control-plane   <none>           <none>
pod/inference-api-7d8669fd97-tv4zx   1/1     Running   0          8m8s    10.244.0.10   mlops-control-plane   <none>           <none>

NAME                    TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)   AGE   SELECTOR
service/inference-api   ClusterIP   10.96.188.123   <none>        80/TCP    24m   app=inference-api

NAME                             DATA   AGE
configmap/inference-api-config   4      24m
configmap/kube-root-ca.crt       1      23m
```

## `kubectl -n mlops describe deployment inference-api`
```
RollingUpdateStrategy:  25% max unavailable, 25% max surge
Pod Template:
  Labels:       app=inference-api
  Annotations:  kubectl.kubernetes.io/restartedAt: 2026-11-15T23:25:11+05:00
  Containers:
   api:
    Image:      mlops-inference-api:1.0.0
    Port:       8000/TCP (http)
    Host Port:  0/TCP (http)
    Limits:
      cpu:     500m
      memory:  512Mi
    Requests:
      cpu:      100m
      memory:   192Mi
    Liveness:   http-get http://:http/health delay=10s timeout=3s period=20s successThreshold=1 failureThreshold=3
    Readiness:  http-get http://:http/ready delay=5s timeout=3s period=10s successThreshold=1 failureThreshold=3
    Environment Variables from:
      inference-api-config  ConfigMap  Optional: false
    Environment:            <none>
    Mounts:                 <none>
  Volumes:                  <none>
  Node-Selectors:           <none>
  Tolerations:              <none>
Conditions:
  Type           Status  Reason
  ----           ------  ------
  Available      True    MinimumReplicasAvailable
  Progressing    True    NewReplicaSetAvailable
OldReplicaSets:  inference-api-6f787b46bc (0/0 replicas created), inference-api-5b5b499968 (0/0 replicas created), inference-api-f988bc7d6 (0/0 replicas created)
NewReplicaSet:   inference-api-7d8669fd97 (2/2 replicas created)
Events:
  Type    Reason             Age    From                   Message
  ----    ------             ----   ----                   -------
  Normal  ScalingReplicaSet  23m    deployment-controller  Scaled up replica set inference-api-6f787b46bc from 0 to 2
  Normal  ScalingReplicaSet  16m    deployment-controller  Scaled up replica set inference-api-5b5b499968 from 0 to 1
  Normal  ScalingReplicaSet  15m    deployment-controller  Scaled down replica set inference-api-6f787b46bc from 2 to 1
  Normal  ScalingReplicaSet  15m    deployment-controller  Scaled up replica set inference-api-5b5b499968 from 1 to 2
  Normal  ScalingReplicaSet  15m    deployment-controller  Scaled down replica set inference-api-6f787b46bc from 1 to 0
  Normal  ScalingReplicaSet  10m    deployment-controller  Scaled up replica set inference-api-f988bc7d6 from 0 to 1
  Normal  ScalingReplicaSet  8m9s   deployment-controller  Scaled down replica set inference-api-f988bc7d6 from 1 to 0
  Normal  ScalingReplicaSet  8m9s   deployment-controller  Scaled up replica set inference-api-7d8669fd97 from 0 to 1
  Normal  ScalingReplicaSet  7m48s  deployment-controller  Scaled down replica set inference-api-5b5b499968 from 2 to 1
  Normal  ScalingReplicaSet  7m47s  deployment-controller  Scaled up replica set inference-api-7d8669fd97 from 1 to 2
  Normal  ScalingReplicaSet  7m30s  deployment-controller  (combined from similar events): Scaled down replica set inference-api-5b5b499968 from 1 to 0
```

## `kubectl -n mlops get events --sort-by=.lastTimestamp`
```
18m         Normal    Pulled              pod/inference-api-6f787b46bc-cmfj4    Container image "mlops-inference-api:1.0.0" already present on machine
16m         Normal    ScalingReplicaSet   deployment/inference-api              Scaled up replica set inference-api-5b5b499968 from 0 to 1
16m         Normal    SuccessfulCreate    replicaset/inference-api-5b5b499968   Created pod: inference-api-5b5b499968-4wv6v
16m         Normal    Scheduled           pod/inference-api-5b5b499968-4wv6v    Successfully assigned mlops/inference-api-5b5b499968-4wv6v to mlops-control-plane
16m         Normal    Pulled              pod/inference-api-5b5b499968-4wv6v    Container image "mlops-inference-api:1.0.0" already present on machine
16m         Normal    Created             pod/inference-api-5b5b499968-4wv6v    Created container: api
16m         Normal    Started             pod/inference-api-5b5b499968-4wv6v    Started container api
15m         Normal    SuccessfulCreate    replicaset/inference-api-5b5b499968   Created pod: inference-api-5b5b499968-4s4xc
15m         Normal    ScalingReplicaSet   deployment/inference-api              Scaled down replica set inference-api-6f787b46bc from 2 to 1
15m         Normal    ScalingReplicaSet   deployment/inference-api              Scaled up replica set inference-api-5b5b499968 from 1 to 2
15m         Normal    SuccessfulDelete    replicaset/inference-api-6f787b46bc   Deleted pod: inference-api-6f787b46bc-lgkx9
15m         Normal    Scheduled           pod/inference-api-5b5b499968-4s4xc    Successfully assigned mlops/inference-api-5b5b499968-4s4xc to mlops-control-plane
15m         Normal    Pulled              pod/inference-api-5b5b499968-4s4xc    Container image "mlops-inference-api:1.0.0" already present on machine
15m         Normal    Started             pod/inference-api-5b5b499968-4s4xc    Started container api
15m         Normal    Created             pod/inference-api-5b5b499968-4s4xc    Created container: api
15m         Normal    ScalingReplicaSet   deployment/inference-api              Scaled down replica set inference-api-6f787b46bc from 1 to 0
15m         Normal    SuccessfulDelete    replicaset/inference-api-6f787b46bc   Deleted pod: inference-api-6f787b46bc-cmfj4
10m         Normal    ScalingReplicaSet   deployment/inference-api              Scaled up replica set inference-api-f988bc7d6 from 0 to 1
10m         Normal    SuccessfulCreate    replicaset/inference-api-f988bc7d6    Created pod: inference-api-f988bc7d6-jmhnj
10m         Normal    Scheduled           pod/inference-api-f988bc7d6-jmhnj     Successfully assigned mlops/inference-api-f988bc7d6-jmhnj to mlops-control-plane
10m         Normal    Created             pod/inference-api-f988bc7d6-jmhnj     Created container: api
10m         Normal    Pulled              pod/inference-api-f988bc7d6-jmhnj     Container image "mlops-inference-api:1.0.0" already present on machine
10m         Normal    Started             pod/inference-api-f988bc7d6-jmhnj     Started container api
8m9s        Normal    SuccessfulDelete    replicaset/inference-api-f988bc7d6    Deleted pod: inference-api-f988bc7d6-jmhnj
8m9s        Normal    ScalingReplicaSet   deployment/inference-api              Scaled up replica set inference-api-7d8669fd97 from 0 to 1
8m9s        Normal    Killing             pod/inference-api-f988bc7d6-jmhnj     Stopping container api
8m9s        Warning   Unhealthy           pod/inference-api-f988bc7d6-jmhnj     Readiness probe failed: HTTP probe failed with statuscode: 503
8m9s        Normal    ScalingReplicaSet   deployment/inference-api              Scaled down replica set inference-api-f988bc7d6 from 1 to 0
8m8s        Normal    SuccessfulCreate    replicaset/inference-api-7d8669fd97   Created pod: inference-api-7d8669fd97-tv4zx
8m7s        Normal    Scheduled           pod/inference-api-7d8669fd97-tv4zx    Successfully assigned mlops/inference-api-7d8669fd97-tv4zx to mlops-control-plane
8m2s        Normal    Pulled              pod/inference-api-7d8669fd97-tv4zx    Container image "mlops-inference-api:1.0.0" already present on machine
8m          Normal    Started             pod/inference-api-7d8669fd97-tv4zx    Started container api
8m          Normal    Created             pod/inference-api-7d8669fd97-tv4zx    Created container: api
7m48s       Normal    ScalingReplicaSet   deployment/inference-api              Scaled down replica set inference-api-5b5b499968 from 2 to 1
7m47s       Normal    SuccessfulDelete    replicaset/inference-api-5b5b499968   Deleted pod: inference-api-5b5b499968-4wv6v
7m47s       Normal    SuccessfulCreate    replicaset/inference-api-7d8669fd97   Created pod: inference-api-7d8669fd97-bxlrk
7m47s       Normal    Scheduled           pod/inference-api-7d8669fd97-bxlrk    Successfully assigned mlops/inference-api-7d8669fd97-bxlrk to mlops-control-plane
7m47s       Normal    Killing             pod/inference-api-5b5b499968-4wv6v    Stopping container api
7m47s       Normal    ScalingReplicaSet   deployment/inference-api              Scaled up replica set inference-api-7d8669fd97 from 1 to 2
7m43s       Normal    Pulled              pod/inference-api-7d8669fd97-bxlrk    Container image "mlops-inference-api:1.0.0" already present on machine
7m42s       Normal    Started             pod/inference-api-7d8669fd97-bxlrk    Started container api
7m42s       Normal    Created             pod/inference-api-7d8669fd97-bxlrk    Created container: api
7m30s       Normal    SuccessfulDelete    replicaset/inference-api-5b5b499968   Deleted pod: inference-api-5b5b499968-4s4xc
7m30s       Normal    Killing             pod/inference-api-5b5b499968-4s4xc    Stopping container api
7m30s       Normal    ScalingReplicaSet   deployment/inference-api              (combined from similar events): Scaled down replica set inference-api-5b5b499968 from 1 to 0
```

## Readiness and liveness probes as deployed
```yaml
(deployment not available)
```

## Application log
```
INFO:     10.244.0.1:47102 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:47986 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:57512 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:57522 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:49712 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:54724 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:54730 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:32952 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:54814 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:54826 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:50358 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:46730 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:46736 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:50088 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:57712 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:57714 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:59888 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:48546 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:48556 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:50214 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:35838 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:35846 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:38972 - "GET /ready HTTP/1.1" 200 OK
INFO:     10.244.0.1:44952 - "GET /health HTTP/1.1" 200 OK
INFO:     10.244.0.1:44954 - "GET /ready HTTP/1.1" 200 OK
```
