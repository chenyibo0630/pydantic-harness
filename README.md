# pydantic-harness

基于 [pydantic-ai](https://ai.pydantic.dev/) 的 Agent Harness。每个 agent 独立运行、通过共享的 gateway / sandbox / skills / LLM 抽象层组合能力。

## 特性

- **Agent 独立**: 每个 agent 自带 `config.yaml` / `server.py` / `prompts/`,与其他 agent 互不依赖
- **沙箱化工具执行**: 工具调用全部走独立的 sandbox 容器,主进程不直接执行 subprocess
- **容器硬化**: sandbox 容器 `read_only` + `cap_drop ALL` + 非 root + 内存/PID/CPU 限额 + symlink 防御
- **多 LLM 提供商**: openai / azure / deepseek / qwen 一套配置
- **Skills 系统**: 把外部能力(搜索 / PDF / 视频生成)打包成可复用 skill,LLM 自动发现
- **流式 SSE**: text / tool_call / tool_result / tool_progress(心跳)/ message_end 标准化事件
- **会话状态(短期)**: 装饰器分层 — 同步摘要压缩 + tool result 始终驱逐 + 按需 recall;首轮锁定 system prompt 让 Anthropic prompt cache 整段命中
- **长期记忆(跨会话)**: hermes 风格 MEMORY.md / USER.md 策展笔记,agent 通过 `memory` 工具自主写入,带注入扫描和字符上限

## 架构

```
┌──────────────────┐    HTTP/SSE    ┌──────────────────┐  HTTP   ┌──────────────────┐
│   Frontend       │ ─────────────> │   main_agent     │ ──────> │   sandbox        │
│ (React + Vite)   │ <───────────── │ (FastAPI + LLM)  │ <────── │ (FastAPI + tools)│
│  port 3000       │   text_delta   │  port 2648       │         │  port 8100       │
└──────────────────┘   tool_call    └──────────────────┘         └──────────────────┘
                       tool_result          │                              │
                                            ▼                              ▼
                                      LLM Provider              host: harness-workspace/
                                  (DeepSeek / Azure / ...)         + skills/ (read-only)
```

- main_agent 没有任何文件操作 / subprocess 能力,只能通过 HTTP RPC 委托给 sandbox
- sandbox 把 `/app/workspace` bind-mount 到宿主目录,文件直接落地;skills/ 只读挂载

## 快速开始

### Docker(推荐)

```bash
# 1. 复制并填写配置
cp main_agent/config.example.yaml main_agent/config.yaml
# 编辑 main_agent/config.yaml,至少填:
#   - llm.api_key / llm.type
#   - sandbox.token (生产)  或  sandbox.allow_no_auth: true (本机 dev)
#   sandbox 启动会强制校验,两个都没填会拒启

# 2. 准备宿主工作区(默认路径在 docker-compose.yaml)
mkdir -p D:/develop/learning/harness-workspace

# 3. 启动
docker compose up -d --build

# 4. 打开
# Frontend: http://localhost:3000
# Backend:  http://localhost:2648/chat/stream (SSE)
```

### 本机开发

```bash
# 安装依赖
uv sync

# 启动 main agent(默认 sandbox=local,工具直接在本机执行)
cd main_agent && uv run server.py

# 启动 frontend(代理到 backend)
cd frontend && npm install && npm run dev

# 跑测试
.venv/Scripts/python -m pytest tests/ -v
```

## 目录结构

```
pydantic-harness/
├── backend/                       # 共享模块
│   ├── core/
│   │   ├── llm/                   # LLM 抽象(build_model)
│   │   ├── sandbox/               # 沙箱接口 + LocalSandbox + RemoteSandbox
│   │   ├── prompt/                # Prompt 加载器(load_prompts 自动跳 MEMORY/USER)
│   │   ├── conversation/          # 短期会话状态(history + tool cache + prompt snapshot)
│   │   ├── memory/                # 长期记忆 store(MEMORY.md / USER.md + 注入扫描)
│   │   ├── tools/                 # agent 工具(ask_user / memory / recall_tool_result)
│   │   ├── hooks/                 # history_processors hooks
│   │   └── skills/                # Skills 加载与工具暴露
│   └── gateway/                   # FastAPI 路由 + SSE 流式桥接
├── main_agent/                    # 主 Agent
│   ├── server.py                  # 入口
│   ├── config.yaml                # 运行配置(LLM / agent / sandbox)
│   ├── agent.py                   # Agent 定义 + build_system_prompt
│   ├── tools/                     # 选择启用的工具列表
│   └── prompts/                   # 系统提示词(SYSTEM/SOUL/EXPERIENCE/MEMORY_GUIDANCE
│                                  #   + MEMORY.md/USER.md 由 memory 工具维护)
├── sub_agents/                    # 其他独立子 Agent(不需要长期记忆)
│   └── demo_agent/
├── sandbox_service/               # 独立沙箱服务(容器化部署)
│   ├── app.py                     # FastAPI 路由
│   ├── schemas.py                 # 请求/响应 schema
│   └── Dockerfile
├── skills/                        # 可热插拔的 Skill 包
│   ├── pdf/                       # PDF 读写(PyMuPDF)
│   ├── tavily/                    # Web 搜索
│   └── seedance/                  # 视频生成(火山引擎 Ark)
├── frontend/                      # React + Vite 前端
├── deploy/k8s/                    # Kubernetes 部署清单 + kustomize overlays
└── tests/                         # pytest 测试
```

**命名要点**：`backend/core/conversation/` 装短期会话状态（一次对话的消息/工具结果/提示词快照），`backend/core/memory/` 装长期跨会话记忆（MEMORY.md/USER.md）。两个概念都叫 "memory" 太混淆 —— 短期叫 conversation，长期叫 memory。

## 配置

`main_agent/config.yaml` 关键字段:

```yaml
llm:
  type: deepseek                   # openai | azure | deepseek | qwen
  model: deepseek-chat
  api_key: ...

server:
  port: 2648
  stream_timeout: 120.0            # SSE 空闲超时(秒)

agent:
  workspace: /path/to/workspace
  skills:                          # 启用的 skill 列表(必须在 skills/ 下存在)
    - pdf
    - tavily

sandbox:
  type: local                      # local | remote(走 sandbox_service 容器)
  remote_url: http://sandbox:8100  # type=remote 时使用
```

环境变量:
- `SANDBOX_LOG_LEVEL` — sandbox 服务日志级别(默认 INFO)
- `SANDBOX_TOKEN` / `SANDBOX_ALLOW_NO_AUTH` — 通常在 `main_agent/config.yaml` 的 `sandbox` 段配置,但同名 env 仍作为应急 override
- `TZ` — 容器时区(默认 `Asia/Shanghai`)
- 各 skill 的 API key:`TAVILY_API_KEY`、`ARK_API_KEY` 等(详见各 skill 的 `SKILL.md`)

## 工具集

LLM 可调用的工具(全部经沙箱):

| 工具 | 说明 |
|---|---|
| `bash_execute` | 执行 shell 命令(默认 timeout 120s,上限 300s) |
| `read_file` | 按行读取文件(单次最多 200 行,可分页) |
| `write_file` | 写文件(支持 append) |
| `str_replace` | 字符串原位替换 |
| `list_dir` | tree 风格列目录 |
| `glob_files` | glob 匹配文件 |
| `grep_search` | 正则全文搜索 |
| `ask_user` | 反问用户(歧义 / 不可逆操作前) |
| `recall_tool_result` | 按 `call_id` 加载被驱逐的旧工具结果快照 |

**路径约定**: 所有路径相对于工作区根。用 `"."` 表示根本身,`"foo.pdf"` 表示根下文件,`"sub/bar.txt"` 表示嵌套。`/skills/<name>/...` 是唯一的特殊绝对路径(只读)。

## SSE 事件

| 事件 | 数据 | 触发时机 |
|---|---|---|
| `message_start` | `{conversation_id}` | 响应开始 |
| `text_delta` | `{text}` | 每个 LLM token |
| `tool_call` | `{tool_name, tool_call_id}` | LLM 决定调用工具 |
| `tool_progress` | `{tool_name, tool_call_id, elapsed}` | 工具运行 ≥10s 时心跳 |
| `tool_result` | `{tool_name, tool_call_id, content}` | 工具执行完毕 |
| `message_end` | `{conversation_id, usage: {input_tokens, output_tokens, total_tokens, cache_read_tokens}}` | 响应结束(`cache_read_tokens` 表示 Anthropic prompt cache 命中量) |
| `error` | `{error, message}` | 异常 |

## 记忆架构

两个独立但配合的子系统:

| | 短期(`backend/core/conversation/`) | 长期(`backend/core/memory/`) |
|---|---|---|
| 抽象 | `Conversation` ABC | `MemoryStore` 类 |
| 寿命 | 单个 `conversation_id` | 跨会话(磁盘) |
| 内容 | 消息历史 + tool result cache + system prompt snapshot | 策展笔记 MEMORY.md / USER.md |
| 写入路径 | 每轮 `sse.py` 自动 set/get | agent 通过 `memory` 工具显式写 |
| 注入到 prompt | 通过 `ConversationDeps.system_prompt` callable | 通过 `MemoryStore.render_system_block` |

### 短期：Conversation 装饰器栈

`Conversation` ABC 管理三类 per-conversation 状态（保证存储层级一致,in-memory ↔ in-memory,未来的 file ↔ file 不会出现孤儿）：
- **消息历史**: `list[ModelMessage]` per `conversation_id`
- **Tool result 缓存**: 被 `EvictingConversation` 驱逐的大工具结果,按 `(conversation_id, call_id)` 寻址
- **系统提示快照**: 会话首轮锁定的 system prompt,后续轮复用同一份字节

运行时组装(`server.py` 启动):
```
SummarizingConversation          # 同步压缩(per-conv asyncio.Lock,inline summarize)
  └─ EvictingConversation        # 始终把 ≥ min_size 的 ToolReturnPart.content 搬到 cache
       └─ FileConversation       # 磁盘持久化(每个 conv_id 一个子目录)
```

`InMemoryConversation` 实现仍然存在,用于 unit test —— prod 路径已切到 `FileConversation`。

#### 磁盘持久化:`FileConversation`

`FileConversation` 把三类状态都落盘,每个 `conversation_id` 一个子目录:
```
{base_dir}/
└── {conv_id}/
    ├── messages.jsonl    ← Claude Code 风格,一条 ModelMessage 一行,append-only
    ├── prompt.txt        ← system prompt snapshot(UTF-8 纯文本)
    └── tool_results/
        └── {call_id}.json  ← 每个被驱逐的 tool result 一个文件
```

**JSONL 写入策略**(`messages.jsonl`):
- **普通一轮**:`set()` 看 disk 已有 N 行,新 `messages` 长度 > N → 只 append 末尾 `(len(messages) - N)` 行,fsync 落盘,不重写整个文件
- **压缩 / sanitize**(`new` ≤ disk)→ atomic tempfile + rename 全量重写,确保旧消息不会残留在压缩摘要后面

好处:`tail -f messages.jsonl` 实时看进展;`jq -c '.' messages.jsonl` 逐行处理;`wc -l` 数消息条数。多模态字节内容仍按 pydantic-ai 的 base64 序列化保真。

**`instructions` 字段 strip**(去掉系统提示词的重复存储):

pydantic-ai 每次 LLM 调用都把**完整 system prompt 字符串**塞进 `ModelRequest.instructions`(`_agent_graph.py:792`),`all_messages()` 返回的 history 里每条 ModelRequest 都自带一份。直接持久化等于 N 行 × N 份重复(实测真实系统提示 ~5-10KB,20 轮就 ~150KB 浪费)。

`FileConversation._strip_instructions` 在写盘前把 `ModelRequest.instructions` 全部置为 `None`。理由:
- `prompt.txt` 是 session 锁定后的**单一真相源**,跨轮字节不变
- pydantic-ai 只在**当前请求**用 `instructions`(`_agent_graph.py:792` 每次重设);**历史请求**的 `.instructions` 只是 merge-equality 检查用的元数据,`None` 不影响行为
- 跟 hermes(`sessions.system_prompt` 单列,messages 表无该字段)、Claude Code(jsonl 开头单条 system event,后续不重复)同构

效果:每行 ModelRequest 字节从 ~778 降到 ~180(实测样例),长会话省一两个数量级 disk。

**写入保证**:
- 全量重写路径:atomic tempfile + `os.replace`,进程崩溃不留半成品
- Append 路径:`os.fsync` 强制刷盘,断电不丢已写入
- 单行损坏的 jsonl 不会让 `get()` 抛错 —— 跳过坏行、保留好行 log warning
- 完全无法读取的文件返回 None,caller 走"开新会话"分支

**并发保护**:per-conv `threading.Lock`(pydantic-ai 用 worker thread 跑同步工具,可能并发两条会话同时写)。**不用文件锁** —— 单进程部署足够。

**路径解析顺序**:
1. `AGENT_SESSION_DIR` 环境变量(docker-compose 设为 `/data/.session`)
2. `agent.session_dir` 配置项
3. 默认 `./.session` 相对 cwd

Docker 部署:host `./.session` ↔ container `/data/.session` bind mount,容器重建不丢任何会话状态。

#### 系统提示词:会话粒度锁定

`Agent.instructions` 是回调,从 `ctx.deps.system_prompt` 取值。Gateway 在每个请求开头 `load-or-lock`:首轮 `build_system_prompt(settings, skills)` 读盘 + 渲染 MemoryStore blocks,缓存到 conversation;后续轮直接复用。

- 改 `SYSTEM.md` / `SOUL.md` / `EXPERIENCE.md` / `MEMORY_GUIDANCE.md` → 只影响**新会话**,进行中的不受影响
- 同一会话内 LLM 每次调用看到的 system message 字节完全一致 → prompt cache 可命中
- USER PROFILE / MEMORY 块同样冻结在首轮,中途 `memory` 工具写盘**不**扰动当前会话

#### Tool result 驱逐:始终 evict

每次 `set()` 都扫描**所有** `ToolReturnPart`,大于 `min_size`(默认 256 字符)的把 `content` 搬到缓存,原位换成占位符:

```
[evicted-tool-result] tool=read_file call_id=call_a3f size=8421chars lines=230
preview: # README...
To reload the original bytes (past snapshot):  recall_tool_result(call_id="call_a3f")
For current state of the underlying source:    call read_file again with the same arguments.
```

**为什么"始终 evict"而不是滑动窗口**:滑动窗口的驱逐边界每轮向后推一格,prefix 字节随之周期性变化,Anthropic prompt cache 每次都 miss。始终 evict 后,从 turn 2 起 stored prefix 完全 byte-stable,长会话 cache 命中率接近 100%。

**代价**:模型在 turn N+1 看不到 turn N 的 tool result 原文,只看到占位符。需要原文时调 `recall_tool_result(call_id=...)`。

#### 同步压缩(per-conv asyncio.Lock)

`SummarizingConversation` 用每个 `conversation_id` 一把 `asyncio.Lock` 串行化 get/set/delete。超过 `threshold`(默认 20 条)时,压缩**inline 在 `set()` 里跑完才返回** —— 下一轮 `get()` 必须等这一轮压缩完成才能开始。

- 跟原 hermes fire-and-forget 比,这里**用户感知延迟换正确性**:`message_end` SSE 事件会延后 3-5 秒(压缩 LLM 调用时间),但消除了「后台 stale write 覆盖新轮」和「delete 时被复活」两种 race
- 压缩 prompt 用 hermes 实战验证的结构化模板(`## Active Task` / `## Completed Actions` / `## Resolved Questions` / `## Remaining Work` 等),并加"DIFFERENT assistant continues"前置,防止模型把摘要当用户提问继续作答
- 压缩失败不影响数据:`set()` 先落盘原始历史再 inline 压缩,LLM 抛错时退回原状

### 长期：MEMORY.md / USER.md(hermes 风格)

跨会话策展记忆,两个文件**和静态 prompt 文件一起放在 `main_agent/prompts/`**:
- `USER.md` — 用户画像(姓名/角色/偏好/沟通风格)
- `MEMORY.md` — agent 自己的笔记(环境事实/项目约定/工具坑)

`load_prompts()` 自动跳过这两个文件 —— 由 `MemoryStore.render_system_block(target)` 用独立的 USER PROFILE / MEMORY 章节(带 usage 百分比头)注入 system prompt,避免双重渲染。

Agent 通过 `memory(action, target, content?, old_text?)` 工具自主写入:
```python
memory("add", "user", content="用户偏好中文简洁回答")
memory("replace", "memory", old_text="Workspace at /old", content="Workspace at /new")
memory("remove", "user", old_text="过时的偏好")
```

**`§` 分隔条目**,多行 entry 支持。substring 匹配 replace/remove,多个匹配返回 previews 让 agent 重试更精确的 `old_text`。

**指引在 system prompt 而非 tool docstring**:`main_agent/prompts/MEMORY_GUIDANCE.md` 描述何时记、何时不记、怎么写(陈述事实 vs 命令式 ✓/✗ 对照)。Tool docstring 只剩 API 表面(action / target / 返回格式)。这样模型**每轮都看到**记忆规则,不只是决定调工具时才看到。

**注入扫描**:写入前过滤 prompt-injection、exfiltration、SSH 后门、不可见 unicode 等 14 种模式 —— 因为内容下次会进 system prompt,必须把住入口。

**字符上限**(条目数不限,总字节硬约束):
- `USER.md` 1375 字符
- `MEMORY.md` 2200 字符
- 超额拒收,需要先 replace/remove 腾位置

**并发**:用 process-wide `threading.Lock`(per-target)。pydantic-ai 把同步工具放进 worker 线程跑,这把锁防止两条会话同时调 `memory.add()` 互相覆盖。**不用文件锁** —— 单进程部署不需要跨进程协调,锁文件还会污染 `prompts/` 目录。多 worker 部署再回来引入文件锁。

**配置**:`agent.memory_dir` 在 `config.yaml` 覆盖默认路径(默认就是 `main_agent/prompts/`)。Docker 部署时 `main_agent/prompts/` 已 bind-mount 到宿主,容器重建不丢笔记。

**Sub-agents 不需要长期记忆**:`init_memory_store` 只在 `main_agent/server.py` 调用;`get_memory_store()` 在未初始化时返回 None,`build_system_prompt` 自动跳过注入。

### Recall 工具

LLM 通过 `recall_tool_result(call_id=...)` 取回被驱逐的旧工具结果。`ConversationDeps`(via pydantic-ai `RunContext`)注入 `store + conversation_id + system_prompt`,会话间天然隔离。

工具 docstring 写明:
- 用于：需要复看**当时那次调用**的精确内容(继续分析已读片段)
- 不用于：要**当前状态**(那就重调原工具)、preview 已经够用、最近 history 里还有原文

### Anthropic prompt cache

当 `llm.type == "anthropic"` 时,自动在 ModelSettings 里打开:
- `anthropic_cache_tool_definitions=True` — tool 定义 (~3000 t) 缓存
- `anthropic_cache_instructions=True` — system 提示 (~800 t,含 USER PROFILE/MEMORY 块) 缓存

两者都是 90% 折扣 × 5 分钟 TTL。配合「系统提示锁定」+「始终 evict」,长会话每轮稳定部分基本全部命中。

### 调参

- `SummarizingConversation`:`threshold=20`(消息数触发压缩)、`keep_recent=10`(压缩后保留最近 N 条原文)
- `EvictingConversation`:`min_size=256`(小于此值的 tool result 不驱逐,因为 placeholder 自身约 250 字符)
- 占位符自带 `[evicted-tool-result]` 前缀,二次驱逐是 no-op(幂等)
- `MemoryStore`:`memory` 2200 / `user` 1375 字符上限(`char_limits` 参数覆盖)

## 新增 Agent

```bash
cp -r sub_agents/demo_agent sub_agents/my_agent
cd sub_agents/my_agent
# 改 config.yaml(LLM、端口、prompt 路径、启用的 skills/tools)
uv run server.py
```

## 新增 Skill

在 `skills/<name>/` 下创建:
- `SKILL.md` — frontmatter(`name`、`description`)+ 工作流文档
- `config.yaml` — skill 私有配置(API key 等,会注入子进程环境变量)
- `scripts/` — 实际执行的脚本(由 LLM 通过 `bash_execute` 调用)

在 `main_agent/config.yaml` 的 `agent.skills` 列表里加上 skill 名即可启用。

## 安全模型

| 威胁 | 防御 |
|---|---|
| LLM 写危险命令 | sandbox 容器隔离;`read_only` 文件系统(除 `/app/workspace` + tmpfs `/tmp`) |
| 路径越狱 | virtual path resolver + `..` 拒绝 + symlink 拒绝 + `relative_to` 二次校验 |
| 容器逃逸 | `cap_drop: ALL` + `no-new-privileges` + 非 root(uid 1000) |
| 资源耗尽 | `mem_limit: 1g` + `pids_limit: 200` + `cpus: 1.0` + subprocess timeout |
| 未授权访问 | sandbox HTTP 端口不暴露宿主,内部走 docker network;Bearer token 鉴权 |

## 技术栈

- **pydantic-ai** — Agent 框架(`Agent`、`run_stream_events`)
- **FastAPI** + **SSE** — HTTP 网关
- **React + Vite + TypeScript** — 前端
- **Docker Compose** — 多服务编排
- Python 3.12+

## 文档

- `CLAUDE.md` — 给 AI Coding Agent 的项目向导(架构约定、新增 agent/tool 步骤)
- `skills/<name>/SKILL.md` — 各 skill 的详细使用说明
- `docs/k8s-deploy-guide.md` — Kubernetes 部署(`deploy/k8s/` 的清单 + kustomize overlays 怎么用)
- `docs/k8s-upgrade-guide.md` — 滚动升级 / 回滚操作
- `main_agent/prompts/MEMORY_GUIDANCE.md` — 注入到 system prompt 的「memory 工具使用规则」(声明事实 vs 命令式的 ✓/✗ 对照)
