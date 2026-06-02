# FastAPI 与 SSE API 设计过程

> 本文记录 AgentWeave 从 Streamlit 本地 UI 走向独立后端 API 的设计过程。
>
> 稳定接口契约请看 `docs/api/http-sse-contract.md`；本文关注为什么这样设计、做过哪些取舍，以及后续如何演进。

## 背景

前一轮 pi-agent 风格分层已经把 `core/memory/storage/registry/ui` 边界拆开，但 Streamlit 仍然承担了事实上的应用入口职责：

- 初始化 `AgentRuntime`。
- 管理 session id、chat messages、diagnostics、ResultStore 预览。
- 直接消费 runtime events。
- 承担本地调试 UI、导出、模板、资源 reload 等产品功能。

为了支持独立 TS Web 前端和后续 TUI，需要把“后端能力”从 Streamlit 中沉淀出来。目标不是把系统拆成多个服务，而是在当前 Python runtime 内新增一个清晰的 HTTP/SSE 适配层，让不同 UI 只通过同一套接口消费 AgentWeave。

## 设计目标

- 后端提供统一能力：session、run、SSE events、ResultStore、diagnostics、resource reload。
- TS Web 项目独立维护，不 import Python 代码。
- Streamlit 退回本地调试台，不再是唯一运行入口。
- TUI 后续保留在本仓库，但优先复用同一 HTTP/SSE 协议。
- 大结果继续通过 ResultStore 分页读取，不塞进模型上下文或 SSE 大包。
- 后端只负责事件结构化，不替前端做展示策略判断。

## 第一版接口形态

第一版采用 FastAPI + SSE：

- FastAPI 提供 HTTP 路由、OpenAPI 文档、CORS 和 token 鉴权。
- SSE 用于 run 内实时事件流。
- 不引入 WebSocket，避免前端和后端同时承担更复杂的连接状态。
- 不拆微服务，所有 state 仍由当前 Python runtime 和 SQLite stores 管理。

接口分两类：

- run 外 HTTP：`POST /sessions`、`POST /resources/reload`、`GET /health`。
- run 内状态与流：`POST /sessions/{session_id}/runs`、`GET /runs/{run_id}/events`、`GET /runs/{run_id}`。

结果和诊断仍是独立资源：

- `GET /results/{result_id}` 分页读取 SQL 结果。
- `GET /results/{result_id}.csv` 导出已保存结果。
- `GET /diagnostics/{run_id}` 读取 model calls、events timeline 和 diagnostic issues。

## 应用服务层

实现时没有让 FastAPI handler 直接拼装 `AgentRuntime`，而是新增 `AgentService`：

- `AgentService` 管理 runtime、diagnostic store、run record、SSE event cache。
- FastAPI 只做请求解析、鉴权、错误映射和 streaming response。
- 这样后续如果要接 TUI、本地 CLI 或替换 HTTP 框架，业务流程不需要重写。

当前 `AgentService` 负责：

- 创建 session 并运行 `SessionStart` hook。
- 创建后台 run。
- 将 runtime events 包装成 `runtime_event` SSE。
- 将 result event 提炼成独立 `result_created` SSE。
- 将模型增量输出包装成 `model_delta` SSE。
- run 完成后写入 diagnostics，并发送 `run_complete`。
- run 失败时写入 diagnostics，并发送 `run_error`。

## SSE 事件设计

SSE 事件保留统一 envelope：

```json
{
  "type": "runtime_event",
  "run_id": "run_xxx",
  "sequence": 1,
  "timestamp": "2026-06-01T10:00:00Z",
  "payload": {}
}
```

当前主要事件：

- `runtime_event`：透传 runtime events，前端可按 `payload.kind` / `payload.payload.stage` 展示。
- `result_created`：提炼 ResultStore 指针，方便前端展示 result chip。
- `model_delta`：模型增量输出，payload 带 `kind/stage/title/model/delta`。
- `run_complete`：最终答案和 result ids 的权威状态。
- `run_error`：失败状态和诊断入口。
- `:keepalive`：空闲时维持 SSE 长连接的注释帧。

