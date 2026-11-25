# 资料说明

所有文件均为合成资料（`source_info.json` 的 `source_kind = synthetic`），仅用于本地可复现运行。
缺失值（`null`）、部分质检、待处理状态都是资料含义的一部分，读取时不做静默填充。
日期均为 ISO 格式（`YYYY-MM-DD`）；区间的 `starts_at` 含当天、`ends_at` 不含当天，`ends_at = null` 表示至今生效。

## orders.json — 订单

| 字段 | 含义 |
| --- | --- |
| `order_id` | 订单标识 |
| `channel` | 渠道（`web` / `market`） |
| `ordered_at` | 下单日期，决定适用的商品版本 |
| `sku` | 商品标识 |
| `quantity` | 订购件数；一个订单可含多件，退货可按件部分发生 |
| `customer_id` | 客户标识（合成），用于复购分析 |
| `promo` | 是否促销期订单，用于订单结构分析 |

## returns.json — 退货申请（客服选项）

| 字段 | 含义 |
| --- | --- |
| `return_id` | 退货单标识 |
| `order_id` | 关联订单 |
| `quantity` | 退货件数；可小于订购件数（部分退货） |
| `reason_code` | 客服标准原因代码，按 `requested_at` 时点的原因字典解释；`null` 表示未选 |
| `free_text` | 客服自由文本；与标准原因是不同证据层 |
| `status` | `pending` / `refunded` / `exchange_pending` / `closed` / `cancelled` |
| `requested_at` | 申请日期，决定适用的原因字典版本 |
| `supersedes` | 取代哪一张退货单（重复开单的去重链路） |

## inspections.json — 仓库到货质检

| 字段 | 含义 |
| --- | --- |
| `inspection_id` | 质检记录标识 |
| `return_id` | 关联退货单 |
| `received_at` | 到货质检日期 |
| `seal` | `sealed` / `opened` |
| `functional` | `ok` / `fail` / `null`（未检，不等于正常） |
| `cosmetic` | `ok` / `damaged` / `null`（未检，不等于正常） |
| `decision` | 仓库质检结论（`pending` / `resell` / `isolate` …），与后续处置记录分开 |

同一退货单可有多条质检记录，分析取 `received_at` 最新的一条，历史记录保留。

## catalog.json — 商品目录与版本

| 字段 | 含义 |
| --- | --- |
| `sku` / `version` | 商品标识与版本；批次标识取 `sku@version` |
| `starts_at` / `ends_at` | 版本生效区间 |
| `dimensions` | 该版本的规格 |
| `merged_from` | 合并来源 SKU（可缺省）；被合并 SKU 的谱系归入合并后 SKU |

## commitments.json — 承诺与采购到货

| 字段 | 含义 |
| --- | --- |
| `kind` | `exchange`（承诺换货）/ `replenish`（采购补货） |
| `return_id` / `sku` | 换货关联退货单；补货关联 SKU |
| `quantity` | 件数；换货缺省时从关联退货单取数并在依据中注明 |
| `status` | `proposed`（未确认，不计入供给）/ `confirmed` / `shipped` / `cancelled` |
| `due_at` / `shipped_at` | 承诺期限 / 实际发出日期 |

## refunds.json — 退款记录

`refund_id`、`return_id`、`amount`、`status`（`executed` / `pending`）、`executed_at`。
已执行的退款是事实，任何重计划都不得改写。

## dispositions.json — 后续处置记录

`disposition_id`、`return_id`、`action`（`resell` / `refurbish` / `isolate` / `scrap`）、
`quantity`、`status`（`planned` / `executed` / `cancelled`）、`planned_at` / `executed_at`。
`planned` 可被新建议取代（原记录保留），`executed` 不可改写。

## reason_dictionary.json — 版本化原因字典

- `versions[]`：`version`、生效区间、`entries`（代码 → `label` 与 `family` 原因族）。
- `mappings[]`：相邻版本间代码映射，`kind = one_to_one` 可直接比较，`split`（一对多）只能做族级比较。

## resources.json — 仓储资源（决策时点快照）

- `as_of`：决策时点。
- `zones`：各库位 `capacity` / `used`（`resale` 可售、`refurb` 翻新、`isolation` 隔离）。
- `qc_capability`：`per_day` 日质检能力、`backlog` 当前积压。
- `resale_stock`：各 SKU 可售现货。
