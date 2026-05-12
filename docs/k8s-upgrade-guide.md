# Kubernetes 升级 Runbook

> 配套文档：`docs/k8s-deploy-guide.md`（首次部署）+ `docs/k8s-migration-design.md`（设计）
> 状态：草案 · 2026-05-12

部署完成后的所有"改东西"操作都在这里。按场景分类，每种给出最短的操作路径。

## 0. 升级前必知

### apply 的标准形式

由于 `base/kustomization.yaml` 用 `configMapGenerator` 引用了 `main_agent/config.yaml`（在 kustomize 根目录之外），所有命令都用**管道形式**：

```bash
kubectl kustomize --load-restrictor=LoadRestrictionsNone deploy/k8s/overlays/dev \
  | kubectl apply -f -
```

下面文档里出现的简写 `kubectl apply -k <dir>` 都应理解为上面的完整形式。

### 单副本约束
backend `replicas = 1`（设计文档 §5.3）：
- 滚动更新期间**正在进行的 SSE 会话会断**
- 重启后**进程内 memory + tool_cache 全部丢失**
- 自用场景接受这个代价；要避免，先做 memory 外置（可选增强）

### 镜像 tag 策略选择（决定升级方式）

| 策略 | 优点 | 缺点 | 适用 |
|---|---|---|---|
| **固定 tag**（如 `:dev`）| 简单 | K8s 不感知"镜像变了"，必须 `rollout restart` | 本地开发 |
| **递增 tag**（如 `:dev-20260512-1430`）| K8s 自动滚动；可回滚 | 每次要更新 tag | 推荐，dev 也建议 |
| **`:latest`** | — | **永远不要用** —— Pod 重启时机不可控 | 禁用 |

下面所有命令默认按**递增 tag 策略**，最规范。固定 tag 的差异在每节末尾标注。

## 1. 代码升级（最常见，>90% 场景）

### 1.1 升级 backend

```bash
# 设变量
TAG=dev-$(date +%Y%m%d-%H%M)

# 1. 构建新镜像
docker build -t pydantic-harness/backend:$TAG -f main_agent/Dockerfile .

# 2. 灌进 kind（真集群替换为 docker push <registry>/...）
kind load docker-image pydantic-harness/backend:$TAG --name pydantic-harness

# 3. 更新 Kustomize images 字段
# 编辑 deploy/k8s/overlays/dev/kustomization.yaml:
#   images:
#     - name: pydantic-harness/backend
#       newTag: dev-20260512-1430    ← 改这里
# 或用命令直接改:
( cd deploy/k8s/overlays/dev && \
  kustomize edit set image pydantic-harness/backend=pydantic-harness/backend:$TAG )

# 4. 应用
kubectl apply -k deploy/k8s/overlays/dev

# 5. 等滚动完成
kubectl -n pydantic-harness rollout status deploy/backend

# 6. 验证
kubectl -n pydantic-harness logs deploy/backend --tail=50
```

**固定 tag 替代方案**（开发期偷懒）：

```bash
docker build -t pydantic-harness/backend:dev -f main_agent/Dockerfile .
kind load docker-image pydantic-harness/backend:dev --name pydantic-harness
kubectl -n pydantic-harness rollout restart deploy/backend
kubectl -n pydantic-harness rollout status deploy/backend
```

`rollout restart` 给 Deployment 加一个时间戳 annotation 触发新 ReplicaSet —— K8s 才会拉起新 Pod。

### 1.2 升级 sandbox / frontend

完全同上，把 `backend` 换成 `sandbox` 或 `frontend`。

### 1.3 多服务一起升级

