# RAG Local Environment

RAG is packaged as a self-contained subagent. The framework only loads the
manifest, prompt, and tools; PDF parsing, chunking, embedding calls, and
retrieval stay inside this package.

## Local PDF Mode

Default local mode reads PDFs declared in `AGENT.yaml`:

```yaml
data:
  roots:
    - subagents/rag/data
  globs:
    - "*.pdf"
```

Expected local files:

```text
subagents/rag/data/
└── *.pdf
```

Each query builds a temporary in-memory retrieval index. No vector database is
started and no vectors are persisted.

## Embedding Endpoint

Configure an OpenAI-compatible embedding endpoint:

```bash
export EMBEDDING_BASE_URL=http://localhost:8002/v1
export EMBEDDING_MODEL=openai-compatible-embedding-model
export OPENAI_API_KEY=not-needed
```

Then start the backend service normally:

```bash
uv run agentweave-server
```

## Production Replacement

To use a real vector database later, keep the `search_knowledge_base` tool
contract and replace implementation details in `env.py` / `scripts/`. Bot config
and framework runtime do not need to change.

## Limitations

- First version only supports PDFs with extractable text.
- OCR is not included.
- PDF files are local test data and should not be committed if private.
