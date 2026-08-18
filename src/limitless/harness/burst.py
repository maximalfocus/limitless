"""Generating a genuinely concurrent burst.

"Concurrent" here means *at the same moment*, not merely "scheduled in the same event-loop
iteration". Every request in a round waits on one :class:`asyncio.Barrier` and is released together,
so the application really does see them arrive at once. A harness that quietly serialized its
requests would satisfy the secure application's zero-violation assertion without ever testing
anything, so how the load is generated is part of what has to be right.

A round is composed rather than uniform, because three different things have to be true *while the
load is in flight* rather than before or after it:

* **work** — ordinary, legitimate requests inside every bound, spread across tenants and replicas;
* **cheap-endpoint probes** — reads of an endpoint that touches no provider, no allowance, and no
  slot, which must be answered throughout; and
* **over-limit probes** — requests naming more than a bound allows, which must be refused
  throughout.

The composition is bounded by the same configured maximum as everything else: the parts add up to
the concurrency level, they never exceed it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final

from .. import fixtures
from ..config import BOUNDS
from ..httpclient import HalyardHTTP, RequestRecord

OVER_LIMIT_PROBES: Final = 4
"""One probe per bounded input dimension: body bytes, batch items, page size, expansion."""

MIN_CONCURRENCY: Final = 12
"""Below this there are not enough slots to compose a meaningful round."""

Factory = Callable[[], Awaitable[RequestRecord]]


@dataclass(frozen=True, slots=True)
class Composition:
    """How one round's concurrency budget is divided."""

    work: int
    cheap: int
    over_limit: int

    @property
    def total(self) -> int:
        return self.work + self.cheap + self.over_limit


def compose(concurrency: int) -> Composition:
    """Divide a concurrency level into work, cheap-endpoint probes, and over-limit probes."""
    if concurrency < MIN_CONCURRENCY:
        raise ValueError(f"concurrency must be at least {MIN_CONCURRENCY}; got {concurrency}")
    cheap = max(4, concurrency // 6)
    work = concurrency - OVER_LIMIT_PROBES - cheap
    return Composition(work=work, cheap=cheap, over_limit=OVER_LIMIT_PROBES)


@dataclass(frozen=True, slots=True)
class WorkSlot:
    """One work request: who sends it, and how much work it names."""

    tenant_id: str
    records: int


@dataclass(frozen=True, slots=True)
class RoundRecords:
    """Everything one round observed, kept apart by what each part was for."""

    work: tuple[RequestRecord, ...]
    cheap: tuple[RequestRecord, ...]
    over_limit: tuple[RequestRecord, ...]

    @property
    def all_records(self) -> tuple[RequestRecord, ...]:
        return self.work + self.cheap + self.over_limit


async def simultaneously(factories: Sequence[Factory]) -> list[RequestRecord]:
    """Release every request at the same instant and collect what came back."""
    if not factories:
        return []
    barrier = asyncio.Barrier(len(factories))

    async def gated(factory: Factory) -> RequestRecord:
        await barrier.wait()
        return await factory()

    return list(await asyncio.gather(*(gated(factory) for factory in factories)))


def _names(count: int, offset: int) -> list[str]:
    return [fixtures.company_name(offset + i) for i in range(1, count + 1)]


class RoundBuilder:
    """Builds the request factories for one round.

    The over-expanding bundle is built once and reused, because regenerating it every round would
    spend the run's time compressing fictional records rather than exercising the application.
    """

    def __init__(self, client: HalyardHTTP, *, probe_tenant_id: str) -> None:
        self._client = client
        self._probe_tenant_id = probe_tenant_id
        self._over_expanding_bundle = fixtures.repetitive_ndjson_bundle(
            fixtures.OVER_EXPANDING_IMPORT_RECORDS
        )
        self._oversized_body = (
            b'{"records": [' + b'{"company_name": "Alder Provisioning"},' * 4000 + b"]}"
        )

    async def run(
        self,
        *,
        slots: Sequence[WorkSlot],
        cheap_probes: int,
        job_id: str,
        start_sequence: int,
        name_offset: int,
    ) -> RoundRecords:
        client = self._client
        tenant = self._probe_tenant_id
        sequence = start_sequence

        work_factories: list[Factory] = []
        for index, slot in enumerate(slots):
            work_factories.append(
                _enrich_factory(
                    client,
                    slot.tenant_id,
                    _names(slot.records, name_offset + index * slot.records),
                    sequence,
                )
            )
            sequence += 1

        cheap_factories: list[Factory] = []
        for _ in range(cheap_probes):
            cheap_factories.append(_job_factory(client, tenant, job_id, sequence))
            sequence += 1

        over_limit_factories: list[Factory] = [
            _enrich_raw_factory(client, tenant, self._oversized_body, sequence),
            _enrich_factory(
                client, tenant, _names(BOUNDS.max_batch_items + 1, name_offset), sequence + 1
            ),
            _page_factory(client, tenant, 1_000_000, sequence + 2),
            _import_factory(client, tenant, self._over_expanding_bundle, sequence + 3),
        ]

        # One barrier across all three groups: the probes have to be in flight *while* the work is,
        # or they prove nothing about what the work was doing to the service.
        released = await simultaneously([*work_factories, *cheap_factories, *over_limit_factories])
        work_count = len(work_factories)
        cheap_count = len(cheap_factories)
        return RoundRecords(
            work=tuple(released[:work_count]),
            cheap=tuple(released[work_count : work_count + cheap_count]),
            over_limit=tuple(released[work_count + cheap_count :]),
        )


def _enrich_factory(
    client: HalyardHTTP, tenant_id: str, company_names: list[str], sequence: int
) -> Factory:
    async def send() -> RequestRecord:
        return await client.enrich(company_names, sequence=sequence, tenant_id=tenant_id)

    return send


def _enrich_raw_factory(client: HalyardHTTP, tenant_id: str, body: bytes, sequence: int) -> Factory:
    async def send() -> RequestRecord:
        return await client.enrich_raw(body, sequence=sequence, tenant_id=tenant_id)

    return send


def _page_factory(client: HalyardHTTP, tenant_id: str, page_size: int, sequence: int) -> Factory:
    async def send() -> RequestRecord:
        return await client.list_records(
            sequence=sequence, tenant_id=tenant_id, page_size=page_size
        )

    return send


def _import_factory(client: HalyardHTTP, tenant_id: str, bundle: bytes, sequence: int) -> Factory:
    async def send() -> RequestRecord:
        return await client.import_bundle(bundle, sequence=sequence, tenant_id=tenant_id)

    return send


def _job_factory(client: HalyardHTTP, tenant_id: str, job_id: str, sequence: int) -> Factory:
    async def send() -> RequestRecord:
        return await client.job(job_id, sequence=sequence, tenant_id=tenant_id)

    return send
