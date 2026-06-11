# Text2SQL 模型与 Memory Toggle 稳定性修复

## 背景

第 11 轮完成 runtime primitive 清理后，系统结构已经收敛到 `RuntimeServices`、
manifest registry、`SubagentContext` 和 `ArtifactStore` 这些少量核心边界。
随后真实测试暴露出两个运行体验问题：

- SQL generation 使用主 `CHAT_MODEL=qwen3.6-27b` 时延迟偏高。
- Streamlit 前端关闭 Memory 能力后，页面可能白屏或长时间卡住。

本轮不改变 subagent/skill 边界，也不新增图表执行能力；只做运行稳定性修复。

## 主要改动

- Text2SQL SQL generation 默认使用 `TEXT2SQL_SQL_MODEL=qwen3-32b`。
- `SubagentContext.call_model(...)` 支持只覆盖 `model_name`，仍复用 runtime 的
  `CHAT_BASE_URL`、`CHAT_API_KEY`、timeout、retry 和 token 配置。
- `.env.example`、README 和 Text2SQL 环境文档记录 `TEXT2SQL_SQL_MODEL`。
- Streamlit Memory toggle 变化时新开 session scope，并清空当前页面消息、模型日志和事件列表。
- Streamlit session id 增加 `mem` / `nomem` scope 标记，避免复用旧 SQLiteSession 中的 memory tool history。
- `SessionStart` 在 `memory_enabled=False` 时不读取 memory context。
- Welcome message 的同步 OpenAI client 统一使用 `OPENAI_CLIENT_TIMEOUT` 和
  `OPENAI_CLIENT_MAX_RETRIES`，避免欢迎词生成卡住造成页面空白。

## 取舍

- 没有恢复旧的 orchestrator/executor/model role 体系；Text2SQL 只是 subagent tool
  在调用模型时覆盖模型名。
- Memory toggle 被视为会话能力边界变化；当前 Streamlit 调试台选择开启新 session，
  而不是尝试迁移旧消息历史。
- 关闭 Memory 只影响长期记忆注入和 memory tools；短期 Todo working state 仍按现有开关独立管理。

## 验证

定向验证：

```bash
PYTHONPATH=. uv run pytest tests/test_text2sql_tools.py tests/test_context.py tests/test_public_imports.py tests/test_subagent_runner.py
PYTHONPATH=. uv run pytest tests/test_streamlit_runtime.py tests/test_orchestrator_tools.py tests/test_hooks.py
```

完整回归：

```bash
PYTHONPATH=. uv run pytest
```

当前结果：236 passed。

## 后续关注

- 如果后续引入更多专用模型，只允许在 subagent extension/tool 层以显式参数覆盖模型名，避免重新扩张 runtime 模型角色体系。
- TS Web 前端如也提供 Memory 开关，应采用同样的 session capability scope 策略：切换后创建新 session，不复用旧 run/tool history。
- 下一阶段仍回到 `data_analysis` worker、`chart_spec` artifact 和图表渲染能力。