```bash
TAG=dev-$(date +%Y%m%d-%H%M)

# 都构建
docker build -t pydantic-harness/sandbox:$TAG  -f sandbox_service/Dockerfile .
docker build -t pydantic-harness/backend:$TAG  -f main_agent/Dockerfile .
docker build -t pydantic-harness/frontend:$TAG  frontend/

# 都灌进 kind
kind load docker-image \
  pydantic-harness/sandbox:$TAG \
  pydantic-harness/backend:$TAG \
  pydantic-harness/frontend:$TAG \
  --name pydantic-harness

# 一次性改三个 tag
( cd deploy/k8s/overlays/dev && \
  kustomize edit set image \
    pydantic-harness/sandbox=pydantic-harness/sandbox:$TAG \
    pydantic-harness/backend=pydantic-harness/backend:$TAG \
    pydantic-harness/frontend=pydantic-harness/frontend:$TAG )

kubectl apply -k deploy/k8s/overlays/dev

# 等三个都好
kubectl -n pydantic-harness rollout status deploy/sandbox
kubectl -n pydantic-harness rollout status deploy/backend
kubectl -n pydantic-harness rollout status deploy/frontend
```

**升级顺序的考虑**：
- K8s 并发滚动，不严格保证顺序
- 我们的依赖图：`frontend → backend → sandbox`
- 实际上每个服务的 readinessProbe 会保证"下游 not ready 时不接流量"，所以**通常不需要手动控顺序**
- 唯一会出问题的场景：sandbox 的 API breaking change。这种情况下分两步发：先单独升 sandbox，确认 ready，再升 backend

## 2. 配置升级

### 2.1 改 ConfigMap（`main_agent/config.yaml`）

**关键陷阱**：ConfigMap 改了，**Pod 不会自动重启**。挂载的文件内容会在几十秒内更新，但应用进程读的是启动时的副本。

```bash
# 1. 改 main_agent/config.yaml

# 2. 重新生成 ConfigMap（kustomize 会自动 hash + 滚动）
kubectl apply -k deploy/k8s/overlays/dev

# 3. 如果 ConfigMap 用 configMapGenerator 生成（推荐），步骤 2 会自动改名
#    并触发 Deployment 滚动；如果是手写 ConfigMap，需要手动重启：
kubectl -n pydantic-harness rollout restart deploy/backend
```

**推荐用 configMapGenerator**（base/backend/kustomization.yaml 里）：

```yaml
configMapGenerator:
  - name: backend-config
    files:
      - config.yaml=../../main_agent/config.yaml
```

Kustomize 会自动给生成的 ConfigMap 加 hash 后缀（如 `backend-config-7m2k4f9`），改文件后 hash 变 → Deployment 引用更新 → 自动滚动。这是 Kustomize 处理配置变更的标准做法。

### 2.2 改 Secret

```bash
# 直接覆盖
kubectl -n pydantic-harness create secret generic backend-secrets \
  --from-literal=OPENAI_API_KEY="新值" \
  --from-literal=SANDBOX_TOKEN="..." \
  --from-literal=DEEPSEEK_API_KEY="..." \
  --dry-run=client -o yaml | kubectl apply -f -

# Secret 不会触发 Pod 重启，手动 kick
kubectl -n pydantic-harness rollout restart deploy/backend
```

**注意**：`kubectl create secret` 重新创建时**必须把所有 key 都带上**，否则没传的会被删。要避免就用 patch：

```bash
kubectl -n pydantic-harness patch secret backend-secrets \
  --type='merge' \
  -p='{"stringData":{"OPENAI_API_KEY":"新值"}}'

kubectl -n pydantic-harness rollout restart deploy/backend
```

## 3. yaml 改动升级（资源限制、副本数、注解等）

最直接：

```bash
# 1. 改 base 或 overlay 下的 yaml

# 2. 预览将要变更（强烈推荐先看）
kubectl diff -k deploy/k8s/overlays/dev

# 3. 应用
kubectl apply -k deploy/k8s/overlays/dev
```

`kubectl diff` 会显示 "服务器现状" vs "将要 apply 的内容" 的差异，避免误改。

