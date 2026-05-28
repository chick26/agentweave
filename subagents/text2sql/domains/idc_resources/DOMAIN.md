---
name: idc_resources
description: 回答关于 IDC 资源的数据问题，包括机房、机柜、数据中心、客户、功率等。
table: resources
text_fields:
  - region
  - data_center
  - dc_build_type
  - country
  - city
  - machine_room
  - cabinet
  - cabinet_business_status
  - operation_status
  - customer
field_descriptions:
  region: 大区/区域
  data_center: 数据中心名称
  dc_build_type: 数据中心建设类型
  country: 国家
  city: 城市
  machine_room: 机房名称
  cabinet: 机柜名称/机柜编号
  cabinet_business_status: 机柜业务状态，例如 Available、Sold、Reserved
  cabinet_network_time: 机柜开通网络时间
  rated_power_watt: 额定功率，单位瓦
  operation_status: 运营状态，例如 出租、空闲、自用
  customer: 客户名称
  sales_power_watt: 销售功率，单位瓦
business_metrics:
  idle_cabinet_count:
    description: 空闲机柜数量
    phrases:
      - 空闲机柜
      - 空闲资源
    aggregation: count
    unit: cabinet
    filters:
      operation_status: 空闲
  available_cabinet_count:
    description: 可用机柜数量
    phrases:
      - 可用机柜
      - 可用资源
    aggregation: count
    unit: cabinet
    filters:
      cabinet_business_status: Available
---

# IDC 资源数据域

用于回答机房、机柜、数据中心、城市、国家、客户、功率和状态等 IDC 资源问题。

## 业务口径

- 查询机房、机柜、城市、状态、客户等具体实体时，先对相应 text_fields 做 value linking。
- “空闲机柜”和“可用机柜”是不同业务口径：空闲机柜优先按 operation_status = 空闲；可用机柜优先按 cabinet_business_status = Available。
- 机柜业务状态常见值包括 Available、Sold、Reserved。
- 功率字段单位为瓦；如用户问功率汇总，应明确使用 rated_power_watt 或 sales_power_watt。
- 没有具体实体过滤条件的汇总问题可以跳过 value linking。
