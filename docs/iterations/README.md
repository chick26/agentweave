# 迭代说明

本目录记录架构演进、设计审查和重要取舍。这里可以讨论历史版本、对比 main、曾经的风险点和后续优化方向；当前稳定架构请看 `docs/architecture/`。

- [Text2SQL 调用链审查](01-text2sql-call-chain-review.md)
- [从纯工具式 Text2SQL 到 Subagent + Memory 架构](02-main-to-subagent-memory-upgrade.md)
- [基于 pi-agent 思路的 AgentWeave Runtime 分层重构](03-pi-agent-runtime-refactor.md)
- [FastAPI 与 SSE API 设计过程](04-fastapi-sse-api-design.md)
- [Subagent Extension 协议重构](05-subagent-extension-refactor.md)
- [Subagent 边界清理与文档治理](06-subagent-boundary-cleanup.md)
- [Text2SQL Typed State 与运行时清理](07-text2sql-state-and-runtime-cleanup.md)
- [Subagent Policy Gateway 与授权边界](08-subagent-policy-gateway.md)
- [Subagent 渐进瘦身与默认审计元数据](09-subagent-progressive-slimming.md)
- [SubagentContext 直接收窄](10-subagent-context-slimming.md)
- [Runtime Primitive 清理](11-runtime-primitive-cleanup.md)
- [Text2SQL 模型与 Memory Toggle 稳定性修复](12-text2sql-model-and-memory-toggle-stability.md)

## 文档治理原则

- 模块内 `README.md`：记录当前职责、边界、review 决策和行动项。
- `docs/architecture/`：只描述当前稳定架构。
- `docs/iterations/`：记录每轮优化的背景、取舍和迁移结果。
- `docs/environment/`：记录项目级环境准备、数据来源和生成物。
