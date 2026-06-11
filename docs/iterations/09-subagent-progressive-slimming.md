# Subagent 渐进瘦身与默认审计元数据

## 背景

第 8 轮把 `capabilities` / `policies` 提升为可校验、可审计的 runtime 契约，这对 Text2SQL、RAG 和未来内部系统 gateway 是必要治理。但在只有普通读取或轻量工具的 subagent 场景下，新增工具也必须同步维护 manifest capability 和 audit name，接入成本偏重。

本轮采用渐进瘦身：不推翻 Orchestrator / Worker / Extension / Core 主架构，不删除 manifest 字段，只把默认路径变轻，同时保留高风险工具的显式治理能力。

## 主要改动

- `SubagentRunner` 收口为 worker 生命周期入口，负责 run isolation、child context、timeout、dispatch/complete event 和 SDK runner 调用。
- 新增 worker agent factory，集中处理 worker agent 构建、prompt context 注入、subagent-local tools 装配和 agent-as-tool 包装。
- 新增 result normalizer，集中维护 `SubagentResult`、tool input、worker final output 规整和 tool payload envelope。
- `api.tool(fn)` 现在可以省略 `capability` 和 `audit_name`，框架默认生成：
  - `capability = "<subagent>.tool.<tool>"`
  - `audit_name = "<subagent>.<tool>"`
  - `policy_path = ""`
- 显式传入 `capability` 时仍要求它出现在 manifest `capabilities` 中；显式传入 `policy_path` 时仍要求路径能在 manifest `policies` 中解析。

## 取舍

- 本轮没有删除 `output_contract`、`domains`、`suggested_questions` 等 manifest 字段，只在文档上弱化它们对普通 subagent 的必填感。
- Text2SQL / RAG 的高风险工具继续显式绑定 capability、policy path 和 audit name，已有审计事件语义不变。
- 普通工具的默认 capability 不要求写入 manifest；manifest `capabilities` 只代表需要显式治理和审计命名的能力清单。
- `SubagentRunner.build_worker_agent_tool(...)` 作为现有调用入口保留，内部委托给 factory，避免打断 runtime tool factory。

## 已验证

目标验证命令：

```bash
PYTHONPATH=. uv run pytest tests/test_subagent_runner.py tests/test_orchestrator_tools.py tests/test_public_imports.py tests/test_subagent_boundaries.py
```

当前结果：61 passed。

## 后续关注

- 如果新增更多轻量 subagent，可继续观察默认 capability 是否足够用于诊断和 UI 展示。
- 如果接入写接口或外部生产系统，仍应优先使用显式 `capability` + `policy_path`，不要只依赖默认 metadata。
