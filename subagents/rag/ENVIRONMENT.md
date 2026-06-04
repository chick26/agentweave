# RAG Local Environment

RAG uses prepared Markdown knowledge files. The runtime reads a local knowledge
index during question answering.

## Startup Order

1. Put Markdown files under `subagents/rag/data/`.
2. Configure the embedding endpoint in `.env`.
3. Prepare the local knowledge index once.
4. Start Streamlit or the backend runtime.

Expected local files:

```text
subagents/rag/data/
└── *.md
```

Default chunking:

```dotenv
RAG_CHUNK_CHARS=500
RAG_CHUNK_OVERLAP=50
```

Prepare the local knowledge index:

```bash
uv run agentweave-prepare-rag
```

This creates, if missing:

```text
.agentweave/rag_index.json
.agentweave/rag.env
```

If `.agentweave/rag_index.json` already exists, the command reuses it and only
ensures `.agentweave/rag.env` points to it. Pass `--overwrite` to rebuild.

Then start Streamlit:

```bash
uv run streamlit run app.py
```
