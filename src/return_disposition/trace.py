"""追溯：从处置建议回查事实链，并做跨批次随访。

- explain_recommendation：建议 -> 订单事实、三层原因证据、
  商品版本、容量/承诺/到货约束、告警，一条链不断；
- batch_followup：同一 SKU 相邻版本（批次）间按原因族比较，
  字典换版时只做族级比较并显式标记，判断后续批次是否支持
  原先的质量判断。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .catalog import definition_at, lineage_sources
from .cohorts import AttributedReturn
from .disposition import Recommendation
from .model import Dataset

MIN_FAMILY_SAMPLE = 3


def explain_recommendation(
    rec: Recommendation,
    dataset: Dataset,
    attribution_index: dict[str, AttributedReturn],
) -> dict[str, Any]:
    """一条建议的完整事实链。"""
    out: dict[str, Any] = {
        "rec_id": rec.rec_id,
        "kind": rec.kind,
        "action": rec.action,
        "quantity": rec.quantity,
        "preconditions": list(rec.preconditions),
        "rationale": list(rec.rationale),
        "alerts": list(rec.alerts),
        "supersedes": rec.supersedes,
        "constraints": [
            {"kind": c.kind, "ref": c.ref, "detail": c.detail} for c in rec.constraints
        ],
    }
    if rec.return_id is None:
        out["sku"] = rec.sku
        return out
    record = attribution_index.get(rec.return_id)
    if record is None:
        return out
    request = record.request
    order = record.order
    definition = definition_at(dataset.catalog, order.sku, order.ordered_at)
    evidence = record.evidence
    attr = record.attribution
    out["order_facts"] = {
        "order_id": order.order_id,
        "channel": order.channel,
        "ordered_at": str(order.ordered_at),
        "quantity": order.quantity,
        "customer_id": order.customer_id,
        "promo": order.promo,
    }
    out["return_facts"] = {
        "return_id": request.return_id,
        "status": request.status,
        "requested_at": str(request.requested_at),
        "quantity": request.quantity,
        "supersedes": request.supersedes,
    }
    out["reason_evidence"] = {
        "cs_reason": None
        if evidence.cs_reason is None
        else {
            "code": evidence.cs_reason.code,
            "label": evidence.cs_reason.label,
            "family": evidence.cs_reason.family,
            "dict_version": evidence.cs_reason.dict_version,
            "expected_version": evidence.cs_reason.expected_version,
            "fallback": evidence.cs_reason.fallback,
        },
        "free_text": evidence.free_text,
        "free_text_family_hint": evidence.free_text_family_hint,
        "inspection": None
        if evidence.inspection is None
        else {
            "inspection_id": evidence.inspection.inspection_id,
            "seal": evidence.inspection.seal,
            "functional": evidence.inspection.functional,
            "cosmetic": evidence.inspection.cosmetic,
            "decision": evidence.inspection.decision,
        },
        "attribution": {
            "family": attr.family,
            "basis": list(attr.basis),
            "verified_by_inspection": attr.verified_by_inspection,
            "unattributable": attr.unattributable,
            "notes": list(attr.notes),
        },
    }
    out["product_version"] = {
        "sku": order.sku,
        "batch": definition.batch,
        "version": definition.entry.version if definition.entry else None,
        "dimensions": definition.entry.dimensions if definition.entry else None,
        "lineage_sources": list(lineage_sources(dataset.catalog, order.sku or "")),
        "flags": list(definition.flags),
    }
    return out


def batch_followup(
    dataset: Dataset,
    attribution_index: dict[str, AttributedReturn],
    sku: str,
) -> dict[str, Any]:
    """同一 SKU 相邻版本间的原因族分布比较。"""
    versions = sorted(
        (v for v in dataset.catalog if v.sku == sku),
        key=lambda v: (v.starts_at is None, v.starts_at),
    )
    superseded_ids = {r.supersedes for r in dataset.returns if r.supersedes}
    cancelled = {"cancelled", "superseded"}
    per_version: list[dict[str, Any]] = []
    for version in versions:
        orders = [
            o
            for o in dataset.orders
            if o.sku == sku and version.covers(o.ordered_at)
        ]
        sold = sum(o.quantity or 0 for o in orders)
        families: Counter[str] = Counter()
        dict_versions: set[str] = set()
        unattributable = 0
        returned = 0
        for order in orders:
            for request in dataset.returns_of_order(order.order_id):
                if request.status in cancelled or request.return_id in superseded_ids:
                    continue
                record = attribution_index.get(request.return_id)
                if record is None:
                    continue
                returned += request.quantity or 0
                if record.attribution.unattributable:
                    unattributable += request.quantity or 0
                elif record.attribution.family:
                    families[record.attribution.family] += request.quantity or 0
                if record.evidence.cs_reason is not None and record.evidence.cs_reason.dict_version:
                    dict_versions.add(record.evidence.cs_reason.dict_version)
        attributed = sum(families.values())
        per_version.append(
            {
                "batch": version.batch,
                "version": version.version,
                "window": [
                    version.starts_at.isoformat() if version.starts_at else None,
                    version.ends_at.isoformat() if version.ends_at else None,
                ],
                "sold": sold,
                "returned": returned,
                "attributed": attributed,
                "unattributable": unattributable,
                "families": dict(families),
                "dict_versions": sorted(dict_versions),
            }
        )

    comparisons: list[dict[str, Any]] = []
    for prev, curr in zip(per_version, per_version[1:]):
        dict_changed = prev["dict_versions"] != curr["dict_versions"]
        notes = []
        if dict_changed:
            notes.append(
                f"原因字典换版（{prev['dict_versions']}→{curr['dict_versions']}），仅族级比较"
            )
        families = sorted(set(prev["families"]) | set(curr["families"]))
        family_rows = []
        for family in families:
            prev_qty = prev["families"].get(family, 0)
            curr_qty = curr["families"].get(family, 0)
            prev_share = prev_qty / prev["attributed"] if prev["attributed"] else None
            curr_share = curr_qty / curr["attributed"] if curr["attributed"] else None
            if (
                prev["attributed"] < MIN_FAMILY_SAMPLE
                or curr["attributed"] < MIN_FAMILY_SAMPLE
                or max(prev_qty, curr_qty) < 2
            ):
                verdict = "样本不足，不能确认"
            elif prev_share is not None and curr_share is not None and curr_share < prev_share:
                verdict = "后续批次该族占比下降，支持问题集中于前一批次的判断"
            elif prev_share is not None and curr_share is not None and curr_share > prev_share:
                verdict = "后续批次该族占比上升，原判断需重新评估"
            else:
                verdict = "占比持平，原判断维持观察"
            family_rows.append(
                {
                    "family": family,
                    "prev_qty": prev_qty,
                    "curr_qty": curr_qty,
                    "prev_share": prev_share,
                    "curr_share": curr_share,
                    "verdict": verdict,
                }
            )
        comparisons.append(
            {
                "from_batch": prev["batch"],
                "to_batch": curr["batch"],
                "dict_changed": dict_changed,
                "notes": notes,
                "families": family_rows,
            }
        )
    return {"sku": sku, "versions": per_version, "comparisons": comparisons}
