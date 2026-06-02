# AgentWeave HTTP/SSE API Contract

本文定义外部 TS Web 前端对接 AgentWeave Python 后端时使用的 HTTP/SSE 协议。TS Web 项目只依赖这里描述的接口，不依赖 Python 包内部结构。

> 状态：第一版由 FastAPI 版 `agent_runtime.server` 实现，可通过 `python -m agent_runtime.server` 启动。
> 设计过程与取舍记录见 `docs/iterations/04-fastapi-sse-api-design.md`。

## 基础约定

- Base URL 示例：`http://127.0.0.1:8765`
- 认证：`Authorization: Bearer <token>`
- JSON 请求头：`Content-Type: application/json`
- SSE 响应头：`Content-Type: text/event-stream`
- 时间字段：ISO 8601 字符串，使用后端 runtime 生成的 timestamp。
- 分页参数：`page` 从 1 开始，`page_size` 默认 100，最大值由后端配置限制。
- SSE 支持 `Last-Event-ID` 或 `after_sequence` 继续读取当前进程内已缓存事件。
- SSE 空闲时会发送 `:keepalive` 注释帧，前端解析 JSON 时应忽略注释行。
- 已完成/失败 run 默认在内存保留最多 1000 条、6 小时；诊断详情以 SQLite 为准。

浏览器原生 `EventSource` 不能设置 Authorization header。TS Web 推荐用 `fetch + ReadableStream` 或 `@microsoft/fetch-event-source` 订阅 SSE。

## Endpoints

### `GET /health`

健康检查。

Response `200`:

```json
{"status": "ok"}
```

### `POST /sessions`

创建一个会话，并返回首页 welcome message 和能力列表。

Request:

```json
{
  "session_id": "",
  "metadata": {
    "client": "agentweave-web"
  }
}
```

Response `200`:

```json
{
  "session_id": "web-9f0c1b2a",
  "message": "你好，我可以回答已接入数据领域的问数问题。",
  "capabilities": {
    "streaming": true,
    "results": true,
    "diagnostics": true,
    "resource_reload": true
  }
}
```

### `POST /sessions/{session_id}/runs`

提交用户问题，创建一次 run。前端拿到 `events_url` 后订阅 SSE。

Request:

```json
{
  "message": "403机房有多少可用机柜？",
  "max_turns": 10,
  "metadata": {
    "client_message_id": "msg_001"
  }
}
```

Response `202`:

```json
{
  "run_id": "run_01HXYZ",
  "session_id": "web-9f0c1b2a",
  "status": "queued",
  "events_url": "/runs/run_01HXYZ/events"
}
```

### `GET /runs/{run_id}/events`

订阅 run 的 SSE 事件。每条 `data:` 都是 JSON，并且必须包含：

- `type`
- `run_id`
- `sequence`
- `timestamp`

SSE 示例：

```text
id: 1
event: runtime_event
data: {"type":"runtime_event","run_id":"run_01HXYZ","sequence":1,"timestamp":"2026-06-01T10:00:00Z","payload":{"kind":"agent_start","payload":{"stage":"agent_start"}}}

id: 8
event: run_complete
data: {"type":"run_complete","run_id":"run_01HXYZ","session_id":"web-9f0c1b2a","sequence":8,"timestamp":"2026-06-01T10:00:08Z","answer":"403机房有 12 个可用机柜。","result_ids":["res_abc"]}
```

如果后端启用了模型流式输出，`run_complete` 之前还会出现 `model_delta`。前端应根据 `payload.kind`、`payload.stage`、`payload.title`、`payload.model` 自行决定展示位置；`run_complete.answer` 仍是最终答案的权威值。

### `GET /runs/{run_id}`

查询 run 当前状态。页面刷新或 SSE 断开后用它恢复最终状态。

Response `200`:

