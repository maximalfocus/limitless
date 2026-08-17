"""The shared store: the tenant's own view of its state, and the money reservation.

The reservation is the heart of secure control C, so it is worth being precise about why it is
shaped the way it is.

**It is charged in the unit of the resource.** The thing that runs out is provider lookups, so
lookups are what the allowance counts. A limit counted in *requests* would be a limit on the wrong
unit entirely — one request can name fifty thousand lookups.

**It happens before the work does.** Money is held the moment work is admitted and converted to a
charge only once the work is performed, so a burst of concurrent requests cannot each look at a
balance that only one of them can actually have.

**It is one conditional write, decided on affected row count.** The predicate that decides whether
there is room lives in the ``WHERE`` clause of the same statement that takes the room. There is no
interval between deciding and taking for a concurrent request to occupy. This technique is borrowed
from a neighbouring demonstration as a known-correct tool; here it is a means, not the subject.

**Both budgets are checked in one transaction.** The tenant's partition and the whole fictional
company's cap are separate rows, and either can refuse. Failing closed on either is what makes it
impossible for one tenant to reach another tenant's money or the global remainder.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from . import fixtures
from .db import Conn
from .models import JobStatus, JobView, RecordPage, Reservation, StoredRecord, UsageView

SQL_RESERVE_TENANT_ALLOWANCE: Final = """
UPDATE tenant_allowances
   SET reserved_cents = reserved_cents + %(cents)s
 WHERE tenant_id = %(tenant_id)s
   AND committed_cents + reserved_cents + %(cents)s <= allowance_cents
RETURNING reserved_cents, committed_cents, allowance_cents
"""

SQL_RESERVE_SPEND_PERIOD: Final = """
UPDATE spend_periods
   SET reserved_cents = reserved_cents + %(cents)s
 WHERE period_id = %(period_id)s
   AND committed_cents + reserved_cents + %(cents)s <= cap_cents
RETURNING reserved_cents, committed_cents, cap_cents
"""

SQL_SETTLE_TENANT_ALLOWANCE: Final = """
UPDATE tenant_allowances
   SET reserved_cents    = reserved_cents - %(reserved_cents)s,
       committed_cents   = committed_cents + %(charged_cents)s,
       lookups_performed = lookups_performed + %(lookups)s
 WHERE tenant_id = %(tenant_id)s
"""

SQL_SETTLE_SPEND_PERIOD: Final = """
UPDATE spend_periods
   SET reserved_cents  = reserved_cents - %(reserved_cents)s,
       committed_cents = committed_cents + %(charged_cents)s
 WHERE period_id = %(period_id)s
"""

SQL_USAGE_VIEW: Final = """
SELECT tenant_id, period_id, lookups_performed, committed_cents
  FROM tenant_allowances
 WHERE tenant_id = %(tenant_id)s
"""

SQL_RECORD_PAGE: Final = """
SELECT record_id, company_name, registry_number, enriched_at
  FROM records
 WHERE tenant_id = %(tenant_id)s
 ORDER BY record_id
 LIMIT %(page_size)s
"""

SQL_JOB_VIEW: Final = """
SELECT job_id, tenant_id, status, records_admitted, cents_charged
  FROM jobs
 WHERE job_id = %(job_id)s AND tenant_id = %(tenant_id)s
"""

SQL_INSERT_JOB: Final = """
INSERT INTO jobs (job_id, tenant_id, status, records_admitted, cents_charged, served_by)
VALUES (%(job_id)s, %(tenant_id)s, %(status)s, %(records_admitted)s, %(cents_charged)s,
        %(served_by)s)
"""

SQL_UPSERT_RECORD: Final = """
INSERT INTO records (record_id, tenant_id, company_name, registry_number, enriched_at)
VALUES (%(record_id)s, %(tenant_id)s, %(company_name)s, %(registry_number)s, %(enriched_at)s)
ON CONFLICT (record_id) DO UPDATE
   SET company_name    = EXCLUDED.company_name,
       registry_number = EXCLUDED.registry_number,
       enriched_at     = EXCLUDED.enriched_at
