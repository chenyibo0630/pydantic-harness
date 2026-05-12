# Kubernetes 迁移设计

> 状态：草案 · 2026-05-12
> 适用范围：**自用单租户**部署（个人项目，无多用户/多租户需求）

将当前 `docker-compose.yaml`（sandbox / backend / frontend 三服务）迁移到 Kubernetes，保持现有架构与硬化策略，做最小复杂度的转译。

## 1. 目标与非目标

### 目标
- 三个服务在 K8s 上稳定运行，行为与 compose 等价
- **保留隔离底线：agent（backend）与 sandbox 跨 Pod**
- SSE（`/chat/stream`）端到端流式不被中间层缓冲
- 复用现有镜像构建（`Dockerfile` 不改）
- 保留 compose 的所有硬化字段（cap_drop / read_only / non-root / 资源限制）

### 非目标
- 多租户、多用户隔离（项目自用）
- 按租户/会话动态拉起 sandbox（不需要）
- backend 水平扩展（单进程 + 进程内 memory 已够用）
- gVisor / Kata 内核级隔离（自用威胁模型低，可选增强，见 §9）

## 2. 现状对照

```
docker-compose.yaml
├── sandbox   工具执行 + skills 只读挂载 + workspace 读写挂载，硬化容器
├── backend   FastAPI agent，SSE 输出，调用 sandbox via SANDBOX_REMOTE_URL
└── frontend  nginx 静态站点
```

关键约束：
- backend 通过 `SANDBOX_REMOTE_URL=http://sandbox:8100` 走集群内服务发现
- sandbox 已做 `read_only` + `cap_drop ALL` + `no-new-privileges` + 非 root + tmpfs + 资源限制
- backend memory（`InMemoryStore` / `EvictingMemory`）是进程内状态，**单副本约束**

## 3. 架构图

```
                          Internet (or LAN)
                                │
                                ▼
                  ┌──────────────────────┐
                  │  NGINX Ingress       │
                  │  (SSE 注解必配)      │
                  └──────────┬───────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
       / (frontend)              /api, /chat/stream (backend)
              │                             │
              ▼                             ▼
       ┌────────────┐                ┌────────────┐
       │ frontend   │                │ backend    │
       │ Deployment │                │ Deployment │
       │ replicas=1 │                │ replicas=1 │
       └────────────┘                └─────┬──────┘
                                           │
                                           ▼
                                  sandbox.svc:8100
                                           │
                                    ┌────────────┐
                                    │ sandbox    │
                                    │ Deployment │
                                    │ replicas=1 │
                                    └─────┬──────┘
                                          │
                                          ▼
                                  workspace-pvc (RWO)
```

三个 Deployment 都是 `replicas=1`，纯线性架构。

## 4. 资源映射表

| docker-compose | k8s 资源 | 备注 |
|---|---|---|
| `services.sandbox` | Deployment + ClusterIP Service | 不暴露 Ingress |
| `services.backend` | Deployment + ClusterIP Service + Ingress | 单副本 |
| `services.frontend` | Deployment + ClusterIP Service + Ingress | 单副本 |
| `volumes: workspace` | PVC `workspace-pvc`（RWO）| 单卷 |
| `volumes: ./skills:ro` | 镜像内置（构建期 COPY）| 见 §6.2 |
| `volumes: config.yaml:ro` | ConfigMap `backend-config` | |
| `SANDBOX_TOKEN` 等密钥 | Secret `backend-secrets` | |
| `cap_drop: ALL` 等 | `securityContext` | 见 §6.4 |
| `mem_limit / cpus` | `resources.limits/requests` | |
| `pids_limit` | LimitRange（namespace 级）| Pod spec 无对应字段 |
| `healthcheck` | livenessProbe + readinessProbe | |
| `depends_on` | 无对应，靠 readinessProbe 自然达成 | |
| `restart: unless-stopped` | Deployment 默认行为 | |

## 5. 关键设计决策

### 5.1 sandbox 常驻单 Pod

自用场景下：
- 不需要按租户/会话切分
- 一个常驻 Pod 最简单、冷启动开销 0
- workspace 单 PVC 即可，无路径前缀切分

agent / sandbox **始终是两个 Pod**，隔离底线由此保证。

### 5.2 SSE 透传：NGINX Ingress 注解

