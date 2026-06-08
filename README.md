# AgentWeave

English | [中文](README.zh-CN.md)

AgentWeave is a composable agent runtime built with Streamlit and the OpenAI Agents SDK. It separates a lightweight Orchestrator from delegated worker subagents, so specialized workflows can run in isolated contexts while the main agent stays focused on routing, memory, and final response synthesis.

Text2SQL and RAG are included worker subagents. The framework is intended to grow beyond database Q&A into broader agentic workflows.

## Why AgentWeave

- **Orchestrator + worker isolation**: keep noisy domain work out of the main conversation state.
- **Extension protocol**: discover subagents from `AGENT.yaml + prompt.md`, then let each `extension.py` register its own tools and readiness checks.
- **Scoped tools**: the Orchestrator sees only high-level subagent tools; each worker owns its local tools.
- **Memory-aware runtime**: durable memory, session summaries, and optional embedding retrieval.
- **Result isolation**: large query results are stored in SQLite and represented through typed artifacts and result pointers.
- **Diagnostics first**: model calls, events, memory retrieval, subagent traces, and result previews are inspectable.

## Architecture

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant O as Orchestrator
    participant M as MemoryManager
    participant T as Subagent Tool
    participant W as Worker Subagent
    participant DB as Database Backend
    participant RS as Result Store

    User->>O: Ask for a domain task
    O->>M: Retrieve query-aware memory
    M-->>O: Scoped memory context
    O->>T: Delegate a self-contained task
    T->>W: Start child RuntimeContext + isolated session
    W->>DB: Plan, validate, and execute read-only work
    DB-->>W: Rows or execution metadata
    W->>RS: Persist full results when needed
    W-->>O: Structured result + trace + artifacts
    O-->>User: Final concise response
```

## Core Concepts

| Concept | Purpose |
|---|---|
| Orchestrator | Routes user requests, loads memory/skills when needed, delegates specialized work, and summarizes results. |
| Subagent | A worker agent declared by `subagents/*/AGENT.yaml + prompt.md`; runs in an isolated context and owns its extension tools. |
| Extension | A `register(api)` module that registers subagent tools, environment validation, and optional prompt context. |
| Skill | A reusable method card declared by `skills/*/SKILL.md`; loaded as guidance, not exposed as an executable agent. |
| Memory | SQLite-backed durable memory with vector, lexical, and recent-record retrieval strategies. |
| Result Store | SQLite-backed storage for large outputs; workers expose typed artifacts with result pointers, row counts, and samples. |
| Diagnostics | Strictly structured run logs for model calls, events, memory retrieval, subagent traces, and result metadata. |

## Included Capabilities

| Type | Name | Description |
|---|---|---|
| subagent | `text2sql` | Natural-language querying over structured data with read-only SQL validation. |
| subagent | `rag` | Markdown knowledge-base retrieval with cited chunks from local test data. |
| skill | `data_analysis` | A method card for profiling data, checking quality signals, and suggesting analysis/report structure. |

## Repository Layout

```text
agentweave/
├── app.py                         # Streamlit entrypoint
├── agent_runtime/                 # Orchestrator, extension API, memory, registry, stores
├── subagents/
│   ├── text2sql/                  # Text2SQL extension, core logic, prepare scripts
│   └── rag/                       # Markdown RAG extension, core logic, prepare scripts
├── bots/                          # Backend bot configs that scope allowed capabilities
├── skills/
│   └── data_analysis/             # Loadable data analysis method card
├── docs/
│   ├── architecture/              # Current architecture docs
│   └── iterations/                # Design iteration notes
├── data/                          # Local private data directory, ignored by git
├── .agentweave/                   # Local runtime state, ignored by git
├── tests/                         # Regression tests
└── .env.example                   # OpenAI-compatible runtime configuration template
```

## Documentation

- [Architecture docs](docs/architecture/)
- [Iteration notes](docs/iterations/)

## Configuration

Copy `.env.example` to `.env` and fill in your own OpenAI-compatible endpoints:

```bash
cp .env.example .env
```

Runtime loads local env files in this order:

1. `.env` for shared model/server settings.
2. `.agentweave/runtime.env` for optional local overrides.
3. `.agentweave/text2sql.env` for prepared Text2SQL database connection.
4. `.agentweave/rag.env` for local RAG settings.

Important environment variables:

| Role | Variables | Default |
|---|---|---|
| Orchestrator | `ORCHESTRATOR_BASE_URL` / `ORCHESTRATOR_MODEL` | `http://localhost:8000/v1` / `qwen3.6-27b` |
| Executor | `EXECUTOR_BASE_URL` / `EXECUTOR_MODEL` | `http://localhost:8001/v1` / `qwen3-32b` |
| Embedding memory | `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` | `http://localhost:8002/v1` / `openai-compatible-embedding-model` |
| Text2SQL DB | `TEXT2SQL_BACKEND` / `TEXT2SQL_DATABASE_URL` | Generated by `uv run agentweave-prepare-text2sql --overwrite` |
| RAG index | `RAG_INDEX_PATH` | Generated by `uv run agentweave-prepare-rag` |
| CSV table mapping override | `TEXT2SQL_TABLES_JSON` | Explicit test-only CSV backend config |
| Optional todo tool | `AGENTWEAVE_ENABLE_TODO_TOOL` | `0`, session-local todo state is hidden by default |

