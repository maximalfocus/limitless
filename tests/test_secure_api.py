"""The secure application through its own HTTP boundary.

Every claim here is established the way the demonstration establishes its claims: from the
application's own responses, its own usage endpoint, and the provider's own ledger. Nothing reads
the database to decide whether something happened.
"""

from __future__ import annotations

import httpx

from limitless import fixtures
from limitless.config import BOUNDS
from limitless.httpclient import HalyardHTTP

PRICE = fixtures.LOOKUP_PRICE_CENTS
GENERIC_REFUSAL = {"detail": "request could not be completed"}


def names(count: int, *, offset: int = 0) -> list[str]:
    return [fixtures.company_name(offset + i) for i in range(1, count + 1)]


async def test_a_legitimate_batch_is_served_and_billed_exactly(client: HalyardHTTP) -> None:
    tenant = fixtures.ATTACKER_TENANT_ID
    record = await client.enrich(names(200), sequence=1, tenant_id=tenant)

    assert record.status_code == 201
    assert record.body is not None
    assert record.body["records_admitted"] == 200
    assert record.body["lookups_performed"] == 200
    assert record.body["cents_charged"] == 200 * PRICE
    assert len(record.body["results"]) == 200
    assert record.served_by in client.replica_labels

    ledger = await client.provider_ledger()
    billed = next(entry for entry in ledger.per_tenant if entry.tenant_id == tenant)
    assert billed.lookups == 200
    assert billed.cents == 200 * PRICE
    assert ledger.price_cents_per_lookup == PRICE
    assert "fictional" in ledger.currency


async def test_usage_reports_spend_and_never_a_remaining_balance(client: HalyardHTTP) -> None:
    """A remaining balance would be a countdown to the exact request that gets refused."""
    tenant = fixtures.ATTACKER_TENANT_ID
    await client.enrich(names(25), sequence=1, tenant_id=tenant)

    usage = await client.usage(sequence=2, tenant_id=tenant)
    assert usage.status_code == 200
    assert usage.body is not None
    assert usage.body["lookups_performed"] == 25
    assert usage.body["cents_charged"] == 25 * PRICE
    assert usage.body["period_id"] == fixtures.SPEND_PERIOD_ID
    for disclosed in ("allowance_cents", "remaining_cents", "cents_remaining", "reset_at"):
        assert disclosed not in usage.body


async def test_a_legitimate_listing_returns_exactly_the_page_asked_for(
    client: HalyardHTTP,
) -> None:
    record = await client.list_records(
        sequence=1, tenant_id=fixtures.ATTACKER_TENANT_ID, page_size=100
    )
    assert record.status_code == 200
    assert record.body is not None
    assert record.body["page_size"] == 100
    assert len(record.body["records"]) == 100


async def test_an_absent_page_size_serves_the_server_side_maximum(client: HalyardHTTP) -> None:
    record = await client.list_records(sequence=1, tenant_id=fixtures.ATTACKER_TENANT_ID)
    assert record.status_code == 200
    assert record.body is not None
    assert len(record.body["records"]) == BOUNDS.max_page_size


async def test_a_legitimate_import_is_admitted_and_readable_as_a_job(
    client: HalyardHTTP,
) -> None:
    tenant = fixtures.ATTACKER_TENANT_ID
    bundle = fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS)
    imported = await client.import_bundle(bundle, sequence=1, tenant_id=tenant)

    assert imported.status_code == 201
    assert imported.body is not None
    assert imported.body["records_admitted"] == fixtures.LEGITIMATE_IMPORT_RECORDS
    assert imported.body["cents_charged"] == fixtures.LEGITIMATE_IMPORT_RECORDS * PRICE

    job = await client.job(str(imported.body["job_id"]), sequence=2, tenant_id=tenant)
    assert job.status_code == 200
    assert job.body is not None
    assert job.body["status"] == "completed"
    assert job.body["records_admitted"] == fixtures.LEGITIMATE_IMPORT_RECORDS


async def test_the_cheap_endpoint_touches_no_provider(client: HalyardHTTP) -> None:
    tenant = fixtures.ATTACKER_TENANT_ID
    bundle = fixtures.ndjson_bundle(10)
    imported = await client.import_bundle(bundle, sequence=1, tenant_id=tenant)
    assert imported.body is not None
    job_id = str(imported.body["job_id"])

    before = await client.provider_ledger()
    for sequence in range(2, 12):
        assert (await client.job(job_id, sequence=sequence, tenant_id=tenant)).status_code == 200
    after = await client.provider_ledger()

    assert after.total_lookups == before.total_lookups
    assert after.total_cents == before.total_cents