```json
{
  "run_id": "run_01HXYZ",
  "session_id": "web-9f0c1b2a",
  "status": "completed",
  "question": "403机房有多少可用机柜？",
  "answer": "403机房有 12 个可用机柜。",
  "result_ids": ["res_abc"],
  "diagnostic_run_id": "run_01HXYZ",
  "created_at": "2026-06-01T10:00:00Z",
  "completed_at": "2026-06-01T10:00:08Z",
  "error": ""
}
```

### `GET /results/{result_id}?page=1&page_size=100`

分页读取 ResultStore。前端不可依赖聊天回答里的完整行数据，大结果必须通过本接口读取。

Response `200`:

```json
{
  "result_id": "res_abc",
  "page": 1,
  "page_size": 100,
  "total_rows": 1000,
  "row_count_is_exact": false,
  "has_more": true,
  "columns": ["machine_room", "available_count"],
  "rows": [
    {"machine_room": "403", "available_count": 12}
  ],
  "sql": "SELECT ...",
  "download_url": "/results/res_abc.csv"
}
```

### `GET /results/{result_id}.csv`

导出 ResultStore 中已保存的结果行。

Response `200`:

```text
Content-Type: text/csv; charset=utf-8
```

### `GET /diagnostics/{run_id}`

读取规范化诊断数据，用于 Model Calls、执行时间线和诊断问题页。

Response `200`:

```json
{
  "run_id": "run_01HXYZ",
  "session_id": "web-9f0c1b2a",
  "summary": {
    "model_call_count": 2,
    "event_count": 12,
    "total_tokens": 1717,
    "duration_ms": 8160
  },
  "model_calls": [],
  "events": [],
  "timeline": [],
  "diagnostic_issues": []
}
```

### `POST /resources/reload`

管理端触发 skills、subagents、domains、project rules 重载。

Request:

```json
{
  "reason": "manual"
}
```

Response `200`:

```json
{
  "reloaded": true,
  "message": "skills=1 subagents=1 domains=2",
  "event": {
    "kind": "resources_reloaded",
    "payload": {"stage": "resources_reloaded"}
  }
}
```

## SSE Event Types

一次问答 run 内通过 runtime `event_callback` 发出的事件都会进入 `/runs/{run_id}/events`，后端统一包装为 `runtime_event`。常见 `payload.kind` 包括：

- `agent_start` / `agent_end` / `error`
- `subagent_dispatch` / `subagent_complete` / `subagent_trace`
- `tool_call_start` / `tool_result` / `tool_call_end`
- `result_created`
- `memory_read` / `memory_write` / `memory_event`
- `todo_event`
- `context_compressed`

后端还会额外派生 Web 友好的 SSE 事件：

- `result_created`：从 runtime `result_created` 提炼出 `result_id`、`sample_rows`、`row_count`、`has_more`。
- `model_delta`：模型输出增量，包含 `kind/stage/title/model/delta` 元信息；后端不判断展示位置。
- `run_complete`：run 成功结束，包含最终 `answer` 和 `result_ids`。
- `run_error`：run 失败结束，包含 `error`、`message` 和可选 `diagnostic_run_id`。
- `:keepalive`：SSE comment frame，不是 JSON 事件，前端应忽略。

不进入 run SSE 的内容：

- Model call 详细日志不默认实时推送；前端通过 `GET /diagnostics/{run_id}` 读取 `model_calls`。
- run 生命周期外的 HTTP 动作不进入 `/runs/{run_id}/events`，例如 `POST /sessions` 的 welcome message、独立 `POST /resources/reload` 的返回值。

### `runtime_event`

透传现有 `RuntimeEvent`，用于执行过程展示。

```json
{
  "type": "runtime_event",
  "run_id": "run_01HXYZ",
  "sequence": 2,
  "timestamp": "2026-06-01T10:00:02Z",
  "payload": {
    "kind": "subagent_trace",
    "payload": {
      "stage": "execute",
      "title": "执行查询"
    }
  }
}
```

### `result_created`

提示前端出现可查看的 ResultStore 结果。

```json
{
  "type": "result_created",
  "run_id": "run_01HXYZ",
  "sequence": 5,
  "timestamp": "2026-06-01T10:00:05Z",
  "result_id": "res_abc",
  "sample_rows": [{"available_count": 12}],
  "row_count": 1,
  "has_more": false
}
```

