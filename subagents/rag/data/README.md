# RAG Local Markdown Data

Put local knowledge-base Markdown files here. This directory is ignored by git except for
this README.

Prepare the local knowledge index before starting runtime:

```bash
uv run agentweave-prepare-rag
```

This writes `.agentweave/rag_index.json` and `.agentweave/rag.env`.

Do not commit customer, production, or private documents.
