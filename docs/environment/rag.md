# RAG Environment

RAG runs only from a prepared knowledge index path. Markdown source files and
index generation are project-level environment setup, not part of the subagent
runtime package.

## Local Markdown Example

Local examples live outside the subagent package:

```text
data/examples/rag/
└── *.md
```

Prepare the local knowledge index:

```bash
uv run agentweave-prepare-rag
```

This writes:

```text
.agentweave/rag_index.json
.agentweave/rag.env
```

If `.agentweave/rag_index.json` already exists, the command reuses it and only
ensures `.agentweave/rag.env` points to it. Pass `--overwrite` to rebuild.

Use another Markdown directory with:

```bash
uv run agentweave-prepare-rag --source-root /absolute/or/project/path --overwrite
```

## Runtime Variables

Runtime starts from `.env` plus `.agentweave/*.env`. RAG expects:

```dotenv
RAG_INDEX_PATH=/absolute/path/to/rag_index.json
```

Default chunking for preparation:

```dotenv
RAG_CHUNK_CHARS=500
RAG_CHUNK_OVERLAP=50
```

The runtime never scans Markdown files directly during a subagent run.
