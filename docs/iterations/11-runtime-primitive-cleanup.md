# Runtime Primitive 清理

## 背景

前几轮已经把 subagent extension、typed state、ResultFormatter 和 SubagentContext 边界逐步收窄。
本轮继续按 pi-agent 风格优化：让 runtime core 更像最小编排壳，把可增长的能力固定在 registry、
extension、context 和 artifact 这些少量 primitive 上。

## 主要改动

- 新增 `RuntimeServices`，集中装配 registry、bot/resource loader、memory、ArtifactStore、formatter、subagent runner、session manager 和 hooks。
- `AgentRuntime` 保留构造参数和行为方法，但不再代理 services 字段；`ask()` 主流程收敛为 build context、prepare session、build agent、run、map result。
- 拆分 manifest model、manifest parser 和 `AgentRegistry`；`skill_registry.py` 只保留 `SkillRegistry`。
- `ResultArtifactSpec` 支持通用 `preview` envelope；默认仍是 `{"kind": "rows"}`，SQL/RAG 行式结果行为不变。
- `ArtifactStore` 使用 `preview_json` 字段保存通用 preview envelope，提供分页和 CSV 导出。
- `SubagentContext` 增加只读 artifact metadata/page 访问方法，使用当前 session/bot scope。
- subagent dispatch/complete 事件 payload 统一使用 `subagent` 字段，不再新写 `skill` 别名。

## 取舍

- 本轮不新增 `data_analysis` subagent，也不执行任意脚本；只为后续 chart/profile 能力铺地基。
- 内部存储类直接改名为 `ArtifactStore`，不保留 `ResultStore` 模块或类型别名。
- `Skill` 继续保持方法卡定位；即使未来 skill 目录带脚本资产，执行入口也仍由 subagent extension tool 管理。

## 已验证

目标验证命令：

```bash
PYTHONPATH=. uv run pytest tests/test_public_imports.py tests/test_artifact_store.py tests/test_orchestrator_tools.py tests/test_server_service.py
```

当前结果：59 passed。

全量验证命令：

```bash
PYTHONPATH=. uv run pytest
```

当前结果：229 passed。

## 后续关注

- 基于新的 artifact 读取 API 新增 `data_analysis` worker subagent。
- 为 `chart_spec` 定义 ResultFormatter 和 Streamlit 渲染策略。
