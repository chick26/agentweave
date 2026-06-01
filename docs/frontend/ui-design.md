# TS Web UI Design

本文给独立 TS Web 前端一个推荐的信息架构。目标是让 Web 端成为跨设备业务入口，而不是复制 Streamlit 调试台。

## 页面结构

- Chat：默认首页，承载问答、实时执行过程和结果入口。
- Results：以 drawer 或独立页展示 ResultStore 分页表格。
- Diagnostics：面向开发/运维，展示 Model Calls、timeline、diagnostic issues。
- Settings：配置 API base URL、Bearer Token、默认 session 行为。

## Chat 页面

推荐布局：

```text
┌──────────────┬──────────────────────────────┬─────────────────────┐
│ Sessions     │ Chat                         │ Run Trace           │
│              │                              │                     │
│ web-001      │ user: 403机房...             │ agent_start         │
│ web-002      │ assistant: 403机房有...      │ subagent_dispatch   │
│              │ [res_abc]                    │ result_created      │
└──────────────┴──────────────────────────────┴─────────────────────┘
```

- 左侧 session list 可以第一版只做当前 session，不急着实现历史列表。
- 中间 chat 流展示用户问题和最终 answer。
- 右侧 trace 默认折叠或窄栏展示，避免业务用户被诊断细节干扰。
- `result_created` 出现时，在 assistant 消息附近显示结果 chip，例如 `res_abc`。

## Result Drawer

- 点击 `result_id` 打开 drawer。
- 顶部显示 SQL、已存储行数、是否截断。
- 表格通过 `/results/{result_id}?page=&page_size=` 分页加载。
- 支持 CSV 下载时使用后端返回的 `download_url`。
- 如果 `row_count_is_exact=false`，显示“行数为已存储行数，不代表真实总数”。

## Diagnostics 页面

- 按 `run_id` 加载 `/diagnostics/{run_id}`。
- 第一屏显示 summary：模型调用数、总 tokens、总耗时、诊断问题数。
- Model Calls 默认折叠；缺 request/response payload 时不要展示空 tab。
- Timeline 优先展示 canonical timestamp 完整的项目。

## 事件展示优先级

前端优先消费这些事件：

1. `run_complete` / `run_error`
2. `result_created`
3. `runtime_event.payload.kind = result_created`
4. `runtime_event.payload.kind = subagent_dispatch`
5. `runtime_event.payload.kind = subagent_trace`
6. `runtime_event.payload.kind = tool_result`

对未知事件使用通用 JSON 折叠展示，不阻断聊天流程。

## 视觉与交互建议

- 主体验是业务问数，不要做营销首页。
- 结果表格要比执行 trace 更显眼。
- trace 文案优先显示 `payload.title`、`payload.stage`、`payload.output.result_id`。
- 错误态要给出诊断入口，而不是只显示模型原始错误。
