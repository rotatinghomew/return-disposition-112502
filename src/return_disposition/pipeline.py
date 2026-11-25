"""流水线编排：加载 -> 归因 -> 队列 -> 指标 -> 处置 -> 追溯。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .catalog import definition_at
from .cohorts import AttributedReturn, Cohort, build_cohorts
from .disposition import ReplanOutcome, plan
from .input_contract import load_bundle
from .metrics import (
    MixReport,
    RepurchaseReport,
    mix_adjustment,
    period_cells,
    reason_distribution,
    repurchase_impact,
    return_rate,
    unattributable_share,
)
from .model import Dataset
from .trace import batch_followup, explain_recommendation


@dataclass
class Report:
    dataset: Dataset
    cohorts: list[Cohort]
    attribution_index: dict[str, AttributedReturn]
    mix: MixReport
    repurchase: RepurchaseReport
    replan: ReplanOutcome
    incomparable_records: list[str]

    def explain(self, rec_id: str) -> dict:
        rec = next(r for r in self.replan.recommendations if r.rec_id == rec_id)
        return explain_recommendation(rec, self.dataset, self.attribution_index)

    def followup(self, sku: str) -> dict:
        return batch_followup(self.dataset, self.attribution_index, sku)


def run(root: str | Path) -> Report:
    bundle = load_bundle(root)
    dataset = Dataset.from_bundle(bundle)
    cohorts, index = build_cohorts(dataset)

    active_returns = [record.request for cohort in cohorts for record in cohort.active]
    batch_of = lambda order: definition_at(dataset.catalog, order.sku, order.ordered_at).batch
    mix = mix_adjustment(period_cells(dataset.orders, active_returns, batch_of))
    repurchase = repurchase_impact(
        dataset.orders, active_returns, dataset.resources.as_of
    )
    evidence_index = {rid: record.evidence for rid, record in index.items()}
    replan = plan(dataset, evidence_index)

    incomparable: list[str] = []
    for cohort in cohorts:
        for record in cohort.active + cohort.duplicates:
            incomparable.extend(f"{record.request.return_id}: {flag}" for flag in record.flags)
        incomparable.extend(f"队列 {cohort.key.label()}: {flag}" for flag in cohort.flags)

    return Report(
        dataset=dataset,
        cohorts=cohorts,
        attribution_index=index,
        mix=mix,
        repurchase=repurchase,
        replan=replan,
        incomparable_records=incomparable,
    )


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def format_report(report: Report) -> str:
    lines: list[str] = []
    lines.append("== 可比队列（渠道×批次） ==")
    for cohort in report.cohorts:
        rate = return_rate(cohort)
        missing, total = unattributable_share(cohort)
        lines.append(
            f"[{cohort.key.label()}] 售出 {cohort.sold_qty} 件，活跃退货 {cohort.returned_qty} 件，"
            f"退货率 {_pct(rate)}，无法归因 {missing}/{total}"
        )
        for family, slot in sorted(reason_distribution(cohort).items()):
            versions = "/".join(sorted(slot["dict_versions"])) or "—"
            lines.append(
                f"    {family}: {slot['qty']} 件（质检核实 {slot['verified']}，未核实 {slot['unverified']}，字典 {versions}）"
            )
        for record in cohort.duplicates:
            lines.append(f"    重复开单：{record.request.return_id}（不计入）")
    lines.append("")
    lines.append("== 跨期结构校正（月度，参考期 %s） ==" % report.mix.reference_period)
    for stat in report.mix.periods:
        lines.append(
            f"[{stat.period}] 售出 {stat.sold}，退货 {stat.returned}，粗率 {_pct(stat.crude_rate)}，"
            f"结构校正率 {_pct(stat.adjusted_rate)}，结构效应 {_pct(stat.mix_effect)}，"
            f"率效应 {_pct(stat.rate_effect)}"
        )
        if stat.low_confidence_cells:
            lines.append(f"    低置信单元（销量<5）：{', '.join(stat.low_confidence_cells)}")
        if stat.missing_cells:
            lines.append(f"    参考期没有的单元：{', '.join(stat.missing_cells)}")
    lines.append("")
    lines.append("== 复购影响 ==")
    returned = report.repurchase.returned_group
    baseline = report.repurchase.baseline_group
    lines.append(
        f"退货客户 {len(returned.customers)} 人，复购 {len(returned.repurchased)}，"
        f"删失 {len(returned.censored)}，复购率 {_pct(returned.rate)}"
    )
    lines.append(
        f"未退货客户 {len(baseline.customers)} 人，复购 {len(baseline.repurchased)}，"
        f"删失 {len(baseline.censored)}，复购率 {_pct(baseline.rate)}"
    )
    lines.append(f"（{report.repurchase.note}）")
    lines.append("")
    lines.append("== 处置建议 ==")
    for rec in report.replan.recommendations:
        target = rec.return_id or rec.sku or ""
        lines.append(f"[{rec.rec_id}] {target} -> {rec.action} ×{rec.quantity}")
        for reason in rec.rationale:
            lines.append(f"    依据：{reason}")
        for constraint in rec.constraints:
            lines.append(f"    约束[{constraint.kind}] {constraint.ref}: {constraint.detail}")
        for alert in rec.alerts:
            lines.append(f"    告警：{alert}")
    lines.append(
        f"维持 {len(report.replan.kept)} 项，取代 {len(report.replan.superseded)} 项，"
        f"已执行不可改写 {len(report.replan.immutable)} 项"
    )
    for alert in report.replan.alerts:
        lines.append(f"告警：{alert}")
    lines.append("")
    lines.append("== 不可直接比较的记录 ==")
    for item in report.incomparable_records:
        lines.append(f"- {item}")
    return "\n".join(lines)
