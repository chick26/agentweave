# 基于 pi-agent 思路的 AgentWeave Runtime 分层重构

> 本文记录这一次参考 pi-agent 架构思路后的 AgentWeave runtime 重构。
>
> 重点是说明：为什么要拆分运行时、拆成了哪些层、各层之间如何通信，以及这次重构后代码边界发生了什么变化。

## 背景

在上一轮 Subagent + Memory 架构之后，AgentWeave 已经把 Text2SQL 从主编排器中隔离出来，但 runtime 自身仍然有几个明显问题：

- `agent_runtime/` 顶层文件承担过多职责，编排、存储、记忆、UI、注册表和工具协议混在一起。
- Streamlit `app.py` 过胖，既负责 UI，也承载运行时初始化、诊断持久化、资源重载和会话操作。
- skill、subagent、domain 的语义已经分开，但代码目录还没有把这种边界表达清楚。
- 工具返回值、UI 展示值、诊断事件之间缺少统一协议，容易让模型上下文、前端和调试日志互相牵连。
- 旧模块路径仍然像是公开 API，后续继续扩展会让兼容面越来越大。

参考 pi-agent 的核心启发是：runtime 应该被拆成可组合的小层，每层只暴露稳定接口；agent loop、tool protocol、state management、resource loading 和 UI rendering 不应该互相穿透。

## 重构目标

这次重构的目标不是照搬 pi-agent 的包结构，而是把 AgentWeave 当前最需要稳定下来的边界显式化：

- Orchestrator 只负责 agent loop、工具装配、上下文压缩和结果汇总。
- Worker subagent 通过声明式 manifest 注册，执行时拥有隔离 `RunContext`。
- Memory、Result Store、Diagnostic Store 都成为 storage/state 子系统，而不是 UI 或 orchestrator 的内部细节。
- Streamlit 只消费 runtime events 和 result pointers，不再拥有核心编排逻辑。
- 工具输出拆成 LLM 与 UI 两个通道，避免大结果或富 UI 数据进入模型上下文。
- 旧顶层 shim 模块主动删除，只保留 `from agent_runtime import AgentRuntime` 这个包级入口。

## 包分层变化

重构后，`agent_runtime` 从一组顶层模块改成分层包：

| 分层 | 主要职责 |
|------|----------|
| `agent_runtime.core` | Orchestrator、RunContext、prompt、model profile、context compressor、events、tool protocol、runtime settings |
| `agent_runtime.memory` | durable memory、embedding retrieval、todo state、session summary、token counting |
| `agent_runtime.storage` | database backend、result store、diagnostic store、session templates |
| `agent_runtime.registry` | skill/subagent manifest discovery、resource snapshot、reload support |
| `agent_runtime.ui.streamlit` | Streamlit 页面、sidebar、chat、diagnostics、results、export、formatting |

第二轮收口后，最初残留在 `agent_runtime/` 顶层的运行时模块也全部迁入 `core`：

- `agent_runtime/core/skill_runner.py`：SubagentRunner、worker agent tool 构建、worker trace 处理。
- `agent_runtime/core/result_events.py`：从 runtime events 中提取 Result Store metadata。
- `agent_runtime/core/hooks.py`：hook runner、hook handler 协议和 SessionStart 调度。
- `agent_runtime/core/preset_questions.py`：首页预设问题生成与欢迎消息格式化。

公开入口收敛为：

```python
from agent_runtime import AgentRuntime
```

内部代码使用分层路径，例如：

```python
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.context import RunContext
from agent_runtime.storage.database import CsvSQLiteBackend
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.registry.skill_registry import AgentRegistry
```

旧顶层路径如 `agent_runtime.orchestrator`、`agent_runtime.database`、`agent_runtime.skill_registry` 被移除。这是一次有意的 breaking change，用来避免旧边界继续泄漏。

第二轮继续移除了 `agent_runtime.skill_runner`、`agent_runtime.result_events`、`agent_runtime.hooks`、`agent_runtime.preset_questions` 这几个残留顶层路径，不再保留 shim。

## Runtime 协议变化

### 1. Typed Runtime Events

运行时活动统一通过 `RuntimeEvent` / `EventBus` 表达。新的主事件包括：

- `agent_start` / `agent_end`
- `subagent_dispatch` / `subagent_complete`
- `tool_call_start` / `tool_result` / `tool_call_end`
- `result_created`
- `memory_read` / `memory_write`
- `context_compressed`
- `resources_reloaded`
- `session_forked`
- `session_template_started` / `session_template_saved`

旧的 `worker_run` 不再作为新执行链路的主事件源，只在诊断读取层保留兼容，便于读取历史日志。

### 2. Dual-Channel Tool Output

工具内部引入 `ToolOutput`：

- `llm_content`：返回给模型的紧凑内容。
- `ui_content`：给前端或诊断使用的富结构内容。
- `metadata`：小型操作摘要，例如 `result_id`、`row_count`、`error`。

Text2SQL 查询结果因此不再把完整行数据塞回模型。模型只看到样例行和 result pointer；UI 通过 `result_created` 事件和 Result Store 分页展示已存储结果。

### 3. Subagent-as-Tool 结构化返回

