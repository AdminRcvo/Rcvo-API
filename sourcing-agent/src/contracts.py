from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass(frozen=True)
class RawItem:
    id: int
    batch_id: int | None
    raw_batch_uuid: str | None
    source_key: str | None
    source_record_id: str | None
    source_row_number: int | None
    captured_at: str | None
    payload: Any

@dataclass
class CandidateLookup:
    contact_rcvo_id: str | None = None
    organization_rcvo_id: str | None = None
    site_rcvo_id: str | None = None
    methods: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

class RawGateway(Protocol):
    def health(self) -> dict[str, Any]: ...
    def claim(self, worker_id: str, limit: int, lease_seconds: int) -> list[RawItem]: ...
    def release(self, worker_id: str, record_id: int) -> None: ...
    def ack(self, worker_id: str, record_id: int) -> None: ...
    def fail(self, worker_id: str, record_id: int, error: str) -> str: ...
    def reject(self, worker_id: str, record_id: int, reason: str) -> None: ...

class ReferenceGateway(Protocol):
    def health(self) -> dict[str, Any]: ...
    def lookup(self, normalized: dict[str, Any]) -> CandidateLookup: ...
    def promote_many(self, promotions: list[dict[str, Any]]) -> dict[str, Any]: ...
