# Environment Setup

Project-level setup prepares local data and generated runtime env files before
AgentWeave starts.

- [Text2SQL](text2sql.md): prepare or connect a read-only structured database.
- [RAG](rag.md): prepare a Markdown knowledge index and expose `RAG_INDEX_PATH`.

Generated state belongs in `.agentweave/`. Example source data belongs in
`data/examples/`. Subagent packages should not contain environment setup files,
sample data directories, or prepare scripts.