NGINX Ingress 默认会缓冲响应，导致 SSE token 卡顿。backend Ingress 必须配（缺一不可）：

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/proxy-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-request-buffering: "off"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "3600"
```

NGINX reload 时会断开长连接。缓解：
- 调高 `worker-shutdown-timeout`（默认 240s）让旧 worker 继续服务存量连接
- 前端 SSE 自动重连（按需补齐）

### 5.3 backend 单副本

`backend.replicas = 1` 是硬约束（进程内 `EvictingMemory` + `tool_cache`）。

- 不开 HPA
- PDB `minAvailable: 0`（允许节点维护时下线）
- 滚动更新 `maxSurge: 1, maxUnavailable: 0`：先起新的再停旧的
- **同一会话会因 Pod 切换丢失上下文**——自用场景接受这个代价

### 5.4 Ingress 选型：NGINX

已确认 NGINX Ingress Controller。理由：
- 主流，文档/排错资料最多
- SSE 通过注解可解决（虽然不是默认行为）
- 一次配置写进 Kustomize base 后稳定

## 6. 各资源详细设计

### 6.1 Namespace 与 PodSecurityAdmission

- Namespace：`pydantic-harness`
- 强制 Pod 安全基线：
  ```yaml
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
  ```

### 6.2 Volumes

| 卷 | 类型 | 大小 | 访问模式 | 备注 |
|---|---|---|---|---|
| `workspace-pvc` | PVC（StorageClass 视集群）| 20Gi 起 | RWO | 单卷 |
| sandbox `/tmp` | emptyDir.medium=Memory | 64Mi | — | 对应 compose 的 tmpfs |

**skills 方案**：构建期把 `skills/` 拷进 sandbox 镜像。
- 优点：版本与镜像绑死，K8s 资源最少
- 缺点：改 skills 要重建镜像（自用场景接受）

如未来 skills 更新频繁，可改 PVC + ROX。

### 6.3 ConfigMap / Secret

**ConfigMap `backend-config`**：
- 来源：`main_agent/config.yaml`
- 挂载到 `/app/main_agent/config.yaml`（subPath 单文件挂载）

**Secret `backend-secrets`**：
- `SANDBOX_TOKEN`
- LLM provider key（按 `config.yaml` 引用的 provider 决定）
- 注入方式：env from secretKeyRef

**禁止**：把任何 key 写进 ConfigMap 或镜像。

### 6.4 securityContext

sandbox Pod：
```yaml
securityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000
  seccompProfile:
    type: RuntimeDefault
containers:
  - name: sandbox
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: ["ALL"]
```

backend Pod：同样 `runAsNonRoot` + 丢弃 caps。`readOnlyRootFilesystem` 视代码是否写临时文件决定（可加 emptyDir 兜底）。

frontend Pod：nginx 需要写 `/var/cache/nginx`、`/var/run`，挂 emptyDir。

### 6.5 探针

| 服务 | livenessProbe | readinessProbe |
|---|---|---|
| sandbox | `GET /health` | `GET /health` |
| backend | `GET /health`（需确认存在）| 同上 |
| frontend | `GET /` | 同上 |

**待办**：检查 backend 是否有 `/health`，没有则补一个不依赖 sandbox 的存活探针。

### 6.6 资源 requests/limits

| 服务 | requests | limits |
|---|---|---|
| sandbox | cpu=200m, mem=256Mi | cpu=1, mem=1Gi |
| backend | cpu=200m, mem=512Mi | cpu=2, mem=2Gi |
| frontend | cpu=50m, mem=64Mi | cpu=500m, mem=256Mi |

LimitRange（namespace 级）补 `pids` 上限对应 compose 的 `pids_limit: 200`。

### 6.7 NetworkPolicy

虽然自用，但保留是好习惯（且与 compose 默认网络隔离对齐）：

**sandbox**：
- ingress：仅允许 backend Pod（按 label selector）访问 8100
- egress：拒绝所有（如工具需联网，按需开白名单：DNS + 特定外部域名）

**backend**：
- ingress：仅 Ingress Controller 命名空间
- egress：sandbox + DNS + LLM provider 域名

**frontend**：
- ingress：仅 Ingress Controller
- egress：拒绝（静态站点）

### 6.8 Ingress 路由

```
Host: harness.local（或按实际填）
  /                  → frontend:80
  /api               → backend:2648
  /chat/stream       → backend:2648（SSE 注解生效）
