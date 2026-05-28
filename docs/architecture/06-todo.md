# 待办与工作记忆 (Todo System)

> 对多步骤任务来说，可见的短期计划不是装饰，而是防止长会话目标漂移的稳定器。

## 概述

当用户提出一个包含 4 个步骤的复杂需求时，如果没有任务拆解和记录，LLM 在执行到第 2 步时，很容易就会被长长的历史对话带偏，忘掉剩下的 2 步。

所以，框架引入了 **Todo 系统**。
必须明确：Todo 是 Orchestrator 会话级别的**短期工作记忆**。它只存在于内存中，一旦会话结束就会销毁，**绝对不会**被写入 SQLite 做持久化存储。

## 数据结构与约束

一个待办项非常简单，定义在 `memory_manager.py`：

```python
TodoStatus = Literal["pending", "in_progress", "completed"]

@dataclass(frozen=True)
class TodoItem:
    content: str
    status: TodoStatus
```

为了保持模型专注，框架在后台强制下达了物理隔离约束：
**在同一个会话下，只允许存在 1 个处于 `in_progress` 的待办项。**
如果 Orchestrator 试图同时标记两件事正在进行，`MemoryManager` 会直接抛异常。这逼迫它只能一次专心干一件事。

## update_todo：全量覆盖工具

暴露给 Orchestrator 的修改途径只有一个，即 `update_todo` 函数工具：

```python
@function_tool
async def update_todo(ctx, items: list[TodoToolItem]) -> str:
    """Update session-local working todos.
    Use this for multi-step work planning and progress tracking.
    Todos are short-lived working memory and are NOT written into
    durable user/project/skill memory."""
    
    # 强制全量覆盖逻辑，模型必须传入完整的新列表
    todos = [TodoItem(content=item.content, status=item.status) for item in items]
    
    # 内部会进行 in_progress 数量校验，并发出 todo_event
    updated = runtime.memory_manager.update_todo(ctx.context.session_id, todos)
    
    return f"Updated {len(updated)} todo items."
```

## System Prompt 约束

光有工具有时没用，模型可能懒得调用。所以必须在 `SYSTEM_PROMPT` 中耳提面命：

```xml
<tool_policy>
...
4. **update_todo** — 对多步骤任务，先用 todo 分解工作并跟踪进度。
   完成一项就立刻标记，不要积攒到最后一起更新。
...
</tool_policy>
```

## 上下文注入：让计划时刻可见

写进去的 Todo 怎么让模型看见？
通过 `build_todo_context` 方法，框架在组装 Orchestrator 的 System Prompt 时，会把当前的 Todo 状态打成一串 Markdown 列表，塞进 `<memory_context>` 标签里：

```python
def build_todo_context(self, session_id: str) -> str:
    todos = self.get_todos(session_id)
    if not todos:
        return ""
        
    lines = ["[todo_working_memory]"]
    for item in todos:
        lines.append(f"- [{item.status}] {item.content}")
        
    return "\n".join(lines)
```

于是模型在每一轮醒来时，都会在自己的脑门上看到类似这样的便签：

```text
[todo_working_memory]
- [completed] 解析用户提到的时间范围
- [in_progress] 查询 IDC 机房可用机柜
- [pending] 查询各机房利用率
- [pending] 汇总分析结论
```
看到这张便签，它就会明确：“哦，我现在应该集中火力去查可用机柜了”。

## Todo 与 持久 Memory 的边界

不要把这两个概念混在一起用：

| 维度 | Todo (待办) | Memory (持久记忆) |
|------|------|--------|
| **生命周期** | 随着 Session 关闭而销毁 | 跨会话，永久保存直到手动删除 |
| **存储介质** | Python 进程内存 (`dict`) | SQLite 表 + 外部 Embedding |
| **存在目的** | 规划短期任务执行路径 | 储存知识、习惯、规则 |
| **修改工具** | `update_todo` | `memory_write` / `memory_search` |

## 一句话记住

**Todo 是给 Orchestrator 准备的会话级动态进度条——它让模型每一轮都能看见自己正在干哪一步，防止在漫长的推理解析中迷失目标。**
