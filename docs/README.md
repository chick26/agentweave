# 文档导航

项目文档按用途拆成两个目录。

## 架构说明

`architecture/` 只描述当前代码采用的稳定架构：

- [主编排器](architecture/01-orchestrator.md)
- [记忆系统](architecture/02-memory.md)
- [工具系统与注册发现](architecture/03-tools.md)
- [技能系统](architecture/04-skill.md)
- [Text2SQL 子智能体](architecture/05-text2sql-subagent.md)
- [Todo 工作记忆](architecture/06-todo.md)

## 迭代说明

`iterations/` 记录设计演进、审查结论和重要架构对比：

- [Text2SQL 调用链审查](iterations/01-text2sql-call-chain-review.md)
- [从纯工具式 Text2SQL 到 Subagent + Memory 架构](iterations/02-main-to-subagent-memory-upgrade.md)
