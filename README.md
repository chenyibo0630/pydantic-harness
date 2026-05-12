# pydantic-harness

基于 [pydantic-ai](https://ai.pydantic.dev/) 的 Agent Harness。每个 agent 独立运行、通过共享的 gateway / sandbox / skills / LLM 抽象层组合能力。

## 特性

- **Agent 独立**: 每个 agent 自带 `config.yaml` / `server.py` / `prompts/`,与其他 agent 互不依赖
- **沙箱化工具执行**: 工具调用全部走独立的 sandbox 容器,主进程不直接执行 subprocess
- **容器硬化**: sandbox 容器 `read_only` + `cap_drop ALL` + 非 root + 内存/PID/CPU 限额 + symlink 防御
- **多 LLM 提供商**: openai / azure / deepseek / qwen 一套配置
- **Skills 系统**: 把外部能力(搜索 / PDF / 视频生成)打包成可复用 skill,LLM 自动发现
- **流式 SSE**: text / tool_call / tool_result / tool_progress(心跳)/ message_end 标准化事件
- **会话记忆**: 装饰器分层(摘要压缩 + tool result 驱逐 + 按需 recall),上下文不爆

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
│   │   ├── prompt/                # Prompt 加载器
│   │   ├── memory/                # 会话记忆 + tool result 驱逐缓存
│   │   ├── tools/                 # agent 工具(ask_user / recall_tool_result)
│   │   └── skills/                # Skills 加载与工具暴露
│   └── gateway/                   # FastAPI 路由 + SSE 流式桥接
├── main_agent/                    # 主 Agent
│   ├── server.py                  # 入口
│   ├── config.yaml                # 运行配置(LLM / agent / sandbox)
│   ├── agent.py                   # Agent 定义 + tool 绑定
│   ├── tools/                     # 选择启用的工具列表
│   └── prompts/                   # 系统提示词(自动拼接所有 .md)
├── sub_agents/                    # 其他独立子 Agent
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
└── tests/                         # pytest 测试
```

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

`Memory` ABC 管理三类 per-conversation 状态,保证存储层级一致(in-memory ↔ in-memory,未来的 file ↔ file 不会出现孤儿):

- **消息历史**: `list[ModelMessage]` per `conversation_id`
- **Tool result 缓存**: 被 `EvictingMemory` 驱逐的大工具结果,按 `(conversation_id, call_id)` 寻址
- **系统提示快照**: 会话首轮锁定的 system prompt,后续轮次复用同一份字节

运行时组装(server.py 启动时):

```
SummarizingMemory          # 超过阈值后台异步压缩旧消息为摘要
  └─ EvictingMemory        # 始终把 ≥ min_size 的 ToolReturnPart.content 搬到 cache
       └─ InMemoryStore    # 进程内 dict(消息 + tool cache + 提示快照 都在这)
```

### 系统提示词:会话粒度锁定

`Agent.instructions` 是回调,从 `ctx.deps.system_prompt` 取值。Gateway 在每个请求开头 `load-or-lock`:首轮 `build_system_prompt(settings, skills)` 读盘并 `memory.put_system_prompt()`;后续轮直接 `memory.get_system_prompt()` 复用。

- 改 `SYSTEM.md` / `SOUL.md` / `EXPERIENCE.md` → 只影响**新会话**,进行中的不受影响
- 同一会话内 LLM 每次调用看到的 system message 字节完全一致 → prompt cache 可命中
- `memory.delete(conv_id)` 同时清快照,下次进入重新读盘

### Tool result 驱逐:始终 evict(为了 prompt cache 字节稳定)

每次 `memory.set()` 都扫描**所有** `ToolReturnPart`(无 keep_recent 窗口),大于 `min_size` 的把 `content` 搬到缓存,原位换成占位符(`tool_call_id` 不动,API 配对不破):

```
[evicted-tool-result] tool=read_file call_id=call_a3f size=8421chars lines=230
preview: # README...
Original tool output was moved to the cache to save context.
NOTE: this is a snapshot of a past call, NOT current state.
For fresh data, call the original tool again.
To reload this exact snapshot, call recall_tool_result(call_id="call_a3f").
```

**为什么"始终 evict"而不是滑动窗口**:滑动窗口意味着驱逐边界每轮向后推一格,prefix 字节随之周期性变化,Anthropic prompt cache 每次都 miss。始终 evict 后,从 turn 2 起 stored prefix 完全 byte-stable,长会话场景 cache 命中率接近 100%。

**代价**:模型在 turn N+1 看不到 turn N 的 tool result 原文,只看到占位符。需要原文时调 `recall_tool_result(call_id=...)`。

### Recall

LLM 通过 `recall_tool_result(call_id=...)` 工具按需取回原文。工具的 docstring 已经写明何时该用、何时该重调原工具(stale 风险)、何时不该 recall(浪费 token)。`MemoryDeps` 通过 pydantic-ai 的 `RunContext` 注入 `memory + conversation_id + system_prompt`,会话间天然隔离。

### 长期记忆(MEMORY.md / USER.md)

Hermes 风格的跨会话策展记忆。两个文件**和静态 prompt 文件一起放在 `main_agent/prompts/`**:
- `USER.md` — 用户画像(姓名/角色/偏好/沟通风格)
- `MEMORY.md` — agent 自己的笔记(环境事实/项目约定/工具坑)

`load_prompts()` 自动跳过这两个文件 —— 它们由 `MemoryStore` 用独立的 USER PROFILE / MEMORY 章节(带 usage 指标头)注入 system prompt,避免双重渲染。

`§` 分隔条目,多行 entry 支持。Agent 通过 `memory(action, target, content?, old_text?)` 工具自主写入:

```python
memory("add", "user", content="用户偏好中文简洁回答")
memory("replace", "memory", old_text="Workspace at /old", content="Workspace at /new")
memory("remove", "user", old_text="过时的偏好")
```

**Frozen snapshot 模式**:每个新会话首轮把当前磁盘内容注入 system prompt,然后**锁定**整个会话不变。中途 `memory` 工具写入只落盘,不影响当前会话(保住 prompt cache)。下一个新会话才看到更新。

**注入扫描**:写入前过滤 prompt-injection、exfiltration、SSH 后门、不可见 unicode 等模式 —— 因为这些内容下次会进 system prompt,必须把住入口。

**字符上限**(条目数不限,总字节硬约束):
- `USER.md` 1375 字符
- `MEMORY.md` 2200 字符
- 超额拒收,需要先 replace/remove 腾位置

文件路径可通过 `agent.memory_dir` 在 `config.yaml` 覆盖。Docker 部署时 `main_agent/prompts/` 已 bind-mount 到宿主,容器重建不丢笔记。

Sub-agents **不需要长期记忆**,只在 `main_agent` 初始化 MemoryStore;`get_memory_store()` 在未初始化时返回 None,`build_system_prompt` 自动跳过注入。

### Anthropic prompt cache

当 `llm.type == "anthropic"` 时,自动在 ModelSettings 里打开:
- `anthropic_cache_tool_definitions=True` — tool 定义 (~3000 t) 缓存
- `anthropic_cache_instructions=True` — system 提示 (~800 t) 缓存

两者都是 90% 折扣 × 5 分钟 TTL。配合上面的「系统提示锁定」+「始终 evict」,长会话每轮重复发送的稳定部分基本全部命中 cache。

调参:
- `min_size=256`(默认)— 小于这个字符数的 tool result 不值得换成占位符(placeholder 本身约 250 字符)
- 占位符自带 `[evicted-tool-result]` 前缀,二次驱逐是 no-op(幂等)

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
