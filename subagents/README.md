# subagents 概览

本文档用于记录 `subagents/` 的职责概览和后续 review 讨论。风格参考 `agent_runtime/core/README.md`：先描述当前事实，再记录边界、决策和行动项。历史迁移过程放在 `docs/iterations/`，稳定架构说明放在 `docs/architecture/`。

## 总体定位

`subagents/` 存放 AgentWeave 可委派 worker subagent 的运行时包。每个 subagent 是一个干净的专业能力单元，只包含运行时必需的 manifest、prompt、extension 和纯业务逻辑。

主链路：

1. `AgentRegistry` 扫描 `subagents/*/AGENT.yaml` 和 `prompt.md`。
2. `SubagentRunner` 按 bot 配置把 subagent 包装为隔离 worker tool。
3. `extension.py register(api)` 注册工具、ResultFormatter、readiness check 和 prompt context。
4. worker 只通过自己的局部 tools 访问专业资源。
5. 大结果通过 `ResultStore` 保存为标准 artifact envelope。
6. 环境准备、样例数据和离线 prepare CLI 放在项目级目录，不进入 subagent 包。

## 当前目录职责

| 路径 | 当前职责 | Review 记录 |
| --- | --- | --- |
| `__init__.py` | subagents 包标识，不承载注册逻辑。 | 保持为空包入口，避免隐式副作用。 |
| `text2sql/AGENT.yaml` | Text2SQL worker 的能力、策略、输出契约、memory namespace 和路由提示。 | `domain_catalog.yaml` 仍属于 Text2SQL 专业能力配置，保留在 subagent 内。 |
| `text2sql/prompt.md` | Text2SQL worker prompt 模板。 | 只描述 worker 行为和工具调用规则，不写本地文件路径。 |
| `text2sql/extension.py` | 注册 SQL tools、SQL ResultFormatter、环境检查和 domain prompt context。 | 运行时只接受 SQLite：`TEXT2SQL_BACKEND=sqlite` + `TEXT2SQL_DATABASE_URL`。 |
| `text2sql/domain_catalog.yaml` | Text2SQL domain/table 目录，描述表、文本字段、业务指标和 notes。 | 这是业务 schema catalog，不是环境准备文件。 |
| `text2sql/core/` | Text2SQL 纯业务逻辑：domain catalog 解析、runtime state、SQL generation prompt、SQL safety。 | runtime state 通过框架 typed state 容器保存，但 domain/table/schema 语义归 Text2SQL 自己管理。 |
| `rag/AGENT.yaml` | RAG worker 的能力、策略、输出契约、memory namespace 和路由提示。 | RAG 运行时资源只通过 `RAG_INDEX_PATH` 暴露。 |
| `rag/prompt.md` | RAG worker prompt 模板。 | 只描述检索和引用行为，不写 Markdown 源目录。 |
| `rag/extension.py` | 注册知识库搜索/摘要 tools、RAG chunks ResultFormatter 和环境检查。 | 运行时只读取已准备好的 index，不扫描 Markdown 源文件。 |
| `rag/core/` | RAG 纯业务逻辑：Markdown loader、chunk、embedding index、检索和 summary 结构。 | loader 可被项目级 prepare CLI 复用，但 subagent runtime 不做准备流程。 |

## 当前边界记录

### Subagent 包边界

- subagent 包内只允许运行时能力文件：`AGENT.yaml`、`prompt.md`、`extension.py`、必要的 `core/`、以及专业能力配置文件。
- subagent 包内不放 `ENVIRONMENT.md`、`data/`、`prepare/`、本地 SQLite、RAG index、私有 CSV/Markdown 或项目绝对路径。
- 环境说明统一放在 `docs/environment/`。
- 本地样例输入统一放在 `data/examples/`。
- 离线准备 CLI 统一放在 `agentweave_prepare/`。

### Runtime 环境变量契约

- Text2SQL SQLite：`TEXT2SQL_BACKEND=sqlite` + `TEXT2SQL_DATABASE_URL=sqlite:////absolute/path/to/readonly.sqlite`。
- RAG：`RAG_INDEX_PATH=/absolute/path/to/rag_index.json`。
- `.agentweave/*.env` 仍由 runtime 启动入口自动加载；这是项目运行态配置，不属于 subagent 包。

### Extension API 使用

- 当前 Text2SQL/RAG extension 注册 tools、ResultFormatter、readiness check 和 prompt context。
- 当前 Text2SQL/RAG extension 的 subagent-local tools 已显式绑定 `capability`、`policy_path` 和 `audit_name`，事件流可审计具体能力与策略快照。
- 仅回显 manifest 的 `capability_resolver` 已从 Text2SQL/RAG 移除；静态能力以 `AGENT.yaml` 为准。
- 如果未来需要动态能力解析，再通过 `api.capability_resolver(...)` 注册真实动态 payload。

## Review 记录区

### 已确认决策

- 已将 `subagents/*/ENVIRONMENT.md` 移到 `docs/environment/`。
- 已将 `subagents/*/prepare/` 移到项目级 `agentweave_prepare/`，CLI 名称保持不变。
- 已将 `subagents/*/data/` 样例文件移到 `data/examples/`。
- 已清理 Text2SQL runtime 对 `runtime_root/subagents/text2sql/data` 的路径拼接。
- 已移除 Text2SQL runtime CSV backend，仅保留离线 CSV -> SQLite prepare 流程。
- 已将 Text2SQL active domain/backend/schema 状态收敛到 `Text2SQLRunStateManager`，不再散写 `run_ctx.cache["active_*"]`。
- 已移除 `TEXT2SQL_STRICT_SCHEMA_VALIDATION`，schema 校验策略统一读取 `AGENT.yaml` 的 `policies.db.require_schema_validation`。
- 已将 Text2SQL `execute_sql` 的行数、样例数、schema validation 和 SQLite 查询超时收口到 `policies.db`。
- 已将 RAG search/summary 绑定到 `rag.search` capability 和 `policies.rag`。
- 已新增边界测试，防止 subagent 重新引入 `ENVIRONMENT.md`、`data/` 或 `prepare/`。

### 待确认问题

- 是否需要把 Text2SQL 的 `domain_catalog.yaml` 进一步拆成可插拔 catalog provider，以支持生产 schema registry。
- 是否需要为动态 capability resolver 定义统一 schema，便于 UI/诊断展示。
- 是否需要为 RAG index 增加外部向量库 adapter，而不是只读取本地 JSON index。

### 行动项

- 后续优化 Text2SQL 时，优先审查 SQL generation 的 structured output/no-thinking 路径。
- 后续优化 RAG 时，优先审查 index 格式、summary 生成和外部向量库边界。
- 每轮 subagent 变更后运行 `tests/test_subagent_boundaries.py`，保持包边界干净。
