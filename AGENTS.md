# AgentWeave 操作规范

## 全局行为

- 默认使用中文回答用户，保持简洁、直接、面向业务问题。
- 不确定时说明不确定来源，不编造数据、字段、结果或执行状态。
- 专业数据查询默认委派给匹配的 subagent，主编排器只做路由和总结。
- Orchestrator 只选择顶层 subagent 并总结其 `answer` 与 typed artifacts；subagent-local tools 不直接暴露给 Orchestrator。
- Worker 状态必须按 run 隔离，不存放在共享 runtime 实例上。

## SQL 安全

- SQL 执行必须保持只读，禁止 DDL、DML、PRAGMA、ATTACH 等修改性操作。
- Text2SQL 必须先规划再执行，优先使用真实 schema、domain 事实和值链接结果。
- 大结果必须通过 ResultStore 暴露 result_id 和 sample_rows，不直接塞入模型上下文。

## 记忆管理

- 只保存跨会话稳定复用的用户偏好、项目规则和业务约定。
- 不保存凭据、密钥、一次性查询结果、中间推理过程或瞬时错误。
- 写入记忆前优先更新既有 key，避免重复堆积。

## 模块优化与文档治理

- 按模块优化时，优先维护模块内 `README.md` 作为 review 台账，记录当前职责、文件职责、边界、已确认决策、待确认问题和行动项。
- `docs/architecture/` 只描述当前稳定架构，不写历史迁移细节。
- `docs/iterations/` 记录每轮优化的背景、取舍、迁移结果和兼容清理，并在 `docs/iterations/README.md` 更新导航。
- `docs/environment/` 记录项目级环境变量、数据准备、生成物和本地/生产连接方式。
- 环境准备、样例数据、离线 prepare CLI 不放入 subagent 包；subagent 应保持运行时契约和纯业务逻辑干净。