async def test_a_job_belongs_to_its_tenant_alone(client: HalyardHTTP) -> None:
    owner = fixtures.ATTACKER_TENANT_ID
    imported = await client.import_bundle(fixtures.ndjson_bundle(5), sequence=1, tenant_id=owner)
    assert imported.body is not None
    job_id = str(imported.body["job_id"])

    other = await client.job(job_id, sequence=2, tenant_id=fixtures.BYSTANDER_TENANT_IDS[0])
    assert other.status_code == 404


async def test_every_over_limit_input_is_refused_identically(client: HalyardHTTP) -> None:
    """Body, batch, page size, and expansion: one status, one body, no way to tell them apart."""
    tenant = fixtures.ATTACKER_TENANT_ID
    refusals = [
        await client.enrich_raw(
            b'{"records": [' + b'{"company_name": "Alder Provisioning"},' * 4000 + b"]}",
            sequence=1,
            tenant_id=tenant,
        ),
        await client.enrich(names(BOUNDS.max_batch_items + 1), sequence=2, tenant_id=tenant),
        await client.list_records(sequence=3, tenant_id=tenant, page_size=1_000_000),
        await client.import_bundle(
            fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS),
            sequence=4,
            tenant_id=tenant,
        ),
    ]

    for record in refusals:
        assert record.status_code == httpx.codes.REQUEST_ENTITY_TOO_LARGE, record.operation
        assert record.body == GENERIC_REFUSAL, record.operation


async def test_a_refused_request_never_reaches_the_provider(client: HalyardHTTP) -> None:
    """ "Refused before the work was allocated", expressed as a quantity anyone can check."""
    tenant = fixtures.ATTACKER_TENANT_ID
    before = await client.provider_ledger()

    await client.enrich(names(BOUNDS.max_batch_items + 1), sequence=1, tenant_id=tenant)
    await client.list_records(sequence=2, tenant_id=tenant, page_size=1_000_000)
    await client.import_bundle(
        fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS),
        sequence=3,
        tenant_id=tenant,
    )
    await client.enrich_raw(b"x" * (BOUNDS.max_body_bytes * 2), sequence=4, tenant_id=tenant)

    after = await client.provider_ledger()
    assert after.total_lookups == before.total_lookups
    assert after.total_cents == before.total_cents

    usage = await client.usage(sequence=5, tenant_id=tenant)
    assert usage.body is not None
    assert usage.body["cents_charged"] == 0


async def test_a_batch_at_exactly_the_bound_is_admitted(client: HalyardHTTP) -> None:
    """The bound refuses what is over it and nothing else."""
    record = await client.enrich(
        names(BOUNDS.max_batch_items), sequence=1, tenant_id=fixtures.ATTACKER_TENANT_ID
    )
    assert record.status_code == 201
    assert record.records_admitted == BOUNDS.max_batch_items


async def test_a_malformed_body_is_a_bad_request_rather_than_a_refusal(
    client: HalyardHTTP,
) -> None:
    tenant = fixtures.ATTACKER_TENANT_ID
    assert (await client.enrich_raw(b"not json", sequence=1, tenant_id=tenant)).status_code == 400
    assert (
        await client.import_bundle(b"not gzip", sequence=2, tenant_id=tenant)
    ).status_code == 400


async def test_both_replicas_serve_and_report_themselves(client: HalyardHTTP) -> None:
    served = set()
    for sequence in range(1, 5):
        record = await client.usage(sequence=sequence, tenant_id=fixtures.ATTACKER_TENANT_ID)
        assert record.status_code == 200
        served.add(record.served_by)
    assert served == set(client.replica_labels)


async def test_one_replica_can_be_addressed_alone(single_replica_client: HalyardHTTP) -> None:
    """The replica count is a real run parameter, not a label."""
    served = set()
    for sequence in range(1, 4):
        record = await single_replica_client.usage(
            sequence=sequence, tenant_id=fixtures.ATTACKER_TENANT_ID
        )
        served.add(record.served_by)
    assert len(served) == 1