### `model_delta`

模型输出增量。前端收到后根据 payload 元信息决定展示在对话区、执行过程还是 diagnostics。

```json
{
  "type": "model_delta",
  "run_id": "run_01HXYZ",
  "sequence": 6,
  "timestamp": "2026-06-01T10:00:06Z",
  "payload": {
    "kind": "orchestration_model",
    "stage": "model_delta",
    "title": "编排模型调用",
    "model": "Qwen/Qwen3-32B",
    "delta": "403机房"
  }
}
```

### `run_complete`

run 成功结束。

```json
{
  "type": "run_complete",
  "run_id": "run_01HXYZ",
  "session_id": "web-9f0c1b2a",
  "sequence": 8,
  "timestamp": "2026-06-01T10:00:08Z",
  "answer": "403机房有 12 个可用机柜。",
  "result_ids": ["res_abc"],
  "diagnostic_run_id": "run_01HXYZ"
}
```

### `run_error`

run 失败结束。

```json
{
  "type": "run_error",
  "run_id": "run_01HXYZ",
  "session_id": "web-9f0c1b2a",
  "sequence": 8,
  "timestamp": "2026-06-01T10:00:08Z",
  "error": "worker_timeout",
  "message": "Text2SQL worker timed out.",
  "diagnostic_run_id": "run_01HXYZ"
}
```

## TypeScript Interfaces

```ts
export interface SessionResponse {
  session_id: string;
  message: string;
  capabilities: {
    streaming: boolean;
    results: boolean;
    diagnostics: boolean;
    resource_reload: boolean;
  };
}

export interface RunCreatedResponse {
  run_id: string;
  session_id: string;
  status: "queued" | "running" | "completed" | "failed";
  events_url: string;
}

export interface RuntimeEvent {
  type: "runtime_event";
  run_id: string;
  sequence: number;
  timestamp: string;
  payload: {
    kind: string;
    payload: Record<string, unknown>;
    error?: string;
  };
}

export interface ResultCreatedEvent {
  type: "result_created";
  run_id: string;
  sequence: number;
  timestamp: string;
  result_id: string;
  sample_rows: Record<string, unknown>[];
  row_count: number;
  has_more: boolean;
}

export interface ModelDeltaEvent {
  type: "model_delta";
  run_id: string;
  sequence: number;
  timestamp: string;
  payload: {
    kind: string;
    stage?: string;
    title?: string;
    model?: string;
    delta: string;
  };
}

export interface RunCompleteEvent {
  type: "run_complete";
  run_id: string;
  session_id: string;
  sequence: number;
  timestamp: string;
  answer: string;
  result_ids: string[];
  diagnostic_run_id?: string;
}

export interface RunErrorEvent {
  type: "run_error";
  run_id: string;
  session_id: string;
  sequence: number;
  timestamp: string;
  error: string;
  message: string;
  diagnostic_run_id?: string;
}

export type AgentWeaveSseEvent =
  | RuntimeEvent
  | ResultCreatedEvent
  | ModelDeltaEvent
  | RunCompleteEvent
  | RunErrorEvent;

export interface ResultPage {
  result_id: string;
  page: number;
  page_size: number;
  total_rows: number;
  row_count_is_exact: boolean;
  has_more: boolean;
  columns: string[];
  rows: Record<string, unknown>[];
  sql: string;
  download_url?: string;
}

export interface DiagnosticRun {
  run_id: string;
  session_id: string;
  summary: Record<string, unknown>;
  model_calls: Record<string, unknown>[];
  events: Record<string, unknown>[];
  timeline: Record<string, unknown>[];
  diagnostic_issues: Record<string, unknown>[];
}
```

稳定字段：`type`、`run_id`、`session_id`、`sequence`、`timestamp`、`payload`、`error`。`payload` 内部可以随事件类型扩展，前端应按 `payload.stage` 或 `payload.kind` 做渐进展示。
