"""处置建议与重计划。

建议动作由证据三态推出，并受库位容量、质检能力、已承诺换货、
采购到货日期约束；每条建议都记录用到了哪些约束，便于回查。

重计划规则：
- 已执行的处置、已完成的退款、已发出的换货是事实，不可静默改写；
  新证据与之冲突时只发告警，保留原记录；
- 只有未执行（planned）的处置可以被新建议取代，被取代者保留审计痕迹。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .evidence import ISSUE, OK, UNKNOWN, EvidenceLayers
from .model import Dataset

ZONE_OF_ACTION = {"resell": "resale", "refurbish": "refurb", "isolate": "isolation", "scrap": None}


@dataclass(frozen=True)
class ConstraintUse:
    kind: str  # zone_capacity | qc_capability | commitment | arrival | stock
    ref: str
    detail: str


@dataclass(frozen=True)
class Recommendation:
    rec_id: str
    kind: str  # disposition | restock
    return_id: str | None
    sku: str | None
    action: str  # resell | refurbish | isolate | scrap | restock
    quantity: int
    preconditions: tuple[str, ...] = ()
    constraints: tuple[ConstraintUse, ...] = ()
    rationale: tuple[str, ...] = ()
    alerts: tuple[str, ...] = ()
    supersedes: str | None = None


@dataclass(frozen=True)
class ReplanOutcome:
    recommendations: tuple[Recommendation, ...]
    kept: tuple[str, ...]  # 维持不变的已计划处置
    superseded: tuple[str, ...]  # 被取代的已计划处置
    immutable: tuple[str, ...]  # 已执行、未改写的处置
    alerts: tuple[str, ...]


def desired_action(evidence: EvidenceLayers) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """由证据三态推出动作：(action, preconditions, rationale)。"""
    inspection = evidence.inspection
    if inspection is None:
        return (
            "isolate",
            ("等待到货并完成质检",),
            ("无到货检验记录，不能按无缺陷处理",),
        )
    if inspection.functional == ISSUE:
        return (
            "isolate",
            ("功能不合格，等待进一步判定",),
            ("质检判定功能不合格",),
        )
    preconditions: list[str] = []
    rationale: list[str] = []
    if inspection.functional == UNKNOWN:
        preconditions.append("完成功能质检")
        rationale.append("功能项未检，不能视为功能正常")
    if inspection.cosmetic == UNKNOWN:
        preconditions.append("完成外观质检")
        rationale.append("外观项未检，不能视为外观正常")
    if preconditions:
        if inspection.cosmetic == ISSUE:
            rationale.append("外观已判损坏，但仍须先完成未检项")
        return "isolate", tuple(preconditions), tuple(rationale)
    if inspection.cosmetic == ISSUE:
        return "refurbish", ("翻新后复检",), ("外观损坏、功能正常，需翻新",)
    if inspection.seal == "sealed":
        return "resell", (), ("未拆封且功能、外观均正常",)
    return "refurbish", ("重封装后复检",), ("已拆封但功能、外观均正常，需重封装",)


def _apply_capacity(
    action: str,
    quantity: int,
    zone_load: dict[str, int],
    dataset: Dataset,
    rationale: list[str],
    alerts: list[str],
    constraints: list[ConstraintUse],
) -> str:
    """容量约束：超限时可售/翻新降级为隔离；隔离超限发告警。"""
    zone = ZONE_OF_ACTION.get(action)
    if zone is None:
        return action
    zones = dataset.resources.zones
    if zone in zones:
        capacity = zones[zone].capacity
        load = zone_load.get(zone, 0)
        if load + quantity <= capacity:
            constraints.append(
                ConstraintUse("zone_capacity", zone, f"{load}+{quantity}≤{capacity}")
            )
            zone_load[zone] = load + quantity
            return action
        rationale.append(f"{zone} 容量不足（{load}+{quantity}>{capacity}），降级为隔离")
    # 降级或本身就是隔离
    fallback = "isolate"
    zone = ZONE_OF_ACTION[fallback]
    if zone in zones:
        capacity = zones[zone].capacity
        load = zone_load.get(zone, 0)
        if load + quantity <= capacity:
            constraints.append(
                ConstraintUse("zone_capacity", zone, f"{load}+{quantity}≤{capacity}")
            )
            zone_load[zone] = load + quantity
        else:
            alerts.append(f"隔离容量超限（{load}+{quantity}>{capacity}），需扩容或加速质检流转")
            zone_load[zone] = load + quantity
    return fallback


def plan(dataset: Dataset, evidence_index: dict[str, EvidenceLayers]) -> ReplanOutcome:
    """以 resources.as_of 为决策时点生成建议并重计划。"""
    as_of = dataset.resources.as_of
    alerts: list[str] = []
    immutable: list[str] = []
    kept: list[str] = []
    superseded: list[str] = []
    recommendations: list[Recommendation] = []

    executed_by_return: dict[str, list] = {}
    planned_by_return: dict[str, list] = {}
    for disposition in dataset.dispositions:
        if disposition.status == "executed":
            executed_by_return.setdefault(disposition.return_id or "", []).append(disposition)
        elif disposition.status == "planned":
            planned_by_return.setdefault(disposition.return_id or "", []).append(disposition)

    # 1) 已执行处置：不可改写；新证据冲突只告警。
    for return_id, dispositions in executed_by_return.items():
        for disposition in dispositions:
            immutable.append(disposition.disposition_id)
            evidence = evidence_index.get(return_id)
            if (
                evidence
                and disposition.action == "resell"
                and evidence.inspection is not None
                and (evidence.inspection.functional == ISSUE or evidence.inspection.cosmetic == ISSUE)
            ):
                alerts.append(
                    f"{disposition.disposition_id} 已执行为再售，但最新质检显示异常；"
                    "已执行记录不可改写，需人工复核在途/已售影响"
                )

    # 2) 已发出换货 / 已完成退款是事实；质检未决时提示风险。
    for commitment in dataset.commitments:
        if commitment.kind == "exchange" and commitment.status == "shipped" and commitment.return_id:
            evidence = evidence_index.get(commitment.return_id)
            inspection = evidence.inspection if evidence else None
            if inspection is None or inspection.functional == UNKNOWN:
                alerts.append(
                    f"{commitment.commitment_id} 换货已发出，但退货 {commitment.return_id} "
                    "功能项未检；已发出换货不可改写，风险需记录跟踪"
                )

    # 3) 活跃退货（未取消、未被取代）生成建议。
    cancelled = {"cancelled", "superseded"}
    superseded_ids = {r.supersedes for r in dataset.returns if r.supersedes}
    active_returns = [
        r for r in dataset.returns if r.status not in cancelled and r.return_id not in superseded_ids
    ]
    zone_load = {name: zone.used for name, zone in dataset.resources.zones.items()}

    # 已承诺未发出的换货占用可售库存，先计入容量。
    for commitment in dataset.commitments:
        if commitment.kind == "exchange" and commitment.status == "confirmed":
            zone_load["resale"] = zone_load.get("resale", 0) + (commitment.quantity or 1)

    qc_needed = 0
    for request in sorted(active_returns, key=lambda r: r.return_id):
        if request.return_id in executed_by_return:
            continue
        evidence = evidence_index.get(request.return_id)
        if evidence is None:
            continue
        quantity = request.quantity or 1
        action, preconditions, rationale = desired_action(evidence)
        constraints: list[ConstraintUse] = []
        rationale_list = list(rationale)
        rec_alerts: list[str] = []
        action = _apply_capacity(
            action, quantity, zone_load, dataset, rationale_list, rec_alerts, constraints
        )
        if preconditions:
            qc_needed += quantity
            constraints.append(
                ConstraintUse(
                    "qc_capability",
                    "qc",
                    f"质检积压 {dataset.resources.qc_backlog} 件，能力 "
                    f"{dataset.resources.qc_per_day} 件/天",
                )
            )
        existing = planned_by_return.get(request.return_id, [])
        supersedes = None
        if existing:
            current = existing[0]
            if current.action == action:
                kept.append(current.disposition_id)
                rationale_list.append(f"维持已计划处置 {current.disposition_id}")
            else:
                superseded.append(current.disposition_id)
                supersedes = current.disposition_id
                rationale_list.append(
                    f"取代已计划处置 {current.disposition_id}（{current.action}→{action}），原记录保留"
                )
        recommendations.append(
            Recommendation(
                rec_id=f"rec-{request.return_id}",
                kind="disposition",
                return_id=request.return_id,
                sku=None,
                action=action,
                quantity=quantity,
                preconditions=preconditions,
                constraints=tuple(constraints),
                rationale=tuple(rationale_list),
                alerts=tuple(rec_alerts),
                supersedes=supersedes,
            )
        )

    if qc_needed:
        per_day = dataset.resources.qc_per_day or 0
        total = dataset.resources.qc_backlog + qc_needed
        if per_day > 0:
            days = (total + per_day - 1) // per_day
            alerts.append(f"质检待处理合计 {total} 件，按 {per_day} 件/天约需 {days} 个工作日")

    # 4) 补货建议：已承诺换货 vs 可售库存 + 承诺日前可到的采购。
    return_by_id = dataset.return_by_id
    order_by_id = dataset.order_by_id
    for commitment in dataset.commitments:
        if commitment.kind != "exchange" or commitment.status != "confirmed":
            continue
        sku = commitment.sku
        quantity = commitment.quantity
        derived_note = None
        if sku is None and commitment.return_id:
            linked = return_by_id.get(commitment.return_id)
            order = order_by_id.get(linked.order_id) if linked else None
            sku = order.sku if order else None
            if quantity is None and linked is not None:
                quantity = linked.quantity
                derived_note = f"数量取自关联退货 {linked.return_id}"
        quantity = quantity if quantity is not None else 1
        arrivals = [
            c
            for c in dataset.commitments
            if c.kind == "replenish" and c.sku == sku and c.status == "confirmed"
        ]
        proposed = [
            c
            for c in dataset.commitments
            if c.kind == "replenish" and c.sku == sku and c.status == "proposed"
        ]
        covering = [
            a
            for a in arrivals
            if a.due_at is not None and commitment.due_at is not None and a.due_at <= commitment.due_at
        ]
        stock = dataset.resources.resale_stock.get(sku or "", 0)
        supply = stock + sum(a.quantity or 0 for a in covering)
        constraints = [
            ConstraintUse("commitment", commitment.commitment_id, f"承诺 {quantity} 件，期限 {commitment.due_at}"),
            ConstraintUse("stock", sku or "未知SKU", f"可售库存 {stock} 件"),
        ]
        constraints.extend(
            ConstraintUse("arrival", a.commitment_id, f"{a.quantity or '?'} 件，到货 {a.due_at}")
            for a in arrivals
        )
        rationale = []
        if derived_note:
            rationale.append(derived_note)
        for p in proposed:
            rationale.append(f"{p.commitment_id} 为未确认采购计划，不计入供给")
        rec_alerts = []
        if commitment.due_at is not None and as_of is not None and commitment.due_at < as_of:
            rec_alerts.append(f"{commitment.commitment_id} 承诺日期 {commitment.due_at} 已过仍未履约")
        if supply < quantity:
            rationale.append(f"可售供给 {supply} 件 < 承诺 {quantity} 件，建议补货")
            if arrivals and all(
                a.due_at is not None and commitment.due_at is not None and a.due_at > commitment.due_at
                for a in arrivals
            ):
                nearest = min(a.due_at for a in arrivals if a.due_at is not None)
                rec_alerts.append(
                    f"最近的确认采购到货 {nearest} 晚于承诺日期 {commitment.due_at}，存在履约冲突"
                )
            recommendations.append(
                Recommendation(
                    rec_id=f"rec-restock-{sku}",
                    kind="restock",
                    return_id=None,
                    sku=sku,
                    action="restock",
                    quantity=quantity - supply,
                    preconditions=(),
                    constraints=tuple(constraints),
                    rationale=tuple(rationale),
                    alerts=tuple(rec_alerts),
                )
            )
        elif rec_alerts:
            alerts.extend(rec_alerts)

    return ReplanOutcome(
        recommendations=tuple(recommendations),
        kept=tuple(kept),
        superseded=tuple(superseded),
        immutable=tuple(immutable),
        alerts=tuple(alerts),
    )
