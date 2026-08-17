"""Deterministic, wholly fictional fixtures.

Every tenant, company, registry number, price, budget, and token in this module is invented for the
demonstration. Nothing here refers to a real business, person, data provider, or credential, and the
currency is labelled fictional wherever an amount appears. Values are stable across runs and
machines so two runs of the same scenario are comparable, and every run recreates them from scratch
so no run inherits another run's state.

The three quantities at the centre of the demonstration are here rather than scattered through the
application, because they are what make work convertible into money:

* ``LOOKUP_PRICE_CENTS`` — what one metered lookup costs at the fictional provider;
* ``GLOBAL_SPEND_CAP_CENTS`` — the whole fictional company's monthly budget;
* ``TENANT_ALLOWANCE_CENTS`` — the partition of that budget one tenant may spend.

The partition is the control. Three tenants at 40 000 fictional cents each cannot between them reach
the 250 000-cent global cap, which is exactly why one tenant exhausting its own share leaves the
others untouched.
"""

from __future__ import annotations

import gzip
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Final

COMPANY_NAME: Final = "Halyard Insights"
"""The fictional business-to-business enrichment service under study."""

PROVIDER_NAME: Final = "Coastwise Registry"
"""The fictional metered company-records provider, operated here as an in-network fixture."""

CURRENCY_LABEL: Final = "fictional cents"

LOOKUP_PRICE_CENTS: Final = 4
"""What Coastwise charges Halyard for one enrichment lookup. Fictional money, fictional provider."""

GLOBAL_SPEND_CAP_CENTS: Final = 250_000
"""Halyard's whole fictional monthly budget."""

TENANT_ALLOWANCE_CENTS: Final = 40_000
"""One tenant's partition of that budget — 10 000 lookups at the fictional price."""

SPEND_PERIOD_ID: Final = "HALYARD-2026-03"
"""The fictional billing period the cap belongs to.

A constant, so no result depends on the clock.
"""

ATTACKER_TENANT_ID: Final = "TEN-ORCHID"
"""An ordinary paying customer in good standing. Nothing it does is unauthenticated."""

BYSTANDER_TENANT_IDS: Final = ("TEN-BASIL", "TEN-WREN")
"""Customers who did nothing and, in the vulnerable variant, lose anyway."""

EXPIRED_TENANT_ID: Final = "TEN-EXPIRED"
"""Owner of the deliberately expired demonstration credential."""


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    display_name: str


TENANTS: Final[tuple[Tenant, ...]] = (
    Tenant(ATTACKER_TENANT_ID, "Orchid Freight Analytics"),
    Tenant(BYSTANDER_TENANT_IDS[0], "Basil Provisioning Group"),
    Tenant(BYSTANDER_TENANT_IDS[1], "Wren Maritime Logistics"),
    Tenant(EXPIRED_TENANT_ID, "Thistle Retail (expired demo session)"),
)

BILLABLE_TENANT_IDS: Final = tuple(
    tenant.tenant_id for tenant in TENANTS if tenant.tenant_id != EXPIRED_TENANT_ID
)
"""The tenants that hold a spending allowance. The expired-credential tenant never spends."""

RECORDS_PER_TENANT: Final = 400
"""Enough stored records that a page size is a meaningful quantity to bound."""

# Invented company-name parts, combined deterministically so record 0042 is the same fictional
# company on every machine and in every run.
_PREFIXES: Final = (
    "Alder", "Bramble", "Cinder", "Dovetail", "Elmgrove", "Fennwick",
    "Gallowmere", "Hearthstone", "Ironvale", "Juniper", "Kelpwood", "Larkspur",
)  # fmt: skip
_SUFFIXES: Final = (
    "Provisioning", "Cartage", "Shipworks", "Textiles", "Foundry", "Chandlery",
    "Coldstore", "Ropeworks", "Salvage", "Quarry",
)  # fmt: skip


