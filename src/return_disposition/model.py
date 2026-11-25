"""原始记录的类型化视图。

只做空缺安全的类型转换（日期、布尔），不静默填充缺失值；
每条记录保留 raw 以便回查原始事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .catalog import CatalogVersion, parse_catalog
from .dictionary import ReasonDictionary


def parse_day(value: Any) -> date | None:
    if isinstance(value, str) and value:
        return date.fromisoformat(value)
    return None


@dataclass(frozen=True)
class Order:
    order_id: str
    channel: str | None
    sku: str | None
    quantity: int | None
    ordered_at: date | None
    customer_id: str | None
    promo: bool
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Order":
        return cls(
            order_id=raw["order_id"],
            channel=raw.get("channel"),
            sku=raw.get("sku"),
            quantity=raw.get("quantity"),
            ordered_at=parse_day(raw.get("ordered_at")),
            customer_id=raw.get("customer_id"),
            promo=bool(raw.get("promo", False)),
            raw=raw,
        )


@dataclass(frozen=True)
class ReturnRequest:
    return_id: str
    order_id: str | None
    quantity: int | None
    reason_code: str | None
    free_text: str | None
    status: str | None
    requested_at: date | None
    supersedes: str | None
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "ReturnRequest":
        return cls(
            return_id=raw["return_id"],
            order_id=raw.get("order_id"),
            quantity=raw.get("quantity"),
            reason_code=raw.get("reason_code"),
            free_text=raw.get("free_text"),
            status=raw.get("status"),
            requested_at=parse_day(raw.get("requested_at")),
            supersedes=raw.get("supersedes"),
            raw=raw,
        )


@dataclass(frozen=True)
class Inspection:
    inspection_id: str
    return_id: str | None
    received_at: date | None
    seal: str | None
    functional: str | None
    cosmetic: str | None
    decision: str | None
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Inspection":
        return cls(
            inspection_id=raw["inspection_id"],
            return_id=raw.get("return_id"),
            received_at=parse_day(raw.get("received_at")),
            seal=raw.get("seal"),
            functional=raw.get("functional"),
            cosmetic=raw.get("cosmetic"),
            decision=raw.get("decision"),
            raw=raw,
        )


@dataclass(frozen=True)
class Commitment:
    commitment_id: str
    kind: str | None  # exchange | replenish
    return_id: str | None
    sku: str | None
    quantity: int | None
    due_at: date | None
    status: str | None  # proposed | confirmed | shipped | cancelled
    shipped_at: date | None
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Commitment":
        return cls(
            commitment_id=raw["commitment_id"],
            kind=raw.get("kind"),
            return_id=raw.get("return_id"),
            sku=raw.get("sku"),
            quantity=raw.get("quantity"),
            due_at=parse_day(raw.get("due_at")),
            status=raw.get("status"),
            shipped_at=parse_day(raw.get("shipped_at")),
            raw=raw,
        )


@dataclass(frozen=True)
class Refund:
    refund_id: str
    return_id: str | None
    amount: float | None
    status: str | None
    executed_at: date | None
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Refund":
        return cls(
            refund_id=raw["refund_id"],
            return_id=raw.get("return_id"),
            amount=raw.get("amount"),
            status=raw.get("status"),
            executed_at=parse_day(raw.get("executed_at")),
            raw=raw,
        )


@dataclass(frozen=True)
class Disposition:
    disposition_id: str
    return_id: str | None
    action: str | None  # resell | refurbish | isolate | scrap
    quantity: int | None
    status: str | None  # planned | executed | cancelled
    planned_at: date | None
    executed_at: date | None
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Disposition":
        return cls(
            disposition_id=raw["disposition_id"],
            return_id=raw.get("return_id"),
            action=raw.get("action"),
            quantity=raw.get("quantity"),
            status=raw.get("status"),
            planned_at=parse_day(raw.get("planned_at")),
            executed_at=parse_day(raw.get("executed_at")),
            raw=raw,
        )


@dataclass(frozen=True)
class Zone:
    capacity: int
    used: int


@dataclass(frozen=True)
class Resources:
    as_of: date | None
    zones: dict[str, Zone]
    qc_per_day: int | None
    qc_backlog: int
    resale_stock: dict[str, int]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "Resources":
        zones = {
            name: Zone(capacity=int(z.get("capacity", 0)), used=int(z.get("used", 0)))
            for name, z in raw.get("zones", {}).items()
        }
        qc = raw.get("qc_capability", {})
        return cls(
            as_of=parse_day(raw.get("as_of")),
            zones=zones,
            qc_per_day=qc.get("per_day"),
            qc_backlog=int(qc.get("backlog", 0)),
            resale_stock=dict(raw.get("resale_stock", {})),
        )


@dataclass
class Dataset:
    """一次资料包的全部记录与常用索引。"""

    orders: list[Order]
    returns: list[ReturnRequest]
    inspections: list[Inspection]
    catalog: list[CatalogVersion]
    commitments: list[Commitment]
    refunds: list[Refund]
    dispositions: list[Disposition]
    resources: Resources
    dictionary: ReasonDictionary

    @classmethod
    def from_bundle(cls, bundle: dict[str, Any]) -> "Dataset":
        return cls(
            orders=[Order.from_raw(r) for r in bundle["orders.json"]],
            returns=[ReturnRequest.from_raw(r) for r in bundle["returns.json"]],
            inspections=[Inspection.from_raw(r) for r in bundle["inspections.json"]],
            catalog=parse_catalog(bundle["catalog.json"]),
            commitments=[Commitment.from_raw(r) for r in bundle["commitments.json"]],
            refunds=[Refund.from_raw(r) for r in bundle["refunds.json"]],
            dispositions=[Disposition.from_raw(r) for r in bundle["dispositions.json"]],
            resources=Resources.from_raw(bundle["resources.json"]),
            dictionary=ReasonDictionary.from_raw(bundle["reason_dictionary.json"]),
        )

    @property
    def order_by_id(self) -> dict[str, Order]:
        return {o.order_id: o for o in self.orders}

    @property
    def return_by_id(self) -> dict[str, ReturnRequest]:
        return {r.return_id: r for r in self.returns}

    def returns_of_order(self, order_id: str) -> list[ReturnRequest]:
        return [r for r in self.returns if r.order_id == order_id]

    def inspections_of_return(self, return_id: str) -> list[Inspection]:
        found = [i for i in self.inspections if i.return_id == return_id]
        return sorted(found, key=lambda i: (i.received_at is None, i.received_at))

    def latest_inspection(self, return_id: str) -> Inspection | None:
        found = self.inspections_of_return(return_id)
        return found[-1] if found else None

    def dispositions_of_return(self, return_id: str) -> list[Disposition]:
        return [d for d in self.dispositions if d.return_id == return_id]