Private CSV, Markdown, SQLite, and runtime database files are intentionally ignored
by git. Place subagent-local test data under each package's `data/` directory,
for example `subagents/text2sql/data/` or `subagents/rag/data/`. See each
subagent's `ENVIRONMENT.md`.

Local generated state lives under `.agentweave/`, including prepared subagent
env files, prepared SQLite/RAG indexes, memory, result store, and Streamlit
session diagnostics. The directory is runtime-local and should not be committed.

## Run

```bash
uv sync
cp .env.example .env
uv run agentweave-prepare-text2sql --overwrite
uv run agentweave-prepare-rag
uv run streamlit run app.py
```

For the FastAPI HTTP/SSE server:

```bash
uv run agentweave-server
```

## Test

```bash
uv run pytest
```

## Adding a Subagent

1. Create a directory under `subagents/`.
2. Add `AGENT.yaml` with `name`, `description`, `execution.mode: worker`, `execution.model_role`, `extension`, `memory`, and `routing_hints`. Built-in workers default to `orchestrator`; use `SUBAGENT_<NAME>_MODEL_ROLE` for local overrides. Extension tools should call the executor model for non-orchestration LLM work.
3. Add `prompt.md` for the worker prompt.
4. Implement `extension.py` with `register(api)` to register tools, environment checks, and prompt context.
5. Optionally add `ENVIRONMENT.md`, `data/`, `core/`, and `prepare/` for subagent-private logic. Tools must be registered from `extension.py register(api)`.

Recommended subagent package shape:

```text
subagents/<name>/
├── AGENT.yaml
├── prompt.md
├── extension.py      # Required: register(api)
├── ENVIRONMENT.md    # Optional setup notes
├── data/             # Optional private local fixtures
├── core/             # Optional pure domain logic
└── prepare/          # Optional offline preparation scripts
```

Subagents do not need per-subagent framework tests when they follow this
contract. The registry validates the package shape at startup.

### AGENT.yaml Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | Yes | Unique subagent name and delegated tool name. Prefer lowercase snake_case. |
| `description` | Yes | Capability description used for routing, capability listings, and welcome payloads. |
| `execution.mode` | Yes | Use `worker` for subagents. |
| `execution.model_role` | Yes | Model role for worker orchestration. Built-in workers use `orchestrator`; override locally with `SUBAGENT_<NAME>_MODEL_ROLE`. Non-orchestration LLM calls should be made inside extensions with `executor`. |
| `execution.max_turns` | No | Per-run SDK turn limit; falls back to `WORKER_MAX_TURNS`. |
| `execution.timeout_seconds` | No | Per-run worker timeout; falls back to `WORKER_TIMEOUT_SECONDS`. |
| `extension.module` | Recommended | Python module containing `register(api)` for tools, environment validation, and prompt context. |
| `model.embedding_role` | No | Embedding role for RAG/indexing, usually `embedding`. |
| `model.embedding` / `model.embedding_base_url` | No | Manifest-local embedding model/base URL override. |
| `model.llm` / `model.llm_base_url` | No | Manifest-local worker model override. Prefer model roles unless a subagent truly needs a dedicated endpoint. |
| `model.extra_body` | No | Manifest-local OpenAI-compatible `extra_body` merged over role-level extra body config. |
| `memory.namespaces` | No | Durable memory namespaces available to the worker prompt. |
| `domains.file` | No | Subagent-local domain catalog path, used by Text2SQL. Core passes this through. |
| `routing_hints` | Recommended | Routing phrases that help the Orchestrator choose the subagent. |
| `suggested_questions` | No | Welcome-screen preset questions declared by the subagent. |

`model.llm_role` is removed; use `execution.model_role`.

## Adding a Skill

1. Create a directory under `skills/`.
2. Add a `SKILL.md` manifest with `name`, `description`, and `activation_hints`.
3. Write the method card body as Markdown.

Skills are loaded as guidance via `load_skill`; they are not executable worker agents.

## Data Safety

This public repository intentionally excludes:

- private datasets
- `.env` files
- SQLite runtime databases
- model endpoint details
- result-store artifacts

Use `.env.example` as the configuration contract and keep private runtime state local.
