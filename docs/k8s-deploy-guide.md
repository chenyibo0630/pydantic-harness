# Kubernetes 部署 Runbook

> 配套文档：`docs/k8s-migration-design.md`（设计决策）
> 状态：草案 · 2026-05-12

本文回答"K8s yaml 落完之后怎么部署"。按顺序执行即可。

## 前置选择：集群在哪

| 场景 | 推荐 | 说明 |
|---|---|---|
| 本地开发/试跑 | **kind**（Docker in Docker）| 单节点，启动快，适合 dev overlay |
| 想离线常驻 | k3s 或 minikube | k3s 更轻，minikube 工具链完善 |
| 已有云集群 | 直连即可 | 跳过集群创建步骤 |

下面以 **kind** 为本地基线写。换成其他集群只是替换"集群创建"那一步，后面流程不变。

## 0. 一次性准备（每台机器装一次）

```bash
# Windows / WSL2 / Linux 都可
# 工具：kubectl + kind + helm（仅用来装 NGINX Ingress）

# 检查是否已装
kubectl version --client
kind version
helm version

# 没装的话（示例命令，按你的包管理器走）
# choco install kubernetes-cli kind kubernetes-helm     # Windows + Chocolatey
# brew install kubectl kind helm                         # macOS
# 其他：参考 https://kind.sigs.k8s.io/docs/user/quick-start/
```

## 1. 创建本地 kind 集群

写一份 kind 配置，把 80/443 端口映射到宿主机，方便 Ingress 直连：

```yaml
# deploy/k8s/kind-cluster.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: pydantic-harness
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 80
        hostPort: 80
        protocol: TCP
      - containerPort: 443
        hostPort: 443
        protocol: TCP
    # 可选：把 workspace 目录从 Windows 直接挂进 kind 节点
    extraMounts:
      - hostPath: D:/develop/learning/harness-workspace
        containerPath: /mnt/workspace
```

创建集群：

```bash
kind create cluster --config deploy/k8s/kind-cluster.yaml

# 验证
kubectl cluster-info --context kind-pydantic-harness
kubectl get nodes
```

销毁：`kind delete cluster --name pydantic-harness`

## 2. 安装 NGINX Ingress Controller

用 Helm 一行装好（**这是 Helm 唯一用得到的地方**，自己的应用还是用 Kustomize）：

```bash
helm upgrade --install ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=NodePort \
  --set controller.hostPort.enabled=true

# 等 controller ready
kubectl -n ingress-nginx wait --for=condition=ready pod \
  -l app.kubernetes.io/component=controller --timeout=120s
```

验证：`curl -I http://localhost`（应返回 404，证明 ingress 已工作）。

## 3. 构建镜像

我们有三个 Dockerfile：

```bash
docker build -t pydantic-harness/sandbox:dev   -f sandbox_service/Dockerfile .
docker build -t pydantic-harness/backend:dev   -f main_agent/Dockerfile .
docker build -t pydantic-harness/frontend:dev   frontend/
```

**把镜像送进 kind 节点**（kind 不会拉本地 docker daemon 里的镜像，必须显式 load）：

```bash
kind load docker-image \
  pydantic-harness/sandbox:dev \
  pydantic-harness/backend:dev \
  pydantic-harness/frontend:dev \
  --name pydantic-harness
```

> 真实远程集群替换为 `docker push <registry>/...` 并在 yaml 里改 image 字段。

## 4. 准备 Secret（不进 git）

Kustomize base 里只有 `secret.example.yaml` 模板。真值用 `kubectl` 直接创建：

```bash
kubectl create namespace pydantic-harness

kubectl -n pydantic-harness create secret generic backend-secrets \
  --from-literal=SANDBOX_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=OPENAI_API_KEY="sk-..." \
  --from-literal=DEEPSEEK_API_KEY="..."
  # 按 main_agent/config.yaml 实际引用的 provider 加
```

> Windows PowerShell 下 `openssl rand` 不可用，改用 `[guid]::NewGuid().ToString("N")` 或自定义生成。

验证：`kubectl -n pydantic-harness get secret backend-secrets`

## 5. 部署应用（核心一行）

```bash
kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev \
  | kubectl apply -f -
```

> 为什么不是 `kubectl apply -k`？因为 `base/kustomization.yaml` 的 `configMapGenerator` 引用了项目根目录的 `main_agent/config.yaml`，超出了 Kustomize 默认的安全沙盒。`--load-restrictor=LoadRestrictionsNone` 放开这个限制；用管道方式才能传该 flag（`kubectl apply -k` 不支持）。

Kustomize 会把 base + dev overlay 合并出最终 yaml，然后 `kubectl apply` 全部资源。

实时观察：

```bash
kubectl -n pydantic-harness get pods -w
```

期望最终看到（3 个 Pod 全 Ready）：

```
NAME                        READY   STATUS    RESTARTS   AGE
backend-xxxx-yyyy           1/1     Running   0          1m
frontend-xxxx-yyyy          1/1     Running   0          1m
sandbox-xxxx-yyyy           1/1     Running   0          1m
```

## 6. 验证连通性

### 6.1 Pod 互联

```bash
# backend 是否能访问 sandbox
kubectl -n pydantic-harness exec deploy/backend -- \
  curl -sf http://sandbox:8100/health
```

### 6.2 Ingress 访问

```bash
# Host 头要匹配 Ingress 配置（dev overlay 应该把 host 设为 harness.local）
curl -H "Host: harness.local" http://localhost/        # 前端
curl -H "Host: harness.local" http://localhost/api/... # 后端 API
```

