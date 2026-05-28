---
name: sea_cable_faults
description: 回答关于海缆故障的数据问题，包括故障时间、故障段、维修状态、影响业务、维修进度等。
table: sea_cable_faults
text_fields:
  - sea_cable_no
  - sea_cable_type
  - pop_fault_seg
  - pop_fault_seg_detail
  - pop_fault_reason_name
  - affect_business
  - repair_status_name
  - repair_progress
  - pop_repair_charge_man
  - repair_type
  - a_city
  - z_city
field_descriptions:
  sea_cable_no: 海缆编号/名称，例如 NCP、SMW5、AAG、APG 等
  sea_cable_type: 海缆类型，例如 参建海缆
  pop_fault_seg: 故障段编号，例如 S1、S2、S3
  pop_fault_seg_detail: 故障段详细描述，包含具体位置
  pop_fault_time: 故障发生时间
  pop_fault_reason_name: 故障原因，例如 未知、设备
  affect_business: 是否影响业务，值为 是 或 否
  business_break_time: 业务中断时间
  repair_status_name: 维修状态，例如 已结束、未开始
  fault_duration: 故障持续时长（小时）
  estimated_repair_completion_time: 预计维修完成时间
  estimated_ship_departure_time: 预计船只出发时间
  repair_progress: 维修进度描述
  pop_repair_plan: 维修计划时间段
  pop_repair_charge_man: 维修负责人
  reserved_field: 记录时间
  repair_type: 维修类型，例如 故障
  relayNum: 中继段数量
  affectNum: 影响数量
  rate: 费率/影响率
  fault_id: 故障编号
  a_city: 该故障可能影响的 A 端城市（可能包含多个城市，逗号分隔）
  z_city: 该故障可能影响的 Z 端城市（可能包含多个城市，逗号分隔）
---

# 海缆故障数据域

用于回答某条海缆的故障次数、故障段、维修状态、是否影响业务、故障时长、维修进度等问题。

## 业务口径

- 查询某条海缆时，先对 sea_cable_no 做 value linking；海缆编号通常是 NCP、SMW5、AAG、APG 等简称。
- a_city 和 z_city 可能包含逗号分隔的多个城市名，城市过滤优先使用 LIKE。
- fault_duration 是小时数，可用于平均故障时长、最长故障等聚合。
- pop_fault_time 是故障发生时间，可用于按时间范围筛选。
