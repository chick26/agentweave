# Text2SQL Local Environment

Text2SQL is packaged as a self-contained subagent. The framework only loads the
manifest, prompt, tools, and the table catalog; CSV loading, real database
connection, schema inspection, value linking, and SQL generation stay inside
this package.

## Required Startup Order

Text2SQL database access is strict:

1. Prepare or connect the database environment.
2. Write database connection variables to a local env file.
3. Start the AgentWeave runtime with `uv`.
4. Call the `text2sql` subagent.

If the runtime starts without a prepared database, Text2SQL tools return a
database-environment error. The runtime will not auto-load CSVs or start a
database for you.

## Local SQLite Mode

Local development starts from CSV files declared in `AGENT.yaml`:

```yaml
data:
  roots:
    - subagents/text2sql/data
  tables:
    resources: resources.csv
    sea_cable_faults: sea_cable_faults.csv
```

Queryable table/domain descriptions are kept in one local catalog:

```text
subagents/text2sql/domain_catalog.yaml
```

Expected local files:

```text
subagents/text2sql/data/
├── resources.csv
└── sea_cable_faults.csv
```

Prepare a local SQLite database:

```bash
uv sync
uv run agentweave-prepare-text2sql --overwrite
```

This creates:

```text
.agentweave/text2sql.sqlite
.agentweave/text2sql.env
```

Then start runtime. The server and Streamlit entrypoints load `.env` and
`.agentweave/text2sql.env` automatically:

```bash
uv run agentweave-server
```

## Temporary CSV Backend

For narrow tests only, an explicit CSV backend can be configured with
`TEXT2SQL_TABLES_JSON`. This is not a startup fallback; it is an explicit
environment choice.

```bash
cat > .agentweave/text2sql.env <<'EOF'
TEXT2SQL_BACKEND=csv
TEXT2SQL_TABLES_JSON={"resources":"subagents/text2sql/data/resources.csv","sea_cable_faults":"subagents/text2sql/data/sea_cable_faults.csv"}
EOF
uv run agentweave-server
```

## Real Database Mode

Production can switch to a read-only database connection without changing the
framework or Bot config:

```bash
cat > .agentweave/text2sql.env <<'EOF'
TEXT2SQL_BACKEND=sqlite
TEXT2SQL_DATABASE_URL=sqlite:////absolute/path/to/readonly.db
EOF
```

The current `SqlDatabaseBackend` supports SQLite-style URLs. Add other database
adapters behind the same `DatabaseBackend` protocol when needed.

## Model Roles

Text2SQL uses:

```bash
export ORCHESTRATOR_BASE_URL=http://localhost:8000/v1
export ORCHESTRATOR_MODEL=qwen3.6-27b
export EXECUTOR_BASE_URL=http://localhost:8001/v1
export EXECUTOR_MODEL=qwen3-32b
export EMBEDDING_BASE_URL=http://localhost:8002/v1
export EMBEDDING_MODEL=openai-compatible-embedding-model
```

Subagent worker orchestration defaults to the main `orchestrator` model role.
Text2SQL SQL generation uses the canonical `executor` model role.
