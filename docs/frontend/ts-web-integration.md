# TS Web Integration Guide

本文面向独立 TS Web 项目，说明如何接入 AgentWeave 后端。Web 项目只需要 HTTP/SSE，不需要 Python 代码。

## 启动后端

在 AgentWeave Python 项目内启动 FastAPI 后端：

```bash
export AGENTWEAVE_SERVER_TOKEN=dev-token
uv run python -m agent_runtime.server
```

默认监听 `http://127.0.0.1:8765`。如果要跨设备访问，显式设置 `AGENTWEAVE_SERVER_HOST=0.0.0.0`，并必须配置 `AGENTWEAVE_SERVER_TOKEN`。

## 推荐项目结构

```text
agentweave-web/
├── src/
│   ├── api/
│   │   ├── agentweave.ts
│   │   └── sse.ts
│   ├── components/
│   │   ├── ChatView.tsx
│   │   ├── EventTimeline.tsx
│   │   ├── ResultDrawer.tsx
│   │   └── DiagnosticsView.tsx
│   └── state/
│       └── sessions.ts
└── .env.local
```

`.env.local`:

```bash
VITE_AGENTWEAVE_API_BASE=http://127.0.0.1:8765
VITE_AGENTWEAVE_TOKEN=dev-token
```

## 调用流程

1. 页面启动时调用 `POST /sessions`，拿到 `session_id` 和 welcome message。
2. 用户输入问题后调用 `POST /sessions/{session_id}/runs`，拿到 `run_id` 和 `events_url`。
3. 使用 `fetch + ReadableStream` 订阅 `GET /runs/{run_id}/events`。
4. 收到 `runtime_event` 时更新执行过程。
5. 收到 `result_created` 时把 `result_id` 加入可点击结果列表。
6. 收到 `model_delta` 时，根据 `payload.kind/stage/title/model` 自行决定展示位置。
7. 收到 `run_complete` 时用最终 `answer` 校准 assistant message。
8. 用户点击结果时调用 `GET /results/{result_id}` 分页加载表格。
9. 用户打开诊断页时调用 `GET /diagnostics/{run_id}`。

## 事件边界

SSE 会包含一次问答 run 内的 runtime events，并以 `runtime_event` 包装。前端应优先读取 `event.payload.kind` 和 `event.payload.payload.stage` 来渲染执行过程。

常见 `payload.kind`：

- `agent_start` / `agent_end` / `error`
- `subagent_dispatch` / `subagent_complete` / `subagent_trace`
- `tool_call_start` / `tool_result` / `tool_call_end`
- `result_created`
- `memory_read` / `memory_write` / `memory_event`
- `todo_event`
- `context_compressed`

后端还会派生更适合 Web 使用的 JSON SSE 事件：`result_created`、`model_delta`、`run_complete`、`run_error`。SSE 空闲时可能收到 `:keepalive` 注释帧，这不是 JSON，解析器应忽略。

`model_delta` 是后端对模型输出增量的结构化透传，payload 会带 `kind`、`stage`、`title`、`model`、`delta`。后端不决定它应该展示在哪里；Chat、Trace、Diagnostics 的展示策略由前端根据这些元信息自行判断。`run_complete.answer` 仍作为最终答案权威值，用于断线恢复和丢包校准。

Model Call 详细日志不默认进入 SSE；需要在 Diagnostics 页面通过 `GET /diagnostics/{run_id}` 读取 `model_calls`。`POST /sessions`、`POST /resources/reload` 这类 run 外 HTTP 动作也不进入某个 run 的 SSE 流。

## 前端状态建议

```ts
type ChatMessage =
  | { role: "assistant"; content: string; run_id?: string }
  | { role: "user"; content: string };

interface WebRunState {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed";
  events: AgentWeaveSseEvent[];
  result_ids: string[];
  answer: string;
  error?: string;
}
```

Chat 页面不应该保存完整 SQL 结果行，只保存 `result_id` 和少量 sample rows。完整结果通过 Result API 按页读取。

## 鉴权与 SSE

所有请求都使用 Bearer Token：

```ts
headers: { Authorization: `Bearer ${token}` }
```

不要用浏览器原生 `EventSource` 连接带鉴权的生产后端，因为它不能设置 Authorization header。推荐直接复制 `docs/frontend/examples/sse-client.ts`，或使用 `@microsoft/fetch-event-source`。

## 错误处理

- `401/403`：提示用户检查 token。
- `404 run/result`：提示本地结果库可能被清理，允许刷新会话。
- SSE 断开：先调用 `GET /runs/{run_id}` 恢复最终状态；如果仍为 running，再重连 SSE。
- `run_error`：展示 `message`，并提供 `diagnostic_run_id` 链接。

## 示例文件

- `docs/frontend/examples/api-client.ts`：HTTP API client 和类型。
- `docs/frontend/examples/sse-client.ts`：无依赖 SSE 订阅器。
- `docs/frontend/examples/usage.ts`：最小聊天调用示例。
