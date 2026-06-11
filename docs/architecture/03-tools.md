# 工具系统与注册发现 (Tools & Registry)

> 主循环本身不用变复杂；工具能力靠一层清晰的注册面增长。

## 概述

Agent 的强大不取决于 `while True` 循环写得多复杂，而取决于它能调用的工具集有多丰富。

在这个框架里，无论是底层的基础能力（如记笔记），还是高阶的业务能力（如 Text2SQL 子代理），都会被抽象成 **Tool** 喂给 Orchestrator。

为了避免硬编码，系统实现了**基于 Manifest（声明文件）的注册发现机制**。
Manifest model、parser 和 registry 是分层的：model 只描述数据结构，parser 只负责读文件和校验，
registry 只负责发现、缓存和 prompt-facing 输出。

1. **AgentRegistry**: 负责扫描严格格式的子代理（`subagents/*/AGENT.yaml + prompt.md`）。
2. **SkillRegistry**: 负责扫描技能方法卡（`skills/*/SKILL.md`）。

## Manifest 驱动发现

无论是 Subagent 还是 Skill，它们都由统一的 `ManifestBase` 数据结构描述：

```python
@dataclass(frozen=True)
class ManifestBase:
    name: str              # e.g., "text2sql"
    description: str       # 简短描述，给主模型看
    location: Path         # 所在文件夹路径
    kind: str              # "skill" 还是 "subagent"
    body: str              # MD 文件的正文（Prompt 模板或方法步骤）
    
    # 针对 Subagent 的执行配置
    execution: ManifestExecution  
    # 包含：mode, max_turns, timeout
    
    tools: list[str]       # 已废弃；Subagent 工具统一通过 extension.py 注册
    memory: ManifestMemory # 该组件需要的 memory namespace
    domains: ManifestDomains
    model: ManifestModel
    extension: ManifestExtension
    routing_hints: list[str] # 意图路由的触发词
    metadata: dict
```

在系统启动时，这两个 Registry 会去读取对应目录下的配置文件。Subagent 固定采用
`AGENT.yaml` 声明元数据，`prompt.md` 存放 worker prompt，`extension.py register(api)`
作为工具、ResultFormatter、capability resolver、环境检查和 prompt context 的唯一标准入口。
`capabilities`、`policies` 和 `output_contract` 是 subagent 的治理声明；框架只透传，实际业务策略仍由 extension/tool 执行。
本地测试环境、生产连接方式和所需环境变量统一放在项目级 `docs/environment/`，
不放入 subagent 包，也不进入模型上下文。

`ArtifactStore` 是通用 artifact store。默认 artifact preview 是
`{"kind": "rows"}` 表格预览；后续图表、文件或报告可以使用同一个 preview envelope
暴露 `chart_spec`、`asset` 等非表格预览，而不改变现有 SQL/RAG 分页和 CSV 能力。

## AgentRegistry 与意图路由

扫描 `subagents/` 目录后，`AgentRegistry` 会暴露出两个核心能力：

1. 如果一个子代理的 `execution.mode == "worker"`，它就会被动态封装成一个 **Agent-as-Tool**，作为工具直接塞给主 Orchestrator。
2. 它会把收集到的描述和 `routing_hints`，拼装成 `<subagents_routing>` 格式的 XML，注入到 Orchestrator 的 System Prompt 中。

```xml
<subagents_routing>
  <subagent name="text2sql" route_when="用户想查询结构化数据;涉及统计等数据操作" />
</subagents_routing>
```

## SkillRegistry 摘要注入

扫描 `skills/` 目录后，`SkillRegistry` 不会把 Skill 变成可执行工具（因为 Skill 不执行代码），而是生成一段 `<skills_catalog>` XML 摘要，同样注入到主 Prompt 里。Skill 的详细内容要在需要时通过 `load_skill` 工具动态加载。

## Orchestrator 视角下的工具库

当主循环运行时，Orchestrator 手里拿着这么一套工具：

1. **`memory_search`**：查询持久记忆。支持级联退化（向量 -> 词法 -> 最近）。
2. **`memory_write`**：写入持久记忆。基于 `(namespace, key)` 自动 Upsert 更新。
3. **`load_skill`**：按需加载 `SKILL.md` 的完整正文（方法卡），注入当前上下文。
4. **`update_todo`**：会话内短时规划，一次只能有一个 `in_progress` 的任务。
5. **动态 Worker 工具**：比如 `text2sql`、`rag`。

## Agent-as-Tool：用工具包裹 Agent

这是整个架构最精妙的地方之一：**把隔离执行的沙箱，伪装成一个普通的 Function Tool**。

看 `SubagentRunner` 是怎么做的：

```python
def build_worker_agent_tool(self, manifest, profile):
    # 1. 用 SDK 建立真正的 Worker Agent（有它自己的 Prompt 和模型）
    worker_agent = self.build_worker_agent(manifest=manifest, profile=profile)
    
    # 2. 把它转成工具对象
    tool = worker_agent.as_tool(
        tool_name=manifest.name,
        tool_description=self._build_worker_agent_tool_description(manifest),
        parameters=SubagentToolInput,  # schema 要求必须传入 { "task": str }
    )
    
    # 3. 拦截原生的执行逻辑，替换成完全隔离的 run_subagent 流程
    async def invoke_tool(ctx, input_json):
        # input_json 就是 {"task": "查询某类业务指标"}
        result = await self.run_subagent(
            subagent_name=manifest.name,
            task=input_json["task"],
            orchestrator_context=parent_context,
        )
        return result.answer
        
    tool.on_invoke_tool = invoke_tool
    return tool
```

Orchestrator 以为自己只是调了一个普通的函数，但实际上这个函数在后台拉起了一个全新的隔离沙箱，带着另一个大模型实例跑了几轮，然后把总结好的 JSON 丢了回来。

## Hook 系统：旁路扩展

除了常规的 Prompt + Tool，系统还需要一些特定时机的介入。`HookRunner` 负责在生命周期关键节点执行扩展逻辑；`agent_runtime/core/hooks.py` 只提供机制，AgentWeave 自定义 hook 实现放在 `agent_runtime/hooks/`。

当前支持 **`SessionStart`、`PreToolUse`、`PostToolUse`** 三个事件。
`SessionStart` 的默认实现负责返回欢迎内容；Bot 在 `BOT.yaml` 里配置
`welcome.message`。如果设置 `welcome.preset: true`，运行时会把
`welcome.prompt` 与已挂载能力的 description 一起交给模型生成欢迎词。
工具调用前后的 hook 则统一收到
`tool_name`、`input`、`output/status` 等 payload，可用于审计、
阻断或向模型返回注入信息。

## 一句话记住

**工具系统的设计哲学是声明式发现 + 动态注入：`AGENT.yaml + prompt.md` / `SKILL.md` 声明能力，Registry 发现并注入到 prompt 或 tool list，Orchestrator 按需调用；大结果统一进入 artifact store。**