K8s 会根据字段类型决定要不要重启 Pod：
- 改 `spec.template.*` 任何字段 → **重启**
- 改 `metadata.annotations / labels`（非 selector 部分）→ **不重启**
- 改 Service 的 port → **不重启**（除非改 selector）
- 改 Ingress 的注解（包括我们的 SSE 注解）→ **不重启**应用 Pod，但 Ingress Controller 会重 reload 配置

## 4. 回滚

### 4.1 回滚到上一个版本（最常用）

```bash
kubectl -n pydantic-harness rollout undo deploy/backend
```

### 4.2 回滚到指定历史版本

```bash
# 看历史
kubectl -n pydantic-harness rollout history deploy/backend

# 输出示例：
# REVISION  CHANGE-CAUSE
# 1         ...
# 2         ...
# 3         ...   ← 想回到这个

kubectl -n pydantic-harness rollout undo deploy/backend --to-revision=3
```

**默认只保留 10 个历史版本**（`spec.revisionHistoryLimit`）。

### 4.3 通过 Kustomize 回滚（更可追溯）

更规范：把 tag 改回上一个稳定值，`git revert` overlay 的提交，再 apply：

```bash
git revert <bad-commit>
kubectl apply -k deploy/k8s/overlays/dev
```

这样回滚动作本身留在 git 历史里。

## 5. 滚动更新策略说明

Backend 单副本，base 里建议这样配（设计文档 §5.3）：

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1          # 允许临时多 1 个
      maxUnavailable: 0    # 不允许少
```

效果：
1. 拉起新 Pod
2. 新 Pod readiness probe 通过
3. Service endpoints 切到新 Pod
4. 杀掉旧 Pod（默认 `terminationGracePeriodSeconds: 30`）

**SSE 长连接的影响**：
- 新建连接 → 走新 Pod
- 已有连接 → 仍连旧 Pod，直到旧 Pod 进入 termination 后超时断开
- 接受这个断开（设计已确认）

## 6. 升级第三方组件

### 6.1 NGINX Ingress Controller

```bash
# 看当前版本
helm -n ingress-nginx list

# 看可用版本
helm search repo ingress-nginx/ingress-nginx --versions

# 升级
helm upgrade ingress-nginx ingress-nginx \
  --repo https://kubernetes.github.io/ingress-nginx \
  -n ingress-nginx \
  --version <新版本> \
  --reuse-values

# 监控
kubectl -n ingress-nginx rollout status deploy/ingress-nginx-controller
```

**注意**：升级期间存量 SSE 长连接会被断开。选择低峰期。

### 6.2 K8s 集群本身

**kind**：直接重建集群最省心：
```bash
# 备份 PVC 数据（如有）
kubectl -n pydantic-harness cp <pod>:/app/workspace ./workspace-backup

# 重建
kind delete cluster --name pydantic-harness
kind create cluster --config deploy/k8s/kind-cluster.yaml --image kindest/node:<新版本>

# 重新走部署 runbook 即可
```

**真集群**：用云厂商提供的滚动升级机制（GKE / EKS / AKS 都有 UI 或 CLI），跳出本文档范围。

## 7. 数据保护

### 7.1 workspace PVC

升级**不会**清除 PVC。但要注意：

- `kubectl delete -k overlays/dev` 默认会删 PVC（如果 PVC 在 overlay 里声明）
- 想保护：把 PVC 拆到独立 namespace / 独立 yaml，**不放进 kustomization.yaml 的 resources 列表**，用 `kubectl apply -f` 单独管
- 或者给 PVC 加 `kubectl.kubernetes.io/keep` annotation（部分 GitOps 工具支持）

### 7.2 备份

dev 环境通常不备份，prod 必须：

```bash
# 一次性备份 workspace（不停机）
kubectl -n pydantic-harness exec deploy/sandbox -- \
  tar czf - /app/workspace > workspace-$(date +%Y%m%d).tar.gz
