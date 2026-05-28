---
name: data_analysis
description: 面向表格结果的数据分析方法卡，用于统计摘要、质量检查、异常发现、图表建议和报告组织。
activation_hints:
  - 分析已有 result_id 或表格结果
  - 需要统计摘要、缺失值、异常值、分布、排行、图表建议
  - 需要把查询结果整理成报告或洞察
memory:
  namespaces:
    - project
    - skill:data_analysis
---

# Data Analysis Skill

这是一个可加载 skill，不是 subagent，也不会作为同名 tool 暴露。需要分析数据结果时，先通过 `load_skill("data_analysis")` 读取本方法卡，再把这里的分析步骤用于当前回答，或作为明确要求写入某个 subagent 的 task。

## Workflow

1. **Data Intake**
   - 明确输入来源：`result_id`、SQL 查询结果样例、CSV/表格文件或某个数据 domain。
   - 保留列名、行数、过滤条件、SQL 口径和采样限制；不要把样例行误当作全量结果。

2. **Profile**
   - 数值列：统计 count、missing、min、max、mean、sum，必要时补充 median、p25、p75。
   - 分类型列：统计 distinct count、top values、空值/未知值。
   - 时间列：识别时间范围、粒度和是否存在断点。

3. **Quality Signals**
   - 标记缺失值、重复键、全空列、疑似类型错误、极端值、异常时间范围。
   - 对无法确认的问题使用“疑似/需要核验”，不要把质量信号说成确定业务结论。

4. **Analysis**
   - 先给总量和口径，再给分组、排行、变化或异常。
   - 对 TopN/排行结果同时说明排序字段和方向。
   - 对异常值说明检测规则，例如 IQR、z-score、环比阈值或业务阈值。

5. **Render Metadata**
   - 给出适合前端渲染的图表建议：
     - 类别 TopN：bar。
     - 时间趋势：line。
     - 数值分布：histogram/box。
     - 构成占比：stacked bar 或 pie，但类别过多时避免 pie。
   - 输出可复核字段：metric、dimension、time_field、filters、limit、result_id。

## Response Contract

面向用户的回答用中文，结构为：

- 直接结论：一句话说明最重要发现。
- 统计摘要：关键数值、样本/全量口径、主要分布。
- 质量与异常：只列有意义的缺失、异常或风险。
- 图表建议：最多 3 个，说明图表类型、维度、指标。

如果输入只有样例数据，必须明确“以下只基于样例行/当前页，不能代表全量”。
