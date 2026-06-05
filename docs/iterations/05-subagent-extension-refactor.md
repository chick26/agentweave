# Subagent Extension 协议重构

## 背景

早期方案尝试在主框架里引入“资源声明 + 标准工具组”，由框架理解 RAG index、SQL backend 等标准资源，再自动生成工具。这个方向能减少单个 subagent 的样板代码，但会让主框架逐渐知道过多垂直业务细节：Markdown 怎么索引、SQLite 怎么连接、未来向量库或 MySQL 怎么替换，都容易被推回框架层。

本轮重构选择更轻的边界：主框架只提供 extension 注册协议、worker 隔离运行、bot 范围 readiness 检查、工具装配和 prompt context 注入；RAG、Text2SQL 的依赖、连接、缓存和工具定义回到各自 subagent 内部。

## Extension 协议

推荐入口是 `AGENT.yaml` 中的 `extension.module`：

```yaml
extension:
  module: subagents.my_agent.extension
```

extension 模块暴露 `register(api)`，通过框架提供的 `SubagentExtensionAPI` 注册能力：

- `api.tool(tool_obj)`：注册 worker 内部可调用工具。
- `api.validate_environment(fn)`：注册启动前环境检查。
- `api.prompt_context(fn)`：注册 worker prompt 的动态上下文。

框架负责加载 extension、收集注册结果，并在 `SubagentRunner` 中先装配 extension 工具，再合并 `AGENT.yaml.tools` 显式声明的自定义 `tools.py` 工具。若 extension 工具和自定义工具重名，启动时报错，避免静默覆盖。

## RAG / Text2SQL 迁移结果

RAG 当前包结构为：

```text
subagents/rag/
├── extension.py
├── core/
├── prepare/
├── data/
├── AGENT.yaml
├── ENVIRONMENT.md
└── prompt.md
```

`extension.py` 注册 `search_knowledge_base`、`get_knowledge_base_summary` 和 `RAG_INDEX_PATH` readiness 检查。Markdown loader、chunk、embedding index、summary 聚合等逻辑位于 `core/`；索引构建 CLI 位于 `prepare/`。

Text2SQL 当前包结构同样采用 `extension.py + core/ + prepare/`。extension 注册 schema、值链接、SQL 生成、只读执行等工具，并自行从 `TEXT2SQL_DATABASE_URL` 或本地 prepared env 连接 SQLite backend。domain catalog、SQL prompt 和 SQL safety 位于 `core/`；CSV 到 SQLite 的本地准备流程位于 `prepare/`。

## 环境与运行边界

主框架不再内置 RAG/Text2SQL toolkit，也不再为这些 subagent 解释业务资源。环境准备由 subagent 自己的 prepare 脚本和 `ENVIRONMENT.md` 说明完成，运行时只执行当前 bot 实际启用 subagents 的 readiness probe。

本地生成状态统一放在 `.agentweave/`：

- `.agentweave/text2sql.env`
- `.agentweave/rag.env`
- `.agentweave/text2sql.sqlite`
- `.agentweave/rag_index.json`
- `.agentweave/agent_memory.sqlite`
- `.agentweave/agent_results.sqlite`
- `.agentweave/streamlit_sessions.sqlite`

这样主框架保持轻量，subagent 可以按自己的需要切换 SQLite、真实数据库、本地 JSON index 或外部向量库。

## 兼容清理

本轮已经清理旧推荐路径和兼容入口：旧资源声明、标准工具组、运行环境模块和运行 hook 字段都不再作为 subagent 标准接入方式；RAG/Text2SQL 不再依赖 `env.py` 或框架工具组 shim。`tools.py` 仍是长期支持的自定义补充入口，但必须在 `AGENT.yaml.tools` 中显式声明。

后续如果继续收口，可以单独评估是否移除 `RunContext.backend` 的旧注入路径；这不影响当前 extension 协议的推荐用法。
