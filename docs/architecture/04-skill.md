# 技能系统 (Skill System)

> 专门知识不该一开始全部塞进上下文，而该在需要时被轻量发现、按需展开。

## 概述

如果把所有的分析方法论、异常排查手册全都写死在主 System Prompt 里，LLM 的注意力就会被冲散。

在这个框架里，**Skill（技能）不是可执行的代码，而是方法卡（Method Cards）**。
它们提供方法论指导、分析框架、标准工作流约定。Orchestrator 知道它们的存在，但不到真正需要用的那一刻，绝不占用宝贵的上下文窗口。

## Skill vs Subagent 对比

千万别把 Skill 和 Subagent 搞混，这很重要：

| 维度 | Skill (方法卡) | Subagent (子智能体) |
|------|---------------|-------------------|
| **位置** | `skills/*/SKILL.md` | `subagents/*/AGENT.md` |
| **注册机制** | SkillRegistry | AgentRegistry |
| **存在感** | 在 prompt 中注入 `<skills_catalog>` 摘要 | 在 prompt 中注入 `<subagents_routing>` 路由 |
| **加载方式** | 通过 `load_skill("名称")` 主动去读取文件 | 作为同名 agent-tool 函数，直接传参数调用 |
| **执行模式** | **它不执行！** 仅仅是把大段指导文本扔进上下文 | 在完全隔离的沙箱（RunContext）里自己跑起来 |
| **资源消耗** | 仅占用读取后的 Token 空间 | 占用独立的数据库连接、新的模型实例和 Token |

## SKILL.md 结构设计

一个标准的技能定义文件是以 Markdown 加上 YAML 前置元数据组成的。

例如 `skills/data_analysis/SKILL.md`：

```yaml
---
name: data_analysis
description: 结构化数据分析的标准化方法卡，包含数据画像、质量检查、异常发现和图表建议等步骤。
activation_hints:
  - 用户要求分析数据特征、分布或质量
  - 用户询问异常数据或需要图表建议
memory:
  namespaces:
    - skill:data_analysis
---

# Data Analysis Skill

## Workflow
1. 数据画像：总是先看关键字段的分布、基数、缺失率。
2. 质量信号检查：是否有重复记录？是否有不合理的空值？
3. 分布/排行分析：识别长尾或头部聚集效应。
4. 图表建议：如果用户要在界面展示，优先推荐饼图（占比）或柱状图（排行）。
5. 报告结构：按照“执行情况 -> 数据质量发现 -> 业务建议”三段式输出。
```

## 发现与摘要注入

系统启动时，`SkillRegistry` 扫描所有这些文件，但**绝不把 Markdown 正文直接塞进 Prompt**。

相反，`format_catalog_for_prompt()` 方法会生成一个非常轻量的 XML 摘要目录：

```xml
<skills_catalog>
  <skill 
    name="data_analysis" 
    description="结构化数据分析的标准化方法卡..." 
    activation_hints="用户要求分析数据特征、分布或质量..." 
  />
</skills_catalog>
```

Orchestrator 每次醒来，只看一眼这个“菜单”，心里有数就行。

## load_skill 工具：按需加载

当用户真的问了一句：“帮我出个 IDC 机房资源的数据画像报告，顺便帮我看下有没有异常。”

Orchestrator 模型在推理时看到 `activation_hints` 被命中了，它就会决定调用 `load_skill("data_analysis")`。

看看这个工具的实现：

```python
@function_tool
async def load_skill(ctx, skill_name: str) -> str:
    """Load a real skill document from skills/*/SKILL.md.
    Skills are method cards or reusable workflows.
    Use this only after consulting skills_catalog."""
    
    # 从注册表中取出完整内容
    skill = ctx.context.skill_registry.get(skill_name)
    if not skill:
        return f"Error: skill {skill_name} not found."
        
    return json_dumps({
        "name": skill.name,
        "description": skill.description,
        "body": skill.body,  # 这里把完整的方法论塞回去了
    })
```

这一轮结束，方法卡的全文内容就实实在在地变成了 `tool_result`，写进了这一轮的消息历史。
在下一轮中，Orchestrator 就可以边看着方法卡，边去调度 `text2sql` 执行具体的取数操作，最后按方法卡约定的“三段式”格式生成报告。

## 新增 Skill 的正确姿势

没有任何代码需要修改，完全声明式接入：

1. 在 `skills/` 建个新文件夹。
2. 写个 `SKILL.md`，把 YAML 和大段文字贴进去。
3. 重启。Orchestrator 就自动学会了。

## 一句话记住

**Skill 是给 Orchestrator 准备的轻量级指南针——它不执行任何代码，只在需要时被 `load_skill` 临时拉取，把专业方法论注入到当前的对话上下文中。**
