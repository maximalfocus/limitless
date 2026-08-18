"""The four unbounded shapes, driven and counted.

These scenarios run against the **unbounded** application, and their expectations are the mirror
image of the secure ones. There, a violation is a failure. Here, a violation is the finding, and
its *absence* is the failure: a shape that does not reproduce has not been demonstrated, whatever
the code looks like.

Every assertion below is an assertion about counted accounting — records admitted, fictional cents
on the provider's own bill, occupied connections, requests answered — and never about how long
anything took. The deterministic mode is what makes that possible: the provider fixture's
hold/release control lets a configured number of calls occupy a configured number of slots, so the
consequence is observed at a known instant instead of being waited for and hoped about.
"""

from __future__ import annotations

import asyncio
import pathlib
from dataclasses import dataclass, field
from typing import Final

from .. import fixtures
from ..config import HarnessConfig
from ..httpclient import HalyardHTTP, RequestRecord
from ..seed import seed

NAMED_WORK_RECORDS: Final = 50_000
"""One request, fifty thousand metered lookups.

Four fifths of the whole fictional company's monthly budget.
"""

UNBOUNDED_PAGE_SIZE: Final = 1_000_000
"""About sixty bytes of query string."""

DRAIN_BATCH_RECORDS: Final = 2_000
DRAIN_MAX_REQUESTS: Final = 60
"""Ordinary-sized requests, repeated. Nothing here is a large request; the total is the problem."""


@dataclass(frozen=True, slots=True)
class ShapeOutcome:
    """What one unbounded shape did, and whether it did it.

    The fields below the first block exist so a scenario can be put in the comparison table beside a
    secure one and read across: same columns, same units, same question answered.
    """

    shape: str
    headline: str
    reproduced: bool
    detail: list[str]
    input_bytes: int
    cents: int

    bound_in_effect: str = "none — nothing here asks how much"
    kind: str = "shape"
    """``shape``, ``half-fix``, or ``control``.

    A control that establishes its boundary has not demonstrated an unbounded path; it has drawn a
    line around one. Calling both of those "unbounded" in the same column would be the table lying
    about what it is showing.
    """

    items_admitted: int = 0
    lookups: int = 0
    cheap_issued: int = 0
    cheap_answered: int = 0
    refusals: dict[str, int] = field(default_factory=dict)
    records: tuple[RequestRecord, ...] = ()
    """The per-request records underlying the row, for the comparison's verbose mode."""

    @property
    def cost_per_input_byte(self) -> float:
        return self.cents / self.input_bytes if self.input_bytes else 0.0


async def _fresh(client: HalyardHTTP, config: HarnessConfig) -> None:
    await seed(config.runner)
    await client.set_provider_control(slow_mode=False, held=False)


