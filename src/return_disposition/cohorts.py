"""可比队列：渠道 × 批次（商品版本）。

队列是可比分析的最小范围：同一渠道、同一商品版本（批次）的订单机会。
重复开单（被取代的退货单）不计入分子分母，但保留记录；
超退（活跃退货数量超过订购数量）显式标记，不静默修正。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import definition_at
from .evidence import Attribution, EvidenceLayers, attribute, build_evidence
from .model import Dataset, Order, ReturnRequest

CANCELLED_STATUSES = {"cancelled", "superseded"}


@dataclass(frozen=True)
class CohortKey:
    channel: str
    batch: str  # sku@version

    def label(self) -> str:
        return f"{self.channel}×{self.batch}"


@dataclass
class AttributedReturn:
    request: ReturnRequest
    order: Order
    evidence: EvidenceLayers
    attribution: Attribution
    flags: list[str] = field(default_factory=list)


@dataclass
class Cohort:
    key: CohortKey
    sold_qty: int = 0
    order_ids: list[str] = field(default_factory=list)
    active: list[AttributedReturn] = field(default_factory=list)
    duplicates: list[AttributedReturn] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    dict_versions: set[str] = field(default_factory=set)

    @property
    def returned_qty(self) -> int:
        return sum(r.request.quantity or 0 for r in self.active)


def _is_duplicate(request: ReturnRequest, superseded_ids: set[str]) -> bool:
    if request.status in CANCELLED_STATUSES:
        return True
    return request.return_id in superseded_ids


def build_cohorts(
    dataset: Dataset,
) -> tuple[list[Cohort], dict[str, AttributedReturn]]:
    """构建队列并返回 (队列列表, 按退货单索引的归因记录)。"""
    order_by_id = dataset.order_by_id
    superseded_ids = {r.supersedes for r in dataset.returns if r.supersedes}

    cohorts: dict[CohortKey, Cohort] = {}
    definitions = {}
    for order in dataset.orders:
        definition = definition_at(dataset.catalog, order.sku, order.ordered_at)
        definitions[order.order_id] = definition
        key = CohortKey(channel=order.channel or "未知渠道", batch=definition.batch)
        cohort = cohorts.setdefault(key, Cohort(key=key))
        cohort.sold_qty += order.quantity or 0
        cohort.order_ids.append(order.order_id)
        cohort.flags.extend(definition.flags)

    index: dict[str, AttributedReturn] = {}
    for request in dataset.returns:
        order = order_by_id.get(request.order_id)
        if order is None:
            continue
        definition = definitions[order.order_id]
        key = CohortKey(channel=order.channel or "未知渠道", batch=definition.batch)
        cohort = cohorts.setdefault(key, Cohort(key=key))
        evidence = build_evidence(request, dataset.latest_inspection(request.return_id), dataset.dictionary)
        result = attribute(evidence)
        record = AttributedReturn(request=request, order=order, evidence=evidence, attribution=result)
        if evidence.cs_reason is not None and evidence.cs_reason.dict_version:
            cohort.dict_versions.add(evidence.cs_reason.dict_version)
        if evidence.cs_reason is not None and evidence.cs_reason.fallback:
            record.flags.append("原因代码跨版本回退解释，与现行版本原生记录不可直接比较")
        if _is_duplicate(request, superseded_ids):
            record.flags.append("重复开单，已被取代，不计入分子分母")
            cohort.duplicates.append(record)
        else:
            cohort.active.append(record)
        index[request.return_id] = record

    # 超退检查：同一订单活跃退货数量超过订购数量。
    for cohort in cohorts.values():
        for order_id in cohort.order_ids:
            order = order_by_id[order_id]
            active_qty = sum(
                r.request.quantity or 0 for r in cohort.active if r.request.order_id == order_id
            )
            if order.quantity is not None and active_qty > order.quantity:
                cohort.flags.append(
                    f"订单 {order_id} 活跃退货 {active_qty} 件超过订购 {order.quantity} 件，疑似重复开单未闭合"
                )

    return sorted(cohorts.values(), key=lambda c: (c.key.channel, c.key.batch)), index