```

或上 Velero（CSI snapshot + 对象存储）做正式备份。

## 8. 升级前后验证清单

```bash
# Pre-upgrade
[ ] kubectl diff -k deploy/k8s/overlays/dev  # 看清变更
[ ] 备份 workspace（如改动可能影响数据）
[ ] 当前 Pod ready，状态正常

# Apply
[ ] kubectl apply -k deploy/k8s/overlays/dev
[ ] kubectl -n pydantic-harness rollout status deploy/<svc>

# Post-upgrade
[ ] 三个 Pod 全 Ready
[ ] kubectl -n pydantic-harness logs deploy/backend --tail=100  没新报错
[ ] 前端能打开
[ ] 一次完整对话（含工具调用）能跑通
[ ] SSE 流式正常
```

## 9. 常见升级故障

| 现象 | 原因 | 处理 |
|---|---|---|
| `rollout status` 一直 Progressing | 新 Pod 起不来 / readiness 失败 | `kubectl describe pod <new>` 看 Events |
| 改了 ConfigMap 但应用没读到新值 | ConfigMap 不会自动重启 Pod；或挂载延迟 | `rollout restart` 或用 configMapGenerator |
| 改 Secret 后 Pod 仍用老值 | Secret 不触发重启 | `rollout restart` |
| 新 Pod 起来了但流量没切 | readinessProbe 没通过 | `kubectl get endpoints <svc>` 看后端列表 |
| 应用 yaml 后 Pod 没动 | yaml 实际无变化（如只改 label）/ 镜像 tag 没变 | 改 tag 或 `rollout restart` |
| 回滚后旧问题复现 | 旧版本本来就有问题 / 配置没回滚 | 检查 ConfigMap/Secret 是否也要回 |
| PVC 数据不见了 | 删 namespace / 删 PVC 时数据丢失 | 看 StorageClass 的 `reclaimPolicy`（Retain 还是 Delete）|
| 升级后 SSE 卡顿 | Ingress 注解被覆盖丢失 | `kubectl get ingress -o yaml \| grep nginx` 检查 |

## 10. 一页流程图

```
日常升级（90% 场景）：
  1. TAG=dev-$(date +%Y%m%d-%H%M)
  2. docker build -t <img>:$TAG ...
  3. kind load docker-image <img>:$TAG
  4. cd deploy/k8s/overlays/dev && kustomize edit set image <img>=<img>:$TAG
  5. kubectl apply -k deploy/k8s/overlays/dev
  6. kubectl -n pydantic-harness rollout status deploy/<svc>
  7. 验证

回滚：
  kubectl -n pydantic-harness rollout undo deploy/<svc>

ConfigMap 升级：
  改文件 → kubectl apply -k （configMapGenerator 自动滚动）

Secret 升级：
  kubectl patch secret ... → rollout restart

紧急救火（绕过流程）：
  kubectl -n pydantic-harness set image deploy/backend backend=<img>:<tag>
  （事后记得回 kustomize 同步，否则下次 apply 又改回去）
```

## 11. 持续部署（CD）扩展（可选）

如果不想每次手动跑那 5 步，未来可以接：

| 工具 | 适合本项目吗 |
|---|---|
| **GitHub Actions + kubectl** | 适合 —— 构建+推镜像+ `kubectl apply -k`，写 200 行 yaml 就行 |
| **ArgoCD** | 自用过重；适合多环境/多团队 |
| **Flux** | 同上 |
| **Tekton** | 同上 |

自用场景**先手动跑流程**，等真烦了再上 GitHub Actions。本文档的每一步都能机械翻译成 CI step。

---

升级流程到此覆盖完整。`docs/` 下现在有三份文档：

- `k8s-migration-design.md` —— 为什么这么设计
- `k8s-deploy-guide.md` —— 怎么首次部署
- `k8s-upgrade-guide.md` —— 怎么持续维护（本文）