def record_id(tenant_id: str, index: int) -> str:
    """The fictional record identifier for a one-based index within a tenant."""
    return f"{tenant_id}-REC-{index:05d}"


def company_name(index: int) -> str:
    """A conspicuously fictional company name, derived deterministically from the index."""
    prefix = _PREFIXES[(index - 1) % len(_PREFIXES)]
    suffix = _SUFFIXES[((index - 1) // len(_PREFIXES)) % len(_SUFFIXES)]
    return f"{prefix} {suffix}"


def registry_number(index: int) -> str:
    """A fictional Coastwise Registry number. Not an identifier scheme any real registry uses."""
    return f"CR-{index % 10:01d}{(index * 7919) % 1_000_000:06d}"


@dataclass(frozen=True, slots=True)
class SeedRecord:
    record_id: str
    tenant_id: str
    company_name: str


def seed_records() -> tuple[SeedRecord, ...]:
    """Every stored record every tenant starts a run with."""
    return tuple(
        SeedRecord(record_id(tenant_id, index), tenant_id, company_name(index))
        for tenant_id in BILLABLE_TENANT_IDS
        for index in range(1, RECORDS_PER_TENANT + 1)
    )


def enrichment_line(index: int) -> str:
    """One line of the fictional NDJSON an import bundle carries."""
    return json.dumps(
        {"company_name": company_name(index), "registry_number": registry_number(index)},
        sort_keys=True,
        separators=(",", ":"),
    )


def _bundle(lines: Iterator[str]) -> bytes:
    """Stream ``lines`` through one gzip compressor and return the single-layer bundle.

    Streaming matters: producing a bundle whose *decompressed* form is large never materializes that
    form in memory, so peak cost here is one line plus the compressed output. The result is
    single-layer — a gzip stream of NDJSON text, never an archive containing another archive.
    """
    buffer = io.BytesIO()
    # mtime=0 keeps the bundle byte-identical across runs, so a fixture is a constant.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressor:
        for line in lines:
            compressor.write(line.encode())
    return buffer.getvalue()


def ndjson_bundle(record_count: int) -> bytes:
    """A bundle of ``record_count`` *distinct* fictional records — what a real import looks like.

    Distinct records compress at an ordinary ratio (about ten to one), which is the point: this is
    the shape a legitimate customer submits, and it sits comfortably inside every secure ceiling.
    """
    if record_count < 0:
        raise ValueError(f"cannot build a bundle of {record_count} records")
    return _bundle(f"{enrichment_line(index)}\n" for index in range(1, record_count + 1))


def repetitive_ndjson_bundle(record_count: int) -> bytes:
    """A bundle of ``record_count`` copies of one fictional record.

    Repetition is the whole mechanism: the same content that compresses at ten to one when it is
    varied compresses at roughly three hundred to one when it is not, so the *compressed* size a
    naive check measures is a number whose relationship to the real one the submitter chose. Still a
    single-layer gzip stream of ordinary NDJSON text — the ratio comes from repetition, not from
    nesting, and nothing here describes how to build a nested or self-referential archive.
    """
    if record_count < 0:
        raise ValueError(f"cannot build a bundle of {record_count} records")
    line = f"{enrichment_line(1)}\n"
    return _bundle(line for _ in range(record_count))


LEGITIMATE_IMPORT_RECORDS: Final = 300
"""A legitimate import: comfortably inside every bound, and real work the tenant pays for.

About 2 KB compressed and 20 KB decompressed, at a ratio near ten.
"""

OVER_EXPANDING_IMPORT_RECORDS: Final = 150_000
"""An import whose *compressed* size is unremarkable and whose expansion is not.

About 36 KB of gzip becoming about 10 MB of NDJSON, at a documented single-layer ratio near 294:
far past both secure ceilings, far inside the import body bound that a naive check would rely on,
and small enough that the demonstration's own worst case stays well within the application
container's declared memory limit.
"""