async def client_names_the_work(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Shape 1. The caller says how much work to do, and the application does exactly that."""
    await _fresh(client, config)
    tenant = fixtures.ATTACKER_TENANT_ID
    detail: list[str] = []

    names = [fixtures.company_name(i) for i in range(1, NAMED_WORK_RECORDS + 1)]
    batch = await client.enrich(names, sequence=1, tenant_id=tenant)
    ledger = await client.provider_ledger()
    share = ledger.total_cents / fixtures.GLOBAL_SPEND_CAP_CENTS
    detail.append(
        f"one POST /v1/enrich naming {NAMED_WORK_RECORDS:,} records was performed in full: "
        f"{ledger.total_cents:,} {fixtures.CURRENCY_LABEL} ({share:.2f}x the monthly cap) "
        f"from {batch.input_bytes:,} B of input"
    )
    detail.append(
        f"  cost per input byte: {ledger.total_cents / batch.input_bytes:.4f} "
        f"{fixtures.CURRENCY_LABEL}"
    )

    page = await client.list_records(sequence=2, tenant_id=tenant, page_size=UNBOUNDED_PAGE_SIZE)
    rows = len(page.body.get("records", [])) if page.body else 0
    query_bytes = len(f"?page_size={UNBOUNDED_PAGE_SIZE}".encode())
    detail.append(
        f"GET /v1/records?page_size={UNBOUNDED_PAGE_SIZE:,} — {query_bytes} B of query string — "
        f"serialized {rows:,} records"
    )
    detail.append(f"  rows per input byte: {rows / query_bytes:.1f}")

    reproduced = batch.succeeded and share >= 0.5 and rows > 10_000
    return ShapeOutcome(
        bound_in_effect="none — the caller says how much work to do",
        items_admitted=NAMED_WORK_RECORDS + rows,
        lookups=ledger.total_lookups,
        records=(batch, page),
        shape="the client names the work",
        headline=(
            f"{NAMED_WORK_RECORDS:,} records named by one request billed {share:.2f}x the whole "
            f"fictional monthly cap"
        ),
        reproduced=reproduced,
        detail=detail,
        input_bytes=batch.input_bytes + query_bytes,
        cents=ledger.total_cents,
    )


async def undivided_budget(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Shape 2. One pool, no partition — so one tenant's spending is everybody's problem."""
    await _fresh(client, config)
    attacker = fixtures.ATTACKER_TENANT_ID
    detail: list[str] = []
    input_bytes = 0
    issued = 0

    # Drain with ordinary requests until one is refused.
    sequence = 0
    for _ in range(DRAIN_MAX_REQUESTS):
        sequence += 1
        names = [
            fixtures.company_name(sequence * DRAIN_BATCH_RECORDS + i)
            for i in range(1, DRAIN_BATCH_RECORDS + 1)
        ]
        record = await client.enrich(names, sequence=sequence, tenant_id=attacker)
        input_bytes += record.input_bytes
        issued += 1
        if record.status_code == 429:
            break

    # A refused request leaves change behind, and change is enough to serve somebody. Take exactly
    # what is left — the amount is arithmetic, read from the provider's own bill — so that the pool
    # is empty rather than merely low when the tenants who spent nothing come to use it.
    spent = (await client.provider_ledger()).total_cents
    leftover = (fixtures.GLOBAL_SPEND_CAP_CENTS - spent) // fixtures.LOOKUP_PRICE_CENTS
    if leftover > 0:
        sequence += 1
        record = await client.enrich(
            [fixtures.company_name(900_000 + i) for i in range(1, leftover + 1)],
            sequence=sequence,
            tenant_id=attacker,
        )
        input_bytes += record.input_bytes
        issued += 1

    ledger = await client.provider_ledger()
    remaining = fixtures.GLOBAL_SPEND_CAP_CENTS - ledger.total_cents
    detail.append(
        f"{issued} ordinary requests from {attacker} spent {ledger.total_cents:,} of the "
        f"{fixtures.GLOBAL_SPEND_CAP_CENTS:,}-cent undivided pool, leaving {remaining:,}"
    )

    refused: list[str] = []
    for offset, bystander in enumerate(fixtures.BYSTANDER_TENANT_IDS):
        probe = await client.enrich(
            [fixtures.company_name(i) for i in range(1, 11)],
            sequence=DRAIN_MAX_REQUESTS + 10 + offset,
            tenant_id=bystander,
        )
        state = "REFUSED" if not probe.succeeded else "served"
        detail.append(
            f"{bystander} then asked for ten records and was {state} ({probe.status_code}) — "
            f"it had spent nothing"
        )
        if not probe.succeeded:
            refused.append(bystander)

    return ShapeOutcome(
        bound_in_effect="one undivided pool, with no per-tenant partition",
        items_admitted=ledger.total_lookups,
        lookups=ledger.total_lookups,
        refusals={"allowance_exhausted": len(refused)},
        shape="unbounded repetition against an un-partitioned budget",
        headline=(
            f"one tenant drained the shared pool and {len(refused)} of "
            f"{len(fixtures.BYSTANDER_TENANT_IDS)} bystanders were refused for spending they "
            f"never did"
        ),
        reproduced=len(refused) == len(fixtures.BYSTANDER_TENANT_IDS),
        detail=detail,
        input_bytes=input_bytes,
        cents=ledger.total_cents,
    )


async def expansion(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Shape 3. The size check is on the compressed number, and it runs after the allocation."""
    await _fresh(client, config)
    tenant = fixtures.ATTACKER_TENANT_ID
    bundle = pathlib.Path(fixtures.EXPANSION_FIXTURE_PATH).read_bytes()

    imported = await client.import_bundle(bundle, sequence=1, tenant_id=tenant)
    usage = await client.usage(sequence=2, tenant_id=tenant)
    admitted_cents = int(usage.body["cents_charged"]) if usage.body else 0
    admitted_records = int(usage.body["lookups_performed"]) if usage.body else 0
    ledger = await client.provider_ledger()
    over = admitted_cents / fixtures.GLOBAL_SPEND_CAP_CENTS

    detail = [
        f"an upload of {len(bundle):,} B was accepted and admitted {admitted_records:,} records "
        f"as billable work",
        f"  that is {admitted_cents:,} {fixtures.CURRENCY_LABEL}, {over:.1f}x the whole "
        f"{fixtures.GLOBAL_SPEND_CAP_CENTS:,}-cent monthly cap",
        f"  cost per input byte: {admitted_cents / len(bundle):.1f} {fixtures.CURRENCY_LABEL}",
        "  the accounting is recorded durably as it is admitted, so it survives the process",
        f"  of the admitted work, {ledger.total_cents:,} was actually spent before the pool "
        f"refused (observed, not asserted)",
    ]
    return ShapeOutcome(
        bound_in_effect="a size check on the compressed number, after buffering",
        items_admitted=admitted_records,
        lookups=ledger.total_lookups,
        records=(imported,),
        shape="expansion, checked in the wrong place on the wrong number",
        headline=(
            f"{len(bundle):,} B of gzip admitted work worth {over:.1f}x the entire monthly cap"
        ),
        reproduced=imported.succeeded
        and len(bundle) <= 200_000
        and admitted_cents >= fixtures.GLOBAL_SPEND_CAP_CENTS * 10,
        detail=detail,
        input_bytes=len(bundle),
        cents=admitted_cents,
    )


async def unbounded_in_flight(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Shape 4. Nothing bounds work in flight, so an endpoint with no defect stops being served.

    This is the scenario the deterministic mode exists for. The provider is **held**, exactly as
    many calls as the replica has connections are put in flight, and the fixture's own occupancy
    count is polled until it confirms they are all there. Only then is the cheap endpoint asked
    whether it can still be served. Nothing waits on a duration; the question is asked at a known
    instant.
    """
    await _fresh(client, config)
    tenant = fixtures.ATTACKER_TENANT_ID
    occupying = config.vulnerable_pool_max_size
    detail: list[str] = []

    setup = await client.import_bundle(fixtures.ndjson_bundle(5), sequence=1, tenant_id=tenant)
    job_id = str(setup.body["job_id"]) if setup.body else ""

    await client.set_provider_control(held=True)
    tasks = [
        asyncio.create_task(
            client.enrich(
                [fixtures.company_name(i) for i in range(1, 6)],
                sequence=100 + slot,
                tenant_id=tenant,
            )
        )
        for slot in range(occupying)
    ]
    try:
        # Arithmetic, not a race: wait until the fixture itself confirms the calls are in flight.
        for _ in range(600):
            stats = await client.provider_stats()
            if stats.in_flight >= occupying:
                break
            await asyncio.sleep(0.05)
        stats = await client.provider_stats()
        detail.append(
            f"{stats.in_flight} upstream calls held in flight, against a replica with "
            f"{occupying} database connections — each request keeps one for the whole call"
        )

        cheap = await client.job(job_id, sequence=900, tenant_id=tenant)
        detail.append(
            f"GET /v1/jobs/{{job_id}} — which touches no provider, no budget and no expensive "
            f"path — answered {cheap.status_code}"
        )
        detail.append("  that endpoint contains no defect of its own")
        cheap_failed = not cheap.succeeded
        occupied = stats.in_flight
    finally:
        await client.set_provider_control(held=False)
        released: list[RequestRecord] = [r for r in await asyncio.gather(*tasks) if r is not None]

    detail.append(
        f"once released, {sum(1 for r in released if r.succeeded)} of the held calls completed"
    )
    return ShapeOutcome(
        bound_in_effect="no in-flight cap and no deadline",
        cheap_issued=1,
        cheap_answered=0 if cheap_failed else 1,
        records=(cheap,),
        shape="unbounded in-flight work and no deadline",
        headline=(
            f"{occupied} held calls occupied every connection and the cheap endpoint stopped "
            f"being served"
        ),
        reproduced=cheap_failed and occupied >= occupying,
        detail=detail,
        input_bytes=0,
        cents=0,
    )


SHAPES: Final = (client_names_the_work, undivided_budget, expansion, unbounded_in_flight)
