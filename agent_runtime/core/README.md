# agent_runtime.core 概览

本文档用于记录 `agent_runtime/core/` 的职责概览和后续 review 讨论。先保持描述性，不直接定义重构方案；review 过程中可在对应条目下补充问题、决策和行动项。

## 总体定位

`agent_runtime.core` 是 AgentWeave 的主运行时核心，负责把 Orchestrator、session、上下文压缩、工具、事件、模型配置和结果映射串起来。

主链路：

1. `AgentRuntime.ask()` 接收用户输入和 session。
2. `SessionManager` 准备 SDK `SQLiteSession`，必要时压缩历史上下文。
3. `runtime.py` 直接构造 Orchestrator Agent，并按普通/流式模式调用 OpenAI Agents SDK Runner。
4. `tool_factory` 按 bot 配置挂载 runtime tools 和 subagent tools。
5. `RuntimeContext` 和 `EventBus` 记录运行事件。
6. subagent extension 注册 tools、prompt context、capability resolver 和 ResultFormatter。
7. `ResultStore` 按标准 artifact envelope 保存大结果，`result_mapper` 将最终输出和事件流整理成 API 返回结构。

## 文件职责


| 文件                   | 当前职责                                                                                              | Review 记录                                                                                                                                                                                                                                                      |
| -------------------- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`        | core 包标识，目前只有模块说明。                                                                                |                                                                                                                                                                                                                                                                |
| `runtime.py`         | 主门面。初始化 registry、memory、result store、subagent runner、session manager、hooks；`ask()` 是一次用户请求的主流程入口。 |                                                                                                                                                                                                                                                                |
| `tool_factory.py`    | 构造主编排器可用工具：memory search/write、load skill、todo，以及按 bot 裁剪后的 subagent worker tools。             | 已移除 get current time 工具，改为 runtime prompt 注入当前时间，减少工具上下文偏移。                                                                                                                                                                                                           |
| `tool_helpers.py`    | 定义 `ToolOutput`，并统一提供 `tool_call_start`、`tool_result`、`tool_call_end` 事件辅助函数。                       | `ToolOutput` 已从 `tool_protocol.py` 合并到这里，tool lifecycle 封装集中到一个模块。                                                                                                                                                                                                              |
| `context.py`         | 定义 `RuntimeContext`，承载一次 run 的共享上下文：session、事件流、result store、registry、父子 subagent 关系和 namespaced typed state。            | typed state 只提供 run-scoped 容器和 namespace 隔离，不解释 Text2SQL/RAG 等业务语义；child run 不继承父 run 的 typed state。                                                                                                                                                                                                                                                                |
| `events.py`          | 定义事件类型 `EventKind`、事件对象 `RuntimeEvent` 和 `EventBus`，统一 runtime 内部事件格式、顺序号和 callback 通知。           | 这个 legacy kind map 感觉就是多余的，模型应该从工具描述去理解工具，而不是从名称。而且用了兼容函数 *normalize*kind ，而且 检索了 `EventBus` 在整个后端代码中对于属性 `.callback` 的调用，确认无外部依赖。                                                                                                                             |
| `session_manager.py` | 准备 SDK `SQLiteSession`，读取历史消息，必要时执行上下文压缩并回写 session。                                              |                                                                                                                                                                                                                                                                |
| `compressor.py`      | 长上下文治理。估算 token，按 soft/hard 阈值做 LLM 摘要压缩或紧急截断，并把 session summary 写入 memory。                       | 现在 compressor 设计太简单了，先保留，后续我要参考 pi-agent 完整重构                                                                                                                                                                                                                  |
| `model_profiles.py`  | 从 `CHAT_*` 环境变量解析单一 chat `ModelProfile`，并规范化输出 token 上限。                              | 已去掉 orchestrator/executor 模型角色；embedding 配置由 memory embedding 模块独立保留。                                                                                                                    |
| `manifest_models.py` | 解析 manifest-local embedding 覆盖配置。                         | 已去掉 subagent worker model role 解析；worker 统一使用 runtime chat profile。                                                                                                                                                                                                             |
| `runtime_utils.py`   | 模型客户端和通用工具函数。包括带日志的 OpenAI ChatCompletions wrapper、直接 chat 调用、当前时间解析、JSON 序列化等。                   |                                                                                                                                                                                                                                                                |
| `result_mapper.py`   | 把 SDK 最终结果和 runtime events 映射成 API 返回结构，拆出 subagent trace、worker runs、todo events、model logs。     |                                                                                                                                                                                                                                                                |
| `result_formatters.py` | 定义 `ResultArtifactSpec`、`ResultFormatter` 协议和 registry。core 只拥有通用 artifact 与 memory formatter；subagent formatter 由各自 extension 注册。 | 已落地 `framework owns the contract, subagent owns the mapping`。formatter 返回完整标准 artifact summary 所需的 spec，core 不再根据 SQL/RAG 业务语义二次推断。 |
| `result_events.py`   | 从标准 `result_created.result` 和兜底 legacy payload 中提取 ResultStore metadata。                            | 业务结果解析不再集中硬编码在这里；新事件优先读取标准 `result` envelope，legacy payload 只做过渡兼容。 |
| `hooks.py`           | Hook 基础设施。定义 `HookResult`、`HookHandler` 协议和 `HookRunner`，目前核心事件是 `SessionStart`。                  |                                                                                                                                                                                                                                                                |
| `prompts.py`         | 集中存放 Orchestrator system prompt、memory policy、上下文压缩 prompt。                                       |                                                                                                                                                                                                                                                                |


## 当前边界记录

### Subagent 协议

- manifest 可声明 `capabilities`、`policies`、`output_contract`，这些字段是角色治理、审计和 UI/诊断理解入口。
- `SubagentExtensionAPI` 负责让 subagent 自己注册 tools、environment validator、prompt context、capability resolver 和 ResultFormatter。
- subagent-local tools 可通过 extension API 绑定 `capability`、`policy_path` 和 `audit_name`；框架在 extension 加载时校验声明，并在工具事件中记录 policy audit metadata。
- Runner 只负责发现、加载 extension、创建 child context、构造 worker、运行、timeout、事件桥接和结果归一化。
- capability/policy 的业务解释不写死在 Runner；Runner 和事件层只负责绑定校验、审计记录和隔离上下文，Text2SQL/RAG 的策略说明由各自 extension/tool 层消费。
- `RuntimeContext.get_typed_state(namespace, factory)` 和 `SubagentContext.typed_state(...)` 提供 run 内 typed state 容器；框架只管理生命周期和隔离，subagent 自己定义 state schema。
- 默认不让 subagent 递归调用其他 subagent；后续如开放，应通过 manifest policy 显式声明。

### ResultFormatter 与 ResultStore

- `ResultFormatter` 是业务 payload 到标准 artifact 的适配接口。业务语义归 subagent extension，core 只维护协议和 registry。
- 标准 artifact summary 形态：

```json
{
  "result_id": "res_xxx",
  "artifact_type": "sql_result",
  "title": "SQL Result",
  "source": "execute_sql",
  "preview": {
    "kind": "rows",
    "columns": ["column_a"],
    "rows": [{"column_a": "value"}]
  },
  "metrics": {
    "row_count": 1,
    "stored_count": 1,
    "count_is_exact": true,
    "truncated": false
  },
  "metadata": {},
  "created_at": "..."
}
```

- core envelope 不再暴露 SQL 专属顶层字段，例如 `sql`、`sample_rows`、`stored_row_count`、`store_truncated`。这些信息如有业务意义，应放入 `metadata` 或由 subagent 自己的 tool output 暴露给 worker。
- `ResultStore` 当前只保存 artifact-neutral schema：`result_artifacts` + `result_artifact_rows`，并在 artifact metadata 外记录 `run_id`、`session_id`、`bot_id` 作为读取 scope。
- 旧 `query_results` / `query_result_rows` 表已删除；本轮接受测试数据直接重建，不做迁移。

### Text2SQL/RAG 迁移状态

- Text2SQL manifest 已声明 `db.readonly`、`schema.inspect` 和 SQL policy；SQL 只读、schema 校验、行数截断和 SQLite 查询超时由 Text2SQL 工具/DB backend 按 manifest policy 强制执行。
- Text2SQL extension 注册 SQL tools、SQL ResultFormatter、readiness check 和 domain prompt context；Text2SQL run state 通过 typed state manager 管理，不再散写裸 cache key。
- Text2SQL runtime 只连接 SQLite；CSV 仅作为项目级 prepare CLI 的离线输入，用来生成 `.agentweave/text2sql.sqlite`。
- RAG manifest 已声明 `rag.search` capability 和轻量 policy；RAG extension 注册 chunks ResultFormatter，并负责搜索结果 artifact 化。
- memory search 使用 core-owned `memory_records` formatter。

### 性能优化暂缓记录

- 曾尝试让 direct `call_chat_model()` 透传 `CHAT_EXTRA_BODY_JSON`，并给 SQL generation 单独压低 `max_tokens`。
- 实测发现 SQL generation 输出可能包含 thinking 内容，低 token 预算会截断最终 SQL，因此该性能实验已回退。
- 后续优化 SQL generation latency 时，应先处理 thinking 内容泄漏和结构化输出稳定性，再调整 token budget 或 provider extra body。


## Review 记录区

### 待确认问题

- ResultFormatter 是否需要进一步支持非 row-shaped artifact，例如 file、chart、markdown answer。
- capability resolver 返回值是否需要形成统一 schema，便于 UI/诊断展示和后续 gateway 接入。
- SQL generation 是否继续作为 direct model call，还是收回到 worker agent 内部统一执行。

### 已确认决策

- 已移除 `agent_runtime/core/orchestrator.py` 兼容入口，内部引用统一改为 `agent_runtime.core.runtime`。
- 已移除 `agent_factory.py`、`run_executor.py`、`tool_protocol.py` 三个轻量包装层。
- 已移除模型角色体系，runtime 只保留单一 chat profile 和独立 embedding profile。
- 已移除 Orchestrator/Text2SQL 的 `get_current_time` 工具，当前时间改为 prompt 上下文注入。
- 已落地 `ResultFormatter` 注册化：Text2SQL/RAG 在 extension 中注册 formatter，core/memory 注册 memory formatter，ResultStore 改为 artifact-first。
- 已落地 namespaced typed state：框架提供 run-scoped 容器，Text2SQL 用自己的 state manager 管理 backend/domain/schema。
- subagent manifest 已新增 `capabilities`、`policies`、`output_contract`，作为角色治理和审计声明；工具层仍负责最终安全 enforcement。
- 已落地 subagent tool policy metadata：工具注册时必须绑定 capability，policy 可按需绑定，工具事件统一带审计字段。
- 已落地 ResultStore scope 字段和可选读取校验，服务层分页/导出接口可传入 run/session/bot scope。
- 标准 artifact summary 由 formatter/spec 直接决定，core 不再做 SQL/RAG 定制化推断。
- ResultStore 旧 SQL 专用表直接删除并使用 artifact-neutral schema，本轮不保留测试数据迁移。
- SQL generation 性能实验暂时回退，架构治理优先于延迟优化。

### 行动项

- 后续继续细化 capability resolver 与外部 MCP/API gateway 的对应关系；新增内部系统工具时优先通过 extension tool metadata 绑定 capability/policy。
- 后续为 SQL generation 设计稳定的 no-thinking/structured-output 路径，再讨论 latency 优化。
