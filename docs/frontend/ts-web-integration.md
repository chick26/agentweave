# TS Web Integration Guide

本文面向独立 TS Web 项目，说明如何接入 AgentWeave 后端。Web 项目只需要 HTTP/SSE，不需要 Python 代码。

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
6. 收到 `run_complete` 时写入最终 answer。
7. 用户点击结果时调用 `GET /results/{result_id}` 分页加载表格。
8. 用户打开诊断页时调用 `GET /diagnostics/{run_id}`。

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
