"""The sequential demonstration of the secure application.

One request at a time, through the product's own HTTP boundary, with every claim read back from the
application's own usage endpoint and the provider's own ledger. There is no concurrency here and no
load: this is the demonstration that the bounds exist, are enforced at the edge, and cost a
legitimate customer nothing.

Four things are shown, in the order a reader needs them:

1. **legitimate work succeeds** and is charged correctly, reconciled against the provider's bill;
2. **every over-limit input is refused**, and the provider's bill does not move — which is what
   "refused before the work was allocated" means in a quantity you can check;
3. **the allowance is charged in lookups and is partitioned**, so a tenant that spends its whole
   share is refused while a tenant that spent nothing is served exactly as before; and
4. **four different credential failures produce one answer**.

The requests are spread round robin across every addressed replica, so the allowance is being
enforced across two processes rather than inside one. That is not a detail: an allowance held in
process memory would be two allowances here, and the totals below would be wrong by a factor of two.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Final

from .. import fixtures
from ..auth import EXPIRED_TOKEN, UNKNOWN_TOKEN
from ..config import BOUNDS, RunnerConfig
from ..httpclient import HalyardHTTP, RequestRecord
from ..models import LedgerView

SUMMARY_PREFIX: Final = "limitless-demo-summary:"

LEGITIMATE_BATCH_RECORDS: Final = 200
LEGITIMATE_PAGE_SIZE: Final = 100


@dataclass(slots=True)
class Counter:
    """What the demonstration observed, in the units the whole project is measured in."""

    sequence: int = 0
    refusals: int = 0
    records_admitted: int = 0
    cents_charged: int = 0
    input_bytes: int = 0
    failures: list[str] = field(default_factory=list)

    def next_sequence(self) -> int:
        self.sequence += 1
        return self.sequence

    def observe(self, record: RequestRecord) -> RequestRecord:
        self.input_bytes += record.input_bytes
        if record.refused:
            self.refusals += 1
        if record.succeeded:
            self.records_admitted += record.records_admitted
            self.cents_charged += record.cents_charged
        return record

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)


def _heading(title: str) -> None:
    print(f"\n{'=' * 96}\n{title}\n{'=' * 96}")


def _row(label: str, record: RequestRecord, note: str = "") -> None:
    outcome = f"{record.status_code}"
    print(
        f"  {label:<46} {outcome:>5}  served by {record.served_by or '?':<6}"
        f"  in {record.input_bytes:>7} B  {note}"
    )


def _ledger_line(ledger: LedgerView) -> str:
    per_tenant = "  ".join(
        f"{e.tenant_id}={e.lookups} lookups = {e.cents}c" for e in ledger.per_tenant
    )
    return (
        f"{ledger.provider}: {ledger.total_lookups} lookups, "
        f"{ledger.total_cents} {ledger.currency}   [{per_tenant or 'empty'}]"
    )


async def run(config: RunnerConfig) -> Counter:
    counter = Counter()
    async with HalyardHTTP(
        config.replica_urls,
        provider_url=config.provider_url,
        timeout=config.request_timeout_seconds,
    ) as client:
        await client.wait_until_ready()
        print(
            f"\n{fixtures.COMPANY_NAME} — secure application, sequential demonstration\n"
            f"  replicas addressed : {', '.join(client.replica_labels)}\n"
            f"  provider           : {fixtures.PROVIDER_NAME} "
            f"({fixtures.LOOKUP_PRICE_CENTS} {fixtures.CURRENCY_LABEL} per lookup)\n"
            f"  fictional cap      : {fixtures.GLOBAL_SPEND_CAP_CENTS} for "
            f"{fixtures.SPEND_PERIOD_ID}, partitioned into "
            f"{fixtures.TENANT_ALLOWANCE_CENTS} per tenant\n"
            f"  bounds in effect   : body {BOUNDS.max_body_bytes} B · batch "
            f"{BOUNDS.max_batch_items} items · page {BOUNDS.max_page_size} · bundle "
            f"{BOUNDS.max_decompressed_bytes} B and ratio {BOUNDS.max_expansion_ratio} · "
            f"{BOUNDS.max_in_flight_upstream} in flight"
        )

        await _legitimate_work(client, counter)
        await _bounds_refuse_before_allocating(client, counter)
        await _allowance_is_partitioned(client, counter)
        await _credentials(client, counter)

        _heading("SUMMARY")
        ledger = await client.provider_ledger()
        print(f"  {_ledger_line(ledger)}")
        print(
            f"  requests issued {counter.sequence}   refusals {counter.refusals}   "
            f"records admitted {counter.records_admitted}   "
            f"{counter.cents_charged} {fixtures.CURRENCY_LABEL} charged"
        )
        # The two sides of the demonstration must agree. The application's own responses and the
        # provider's own bill are independent records of the same fictional money, and a run in
        # which they disagree is not reporting a result — it is reporting a defect in the report.
        counter.require(
            counter.cents_charged == ledger.total_cents,
            f"the application reported {counter.cents_charged} {fixtures.CURRENCY_LABEL} charged "
            f"and the provider billed {ledger.total_cents}",
        )
        counter.require(
            counter.records_admitted == ledger.total_lookups,
            f"the application admitted {counter.records_admitted} records and the provider "
            f"performed {ledger.total_lookups} lookups",
        )
        counter.require(
            ledger.total_cents <= fixtures.GLOBAL_SPEND_CAP_CENTS,
            f"the global fictional cap was breached: {ledger.total_cents} spent against "
            f"{fixtures.GLOBAL_SPEND_CAP_CENTS}",
        )
        print(
            f"  {SUMMARY_PREFIX} "
            + json.dumps(
                {
                    "refusals": counter.refusals,
                    "requests": counter.sequence,
                    "records_admitted": counter.records_admitted,
                    "cents_charged": counter.cents_charged,
                    "provider_cents": ledger.total_cents,
                    "provider_lookups": ledger.total_lookups,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return counter


async def _legitimate_work(client: HalyardHTTP, counter: Counter) -> None:
    _heading("1 — legitimate work, within every bound, is served and charged correctly")
    tenant = fixtures.ATTACKER_TENANT_ID

    names = [fixtures.company_name(i) for i in range(1, LEGITIMATE_BATCH_RECORDS + 1)]
    enrich = counter.observe(
        await client.enrich(names, sequence=counter.next_sequence(), tenant_id=tenant)
    )
    _row(
        f"enrich {LEGITIMATE_BATCH_RECORDS} records",
        enrich,
        f"{enrich.records_admitted} admitted, {enrich.cents_charged} c",
    )
    counter.require(
        enrich.status_code == 201, f"legitimate batch was refused: {enrich.status_code}"
    )
    counter.require(
        enrich.records_admitted == LEGITIMATE_BATCH_RECORDS,
        "legitimate batch did not admit every record",
    )

    listing = counter.observe(
        await client.list_records(
            sequence=counter.next_sequence(), tenant_id=tenant, page_size=LEGITIMATE_PAGE_SIZE
        )
    )
    served = len(listing.body.get("records", [])) if listing.body else 0
    _row(f"list records page_size={LEGITIMATE_PAGE_SIZE}", listing, f"{served} records returned")
    counter.require(listing.status_code == 200, "legitimate listing was refused")
    counter.require(served == LEGITIMATE_PAGE_SIZE, f"listing returned {served} records")

    bundle = fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS)
    imported = counter.observe(
        await client.import_bundle(bundle, sequence=counter.next_sequence(), tenant_id=tenant)
    )
    _row(
        f"import bundle, {fixtures.LEGITIMATE_IMPORT_RECORDS} records",
        imported,
        f"{imported.records_admitted} admitted, {imported.cents_charged} c",
    )
    counter.require(imported.status_code == 201, "legitimate import was refused")

    job_id = str(imported.body.get("job_id", "")) if imported.body else ""
    job = counter.observe(
        await client.job(job_id, sequence=counter.next_sequence(), tenant_id=tenant)
    )
    _row("read job status (the cheap endpoint)", job, "no provider, no allowance, no slot")
    counter.require(job.status_code == 200, "the cheap endpoint did not answer")

    usage = counter.observe(await client.usage(sequence=counter.next_sequence(), tenant_id=tenant))
    charged = int(usage.body.get("cents_charged", -1)) if usage.body else -1
    lookups = int(usage.body.get("lookups_performed", -1)) if usage.body else -1
    _row("read own usage", usage, f"{lookups} lookups, {charged} c")

    expected_lookups = LEGITIMATE_BATCH_RECORDS + fixtures.LEGITIMATE_IMPORT_RECORDS
    expected_cents = expected_lookups * fixtures.LOOKUP_PRICE_CENTS
    counter.require(
        lookups == expected_lookups and charged == expected_cents,
        f"usage reported {lookups} lookups / {charged} c, expected "
        f"{expected_lookups} / {expected_cents}",
    )

    ledger = await client.provider_ledger()
    print(f"\n  reconciled against the provider's own bill:\n    {_ledger_line(ledger)}")
    billed = next((e for e in ledger.per_tenant if e.tenant_id == tenant), None)
    counter.require(
        billed is not None
        and billed.lookups == expected_lookups
        and billed.cents == expected_cents,
        "the provider's bill does not agree with the application's usage endpoint",
    )


async def _bounds_refuse_before_allocating(client: HalyardHTTP, counter: Counter) -> None:
    _heading("2 — every over-limit input is refused, and nothing is billed for it")
    tenant = fixtures.ATTACKER_TENANT_ID
    before = await client.provider_ledger()

    oversized = b'{"records": [' + b'{"company_name": "Alder Provisioning"},' * 4000 + b"]}"
    body = counter.observe(
        await client.enrich_raw(oversized, sequence=counter.next_sequence(), tenant_id=tenant)
    )
    _row(
        f"body of {len(oversized)} B (bound {BOUNDS.max_body_bytes} B)",
        body,
        "refused while reading, never buffered",
    )
    counter.require(body.status_code == 413, f"an over-large body returned {body.status_code}")

    long_batch = [fixtures.company_name(i) for i in range(1, BOUNDS.max_batch_items + 2)]
    batch = counter.observe(
        await client.enrich(long_batch, sequence=counter.next_sequence(), tenant_id=tenant)
    )
    _row(
        f"batch of {len(long_batch)} items (bound {BOUNDS.max_batch_items})",
        batch,
        "each item is a metered lookup",
    )
    counter.require(batch.status_code == 413, f"an over-long batch returned {batch.status_code}")

    page = counter.observe(
        await client.list_records(
            sequence=counter.next_sequence(), tenant_id=tenant, page_size=1_000_000
        )
    )
    _row(
        f"page_size=1000000 (bound {BOUNDS.max_page_size})",
        page,
        "about sixty bytes of query string",
    )
    counter.require(page.status_code == 413, f"an over-large page size returned {page.status_code}")

    bomb = fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS)
    expansion = counter.observe(
        await client.import_bundle(bomb, sequence=counter.next_sequence(), tenant_id=tenant)
    )
    _row(
        f"bundle of {len(bomb)} B expanding past {BOUNDS.max_decompressed_bytes} B",
        expansion,
        "aborted mid-stream",
    )
    counter.require(
        expansion.status_code == 413, f"an over-expanding bundle returned {expansion.status_code}"
    )

    after = await client.provider_ledger()
    print(
        f"\n  the provider's bill before and after those four refusals:\n"
        f"    before  {before.total_lookups} lookups, {before.total_cents} {before.currency}\n"
        f"    after   {after.total_lookups} lookups, {after.total_cents} {after.currency}"
    )
    counter.require(
        after.total_lookups == before.total_lookups and after.total_cents == before.total_cents,
        "a refused request still reached the provider — the bound ran after the work",
    )
    print("    unchanged: every refusal happened before any work was allocated")


async def _allowance_is_partitioned(client: HalyardHTTP, counter: Counter) -> None:
    _heading("3 — the allowance is charged in lookups, and one tenant cannot spend another's")
    attacker = fixtures.ATTACKER_TENANT_ID
    bystander = fixtures.BYSTANDER_TENANT_IDS[0]

    exhausting = 0
    refusal: RequestRecord | None = None
    # Full batches, alternating across every addressed replica, until the allowance is spent.
    # A generous ceiling on the loop, so a defect fails the run instead of hanging it.
    max_batches = (
        fixtures.TENANT_ALLOWANCE_CENTS // (BOUNDS.max_batch_items * fixtures.LOOKUP_PRICE_CENTS)
        + 4
    )
    for batch_index in range(max_batches):
        offset = batch_index * BOUNDS.max_batch_items
        names = [fixtures.company_name(offset + i) for i in range(1, BOUNDS.max_batch_items + 1)]
        record = counter.observe(
            await client.enrich(names, sequence=counter.next_sequence(), tenant_id=attacker)
        )
        exhausting += 1
        if record.status_code == 429:
            refusal = record
            break

    print(
        f"  {exhausting} full batches of {BOUNDS.max_batch_items} sent as {attacker}, "
        f"alternating across {', '.join(client.replica_labels)}"
    )
    if refusal is None:
        counter.require(False, "the allowance never refused; it is not being enforced")
    else:
        _row(
            "the request that exceeded the allowance",
            refusal,
            f"generic 429, Retry-After {refusal.retry_after} (a constant, not the reset)",
        )
        counter.require(
            refusal.status_code == 429, f"allowance exhaustion returned {refusal.status_code}"
        )

    usage = counter.observe(
        await client.usage(sequence=counter.next_sequence(), tenant_id=attacker)
    )
    charged = int(usage.body.get("cents_charged", -1)) if usage.body else -1
    print(
        f"  {attacker} has spent {charged} of its {fixtures.TENANT_ALLOWANCE_CENTS}-cent share "
        f"and can spend no more"
    )
    counter.require(
        0 <= charged <= fixtures.TENANT_ALLOWANCE_CENTS,
        f"{attacker} spent {charged}, past its {fixtures.TENANT_ALLOWANCE_CENTS}-cent partition",
    )

    names = [fixtures.company_name(i) for i in range(1, LEGITIMATE_BATCH_RECORDS + 1)]
    unaffected = counter.observe(
        await client.enrich(names, sequence=counter.next_sequence(), tenant_id=bystander)
    )
    _row(
        f"{bystander} does exactly what it always did",
        unaffected,
        f"{unaffected.records_admitted} admitted, {unaffected.cents_charged} c",
    )
    counter.require(
        unaffected.status_code == 201,
        f"a bystander tenant was refused ({unaffected.status_code}) because another tenant spent",
    )
    print("  the partition is the control: an exhausted tenant costs the others nothing")


async def _credentials(client: HalyardHTTP, counter: Counter) -> None:
    _heading("4 — four different credential failures, one indistinguishable answer")
    probes = (
        ("missing", None),
        ("malformed", "NotBearer whatever"),
        ("unknown but well formed", f"Bearer {UNKNOWN_TOKEN}"),
        ("expired", f"Bearer {EXPIRED_TOKEN}"),
    )
    statuses: set[int] = set()
    for label, authorization in probes:
        record = counter.observe(
            await client.probe_credential(
                sequence=counter.next_sequence(), authorization=authorization
            )
        )
        statuses.add(record.status_code)
        _row(f"credential: {label}", record, "generic 401")
    counter.require(
        statuses == {401},
        f"credential failures produced more than one answer: {sorted(statuses)}",
    )
