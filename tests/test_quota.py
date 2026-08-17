"""Secure control C: the allowance is counted in the unit of the resource, and it is atomic.

Three properties are proved here, and they are the three that a limiter usually gets wrong:

1. **the unit** — the allowance counts provider lookups, not requests, so a caller cannot buy
   fifty thousand units of work with one request;
2. **the reservation** — money is held before the work happens, by one conditional write decided on
   affected row count, so concurrent requests cannot each be told there is room that only one of
   them can actually have; and
3. **the partition** — the tenant's own share and the whole fictional company's cap both refuse
   independently, so no tenant can reach another tenant's money or the global remainder.

Nothing here asserts on how long anything took. Every assertion is about counted accounting.
"""

from __future__ import annotations

import asyncio

from limitless import fixtures
from limitless.config import BOUNDS, RunnerConfig
from limitless.httpclient import HalyardHTTP, RequestRecord

from .conftest import set_allowance, set_global_cap

PRICE = fixtures.LOOKUP_PRICE_CENTS


def names(count: int, *, offset: int = 0) -> list[str]:
    return [fixtures.company_name(offset + i) for i in range(1, count + 1)]


async def usage_cents(client: HalyardHTTP, tenant_id: str, *, sequence: int = 9000) -> int:
    record = await client.usage(sequence=sequence, tenant_id=tenant_id)
    assert record.body is not None
    return int(record.body["cents_charged"])


async def usage_lookups(client: HalyardHTTP, tenant_id: str, *, sequence: int = 9001) -> int:
    record = await client.usage(sequence=sequence, tenant_id=tenant_id)
    assert record.body is not None
    return int(record.body["lookups_performed"])


async def test_the_allowance_is_charged_in_lookups_not_requests(client: HalyardHTTP) -> None:
    """Ten requests of one record each cost ten lookups; one request of ten costs the same.

    A request-count limit would price these two identically at ten and one. The resource does not
    care how the work was packaged, so neither does the allowance.
    """
    tenant = fixtures.BYSTANDER_TENANT_IDS[0]
    for sequence in range(1, 11):
        record = await client.enrich(names(1, offset=sequence), sequence=sequence, tenant_id=tenant)
        assert record.status_code == 201
    spread = await usage_lookups(client, tenant)

    other = fixtures.BYSTANDER_TENANT_IDS[1]
    record = await client.enrich(names(10, offset=100), sequence=20, tenant_id=other)
    assert record.status_code == 201
    packed = await usage_lookups(client, other)

    assert spread == packed == 10