### 从 `answer_delta` 到 `model_delta`

最初讨论过只增加 `answer_delta`，表示最终 assistant answer 的增量。但这个语义会把后端推向 UI 策略层：后端需要判断哪些模型输出属于“答案”，哪些属于 worker、reasoning、tool arguments 或 diagnostics。

最终改为统一的 `model_delta`：

- 后端不判断展示位置。
- 后端只标注模型增量的来源和元信息。
- 前端根据 `payload.kind/stage/title/model` 决定放到 Chat、Trace 还是 Diagnostics。
- `run_complete.answer` 仍是最终答案权威值，用于断线恢复和丢包校准。

这更符合“后端能力层，前端解释器”的边界。

## Streaming 实现取舍

`AgentRuntime.ask()` 默认仍走 `Runner.run(...)`，保持 Streamlit 和旧调用行为不变。

当调用方传入 `model_delta_callback` 时，才走 `Runner.run_streamed(...)`：

- 消费 SDK `stream_events()`。
- 当前只从 raw response text delta 中提取可见文本 delta。
- 忽略 reasoning delta、function call argument delta、tool-call 中间参数。
- 通过 callback 把模型增量交给 `AgentService` 发布为 `model_delta`。

这让 streaming 成为可选能力，而不是破坏性替换。后续如果 worker、hook、compressor 也切到 streaming，可复用同一 `model_delta` 事件类型。

## 安全与运行时保护

第一版采用轻量但明确的安全默认值：

- 默认监听 `127.0.0.1`。
- 如果绑定非 loopback host，必须配置 `AGENTWEAVE_SERVER_TOKEN`。
- 前端通过 Bearer Token 调用。
- CORS 默认只允许 localhost 前端，可通过 `AGENTWEAVE_CORS_ORIGINS` 配置。

长期运行保护：

- run record 保存在内存里用于 SSE 断线恢复。
- 已完成/失败 run 默认最多保留 1000 条。
- 已完成/失败 run 默认 6 小时 TTL。
- 诊断详情以 SQLite `DiagnosticStore` 为准，内存 cache 只是在线恢复窗口。

## 前端消费策略

TS Web 可以按如下方式消费：

- Chat 区：
  - 可以选择消费 `model_delta` 中 `payload.kind === "orchestration_model"` 的文本。
  - 收到 `run_complete.answer` 后用最终答案覆盖校准。
- Trace 区：
  - 消费 `runtime_event`、`result_created`、必要的 `model_delta`。
- Results：
  - 点击 `result_id` 后通过 ResultStore API 分页读取。
- Diagnostics：
  - 通过 `/diagnostics/{run_id}` 读取 model calls 和事件时间线。

后端不规定这些展示策略，只保证事件顺序、元信息和最终状态。

## 已覆盖测试

当前测试覆盖：

- FastAPI token 鉴权。
- 非 localhost 绑定必须配置 token。
- session 创建与 run 创建。
- SSE 输出 `runtime_event`、`result_created`、`model_delta`、`run_complete`。
- `after_sequence` 恢复包含 `model_delta` 的事件流。
- ResultStore 分页和 CSV 导出。
- diagnostics 持久化读取。
- run cache 最大数量与 TTL 淘汰。
- 文档契约包含 endpoint、事件类型和 TypeScript interface。

当前全量结果：

```bash
PYTHONPATH=. uv run pytest
```

结果：`160 passed`。

## 后续方向

- 将 TUI 作为同一 HTTP/SSE 协议的第二个消费者，而不是直接 import runtime。
- 评估是否把 `model_delta` 扩展到 Text2SQL worker、hook 和 context compressor。
- 为 TS Web 补充一个最小端到端 smoke 示例。
- 如果未来出现多用户需求，再单独设计 session ownership、用户鉴权和权限边界。
