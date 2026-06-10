# Text2SQL Environment

Text2SQL runs only from explicit database environment variables. The subagent
package owns the SQL tools and domain catalog, while project-level preparation
owns local CSV examples, generated SQLite files, and env files.

## Local SQLite Example

Local examples live outside the subagent package:

```text
data/examples/text2sql/
├── resources.csv
└── sea_cable_faults.csv
```

Prepare a local SQLite database:

```bash
uv run agentweave-prepare-text2sql --overwrite
```

The command reads `data/examples/text2sql/<table>.csv` for each table declared
in `subagents/text2sql/domain_catalog.yaml`, then writes:

```text
.agentweave/text2sql.sqlite
.agentweave/text2sql.env
```

Use another CSV directory with:

```bash
uv run agentweave-prepare-text2sql --csv-root /absolute/or/project/path --overwrite
```

## Runtime Variables

Runtime starts from `.env` plus `.agentweave/*.env`. Text2SQL only supports
SQLite runtime connections.

Required configuration:

```dotenv
TEXT2SQL_BACKEND=sqlite
TEXT2SQL_DATABASE_URL=sqlite:////absolute/path/to/readonly.sqlite
```

Production should point `TEXT2SQL_DATABASE_URL` at a read-only database. The
runtime never loads CSV files automatically and never builds a database during a
subagent run.
