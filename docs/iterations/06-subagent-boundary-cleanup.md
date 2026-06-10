# Subagent 边界清理与文档治理

## 背景

在 core review 后，subagent 也需要同样严格的架构审查：识别冗余、过度设计和职责混杂，避免 worker subagent 目录逐渐变成“运行时代码 + 本地环境 + 样例数据 + prepare 脚本”的混合包。

本轮目标是让 subagent 成为干净的运行单元：只保留 manifest、prompt、extension、纯业务逻辑和必要专业配置；环境准备与本地数据全部移到项目级目录。

## 本轮清理结果

- `subagents/text2sql/` 只保留 `AGENT.yaml`、`prompt.md`、`extension.py`、`domain_catalog.yaml` 和 `core/`。
- `subagents/rag/` 只保留 `AGENT.yaml`、`prompt.md`、`extension.py` 和 `core/`。
- `subagents/*/ENVIRONMENT.md` 已迁移到 `docs/environment/`。
- `subagents/*/prepare/` 已迁移到 `agentweave_prepare/`，CLI 名称保持不变：
  - `agentweave-prepare-text2sql`
  - `agentweave-prepare-rag`
- `subagents/*/data/` 样例数据已迁移到 `data/examples/`。
- Text2SQL runtime 不再根据项目根目录拼接 CSV 路径；CSV backend 只接受环境变量中显式提供的绝对路径。
- RAG runtime 只读取 `RAG_INDEX_PATH` 指向的已准备索引，不扫描 Markdown 源目录。

## 文档管理约定

后续按模块逐个优化时，建议固定四类文档，各司其职：

| 文档位置 | 用途 | 写什么 | 不写什么 |
| --- | --- | --- | --- |
| `<module>/README.md` | 模块 review 台账 | 当前职责、文件职责、边界记录、已确认决策、待确认问题、行动项 | 长篇历史迁移过程 |
| `docs/architecture/` | 当前稳定架构 | 面向读者的稳定设计、运行链路、接口边界 | 已废弃方案、临时讨论 |
| `docs/iterations/` | 历史演进记录 | 每轮优化背景、取舍、迁移结果、兼容清理 | 每个文件的琐碎实现细节 |
| `docs/environment/` | 项目环境准备 | 本地/生产环境变量、prepare 命令、数据来源、生成物 | subagent 内部实现解释 |

推荐流程：

1. 优化某个模块前，先新增或更新 `<module>/README.md`，记录当前事实和 review 问题。
2. 做完一轮有边界意义的改动后，在 `docs/iterations/NN-topic.md` 记录背景、决策和结果。
3. 如果稳定架构发生变化，再更新 `docs/architecture/`。
4. 如果环境变量、数据准备或本地生成物发生变化，再更新 `docs/environment/`。
5. `docs/iterations/README.md` 和 `docs/README.md` 只做导航，不承载具体设计内容。

## 测试与防回退

本轮新增/更新的关键测试：

- `tests/test_subagent_boundaries.py`：确认 subagent 不导入 runtime internals，且不包含 `ENVIRONMENT.md`、`data/`、`prepare/`。
- `tests/test_db_backend.py`：覆盖 Text2SQL 环境变量连接、缺失配置错误、CSV 绝对路径要求。
- `tests/test_rag_subagent.py`：覆盖项目级 `data/examples/rag` 默认准备路径和 `RAG_INDEX_PATH` runtime 行为。
- `tests/test_resource_loader.py`：确认 resource reload 追踪 subagent manifest/prompt/domain catalog，不追踪项目级样例数据。

目标验证命令：

```bash
uv run pytest tests/test_subagent_boundaries.py tests/test_text2sql_tools.py tests/test_db_backend.py tests/test_rag_subagent.py tests/test_resource_loader.py tests/test_public_imports.py
```

当前结果：70 passed。

## 后续优化建议

- Text2SQL：继续审查 SQL generation 的 direct model call、structured output 和 no-thinking 控制。
- RAG：继续审查 index 格式、summary 生成、外部向量库 adapter 边界。
- Worker runtime：继续审查 subagent runner 的隔离、timeout、事件桥接和 extension API 是否还能再瘦身。
