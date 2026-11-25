"""原因字典的版本化解释与跨版本可比性。

客服选项（标准原因代码）按退货请求时点生效的字典版本解释；
字典换版后，历史记录仍按当时口径解释，跨版本比较只能走映射表，
拆分（一对多）的代码族只能做族级比较，且必须显式标记。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

ONE_TO_ONE = "one_to_one"
SPLIT = "split"


def _parse_day(value: Any) -> date | None:
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None


@dataclass(frozen=True)
class DictEntry:
    code: str
    label: str
    family: str


@dataclass(frozen=True)
class DictVersion:
    version: str
    starts_at: date | None
    ends_at: date | None
    entries: dict[str, DictEntry]

    def covers(self, day: date | None) -> bool:
        if day is None:
            return False
        if self.starts_at is not None and day < self.starts_at:
            return False
        if self.ends_at is not None and day >= self.ends_at:
            return False
        return True


@dataclass(frozen=True)
class MappingLink:
    from_version: str
    to_version: str
    from_code: str
    to_codes: tuple[str, ...]
    kind: str  # ONE_TO_ONE | SPLIT


@dataclass(frozen=True)
class Interpretation:
    """一条原因代码在特定请求时点下的解释结果。"""

    code: str
    label: str
    family: str | None
    dict_version: str | None  # 实际用于解释的字典版本
    expected_version: str | None  # 请求时点生效的字典版本
    fallback: bool  # 代码不属于生效版本，回退到其它版本解释
    note: str | None


@dataclass(frozen=True)
class ReasonDictionary:
    versions: tuple[DictVersion, ...]
    links: tuple[MappingLink, ...]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "ReasonDictionary":
        versions: list[DictVersion] = []
        for item in raw.get("versions", []):
            entries = {
                code: DictEntry(code=code, label=entry.get("label", code), family=entry.get("family", code))
                for code, entry in item.get("entries", {}).items()
            }
            versions.append(
                DictVersion(
                    version=item["version"],
                    starts_at=_parse_day(item.get("starts_at")),
                    ends_at=_parse_day(item.get("ends_at")),
                    entries=entries,
                )
            )
        links: list[MappingLink] = []
        for mapping in raw.get("mappings", []):
            for link in mapping.get("links", []):
                links.append(
                    MappingLink(
                        from_version=mapping["from_version"],
                        to_version=mapping["to_version"],
                        from_code=link["from"],
                        to_codes=tuple(link.get("to", ())),
                        kind=link.get("kind", SPLIT),
                    )
                )
        return cls(versions=tuple(versions), links=tuple(links))

    def version_at(self, day: date | None) -> DictVersion | None:
        for version in self.versions:
            if version.covers(day):
                return version
        return None

    def interpret(self, code: str | None, day: date | None) -> Interpretation | None:
        """按请求时点解释原因代码；代码不属于生效版本时回退并标记。"""
        if not code:
            return None
        expected = self.version_at(day)
        expected_name = expected.version if expected else None
        if expected is not None and code in expected.entries:
            entry = expected.entries[code]
            return Interpretation(code, entry.label, entry.family, expected.version, expected_name, False, None)
        for version in self.versions:
            if code in version.entries:
                entry = version.entries[code]
                note = (
                    f"原因代码 {code} 不属于请求时点生效的字典 "
                    f"{expected_name or '（无生效版本）'}，按 {version.version} 口径解释，"
                    "与现行版本原生记录不可直接比较"
                )
                return Interpretation(code, entry.label, entry.family, version.version, expected_name, True, note)
        return Interpretation(
            code, code, None, None, expected_name, False, f"原因代码 {code} 不在任何字典版本中，无法解释"
        )

    def link(self, from_version: str, to_version: str, from_code: str) -> MappingLink | None:
        for item in self.links:
            if (
                item.from_version == from_version
                and item.to_version == to_version
                and item.from_code == from_code
            ):
                return item
        return None

    def comparability(self, from_version: str, to_version: str, from_code: str) -> str:
        """两个版本间某代码的可比性：same / one_to_one / family_only / unknown。"""
        if from_version == to_version:
            return "same"
        link = self.link(from_version, to_version, from_code)
        if link is None:
            return "unknown"
        return ONE_TO_ONE if link.kind == ONE_TO_ONE else "family_only"