Subagent 作为 Orchestrator tool 调用时，不再只返回 `answer` 字符串，而是返回紧凑 JSON：

```json
{
  "answer": "中文回答",
  "error": "",
  "subagent": "text2sql",
  "domain": "idc_resources",
  "sql": "SELECT ...",
  "result_id": "res_xxx",
  "row_count": 10,
  "truncated": false,
  "sample_rows": []
}
```

这样 Orchestrator 可以稳定获得 `result_id`、SQL、行数、错误等结构化事实，不需要从自然语言回答中反推。

### 4. Result Store 语义收紧

大结果不再把 `row_count` 表述成真实总数。当前语义是：

- `stored_row_count`：Result Store 实际保存的行数。
- `has_more`：后端执行时发现超过存储上限。
- `row_count_is_exact`：当前行数是否可以视为精确值。
- `store_truncated`：存储层是否截断。

Streamlit Results 页签也改成展示“已存储行数”，避免用户把存储上限误读为真实总数。

### 5. Hook 化 Preset Questions

`SessionStart` 不再由 `HookRunner` 内部硬编码调用预设问题生成函数，而是拆成轻量 hook handler：

- handler 声明 `event_name` 并实现 `run(context) -> HookResult`。
- 默认注册 `PresetQuestionsSessionStartHook`，维持原有欢迎消息、预设问题和 memory context 提示行为。
- `HookRunner` 按事件名选择第一个匹配 handler 执行；当前不做多 hook 结果合并，避免把简单启动消息机制设计得过重。

这个变化让 preset questions 后续可以被替换、叠加或禁用，而不用继续修改主 hook runner。

## Resource Loading 与声明式扩展

资源加载抽到 `ResourceLoader`，统一发现 prompt-facing 资源：

- project rules：`AGENT_PROJECT_RULES_PATH`、`AGENTS.md`、`PROJECT.md`
- skills：`skills/*/SKILL.md`
- subagents：`subagents/*/AGENT.md`
- domains：`subagents/*/domains/*/DOMAIN.md`

Streamlit 的 Reload Resources 操作会刷新 registry cache、重新计算资源快照、清理预设问题缓存，并记录 `resources_reloaded` 事件。

同时，manifest 加载从宽松兜底改成更严格：

- worker subagent 必须声明 `execution.model_role`。
- manifest YAML/frontmatter 解析失败会直接报错。
- subagent 声明的 tool 在模块中不存在时直接报错。
- 资源目录不存在仍返回空列表，这是允许的部署形态。

## Streamlit 拆分

原来的 `app.py` 从大文件退化为入口：

```python
from agent_runtime.ui.streamlit import run_app

if __name__ == "__main__":
    run_app()
```

UI 代码拆到 `agent_runtime.ui.streamlit`：

- `app.py`：页面编排和 Streamlit runtime glue。
- `sidebar.py`：模型、memory、reload、fork 等侧栏配置。
- `chat.py`：聊天渲染与流式输出。
- `diagnostics.py`：模型调用、事件时间线、诊断概览。
- `results.py`：Result Store 分页预览与 CSV 导出。
- `export.py`：会话 Markdown/HTML 导出。
- `events.py` / `formatting.py`：事件展示与文本格式化。

这让 UI 成为 runtime events 的消费者，而不是核心 runtime 的拥有者。

## 新增会话能力

这次重构顺手把与会话相关的功能从 UI 逻辑中抽出来：

- Session Fork：复制 OpenAI Agents SDK `SQLiteSession` items 到新 session id。
- Session Template：保存当前可见对话为模板，并可从模板启动新会话。
- Session Export：从 chat messages 和 runtime events 生成 Markdown / HTML。

这些能力都通过 runtime event 记录，方便后续诊断和导出复盘。

## 迁移结果

重构后的关键变化：

- `agent_runtime` 顶层旧模块删除，分层包成为内部代码标准路径。
- `skill_runner`、`result_events`、`hooks`、`preset_questions` 这几个残留 runtime 模块迁入 `agent_runtime.core`。
- `AgentRuntime` 包级 API 保留，外部最小入口不变。
- `SubagentRunner` 使用 manifest 动态构建 worker agent tool。
- `PresetQuestionsSessionStartHook` 成为默认 SessionStart handler，预设问题生成从普通 helper 调用升级为 hook 扩展点。
- Text2SQL domain registry 从 subagent manifest 中解析本地 domain root。
- Diagnostic / Results / Memory / Session Template 等状态都落入明确 storage 子系统。
- Streamlit 页面只做展示与交互编排。

全量回归测试覆盖当前边界：

```bash
PYTHONPATH=. uv run pytest
```

当前结果：`138 passed`。

## 后续方向

- 进一步减少兼容旧诊断 wire shape 的代码，只保留必要的历史读取层。
- 将 manifest schema 校验集中化，避免 registry 与 runner 分散校验。
- 继续压缩 Worker prompt 和 tool output，降低长 domain / 大 schema 场景的上下文压力。
- 评估 Result Store 是否需要支持真实总数统计，或在不同 backend 中提供可选 count strategy。
- 为更多 subagent 类型沉淀通用 manifest 模板，避免 Text2SQL 特例继续扩散。
