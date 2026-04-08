# pydantic-harness

基于 pydantic-ai 的 Agent Harness，每个 agent 独立运行，共享 gateway 和 LLM 抽象层。

## 项目结构

```
pydantic-harness/
├── backend/                  # 共享模块（所有 agent 复用）
│   ├── core/llm/             #   LLM 抽象：LLMConfig + build_model()
│   │   ├── config.py         #     支持 openai / azure / deepseek / qwen
│   │   └── factory.py        #     按 type 构建 pydantic-ai Model
│   └── gateway/              #   FastAPI 网关层
│       ├── routes.py         #     POST /chat/stream（SSE）
│       ├── schemas.py        #     ChatRequest / ChatError
│       └── sse.py            #     SSE 格式化 + 流式桥接
├── main_agent/               # 主 Agent（独立项目）
│   ├── server.py             #   入口：uv run server.py
│   ├── config.yaml           #   配置（LLM、server、agent）
│   ├── config.py             #   Settings 加载
│   ├── agent.py              #   Agent 定义 + tool 绑定
│   ├── tools/                #   Agent 专属工具
│   │   └── bash/             #     bash 命令执行
│   └── prompts/              #   系统提示词
│       └── SYSTEM.md
├── sub_agents/               # 子 Agent 目录
│   └── demo_agent/           #   示例子 Agent（独立项目）
│       ├── server.py         #     入口：uv run server.py
│       ├── config.py
│       └── agent.py
├── frontend/                 # React + Vite 前端
├── tests/                    # pytest 测试
└── pyproject.toml            # 根项目（共享开发依赖）
```

## 启动方式

```bash
# Main Agent（默认端口见 config.yaml）
cd main_agent && uv run server.py

# Sub Agent
cd sub_agents/demo_agent && uv run server.py

# Frontend（开发模式，proxy → backend）
cd frontend && npm run dev

# 测试
.venv/Scripts/python -m pytest tests/ -v
```

## 关键约定

### Agent 独立性
- 每个 agent 是独立项目，有自己的 `pyproject.toml`、`config.yaml`、`server.py`
- 通过 `sys.path.insert` 引用根目录下的共享模块（backend/）
- agent 之间互不依赖

### LLM 配置（backend/core/llm）
- `LLMConfig` 统一描述所有 LLM provider
- `build_model(config)` 返回 pydantic-ai `Model` 实例
- 支持类型：`openai` / `azure` / `deepseek` / `qwen`
- 在各 agent 的 `config.yaml` 中配置

### Gateway（backend/gateway）
- 共享的 FastAPI 路由和 SSE 流式处理
- 路由从 `app.state.agent_registry` 和 `app.state.stream_timeout` 读取配置
- 不依赖任何具体 agent 或 config 模块

### SSE 事件格式
| 事件 | 数据 | 时机 |
|------|------|------|
| `text_delta` | `{"text": "..."}` | 每个 LLM token |
| `done` | `{"usage": {input_tokens, output_tokens, total_tokens}}` | 流结束 |
| `error` | `{"error": "Type", "message": "..."}` | 异常 |

### Tools
- 工具定义在各 agent 的 `tools/` 目录下
- 每个 tool 是普通 Python 函数，通过 `TOOLS` 列表注册到 agent
- pydantic-ai 自动从函数签名和 docstring 生成 tool schema

### 新增 Agent 步骤
1. 在 `sub_agents/` 下创建目录（如 `sub_agents/code_agent/`）
2. 复制 `demo_agent/` 结构：`server.py`、`config.py`、`config.yaml`、`agent.py`
3. 修改 `config.yaml` 中的 LLM 和端口配置
4. `cd sub_agents/code_agent && uv run server.py`

### 新增 Tool 步骤
1. 在 agent 的 `tools/` 下创建模块（如 `tools/web_search/`）
2. 实现 tool 函数（普通函数或 async 函数，带 docstring）
3. 在 `tools/__init__.py` 的 `TOOLS` 列表中注册

## 技术栈
- **pydantic-ai** — Agent 框架（`Agent`、`run_stream`、`Tool`）
- **FastAPI** — HTTP 网关（SSE streaming）
- **Pydantic** — 数据校验（config、schemas）
- **PyYAML** — 配置文件解析
- Python 3.12+
