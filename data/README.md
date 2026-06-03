# Local Data Directory

This global directory is kept for ad-hoc local files.
New subagent-specific local test data should live inside the corresponding
capability package:

- Text2SQL: `subagents/text2sql/data/`
- RAG: `subagents/rag/data/`

Each subagent owns its own environment instructions:

- `subagents/text2sql/ENVIRONMENT.md`
- `subagents/rag/ENVIRONMENT.md`

This directory is intentionally ignored by git. Do not commit customer,
production, or private datasets.
