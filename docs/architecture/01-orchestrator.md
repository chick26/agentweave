# 主编排器 (Main Orchestrator Agent)

> 编排器不自己干活——它识别意图、选择专家、汇总结论。

## 概述

在 General Agent Framework 中，核心大脑被称为 **Orchestrator（编排器）**。它采用 **Orchestrator + Ephemeral Worker** 的双层隔离架构。

为什么需要这种隔离？
因为大型语言模型的上下文很容易被噪音污染。如果让同一个 Agent 既负责和用户闲聊、又负责翻看数百行 SQL 报错日志、还要查阅历史记忆，它的注意力很快就会崩溃。

通过双层架构：
1. **Orchestrator** 保持清晰的头脑，专门负责：理解用户要干嘛、把任务丢给专门的 Subagent、拿到结果后给用户总结。
2. **Worker**（如 Text2SQL 子代理）在完全隔离的沙箱（独立的 `RunContext` 和内存数据库）中执行脏活累活。

## 核心数据结构：AgentRuntime

整个编排器运行时由 `agent_runtime/core/orchestrator.py` 中的 `AgentRuntime` 类驱动。

当启动应用时，这个类会将所有子系统连接在一起。当前实现的构造入口仍保持直接参数形态，核心依赖包括：

```python
class AgentRuntime:
    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str,
        session_db_path: Path,
        tables: dict[str, Path | str] | None = None,
        backend: DatabaseBackend | None = None,
        max_tokens: int = 4096,
        sql_base_url: str | None = None,
        sql_model_name: str | None = None,
        embedding_base_url: str | None = None,
        embedding_model_name: str | None = None,
        memory_enabled: bool | None = None,
        timezone_name: str | None = None,
    ) -> None:
        self.session_db_path = session_db_path
        
        # 挂载各个核心组件
        self.backend = backend or CsvSQLiteBackend(tables)
        self.model_profiles = build_model_profiles(...)
        
        self.skill_registry = SkillRegistry(skills_root=self.root / "skills")
        self.agent_registry = AgentRegistry(subagents_root=self.root / "subagents")
        
        self.memory_manager = MemoryManager(...)
        self.result_store = ResultStore(self.root / "agent_results.sqlite")
        
        # 隔离执行桥梁
        self.subagent_runner = SubagentRunner(...)
        
        # 上下文压缩和 Hook
        self.compressor = ContextCompressor(model=...)
        self.hook_runner = HookRunner(...)
```

## Agent 循环：ask() 的生命周期

当用户在 UI 输入一句话时，实际上触发了 `ask()` 方法的一套标准工作流：

1. **上下文压缩**：防止历史消息把 Token 撑爆。
2. **构建 System Prompt**：把能力路由、记忆偏好拼装给模型。
3. **构建 Tools**：把基础能力和 Subagent 能力都变成工具。
4. **执行多轮循环**：借助 SDK 的 `Runner.run()` 执行。

```python
async def ask(
    self, 
    user_input: str, 
    session_id: str, 
    event_callback, 
    max_turns: int = 10
) -> dict:
    
    context = OrchestratorContext(...)
    session = SQLiteSession(session_id, str(self.session_db_path))
    
    # 1. 压缩历史消息
    prior_messages = session.get_messages()
    compressed_messages = await self.compressor.compress(prior_messages, ...)
    
    # 2. 构建 Agent 实例 (包含 System Prompt 和 Tools)
    agent = Agent[OrchestratorContext](
        name="Agent Orchestrator",
        instructions=self._build_instructions(session_id, ...),
        model=build_model(profile, ...),
        tools=self._build_tools(),
    )
    
    # 3. 运行主循环
    result = await Runner.run(
        agent, 
        user_input, 
        context=context, 
        session=session, 
        max_turns=max_turns
    )
    
    return {"answer": result.final_answer, ...}
```

## System Prompt 组装流水线

模型看到的不是一段静态文本，而是一条动态拼装的输入流水线。`_build_instructions()` 会把各个子系统的信息注入到 `SYSTEM_PROMPT` 模板中。

```python
# agent_runtime/core/prompts.py
SYSTEM_PROMPT = """\
你是通用 Agent Framework 的主编排器（Orchestrator），负责理解用户意图、选择合适的专用 Subagent 执行任务、汇总结果。

# 核心行为

<truthfulness>
如实汇报结果。如果 subagent 失败，直接说明失败原因和输出...
</truthfulness>

<role_boundary>
- 专业查询或数据处理默认委派给匹配的 subagent；不要猜测其内部 schema...
{memory_role_policy}
- 只有需要方法卡时才加载 skill。
</role_boundary>

# 工具使用

<tool_policy>
1. **get_current_time** — 解析相对时间。
2. **Subagent tools** — 根据下方 subagents_routing 选择... task 必须自包含。
3. **load_skill** — 根据下方 skills_catalog 加载方法卡。
4. **update_todo** — 对多步骤任务跟踪进度。
{memory_tool_policy}
</tool_policy>

{skills_section}

{memory_policy_section}

# 沟通风格
<communication_style>
- 默认用中文，简洁、直接、面向业务问题。
...
</communication_style>
"""
```

这里面充满了 XML 标签（如 `<truthfulness>`, `<tool_policy>`）。这种设计能让模型更明确地区分指令边界，减少幻觉。

## 工具注册：_build_tools()

Orchestrator 手里的工具分为两类：

1. **内建工具**：`get_current_time`, `memory_search`, `memory_write`, `load_skill`, `update_todo`。
2. **动态 Worker 工具**：通过扫描 `subagents/` 目录，把里面的独立 Agent 包装成当前 Orchestrator 可调用的一个函数工具。

## 意图路由

当系统里有多个 Subagent 时，Orchestrator 怎么知道该调用谁？
靠的是注入到 Prompt 中的 `<subagents_routing>` 标签。

```xml
<subagents_routing>
  <subagent 
    name="text2sql" 
    execution_mode="worker" 
    description="使用自然语言查询结构化数据..." 
    route_when="用户想查询结构化数据;涉及统计、筛选、排行等操作" 
  />
</subagents_routing>
```

模型读到这段 XML，就会自己做意图识别。当用户提出结构化数据查询时，模型判断命中 `text2sql` 的路由提示，于是它就会决定调用 `text2sql(task="...")` 这个工具。

## 一句话记住

**Orchestrator 的本质是一个受 system prompt 驱动的意图路由器——它通过 XML routing 发现能力，通过 agent-as-tool 委派任务，通过事件总线收集执行痕迹。**
