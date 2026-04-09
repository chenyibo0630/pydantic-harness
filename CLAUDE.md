# pydantic-harness

基于 pydantic-ai 的 Agent Harness，每个 agent 独立运行，共享 gateway、sandbox 和 LLM 抽象层。

## 项目结构

```
pydantic-harness/
├── backend/                       # 共享模块（所有 agent 复用）
│   ├── core/
│   │   ├── llm/                   #   LLM 抽象
│   │   │   ├── config.py          #     LLMConfig（openai/azure/deepseek/qwen）
│   │   │   └── factory.py         #     build_model() → pydantic-ai Model
│   │   ├── sandbox/               #   沙箱：工具执行环境
│   │   │   ├── base.py            #     Sandbox ABC（统一接口）
│   │   │   ├── local.py           #     LocalSandbox（本地文件系统 + 虚拟路径）
│   │   │   ├── tools.py           #     Tool 函数（注册给 pydantic-ai Agent）
│   │   │   └── exceptions.py      #     ToolError / PathDeniedError / CommandError
│   │   ├── prompt/                #   Prompt 加载器
│   │   │   └── loader.py          #     load_prompts(dir, main_file)
│   │   └── memory/                #   会话记忆
│   │       ├── base.py            #     Memory ABC
│   │       └── in_memory.py       #     InMemoryStore
│   └── gateway/                   #   FastAPI 网关层
│       ├── routes.py              #     POST /chat/stream（SSE）
│       ├── schemas.py             #     ChatRequest / ChatError
│       └── sse.py                 #     SSE 流式桥接（run_stream_events）
├── main_agent/                    # 主 Agent（独立项目）
│   ├── server.py                  #   入口：uv run server.py
│   ├── config.yaml                #   配置（LLM、server、agent）
│   ├── config.py                  #   Settings 加载（使用 load_prompts）
│   ├── agent.py                   #   Agent 定义 + tool 绑定
│   ├── tools/
│   │   └── tools.py               #   从 sandbox 中选择需要的工具列表
│   └── prompts/                   #   系统提示词（自动拼接所有 .md）
│       ├── SYSTEM.md              #     主提示词（优先加载）
│       ├── SOUL.md                #     人格定义
│       └── EXPERIENCE.md          #     工具使用经验（失败教训）
├── sub_agents/                    # 子 Agent 目录
│   └── demo_agent/                #   示例子 Agent
├── frontend/                      # React + Vite 前端
├── tests/                         # pytest 测试
└── pyproject.toml                 # 根项目（共享开发依赖）
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
- 每个 agent 是独立项目，有自己的 `config.yaml`、`server.py`
- 通过 `sys.path.insert` 引用根目录下的共享模块（backend/）
- agent 之间互不依赖

### Sandbox（backend/core/sandbox）
- `Sandbox` ABC 定义统一接口：execute_command / read_file / write_file / str_replace / list_dir / glob_files / grep_search
- `LocalSandbox` 实现本地文件系统沙箱，将 `/workspace/` 虚拟路径映射到真实 workspace 目录
- `tools.py` 中的 tool 函数是给 pydantic-ai 注册的薄包装，委托给活跃的 Sandbox 实例
- 启动时调用 `init_sandbox(workspace_path)` 初始化

### Tools
- 工具定义在 `backend/core/sandbox/tools.py`，所有 agent 共享
- 每个 agent 在自己的 `tools/tools.py` 中显式选择需要的工具：
  ```python
  from backend.core.sandbox import bash_execute, read_file, glob_files
  DEFAULT_TOOLS = [bash_execute, read_file, glob_files]
  ```
- 可用工具：`bash_execute` / `read_file` / `write_file` / `str_replace` / `list_dir` / `glob_files` / `grep_search`
- pydantic-ai 自动从函数签名和 docstring 生成 tool schema

### LLM 配置（backend/core/llm）
- `LLMConfig` 统一描述所有 LLM provider
- `build_model(config)` 返回 pydantic-ai `Model` 实例
- 支持类型：`openai` / `azure` / `deepseek` / `qwen`

### Prompt 加载（backend/core/prompt）
- `load_prompts(prompts_dir, main_file)` 自动拼接目录下所有 `.md` 文件
- `main_file`（默认 SYSTEM.md）优先加载，其余按文件名排序追加
- 新增 prompt 文件放入 `prompts/` 目录即可自动生效

### Memory（backend/core/memory）
- `Memory` ABC 定义 get/set/delete 接口
- `InMemoryStore` 实现进程内会话记忆
- 通过 `app.state.memory` 注入到 gateway

### Gateway（backend/gateway）
- 共享的 FastAPI 路由和 SSE 流式处理
- 使用 `agent.run_stream_events()` 捕获文本、工具调用和工具结果事件
- 路由从 `app.state.agent_registry`、`app.state.memory`、`app.state.stream_timeout` 读取配置
- 不依赖任何具体 agent 或 config 模块

### SSE 事件格式
| 事件 | 数据 | 时机 |
|------|------|------|
| `message_start` | `{conversation_id}` | 响应开始 |
| `text_delta` | `{text}` | 每个 LLM token |
| `tool_call` | `{tool_name, tool_call_id}` | LLM 决定调用工具 |
| `tool_result` | `{tool_name, tool_call_id, content}` | 工具执行完毕 |
| `message_end` | `{conversation_id, usage: {input_tokens, output_tokens, total_tokens}}` | 响应结束 |
| `error` | `{error, message}` | 异常 |

### 新增 Agent 步骤
1. 在 `sub_agents/` 下创建目录（如 `sub_agents/code_agent/`）
2. 复制 `demo_agent/` 结构：`server.py`、`config.py`、`config.yaml`、`agent.py`
3. 修改 `config.yaml` 中的 LLM 和端口配置
4. 在 `tools/tools.py` 中从 `backend.core.sandbox` 选择需要的工具
5. `cd sub_agents/code_agent && uv run server.py`

### 新增 Tool 步骤
1. 在 `backend/core/sandbox/base.py` 的 `Sandbox` ABC 中添加抽象方法
2. 在 `backend/core/sandbox/local.py` 的 `LocalSandbox` 中实现
3. 在 `backend/core/sandbox/tools.py` 中添加 tool 包装函数（带 docstring）
4. 在 `__init__.py` 中导出
5. 在需要该工具的 agent 的 `tools/tools.py` 中注册

## 技术栈
- **pydantic-ai** — Agent 框架（`Agent`、`run_stream_events`、`Tool`）
- **FastAPI** — HTTP 网关（SSE streaming）
- **Pydantic** — 数据校验（config、schemas）
- **PyYAML** — 配置文件解析
- **React + Vite** — 前端（tool call badge 实时展示）
- Python 3.12+