"""


class AllowanceExhaustedError(Exception):
    """Raised inside the reservation transaction so it unwinds; never reaches the client as-is."""


async def reserve(conn: Conn, *, tenant_id: str, lookups: int, price_cents: int) -> Reservation:
    """Hold the money for ``lookups`` before any of that work is performed.

    Raises :class:`AllowanceExhaustedError` when either the tenant's own partition or the whole
    fictional company's cap has no room. Both are decided on affected row count, inside one
    transaction, so a partial reservation cannot survive.
    """
    if lookups < 0:
        raise ValueError(f"cannot reserve {lookups} lookups")
    cents = lookups * price_cents
    async with conn.transaction():
        tenant_cursor = await conn.execute(
            SQL_RESERVE_TENANT_ALLOWANCE, {"tenant_id": tenant_id, "cents": cents}
        )
        if tenant_cursor.rowcount != 1:
            # Zero rows affected: there was no room, and that decision is already final.
            raise AllowanceExhaustedError
        period_cursor = await conn.execute(
            SQL_RESERVE_SPEND_PERIOD,
            {"period_id": fixtures.SPEND_PERIOD_ID, "cents": cents},
        )
        if period_cursor.rowcount != 1:
            # The tenant had room but the company does not. Unwinding takes the tenant-side hold
            # with it, so no money stays reserved for work that will never be admitted.
            raise AllowanceExhaustedError
    return Reservation(tenant_id=tenant_id, cents=cents, lookups=lookups)


async def settle(
    conn: Conn, reservation: Reservation, *, lookups_performed: int, cents_charged: int
) -> None:
    """Convert a reservation into a charge, releasing whatever was held and not spent.

    Called with ``lookups_performed=0`` and ``cents_charged=0`` this is a pure release, which is
    what
    a request that was admitted and then failed must do: money held for work that never happened
    belongs back in the allowance.
    """
    async with conn.transaction():
        await conn.execute(
            SQL_SETTLE_TENANT_ALLOWANCE,
            {
                "tenant_id": reservation.tenant_id,
                "reserved_cents": reservation.cents,
                "charged_cents": cents_charged,
                "lookups": lookups_performed,
            },
        )
        await conn.execute(
            SQL_SETTLE_SPEND_PERIOD,
            {
                "period_id": fixtures.SPEND_PERIOD_ID,
                "reserved_cents": reservation.cents,
                "charged_cents": cents_charged,
            },
        )


async def release(conn: Conn, reservation: Reservation) -> None:
    """Give back money held for work that was never performed."""
    await settle(conn, reservation, lookups_performed=0, cents_charged=0)


async def read_usage(conn: Conn, tenant_id: str) -> UsageView | None:
    cursor = await conn.execute(SQL_USAGE_VIEW, {"tenant_id": tenant_id})
    row = await cursor.fetchone()
    if row is None:
        return None
    return UsageView(
        tenant_id=str(row["tenant_id"]),
        period_id=str(row["period_id"]),
        lookups_performed=int(row["lookups_performed"]),
        cents_charged=int(row["committed_cents"]),
        currency=fixtures.CURRENCY_LABEL,
    )


async def read_records(conn: Conn, *, tenant_id: str, page_size: int) -> RecordPage:
    cursor = await conn.execute(SQL_RECORD_PAGE, {"tenant_id": tenant_id, "page_size": page_size})
    rows = await cursor.fetchall()
    return RecordPage(
        tenant_id=tenant_id,
        page_size=page_size,
        records=[
            StoredRecord(
                record_id=str(row["record_id"]),
                company_name=str(row["company_name"]),
                registry_number=(
                    None if row["registry_number"] is None else str(row["registry_number"])
                ),
                enriched_at=row["enriched_at"],
            )
            for row in rows
        ],
    )


async def read_job(conn: Conn, *, job_id: UUID, tenant_id: str) -> JobView | None:
    cursor = await conn.execute(SQL_JOB_VIEW, {"job_id": job_id, "tenant_id": tenant_id})
    row = await cursor.fetchone()
    if row is None:
        return None
    return JobView(
        job_id=row["job_id"],
        tenant_id=str(row["tenant_id"]),
        status=JobStatus(str(row["status"])),
        records_admitted=int(row["records_admitted"]),
        cents_charged=int(row["cents_charged"]),
    )


async def insert_job(
    conn: Conn,
    *,
    job_id: UUID,
    tenant_id: str,
    records_admitted: int,
    cents_charged: int,
    served_by: str,
) -> None:
    await conn.execute(
        SQL_INSERT_JOB,
        {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "status": JobStatus.COMPLETED.value,
            "records_admitted": records_admitted,
            "cents_charged": cents_charged,
            "served_by": served_by,
        },
    )


async def upsert_records(
    conn: Conn,
    *,
    tenant_id: str,
    entries: list[tuple[str, str, str]],
    enriched_at: datetime,
) -> None:
    """Store enriched records. ``entries`` is ``(record_id, company_name, registry_number)``."""
    if not entries:
        return
    await conn.cursor().executemany(
        SQL_UPSERT_RECORD,
        [
            {
                "record_id": record_id,
                "tenant_id": tenant_id,
                "company_name": company_name,
                "registry_number": registry_number,
                "enriched_at": enriched_at,
            }
            for record_id, company_name, registry_number in entries
        ],
    )
