# Kubernetes Deployment

Kustomize-based deployment for pydantic-harness. See `docs/k8s-migration-design.md`
for design decisions, `docs/k8s-deploy-guide.md` for full deployment runbook,
and `docs/k8s-upgrade-guide.md` for upgrade procedures.

## Layout

```
deploy/k8s/
├── kind-cluster.yaml          # Local kind cluster config
├── base/                      # Common resources for all environments
│   ├── namespace.yaml
│   ├── limitrange.yaml
│   ├── sandbox/               # Sandbox Deployment + Service + PVC + NetworkPolicy
│   ├── backend/               # Backend Deployment + Service + Ingress + PDB + NP
│   ├── frontend/              # Frontend Deployment + Service + Ingress + NP
│   └── kustomization.yaml     # configMapGenerator + image refs
└── overlays/
    ├── dev/                   # Local kind / minikube
    └── prod/                  # Real cluster (host, resources, image tags)
```

## Quickstart (local kind)

```bash
# 1. Create kind cluster (one-time)
kind create cluster --config deploy/k8s/kind-cluster.yaml

# 2. Install NGINX Ingress (one-time)
helm upgrade --install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  -n ingress-nginx --create-namespace \
  --set controller.hostPort.enabled=true

# 3. Build + load images
docker build -t pydantic-harness/sandbox:dev   -f sandbox_service/Dockerfile .
docker build -t pydantic-harness/backend:dev   -f main_agent/Dockerfile .
docker build -t pydantic-harness/frontend:dev   frontend/
kind load docker-image \
  pydantic-harness/sandbox:dev \
  pydantic-harness/backend:dev \
  pydantic-harness/frontend:dev \
  --name pydantic-harness

# 4. Create Secret (do NOT commit values)
kubectl create namespace pydantic-harness
kubectl -n pydantic-harness create secret generic backend-secrets \
  --from-literal=SANDBOX_TOKEN="dev-token" \
  --from-literal=DEEPSEEK_API_KEY="sk-..."

# 5. Deploy
# The configMapGenerator references main_agent/config.yaml which lives outside
# the kustomization root. Kustomize blocks this by default; pass
# --load-restrictor=LoadRestrictionsNone via the pipe form:
kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev \
  | kubectl apply -f -

# 6. Verify
kubectl -n pydantic-harness get pods
kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev | less

# 7. Access
echo "127.0.0.1 harness.local" | sudo tee -a /etc/hosts
# Windows: add the line to C:\Windows\System32\drivers\etc\hosts
# Then open http://harness.local/ in browser.
```

## Day-to-day operations

| Task | Command |
|---|---|
| Preview rendered YAML | `kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev` |
| Apply changes | `kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev \| kubectl apply -f -` |
| Diff before apply | `kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev \| kubectl diff -f -` |
| Bump an image tag | `cd deploy/k8s/overlays/dev && kustomize edit set image pydantic-harness/backend=pydantic-harness/backend:<tag>` |
| Force restart (same tag) | `kubectl -n pydantic-harness rollout restart deploy/<name>` |
| Rollback | `kubectl -n pydantic-harness rollout undo deploy/<name>` |
| Logs | `kubectl -n pydantic-harness logs deploy/<name> -f` |
| Tear down (keep cluster) | `kubectl delete -k deploy/k8s/overlays/dev` |
| Destroy cluster | `kind delete cluster --name pydantic-harness` |

## Known gaps / TODOs

- **`main_agent/config.yaml` contains `llm.api_key` inline** → it gets rendered into a plain ConfigMap, visible to anyone with namespace read access. Pre-existing repo issue (also in git history). To fix: change `config.py` to read `api_key` from env var, then keep the secret in `backend-secrets` only.
- **backend has no `/health` endpoint** → using TCP probe; replace with HTTP probe once endpoint exists.
- **kind default CNI (kindnet) does not enforce NetworkPolicy** → policies are documented intent only on kind; effective on real clusters with Calico/Cilium.
- **prod overlay hostname placeholder** is `harness.example.com` → patch in `overlays/prod/ingress-host-patch.yaml`.
- **gVisor on K8s** not enabled. To opt in, install runsc + RuntimeClass and add `runtimeClassName: gvisor` to sandbox Pod spec.
- **`--load-restrictor=LoadRestrictionsNone` required** because `configMapGenerator` reads `main_agent/config.yaml` outside the kustomize root. Alternative: copy/symlink the file into `deploy/k8s/base/backend/`.
