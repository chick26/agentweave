# AgentWeave Runtime Architecture

AgentWeave is organized around small runtime primitives inspired by pi-agent:
layered packages, typed events, reloadable resources, and dual-channel tool
output.

## Package Boundaries

- `agent_runtime.core`: orchestration, subagent runner, run context, prompts,
  model profiles, context compression, runtime settings, events, hook handlers,
  preset question generation, result event extraction, and tool protocols.
- `agent_runtime.memory`: durable memory, embeddings, session summaries, todo
  state, and token counting.
- `agent_runtime.storage`: database backends, diagnostic persistence, and
  result storage.
- `agent_runtime.registry`: manifest-driven discovery for skills, subagents,
  domains, project rules, and reloadable resource snapshots.
- `agent_runtime.server`: HTTP/SSE API and application service layer for TUI
  and external TS Web clients.
- `agent_runtime.ui.streamlit`: Streamlit rendering only. It consumes runtime
  results and events rather than owning orchestration logic.

The package-level API remains:

```python
from agent_runtime import AgentRuntime
```

Internal code should use the layered paths directly, for example:

```python
from agent_runtime.core.orchestrator import AgentRuntime
from agent_runtime.core.context import RunContext
from agent_runtime.core.skill_runner import SubagentRunner
from agent_runtime.storage.database import CsvSQLiteBackend
from agent_runtime.memory.memory_manager import MemoryManager
from agent_runtime.registry.skill_registry import AgentRegistry
```

The old top-level shim modules such as `agent_runtime.orchestrator`,
`agent_runtime.skill_runner`, and `agent_runtime.database` are intentionally
removed.

## Runtime Events

Runtime activity is represented as typed `RuntimeEvent` dictionaries emitted by
`EventBus`. Important event kinds include:

- `agent_start`, `agent_end`, `error`
- `subagent_dispatch`, `subagent_complete`, `subagent_trace`
- `tool_call_start`, `tool_result`, `tool_call_end`, `result_created`
- `memory_read`, `memory_write`
- `context_compressed`
- `resources_reloaded`

Diagnostics and Streamlit rendering should consume these typed events first.
Legacy `worker_run` rows remain readable where old diagnostic rows exist.

## Dual-Channel Tool Output

Tools should return `ToolOutput` internally:

- `llm_content`: compact JSON/string returned to the model.
- `ui_content`: richer structured content emitted through runtime events.
- `metadata`: small operational summary such as result id, row count, and error.

For SQL tools, the LLM receives sample rows and a result pointer, while the UI
loads full pages from `ResultStore`.

## Resource Loading

`ResourceLoader` discovers prompt-facing resources and supports explicit reload:

- project rules: `AGENT_PROJECT_RULES_PATH` override, then `AGENTS.md`, then
  `PROJECT.md`
- skills: `skills/*/SKILL.md`
- subagents: `subagents/*/AGENT.md`
- domains: `subagents/*/domains/*/DOMAIN.md`

Streamlit exposes a `Reload Resources` action that invalidates registries,
refreshes the resource snapshot, clears cached preset questions when needed,
and records a `resources_reloaded` event for diagnostics.

## Session Export

Streamlit can export the current session as Markdown or HTML. The export is
assembled from chat messages plus runtime event runs, so it preserves both the
user-facing conversation and the operational trace needed for review.

## Session Forking

Streamlit exposes `Fork Session`, which copies the underlying OpenAI Agents SDK
`SQLiteSession` items from the current session id into a fresh session id. The
visible chat remains as the fork point, while subsequent turns are persisted
under the new session and can diverge from the original path. The fork action is
recorded as a `session_forked` event.

## Session Templates

Streamlit can persist the current visible chat as a session template. Templates
are stored in SQLite, can be deleted, and can start a fresh session by writing
the template messages into a new SDK `SQLiteSession`. Template actions are
recorded as `session_template_saved` and `session_template_started` events.