Windows 浏览器访问，需要在 `C:\Windows\System32\drivers\etc\hosts` 加：
```
127.0.0.1 harness.local
```
然后浏览器打开 `http://harness.local/`。

### 6.3 SSE 流式

最关键的回归项。直接打开前端发起一次对话，观察 token 是否连续流出。如果出现"等几秒一坨"的情况，说明 Ingress 注解没生效（回到设计文档 §5.2 检查）。

或用 `curl` 验证（应该看到事件逐行打印，而不是一次性返回）：

```bash
curl -N -H "Host: harness.local" \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}' \
  http://localhost/chat/stream
```

`-N` 强制不缓冲，能立刻看出服务端/Ingress 是否在缓冲。

## 7. 日常迭代流程

改代码后的最小循环：

```bash
# A. 改 backend 代码
docker build -t pydantic-harness/backend:dev -f main_agent/Dockerfile .
kind load docker-image pydantic-harness/backend:dev --name pydantic-harness

# B. 触发滚动更新（镜像 tag 不变时 kubectl 不会重启 Pod，必须显式 kick）
kubectl -n pydantic-harness rollout restart deploy/backend

# C. 等 ready
kubectl -n pydantic-harness rollout status deploy/backend
```

> 更规范的做法是每次构建打新 tag（如 `:dev-20260512-1430`），在 `overlays/dev/kustomization.yaml` 的 `images:` 段更新 tag，再 `kubectl apply -k`。这样 K8s 自动滚动更新且可回滚。

改 K8s yaml（base 或 overlay）：

```bash
# 预览将要 apply 的最终 yaml
kubectl kustomize deploy/k8s/overlays/dev

# 应用
kubectl apply -k deploy/k8s/overlays/dev
```

改 Secret：

```bash
kubectl -n pydantic-harness create secret generic backend-secrets \
  --from-literal=OPENAI_API_KEY="新值" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n pydantic-harness rollout restart deploy/backend
```

## 8. 排查与速查命令

| 现象 | 命令 |
|---|---|
| Pod 起不来 | `kubectl -n pydantic-harness describe pod <name>` 看 Events |
| 看日志 | `kubectl -n pydantic-harness logs deploy/<name> -f` |
| 看上一次崩溃日志 | `kubectl logs <pod> --previous` |
| 进 Pod | `kubectl -n pydantic-harness exec -it deploy/<name> -- sh` |
| Ingress 不通 | `kubectl -n ingress-nginx logs deploy/ingress-nginx-controller` |
| 检查 SSE 注解是否注入 | `kubectl -n pydantic-harness get ingress -o yaml \| grep nginx` |
| 资源被 PodSecurity 拒 | `kubectl -n pydantic-harness get events --sort-by=.lastTimestamp` |
| NetworkPolicy 误杀 | 先 `kubectl delete networkpolicy --all -n pydantic-harness` 排除嫌疑 |
| 看 PVC 状态 | `kubectl -n pydantic-harness get pvc` |
| 看 Pod 资源占用 | `kubectl -n pydantic-harness top pod`（需 metrics-server）|
| 强删卡住的资源 | `kubectl delete <kind> <name> --force --grace-period=0` |

## 9. 拆撤

```bash
# 删除应用（保留集群）
kubectl delete -k deploy/k8s/overlays/dev

# 注意：PVC 删除策略视 StorageClass 而定，workspace 数据可能保留
kubectl -n pydantic-harness get pvc
kubectl -n pydantic-harness delete pvc workspace-pvc   # 如确认要清

# 干掉整个集群
kind delete cluster --name pydantic-harness
```

## 10. 生产集群（如有）

跟 dev 几乎一样，差异：

| 项 | dev | prod |
|---|---|---|
| 集群 | kind | 云厂商托管 K8s / 自建 |
| Ingress host | `harness.local` | 真实域名 + cert-manager 自动 TLS |
| 镜像来源 | `kind load` | 推到 registry（如 GHCR / 阿里云 ACR）|
| Secret 注入 | 手动 `kubectl create` | 外部秘钥系统（External Secrets Operator / SOPS）|
| StorageClass | kind 默认 | 集群默认 SC（CSI driver）|
| 资源限制 | 见 base 默认 | `overlays/prod/resources-patch.yaml` 调大 |

部署命令本身仍然是：

```bash
kubectl apply -k deploy/k8s/overlays/prod
```

## 11. 一页流程图

```
首次：
  1. kind create cluster (--config kind-cluster.yaml)
  2. helm install ingress-nginx ...
  3. docker build + kind load (×3 镜像)
  4. kubectl create secret backend-secrets
  5. kubectl apply -k deploy/k8s/overlays/dev
  6. 验证 Pod Ready / Ingress 通 / SSE 不卡

迭代：
  1. 改代码
  2. docker build + kind load
  3. kubectl rollout restart deploy/<name>
  4. kubectl rollout status

拆撤：
  1. kubectl delete -k deploy/k8s/overlays/dev
  2. kind delete cluster
```

---

## 当前状态

- [x] `docker-compose.yaml`（compose 部署，已可用）
- [x] `docker-compose.gvisor.yaml`（Linux gVisor opt-in）
- [x] `docs/k8s-migration-design.md`（设计文档）
- [x] `docs/k8s-deploy-guide.md`（本文）
- [ ] `deploy/k8s/base/...`（**尚未生成**——按设计文档 §7 目录结构落地）
- [ ] `deploy/k8s/overlays/dev/...`
- [ ] `deploy/k8s/kind-cluster.yaml`

下一步可以让我生成 `deploy/k8s/` 全部骨架文件，之后就能按本文 §5 直接 `kubectl apply -k` 跑起来。
