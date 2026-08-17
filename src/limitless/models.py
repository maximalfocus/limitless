"""Client-visible payloads and internal outcome types.

These payloads are shared rather than per-variant on purpose. A later, deliberately unbounded
application must return **identical** bodies for the same legitimate request, so that the only
difference between the two is how much work a caller is permitted to name — never how the service
answers when the request is a reasonable one.

One omission is deliberate. ``UsageView`` reports what a tenant has *spent* and never what it has
*left*. Spend is what makes the demonstration readable — a reader reconciles it against the
provider's own ledger — while a remaining balance would hand a caller a countdown to the exact
request that gets refused, which is the same oracle the refusal itself is careful not to be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    COMPLETED = "completed"


class RecordInput(BaseModel):
    """One record a caller submits for enrichment. Each one is a metered provider lookup."""

    company_name: str = Field(min_length=1, max_length=200)


class EnrichRequest(BaseModel):
    records: list[RecordInput]


class EnrichedRecord(BaseModel):
    record_id: str
    company_name: str
    registry_number: str


class EnrichResponse(BaseModel):
    tenant_id: str
    records_admitted: int
    lookups_performed: int
    cents_charged: int
    results: list[EnrichedRecord]


class StoredRecord(BaseModel):
    record_id: str
    company_name: str
    registry_number: str | None
    enriched_at: datetime | None


class RecordPage(BaseModel):
    tenant_id: str
    page_size: int
    """The page size actually served. The secure application never serves more than its bound."""

    records: list[StoredRecord]


class ImportResponse(BaseModel):
    job_id: UUID
    tenant_id: str
    status: JobStatus
    records_admitted: int
    cents_charged: int


class JobView(BaseModel):
    """The cheap endpoint's answer. Touches no provider, no allowance, and no in-flight slot."""

    job_id: UUID
    tenant_id: str
    status: JobStatus
    records_admitted: int
    cents_charged: int


class UsageView(BaseModel):
    """A tenant's own spend, in the two units that make the demonstration reconcilable."""

    tenant_id: str
    period_id: str
    lookups_performed: int
    cents_charged: int
    currency: str


class HealthResponse(BaseModel):
    status: str
    replica: str
    variant: str
    """``secure`` or ``vulnerable`` — so no run can be confused about what it was driving."""


class LookupItem(BaseModel):
    """One lookup, as the application asks the provider fixture for it."""

    company_name: str = Field(min_length=1, max_length=200)


class LookupRequest(BaseModel):
    tenant_id: str
    items: list[LookupItem]


class LookupResult(BaseModel):
    company_name: str
    registry_number: str


class LookupResponse(BaseModel):
    """What the provider charged, decided and reported by the provider itself."""

    lookups: int
    cents_charged: int
    results: list[LookupResult]


class TenantLedgerEntry(BaseModel):
    tenant_id: str
    lookups: int
    cents: int


class LedgerView(BaseModel):
    """The provider's own bill — the impact readout this demonstration is built around."""

    provider: str
    currency: str
    price_cents_per_lookup: int
    total_lookups: int
    total_cents: int
    per_tenant: list[TenantLedgerEntry]


class ProviderControlView(BaseModel):
    """The provider fixture's instrumentation state.

    Both controls change only *when* the provider answers, never whether the application under study
    bounded its own work.
    """

    slow_mode: bool
    held: bool
    slow_delay_seconds: float


@dataclass(frozen=True, slots=True)
class Reservation:
    """Money held against a tenant's allowance for work that has been admitted but not performed."""

    tenant_id: str
    cents: int
    lookups: int
