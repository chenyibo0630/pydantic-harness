# pydantic-harness

基于 [pydantic-ai](https://ai.pydantic.dev/) 的 Agent Harness。每个 agent 独立运行、通过共享的 gateway / sandbox / skills / LLM 抽象层组合能力。

## 特性

- **Agent 独立**: 每个 agent 自带 `config.yaml` / `server.py` / `prompts/`,与其他 agent 互不依赖
- **沙箱化工具执行**: 工具调用全部走独立的 sandbox 容器,主进程不直接执行 subprocess
- **容器硬化**: sandbox 容器 `read_only` + `cap_drop ALL` + 非 root + 内存/PID/CPU 限额 + symlink 防御
- **多 LLM 提供商**: openai / azure / deepseek / qwen 一套配置
- **Skills 系统**: 把外部能力(搜索 / PDF / 视频生成)打包成可复用 skill,LLM 自动发现
- **流式 SSE**: text / tool_call / tool_result / tool_progress(心跳)/ message_end 标准化事件
- **会话记忆**: in-memory + 摘要式压缩,上下文不爆

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
# 填入 llm.api_key / llm.type 等

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
│   │   ├── memory/                # 会话记忆(InMemory + Summarizing)
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
- `SANDBOX_TOKEN` — sandbox HTTP 鉴权(生产必填)
- `SANDBOX_ALLOW_NO_AUTH=true` — 仅本地开发可关闭鉴权
- `SANDBOX_LOG_LEVEL` — sandbox 服务日志级别(默认 INFO)
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

**路径约定**: 所有路径相对于工作区根。用 `"."` 表示根本身,`"foo.pdf"` 表示根下文件,`"sub/bar.txt"` 表示嵌套。`/skills/<name>/...` 是唯一的特殊绝对路径(只读)。

## SSE 事件

| 事件 | 数据 | 触发时机 |
|---|---|---|
| `message_start` | `{conversation_id}` | 响应开始 |
| `text_delta` | `{text}` | 每个 LLM token |
| `tool_call` | `{tool_name, tool_call_id}` | LLM 决定调用工具 |
| `tool_progress` | `{tool_name, tool_call_id, elapsed}` | 工具运行 ≥10s 时心跳 |
| `tool_result` | `{tool_name, tool_call_id, content}` | 工具执行完毕 |
| `message_end` | `{conversation_id, usage}` | 响应结束 |
| `error` | `{error, message}` | 异常 |

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
- **PyMuPDF** — PDF 解析(pdf skill)
- **React + Vite + TypeScript** — 前端
- **Docker Compose** — 多服务编排
- Python 3.12+

## 文档

- `CLAUDE.md` — 给 AI Coding Agent 的项目向导(架构约定、新增 agent/tool 步骤)
- `skills/<name>/SKILL.md` — 各 skill 的详细使用说明
