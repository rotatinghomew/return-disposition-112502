"""商品版本、批次与 SKU 谱系（替换 / 合并）。

每个 SKU 可有多个版本（规格换版），批次标识取 ``sku@version``；
SKU 合并通过目录条目上的 ``merged_from`` 记录，谱系键让
被合并的旧 SKU 能归入合并后的 SKU 继续比较。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


def _parse_day(value: Any) -> date | None:
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None


@dataclass(frozen=True)
class CatalogVersion:
    sku: str
    version: str
    starts_at: date | None
    ends_at: date | None
    dimensions: str | None
    merged_from: tuple[str, ...]
    raw: dict[str, Any]

    def covers(self, day: date | None) -> bool:
        if day is None:
            return False
        if self.starts_at is not None and day < self.starts_at:
            return False
        if self.ends_at is not None and day >= self.ends_at:
            return False
        return True

    @property
    def batch(self) -> str:
        return f"{self.sku}@{self.version}"


@dataclass(frozen=True)
class ProductDefinition:
    """某 SKU 在某个时点生效的商品定义；取不到时显式标记。"""

    sku: str
    entry: CatalogVersion | None
    batch: str
    flags: tuple[str, ...]


def parse_catalog(raw_list: list[dict[str, Any]]) -> list[CatalogVersion]:
    return [
        CatalogVersion(
            sku=item["sku"],
            version=item["version"],
            starts_at=_parse_day(item.get("starts_at")),
            ends_at=_parse_day(item.get("ends_at")),
            dimensions=item.get("dimensions"),
            merged_from=tuple(item.get("merged_from", ())),
            raw=item,
        )
        for item in raw_list
    ]


def definition_at(catalog: list[CatalogVersion], sku: str, day: date | None) -> ProductDefinition:
    """订单时点生效的商品定义；无版本覆盖时保留事实并标记。"""
    flags: list[str] = []
    for entry in catalog:
        if entry.sku == sku and entry.covers(day):
            return ProductDefinition(sku=sku, entry=entry, batch=entry.batch, flags=())
    known = any(entry.sku == sku for entry in catalog)
    if not known:
        flags.append(f"SKU {sku} 不在目录中")
    else:
        flags.append(f"SKU {sku} 在 {day or '未知日期'} 无生效版本，不能套用现行规格")
    return ProductDefinition(sku=sku, entry=None, batch=f"{sku}@unknown", flags=tuple(flags))


def merged_successors(catalog: list[CatalogVersion]) -> dict[str, str]:
    """被合并 SKU -> 合并后 SKU。"""
    successors: dict[str, str] = {}
    for entry in catalog:
        for source in entry.merged_from:
            successors[source] = entry.sku
    return successors


def lineage_key(catalog: list[CatalogVersion], sku: str) -> str:
    """谱系键：被合并的旧 SKU 归入合并后 SKU，其余为自身。"""
    return merged_successors(catalog).get(sku, sku)


def lineage_sources(catalog: list[CatalogVersion], sku: str) -> tuple[str, ...]:
    """某 SKU 的合并来源（含自身），用于追溯合并前的商品定义。"""
    sources: list[str] = [sku]
    for entry in catalog:
        if entry.sku == sku:
            for source in entry.merged_from:
                if source not in sources:
                    sources.append(source)
    return tuple(sources)
