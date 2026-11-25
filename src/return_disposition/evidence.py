"""证据分层与归因。

自由文本、标准原因（客服选项）、到货质检结论是三层不同证据：
- 三层分别记录，不互相覆盖；
- 缺少到货检验是「未知」，不能记为「无缺陷」；
- 外观判损坏不能覆盖尚未检查的功能项；
- 自由文本只做启发式族提示，永远标记为未核实。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .dictionary import ReasonDictionary
from .model import Inspection, ReturnRequest

OK = "ok"
ISSUE = "issue"
UNKNOWN = "unknown"

_FUNCTIONAL = {"ok": OK, "fail": ISSUE}
_COSMETIC = {"ok": OK, "damaged": ISSUE}

# 自由文本 -> 原因族的启发式提示（仅提示，不等于标准原因）。
FREE_TEXT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("size", ("太大", "太小", "尺寸", "偏大", "偏小", "放不下", "不合身")),
    ("description", ("颜色", "色差", "描述", "页面", "不符")),
    ("damage", ("破损", "损坏", "摔", "裂", "碎", "坏")),
)


def tri_functional(raw: str | None) -> str:
    return _FUNCTIONAL.get(raw, UNKNOWN)


def tri_cosmetic(raw: str | None) -> str:
    return _COSMETIC.get(raw, UNKNOWN)


def hint_family_from_text(text: str | None) -> str | None:
    if not text:
        return None
    for family, keywords in FREE_TEXT_HINTS:
        if any(word in text for word in keywords):
            return family
    return None


@dataclass(frozen=True)
class InspectionView:
    inspection_id: str
    received_at: date | None
    seal: str | None
    functional: str  # 三态：ok / issue / unknown
    cosmetic: str  # 三态：ok / issue / unknown
    decision: str | None


@dataclass(frozen=True)
class CsReason:
    code: str
    label: str
    family: str | None
    dict_version: str | None
    expected_version: str | None
    fallback: bool
    note: str | None


@dataclass(frozen=True)
class EvidenceLayers:
    """一条退货的三层证据，层与层不合并。"""

    return_id: str
    cs_reason: CsReason | None
    free_text: str | None
    free_text_family_hint: str | None
    inspection: InspectionView | None


@dataclass(frozen=True)
class Attribution:
    """归因结果：原因族 + 依据层 + 未决项。"""

    return_id: str
    family: str | None
    basis: tuple[str, ...]  # cs_reason / free_text_hint / inspection
    verified_by_inspection: bool
    functional: str
    cosmetic: str
    unattributable: bool
    notes: tuple[str, ...]


def build_evidence(
    request: ReturnRequest,
    inspection: Inspection | None,
    dictionary: ReasonDictionary,
) -> EvidenceLayers:
    cs_reason = None
    interpretation = dictionary.interpret(request.reason_code, request.requested_at)
    if interpretation is not None:
        cs_reason = CsReason(
            code=interpretation.code,
            label=interpretation.label,
            family=interpretation.family,
            dict_version=interpretation.dict_version,
            expected_version=interpretation.expected_version,
            fallback=interpretation.fallback,
            note=interpretation.note,
        )
    view = None
    if inspection is not None:
        view = InspectionView(
            inspection_id=inspection.inspection_id,
            received_at=inspection.received_at,
            seal=inspection.seal,
            functional=tri_functional(inspection.functional),
            cosmetic=tri_cosmetic(inspection.cosmetic),
            decision=inspection.decision,
        )
    return EvidenceLayers(
        return_id=request.return_id,
        cs_reason=cs_reason,
        free_text=request.free_text,
        free_text_family_hint=hint_family_from_text(request.free_text),
        inspection=view,
    )


def attribute(evidence: EvidenceLayers) -> Attribution:
    """按证据层归因；任何一层都不被另一层静默覆盖。"""
    basis: list[str] = []
    notes: list[str] = []
    family: str | None = None

    if evidence.cs_reason is not None and evidence.cs_reason.family:
        family = evidence.cs_reason.family
        basis.append("cs_reason")
        if evidence.cs_reason.fallback and evidence.cs_reason.note:
            notes.append(evidence.cs_reason.note)
    elif evidence.cs_reason is not None and evidence.cs_reason.note:
        notes.append(evidence.cs_reason.note)

    if family is None and evidence.free_text:
        if evidence.free_text_family_hint:
            family = evidence.free_text_family_hint
            basis.append("free_text_hint")
            notes.append("仅自由文本映射，未经标准原因确认")
        else:
            notes.append("自由文本无法映射到原因族")

    inspection = evidence.inspection
    functional = inspection.functional if inspection else UNKNOWN
    cosmetic = inspection.cosmetic if inspection else UNKNOWN

    if inspection is None:
        notes.append("缺少到货检验，不能记为无缺陷")
    else:
        if cosmetic == ISSUE and functional == UNKNOWN:
            notes.append("外观已判损坏，功能项尚未检查，不能视为功能正常")
        if family is None and (functional == ISSUE or cosmetic == ISSUE):
            family = "damage"
            basis.append("inspection")
            notes.append("仅质检证据归因，缺少客服原因")

    verified = False
    if family and inspection is not None and "cs_reason" in basis:
        if family == "damage" and (functional == ISSUE or cosmetic == ISSUE):
            verified = True
        elif family in ("size", "description"):
            notes.append("尺寸/描述类原因质检无法直接验证")

    return Attribution(
        return_id=evidence.return_id,
        family=family,
        basis=tuple(basis),
        verified_by_inspection=verified,
        functional=functional,
        cosmetic=cosmetic,
        unattributable=family is None,
        notes=tuple(notes),
    )