```

## 7. 目录结构

```
deploy/
└── k8s/
    ├── base/
    │   ├── namespace.yaml
    │   ├── sandbox/
    │   │   ├── deployment.yaml
    │   │   ├── service.yaml
    │   │   ├── pvc-workspace.yaml
    │   │   └── networkpolicy.yaml
    │   ├── backend/
    │   │   ├── deployment.yaml
    │   │   ├── service.yaml
    │   │   ├── configmap.yaml
    │   │   ├── secret.example.yaml   # 模板，真值走外部注入
    │   │   ├── ingress.yaml
    │   │   ├── pdb.yaml
    │   │   └── networkpolicy.yaml
    │   ├── frontend/
    │   │   ├── deployment.yaml
    │   │   ├── service.yaml
    │   │   ├── ingress.yaml
    │   │   └── networkpolicy.yaml
    │   ├── limitrange.yaml
    │   └── kustomization.yaml
    └── overlays/
        ├── dev/                       # 本地 kind / minikube
        │   ├── kustomization.yaml
        │   └── ingress-host-patch.yaml
        └── prod/                      # 真实集群（如有）
            ├── kustomization.yaml
            └── resources-patch.yaml
```

工具：**Kustomize**（单项目、模板需求弱，比 Helm 直观）。

## 8. 迁移步骤

1. **代码侧准备**
   - 补 backend `/health` endpoint（如缺）
2. **镜像构建**：本地构建并推到 registry（私有/公共按集群环境）
3. **集群准备**：
   - 安装 NGINX Ingress Controller
   - 创建 namespace + 打 PodSecurity 标签
   - 准备 StorageClass（PVC 后端）
4. **Kustomize 落地**：先 base + dev overlay，本地 kind 验证
5. **冒烟测试**：
   - 三个 Pod Ready
   - 前端打开能用
   - SSE 流式无卡顿（重点）
   - 工具调用能正确读写 workspace
6. **生产部署**（如需）：prod overlay + 真实 Secret + Ingress host

## 9. 可选增强（按需采用）

| 增强项 | 触发条件 | 实现要点 |
|---|---|---|
| **gVisor sandbox** | 跑不受信任代码 | RuntimeClass `gvisor` + Pod `runtimeClassName: gvisor`；节点装 runsc |
| **memory 外置（Redis/PG）** | 想做 backend 多副本或跨重启会话保留 | 实现 `Memory` 接口 Redis 版，backend 改 `replicas > 1` |
| **HPA** | 当前 1 个用户不需要；如开放给他人才考虑 | 前置必须先做 memory 外置 |
| **skills 改 PVC** | skills 更新频繁，不想每次重建镜像 | RWX PVC + init job 同步 |

> 这些都不在本期范围内。`docker-compose.gvisor.yaml` 已经提供了 compose 层的 gVisor opt-in 路径，K8s 层等真正有需求再启用。

## 10. 风险与开放问题

| 项 | 风险/问题 | 处理 |
|---|---|---|
| backend 单副本 | 滚动更新断会话 | 自用场景接受 |
| NGINX reload 断 SSE | 偶发卡顿 | 调 worker-shutdown-timeout + 客户端重连 |
| skills 镜像内置 | 改 skills 要重建 | 接受；评估后再换 PVC |
| backend `/health` | 是否存在未确认 | 迁移前先检查/补齐 |
| LLM provider 出站域名 | NetworkPolicy 维护成本 | 用 FQDN policy（需 CNI 支持，如 Cilium）；不支持则放宽到 DNS + egress gateway |
| PVC StorageClass | 不同集群差异 | dev overlay 可用 hostPath / kind extraMounts；prod 用集群默认 SC |
| Windows 路径 `D:/...` | compose 直挂宿主盘，K8s 不可移植 | PVC 替代，dev 环境用 kind extraMounts 接 Windows 路径 |

## 11. 验收清单

- [ ] 三个 Pod 在 dev overlay 下 Ready 且 5 分钟稳定
- [ ] `/chat/stream` 端到端 token 流式无卡顿（对比 compose 基线）
- [ ] 工具调用读写 workspace 正常
- [ ] sandbox NetworkPolicy 生效（egress 测试被拒）
- [ ] PodSecurityAdmission `restricted` 通过
- [ ] backend 重启后会话丢失符合预期（明确文档化）
- [ ] Secret 不出现在镜像或 ConfigMap 中