async def test_an_exhausted_allowance_is_refused_generically_with_retry_after(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    tenant = fixtures.BYSTANDER_TENANT_IDS[0]
    await set_allowance(config, tenant, 40 * PRICE)

    admitted = await client.enrich(names(40), sequence=1, tenant_id=tenant)
    assert admitted.status_code == 201

    refused = await client.enrich(names(1, offset=500), sequence=2, tenant_id=tenant)
    assert refused.status_code == 429
    assert refused.body == {"detail": "request could not be completed"}


async def test_the_retry_after_is_present_constant_and_not_the_real_reset(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    """A caller must not be able to read the shape of the allowance out of a refusal.

    ``Retry-After`` is required by the contract, so it is sent — as a fixed constant. Two tenants
    whose allowances were exhausted at different points, by different amounts, receive the identical
    value, which is exactly as much as a caller is entitled to learn.
    """
    first_tenant = fixtures.BYSTANDER_TENANT_IDS[0]
    second_tenant = fixtures.BYSTANDER_TENANT_IDS[1]
    await set_allowance(config, first_tenant, 4 * PRICE)
    await set_allowance(config, second_tenant, 60 * PRICE)

    assert (await client.enrich(names(4), sequence=1, tenant_id=first_tenant)).status_code == 201
    assert (await client.enrich(names(60), sequence=2, tenant_id=second_tenant)).status_code == 201

    first = await client.enrich(names(1, offset=300), sequence=3, tenant_id=first_tenant)
    second = await client.enrich(names(1, offset=400), sequence=4, tenant_id=second_tenant)

    assert first.status_code == second.status_code == 429
    assert first.retry_after == str(BOUNDS.retry_after_seconds)
    assert first.retry_after == second.retry_after, "Retry-After leaked the shape of the allowance"
    assert first.body == second.body == {"detail": "request could not be completed"}


async def test_an_input_refusal_carries_no_retry_after_at_all(client: HalyardHTTP) -> None:
    """Only an exhausted allowance gets one; nothing else hands the caller a hint."""
    refused = await client.list_records(
        sequence=1, tenant_id=fixtures.ATTACKER_TENANT_ID, page_size=1_000_000
    )
    assert refused.status_code == 413
    assert refused.retry_after is None


async def test_concurrent_requests_cannot_together_exceed_the_allowance(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    """The reservation is the control: the check *is* the write.

    Twenty requests arrive together, spread across both replicas, asking for twice the money that
    exists. Exactly the money that exists is spent — not a cent more, and none of it lost.
    """
    tenant = fixtures.BYSTANDER_TENANT_IDS[1]
    available_lookups = 100
    per_request = 10
    await set_allowance(config, tenant, available_lookups * PRICE)

    records: list[RequestRecord] = list(
        await asyncio.gather(
            *(
                client.enrich(
                    names(per_request, offset=sequence * per_request),
                    sequence=sequence,
                    tenant_id=tenant,
                )
                for sequence in range(1, 21)
            )
        )
    )

    admitted = [record for record in records if record.status_code == 201]
    refused = [record for record in records if record.status_code == 429]
    assert len(admitted) + len(refused) == 20, [r.status_code for r in records]
    assert len(admitted) == available_lookups // per_request

    charged = await usage_cents(client, tenant)
    assert charged == available_lookups * PRICE

    ledger = await client.provider_ledger()
    billed = next(entry for entry in ledger.per_tenant if entry.tenant_id == tenant)
    assert billed.lookups == available_lookups, "the provider's bill disagrees with the allowance"

    served = {record.served_by for record in records}
    assert len(served) == len(client.replica_urls), (
        f"the burst did not reach every replica ({served}); "
        f"an allowance shared across processes is what is under test"
    )


async def test_one_tenants_exhaustion_costs_the_others_nothing(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    attacker = fixtures.ATTACKER_TENANT_ID
    await set_allowance(config, attacker, 20 * PRICE)

    assert (await client.enrich(names(20), sequence=1, tenant_id=attacker)).status_code == 201
    assert (await client.enrich(names(1), sequence=2, tenant_id=attacker)).status_code == 429

    for sequence, bystander in enumerate(fixtures.BYSTANDER_TENANT_IDS, start=3):
        served = await client.enrich(names(50), sequence=sequence, tenant_id=bystander)
        assert served.status_code == 201, f"{bystander} lost work it never asked to lose"
        assert served.records_admitted == 50


async def test_the_global_cap_refuses_even_when_the_tenant_has_room(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    """Fail closed on *either* budget, so the two controls cannot be played against each other."""
    tenant = fixtures.ATTACKER_TENANT_ID
    await set_allowance(config, tenant, 10_000 * PRICE)
    await set_global_cap(config, 30 * PRICE)

    assert (await client.enrich(names(30), sequence=1, tenant_id=tenant)).status_code == 201
    refused = await client.enrich(names(1, offset=900), sequence=2, tenant_id=tenant)
    assert refused.status_code == 429

    ledger = await client.provider_ledger()
    assert ledger.total_cents <= 30 * PRICE


async def test_money_held_for_work_that_never_happened_is_given_back(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    """A reservation is not a charge. What is not spent must return to the allowance."""
    tenant = fixtures.BYSTANDER_TENANT_IDS[0]
    await set_allowance(config, tenant, 100 * PRICE)

    for sequence in range(1, 11):
        record = await client.enrich(
            names(10, offset=sequence * 10), sequence=sequence, tenant_id=tenant
        )
        assert record.status_code == 201

    assert await usage_cents(client, tenant) == 100 * PRICE
    ledger = await client.provider_ledger()
    billed = next(entry for entry in ledger.per_tenant if entry.tenant_id == tenant)
    assert billed.cents == 100 * PRICE, "the allowance and the provider's bill must agree exactly"


async def test_the_allowance_is_keyed_on_the_credential_not_on_anything_the_caller_sends(
    client: HalyardHTTP, config: RunnerConfig
) -> None:
    """A bucket keyed on caller-supplied data is a bucket the caller can mint a fresh one of."""
    tenant = fixtures.BYSTANDER_TENANT_IDS[1]
    await set_allowance(config, tenant, 10 * PRICE)
    assert (await client.enrich(names(10), sequence=1, tenant_id=tenant)).status_code == 201

    for rotation, header in enumerate(
        ({"X-Tenant-Id": "TEN-SOMETHING-ELSE"}, {"X-Client-Id": "rotated"}), start=2
    ):
        refused = await client.send(
            "POST",
            "/v1/enrich",
            operation="enrich",
            sequence=rotation,
            tenant_id=tenant,
            json_body={"records": [{"company_name": "Alder Provisioning"}]},
            headers=header,
        )
        assert refused.status_code == 429, "a caller-supplied value moved the allowance"
