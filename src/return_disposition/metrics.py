"""队列指标：原因分布、退货率、订单结构校正、复购影响、无法归因比例。

只在可比队列（渠道 × 批次）内计算原因分布；跨期比较用
直接标准化把订单结构（渠道 × 批次构成）固定到参考期，
把「结构效应」和「率效应」分开，避免把促销期结构变化误读为质量恶化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .cohorts import Cohort
from .model import Order, ReturnRequest

LOW_CONFIDENCE_SOLD = 5


def return_rate(cohort: Cohort) -> float | None:
    if cohort.sold_qty <= 0:
        return None
    return cohort.returned_qty / cohort.sold_qty


def reason_distribution(cohort: Cohort) -> dict[str, dict]:
    """队列内按原因族的分布；每层证据的强弱分开计数。"""
    dist: dict[str, dict] = {}
    for record in cohort.active:
        attr = record.attribution
        family = attr.family or "unattributable"
        slot = dist.setdefault(
            family,
            {"qty": 0, "verified": 0, "unverified": 0, "dict_versions": set(), "notes": set()},
        )
        slot["qty"] += record.request.quantity or 0
        if attr.verified_by_inspection:
            slot["verified"] += record.request.quantity or 0
        else:
            slot["unverified"] += record.request.quantity or 0
        if record.evidence.cs_reason is not None and record.evidence.cs_reason.dict_version:
            slot["dict_versions"].add(record.evidence.cs_reason.dict_version)
        slot["notes"].update(attr.notes)
    return dist


def unattributable_share(cohort: Cohort) -> tuple[int, int]:
    """(无法归因件数, 活跃退货件数)。"""
    total = sum(r.request.quantity or 0 for r in cohort.active)
    missing = sum(r.request.quantity or 0 for r in cohort.active if r.attribution.unattributable)
    return missing, total


def compare_cohorts(cohort_a: Cohort, cohort_b: Cohort, dictionary) -> dict:
    """两个队列间按原因族的可比性报告。"""
    dist_a = reason_distribution(cohort_a)
    dist_b = reason_distribution(cohort_b)
    families = sorted(set(dist_a) | set(dist_b))
    entries = {}
    for family in families:
        versions_a = dist_a.get(family, {}).get("dict_versions", set())
        versions_b = dist_b.get(family, {}).get("dict_versions", set())
        notes = []
        comparable = True
        if versions_a and versions_b and versions_a != versions_b:
            comparable = False
            notes.append(
                f"字典版本不同（{sorted(versions_a)} vs {sorted(versions_b)}），仅族级比较"
            )
        entries[family] = {
            "qty_a": dist_a.get(family, {}).get("qty", 0),
            "qty_b": dist_b.get(family, {}).get("qty", 0),
            "directly_comparable": comparable,
            "notes": notes,
        }
    return {
        "cohort_a": cohort_a.key.label(),
        "cohort_b": cohort_b.key.label(),
        "families": entries,
    }


@dataclass(frozen=True)
class GroupStat:
    customers: tuple[str, ...]
    repurchased: tuple[str, ...]
    censored: tuple[str, ...]

    @property
    def rate(self) -> float | None:
        effective = len(self.customers) - len(self.censored)
        if effective <= 0:
            return None
        return len(self.repurchased) / effective


@dataclass(frozen=True)
class RepurchaseReport:
    window_days: int
    as_of: date | None
    returned_group: GroupStat
    baseline_group: GroupStat
    note: str


def _repurchase_within(orders: list[Order], customer: str, after: date, window: int) -> bool:
    deadline = after + timedelta(days=window)
    return any(
        o.customer_id == customer and o.ordered_at is not None and after < o.ordered_at <= deadline
        for o in orders
    )


def repurchase_impact(
    orders: list[Order],
    active_returns: list[ReturnRequest],
    as_of: date | None,
    window_days: int = 90,
) -> RepurchaseReport:
    """退货客户 vs 未退货客户在窗口期内的复购；窗口未关闭的客户记为删失。"""
    order_by_id = {o.order_id: o for o in orders}
    first_return: dict[str, date] = {}
    for request in active_returns:
        order = order_by_id.get(request.order_id or "")
        if order is None or order.customer_id is None or request.requested_at is None:
            continue
        current = first_return.get(order.customer_id)
        if current is None or request.requested_at < current:
            first_return[order.customer_id] = request.requested_at

    first_order: dict[str, date] = {}
    for order in orders:
        if order.customer_id is None or order.ordered_at is None:
            continue
        current = first_order.get(order.customer_id)
        if current is None or order.ordered_at < current:
            first_order[order.customer_id] = order.ordered_at

    def classify(customers: dict[str, date]) -> GroupStat:
        repurchased: list[str] = []
        censored: list[str] = []
        for customer, start in sorted(customers.items()):
            if _repurchase_within(orders, customer, start, window_days):
                repurchased.append(customer)
            elif as_of is not None and start + timedelta(days=window_days) > as_of:
                censored.append(customer)
        return GroupStat(tuple(customers.keys()), tuple(repurchased), tuple(censored))

    returned = classify(first_return)
    baseline_customers = {c: d for c, d in first_order.items() if c not in first_return}
    baseline = classify(baseline_customers)
    note = "复购窗口未关闭的客户已删失；样本量小，比例仅供参考"
    return RepurchaseReport(window_days, as_of, returned, baseline, note)


@dataclass(frozen=True)
class PeriodStat:
    period: str
    sold: int
    returned: int
    crude_rate: float | None
    adjusted_rate: float | None
    mix_effect: float | None
    rate_effect: float | None
    residual: float | None
    low_confidence_cells: tuple[str, ...]
    missing_cells: tuple[str, ...]


@dataclass(frozen=True)
class MixReport:
    reference_period: str
    periods: tuple[PeriodStat, ...]
    note: str


def mix_adjustment(
    cells: dict[str, dict[tuple[str, str], list[int]]],
) -> MixReport:
    """直接标准化：cells[period][(channel, batch)] = [sold, returned]。

    以最早期的结构为参考，给出每期粗率、结构校正率，
    并把与参考期的粗率差分解为结构效应与率效应（残差单列）。
    """
    periods = sorted(cells)
    note = "结构校正以最早期为参考；单元销量 <5 标记为低置信"
    if not periods:
        return MixReport("", (), note)
    reference = periods[0]
    ref_cells = cells[reference]
    ref_sold = sum(v[0] for v in ref_cells.values())

    def rate(cell: list[int]) -> float | None:
        return (cell[1] / cell[0]) if cell[0] > 0 else None

    stats: list[PeriodStat] = []
    for period in periods:
        current = cells[period]
        sold = sum(v[0] for v in current.values())
        returned = sum(v[1] for v in current.values())
        crude = (returned / sold) if sold > 0 else None
        low_conf = tuple(
            f"{channel}×{batch}"
            for (channel, batch), v in sorted(current.items())
            if v[0] < LOW_CONFIDENCE_SOLD
        )
        missing = tuple(
            f"{channel}×{batch}"
            for (channel, batch) in sorted(current)
            if (channel, batch) not in ref_cells
        )
        adjusted = None
        mix_effect = None
        rate_effect = None
        residual = None
        if period != reference and ref_sold > 0 and sold > 0:
            overlap = 0
            adjusted_value = 0.0
            mix_effect = 0.0
            rate_effect = 0.0
            for key, ref_cell in ref_cells.items():
                w_ref = ref_cell[0] / ref_sold
                r_ref = rate(ref_cell)
                cur = current.get(key)
                w_cur = (cur[0] / sold) if cur else 0.0
                r_cur = rate(cur) if cur else None
                if r_ref is not None:
                    mix_effect += (w_cur - w_ref) * r_ref
                if r_cur is not None and r_ref is not None:
                    overlap += 1
                    rate_effect += w_cur * (r_cur - r_ref)
                    adjusted_value += w_ref * r_cur
            if overlap:
                adjusted = adjusted_value
            ref_crude = rate([ref_sold, sum(v[1] for v in ref_cells.values())])
            if crude is not None and ref_crude is not None:
                residual = (crude - ref_crude) - (mix_effect or 0.0) - (rate_effect or 0.0)
        stats.append(
            PeriodStat(
                period=period,
                sold=sold,
                returned=returned,
                crude_rate=crude,
                adjusted_rate=adjusted,
                mix_effect=mix_effect,
                rate_effect=rate_effect,
                residual=residual,
                low_confidence_cells=low_conf,
                missing_cells=missing,
            )
        )
    return MixReport(reference, tuple(stats), note)


def period_cells(orders: list[Order], active_returns: list[ReturnRequest], batch_of) -> dict:
    """按月汇总 (channel, batch) 单元的销量与退货量。"""
    order_by_id = {o.order_id: o for o in orders}
    cells: dict[str, dict[tuple[str, str], list[int]]] = {}
    for order in orders:
        if order.ordered_at is None:
            continue
        period = order.ordered_at.strftime("%Y-%m")
        key = (order.channel or "未知渠道", batch_of(order))
        cells.setdefault(period, {}).setdefault(key, [0, 0])[0] += order.quantity or 0
    for request in active_returns:
        order = order_by_id.get(request.order_id or "")
        if order is None or order.ordered_at is None:
            continue
        period = order.ordered_at.strftime("%Y-%m")
        key = (order.channel or "未知渠道", batch_of(order))
        bucket = cells.setdefault(period, {}).setdefault(key, [0, 0])
        bucket[1] += request.quantity or 0
    return cells
