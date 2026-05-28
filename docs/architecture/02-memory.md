# 记忆系统 (Memory System)

> 只有跨会话、无法从当前工作重新推导的知识，才值得进入 memory。

## 概述

Agent 系统的上下文窗口不是无限的。如果把所有的运行日志、原始聊天记录、历史查询结果全都塞进模型，模型很快就会因为信息过载而变笨。

在这个框架里，Memory（记忆）**不是聊天记录的流水账**，而是经过提纯的、可复用的知识。

系统采用了三层记忆生命周期设计：

| 层级 | 命名空间 | 持久化 | 用途 |
|------|---------|--------|------|
| **持久记忆** | `project`, `user` | 永久 SQLite | 跨会话的项目规则、业务约定、用户偏好 |
| **会话续航** | `session:<id>` | TTL 24h | 上下文压缩时的 LLM 摘要，保证长对话不断层 |
| **短期 Todo** | 内存 dict | 仅当前会话 | 会话内多步规划的进度追踪 |

## 存储层：MemoryStore

底层存储基于 SQLite 数据库 (`agent_memory.sqlite`)。它不仅存纯文本，还同时支持向量搜索。

```sql
-- 主记录表：支持 UPSERT，确保相同 namespace 和 key 下只有一条最新记录
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    UNIQUE(namespace, key)
);

-- 全文检索表
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, tags, key, namespace, content="memories"
);

-- 向量存储表（外挂 Embedding）
CREATE TABLE memory_vectors (
    memory_id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL
);
```

## 检索策略 (Cascading Fallback)

记忆怎么查最准？框架使用了一套**级联退化（Cascading Fallback）**的检索策略。

```text
向量检索 (Vector Search) 
  → 降级为: 词法检索 (Lexical / FTS5)
    → 降级为: 最近记录 (Recent records)
```

`MemoryManager.retrieve()` 是这样实现的：

```python
def retrieve(self, query: str, namespaces: list[str], limit: int = 10):
    
    # 1. 如果有 Embedding 模型，优先尝试向量检索 (Cosine Similarity)
    if self.embedding_client is not None:
        self._backfill_embeddings(namespaces)  # 自动回填缺失的向量
        query_vectors = self.embedding_client.embed_texts([query])
        records = self.store.search_vectors(query_vectors[0], namespaces, limit)
        if records:
            return MemorySearchResult(records=records, strategy="vector")
            
    # 2. 如果没配 Embedding 模型、或者查不到，降级到词法检索
    return MemorySearchResult(
        records=self.store.search(query, namespaces, limit),
        strategy="lexical_fallback",
    )
```

### 增量 Embedding 机制

在 `_backfill_embeddings()` 中，系统会检查 `content_hash`。只有当记忆内容被更新过，或者这条记忆是新加的，它才会被送去调用 `EMBEDDING_MODEL` 配置的 Embedding 模型。这避免了每次查询都要重新计算所有记忆的向量，大幅节省了耗时和 Token。

## Memory Tools（Orchestrator 侧）

对模型暴露了两个修改持久记忆的工具：

- `memory_search(query, namespaces, limit)`: 调用底层的级联检索。
- `memory_write(namespace, key, content, tags)`: 插入或更新一条记忆，底层会自动触发对应的 Embedding 更新。

## 上下文压缩 (ContextCompressor)

如果用户一直在当前会话里提问，历史消息越来越长怎么办？`ContextCompressor` 实现了三级压缩策略：

1. **Token Budget 估算**：优先用模型 tokenizer 估算 chat prompt token；Qwen 模型优先尝试 `transformers.AutoTokenizer`，OpenAI 系模型优先尝试 `tiktoken`，不可用时才退回启发式估算，并在诊断中记录 counter/fallback 信息。
2. **`none` (无需压缩)**：当输入 token 占用低于预算 70% 时，保留原始历史。
3. **`soft` (软压缩)**：当输入 token 占用达到 70%-90% 时触发。将前面大部分消息送给 LLM 进行摘要提取，把摘要写入 `session:<id>` 记忆中，并在原消息流中插入占位符 `[系统已将先前多轮对话压缩为摘要]`。
4. **`hard` (硬截断)**：当输入 token 占用超过 90% 时触发急救。保留 system 和最近消息，并尽量避免破坏 assistant tool call 与 tool result 配对。

压缩判断使用独立的上下文窗口配置，不再把 `max_tokens` 混作上下文窗口：

```bash
export QWEN36_CONTEXT_WINDOW=32768
export QWEN32_CONTEXT_WINDOW=32768
```

### 软压缩的 Prompt 设计

```text
你的任务是为以下对话创建一份详细的摘要。这份摘要将被放在后续会话的开头作为上下文延续——之后的新消息会跟在摘要后面，而原始对话历史将被替换。

1. **核心请求与意图**：用户的完整请求和意图
2. **关键技术概念**：讨论过的重要技术概念和约定
3. **文件与代码**：检查、修改或创建的文件及关键代码片段
...
8. **当前工作**：中断前正在进行的具体工作，包含文件名和代码片段
```

## System Prompt 中的记忆策略

为了防止模型乱写记忆，Orchestrator 的 Prompt 中有严格的行为准则：

```xml
# 记忆管理

<memory_policy>
写入 memory 前，先检查是否已有覆盖该内容的记录——如果有，更新而不是新建。删除已证实错误的记忆。

应该保存的：用户明确表达的偏好、项目规则/约定、跨会话有价值的事实。
不要保存的：一次性查询结果、凭据/密码、中间推理过程、瞬时错误。
</memory_policy>
```

## 一句话记住

**Memory 系统的核心是三级分层 + 三级检索退化：持久/续航/Todo 三种生命周期，向量/词法/最近三种检索策略，各自在正确的场景被激活。**
