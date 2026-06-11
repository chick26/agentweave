# SubagentContext 直接收窄

## 背景

前一轮已经把 subagent 默认接入变轻，但 `SubagentContext` 仍然暴露了较宽的 runtime-like 能力：裸 state cache、manifest 派生字段、profile、旧 SQL result store 和手动 result event。当前项目仍处于测试阶段，本轮选择直接收窄公共 API，不做分组接口和软迁移。

## 主要改动

- `SubagentContext` 只保留 worker extension 实际需要的能力：`run_id`、`runtime_root`、`timezone_name`、`manifest`、`policies`、`typed_state(...)`、`trace(...)`、`call_model(...)`、`embedding_client(...)` 和 `store_artifact(...)`。
- 直接移除 `cache`、`capabilities`、`output_contract`、`model_profile`、`embedding_profile`、`store_result`、`result_created` 和 `emit_tool_result`。
- `tool_finish(...)` 直接通过内部 runtime context 调用 framework event helper，不再依赖公开的 `emit_tool_result(...)`。
- `store_artifact(...)` 仍自动写入 ArtifactStore 并发出 `RESULT_CREATED` 事件，但不再暴露单独的手动 result event API。

## 取舍

- 不引入 `ctx.meta`、`ctx.model`、`ctx.artifacts` 等分组对象，避免在测试阶段继续增加新抽象。
- `capabilities` 和 `output_contract` 仍保留在 manifest/resource discovery 层，只是不再通过 runtime context 暴露给 subagent extension。
- Text2SQL/RAG 的业务行为不变；它们继续使用 manifest、policies、typed state、trace、model call、embedding client 和 artifact store。

## 已验证

目标验证命令：

```bash
PYTHONPATH=. uv run pytest tests/test_public_imports.py tests/test_subagent_boundaries.py tests/test_subagent_runner.py tests/test_text2sql_tools.py tests/test_rag_subagent.py
```

当前结果：70 passed。

全量验证建议：

```bash
PYTHONPATH=. uv run pytest
```

当前结果：226 passed。
