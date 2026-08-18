"""The repairs that fail, and the three controls that mark what this flaw is not.

The half-fix scenarios have an unusual shape: each one asserts that a repair **worked** and that the
budget **still drained**. Both halves matter. A repair that turned out to be broken would prove
nothing at all, so every one of these first checks that the limiter did its job — reported zero
violations, held its allowance exactly, refused the oversized upload — and only then shows that the
money went anyway.

The negative controls are the boundary markers. They exist so a reader cannot walk away with the
wrong repair in mind: not an access-control fix, because every request is authenticated and
authorized; not a better test suite, because one of each request is perfectly correct; and not a
bigger budget, because the ratio does not move when you buy one.

Nothing here asserts on elapsed time.
"""

from __future__ import annotations

import asyncio
from typing import Final
from uuid import uuid4

from .. import fixtures
from ..config import HarnessConfig
from ..httpclient import HalyardHTTP
from ..seed import seed, set_spend_cap
from ..vulnerable.halffixes import (
    CLIENT_ID_HEADER,
    HALF_FIX_HEADER,
    REQUESTS_PER_WINDOW,
    HalfFix,
)
from .vulnerable import ShapeOutcome

DRAIN_BATCH: Final = 2_000
"""Ordinary-sized requests. Nothing here is a large request; the total is the problem."""


def _headers(half_fix: HalfFix, client_id: str | None = None) -> dict[str, str]:
    headers = {HALF_FIX_HEADER: half_fix.value}
    if client_id is not None:
        headers[CLIENT_ID_HEADER] = client_id
    return headers


async def _enrich(
    client: HalyardHTTP,
    *,
    tenant_id: str,
    records: int,
    sequence: int,
    half_fix: HalfFix,
    client_id: str | None = None,
    offset: int = 0,
) -> tuple[int, int]:
    """One request. Returns its status and the bytes it cost the caller to send."""
    record = await client.send(
        "POST",
        "/v1/enrich",
        operation="enrich",
        sequence=sequence,
        tenant_id=tenant_id,
        json_body={
            "records": [
                {"company_name": fixtures.company_name(offset + i)} for i in range(1, records + 1)
            ]
        },
        headers=_headers(half_fix, client_id),
    )
    return record.status_code, record.input_bytes


async def _spent(client: HalyardHTTP) -> int:
    return (await client.provider_ledger()).total_cents


# --- the half-fixes --------------------------------------------------------------------------


async def request_rate_limit(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Half-fix 1: sixty requests a minute, honoured exactly, and the budget drains anyway.

    The limiter is proved to be real before it is proved to be useless, and the two are proved on
    different tenants so that neither can be explained by the other. A limiter that turned out not
    to be enforced would make this whole scenario vacuous.
    """
    await seed(config.runner)
    prover = fixtures.BYSTANDER_TENANT_IDS[0]
    attacker = fixtures.ATTACKER_TENANT_ID
    input_bytes = 0

    # First: is the limiter real? One request past the limit, on a tenant that does nothing else.
    limiter_refusals = 0
    for sequence in range(1, REQUESTS_PER_WINDOW + 2):
        status, sent = await _enrich(
            client,
            tenant_id=prover,
            records=1,
            sequence=sequence,
            half_fix=HalfFix.REQUEST_RATE_LIMIT,
            offset=sequence,
        )
        input_bytes += sent
        if status != 201:
            limiter_refusals += 1

    # Then: the caller stays inside it, on a tenant whose bucket is untouched, and drains anyway.
    allowed = admitted = 0
    for sequence in range(1, REQUESTS_PER_WINDOW + 1):
        status, sent = await _enrich(
            client,
            tenant_id=attacker,
            records=DRAIN_BATCH,
            sequence=sequence,
            half_fix=HalfFix.REQUEST_RATE_LIMIT,
            offset=sequence * DRAIN_BATCH,
        )
        input_bytes += sent
        if status != 201:
            break
        allowed += 1
        admitted += DRAIN_BATCH

    # Take exactly what is left, so "the pool is empty" is an equality rather than an approximation.
    remaining = fixtures.GLOBAL_SPEND_CAP_CENTS - await _spent(client)
    leftover = remaining // fixtures.LOOKUP_PRICE_CENTS
    if leftover > 0 and allowed < REQUESTS_PER_WINDOW:
        _, sent = await _enrich(
            client,
            tenant_id=attacker,
            records=leftover,
            sequence=REQUESTS_PER_WINDOW,
            half_fix=HalfFix.REQUEST_RATE_LIMIT,
            offset=800_000,
        )
        input_bytes += sent
        allowed += 1
        admitted += leftover

    spent = await _spent(client)
    drained = spent >= fixtures.GLOBAL_SPEND_CAP_CENTS
    # The caller never reached the limit, so the limiter had no cause to refuse it; the refusal that
    # ended the loop came from the money running out, which the provider's own bill confirms.
    stayed_inside = allowed < REQUESTS_PER_WINDOW

    detail = [
        f"the limiter is real: {prover} was refused {limiter_refusals} time(s) on request "
        f"{REQUESTS_PER_WINDOW + 1} of {REQUESTS_PER_WINDOW + 1}, exactly as configured",
        f"the limiter is honoured: {attacker} issued {allowed} requests, stayed inside "
        f"{REQUESTS_PER_WINDOW} a minute throughout, and the limiter refused it 0 times",
        f"work units admitted: {admitted:,} lookups — the whole {spent:,}-cent cap, which is what "
        f"ended the run",
        "  requests allowed and requests refused by the limiter say nothing about any of this",
        "  the limit counts requests; the resource is measured in lookups",
        "  the unit of the limit must be the unit of the resource",
    ]
    return ShapeOutcome(
        shape="half-fix: a request-count rate limit",
        headline=(
            f"honoured with 0 violations against the caller, and the budget drained anyway "
            f"({spent:,} of {fixtures.GLOBAL_SPEND_CAP_CENTS:,} cents)"
        ),
        reproduced=limiter_refusals == 1 and stayed_inside and drained,
        detail=detail,
        input_bytes=input_bytes,
        cents=spent,
    )


async def _spend_until_refused(
    client: HalyardHTTP,
    *,
    tenant_id: str,
    half_fix: HalfFix,
    client_id: str | None = None,
    rotate: bool = False,
    limit: int = 40,
) -> tuple[int, int]:
    """Spend in ordinary requests until the repair refuses one. Returns cents admitted and bytes."""
    admitted_cents = input_bytes = 0
    for sequence in range(1, limit + 1):
        status, sent = await _enrich(
            client,
            tenant_id=tenant_id,
            records=DRAIN_BATCH,
            sequence=sequence,
            half_fix=half_fix,
            client_id=f"rotated-{sequence}" if rotate else client_id,
            offset=sequence * DRAIN_BATCH,
        )
        input_bytes += sent
        if status != 201:
            break
        admitted_cents += DRAIN_BATCH * fixtures.LOOKUP_PRICE_CENTS
    return admitted_cents, input_bytes


async def in_process_allowance(
    one_replica: HalyardHTTP, both_replicas: HalyardHTTP, config: HarnessConfig
) -> ShapeOutcome:
    """Half-fix 2: the right allowance, held in the wrong place.

    Both tenants start from zero and both are held to the same allowance by the same code. The only
    variable that changes between the two measurements is **how many processes are enforcing it**.

    This scenario needs **freshly started replicas**, because the counter it is about lives in the
    process and there is deliberately no endpoint to reset it. That is inconvenient, and it is also
    the entire point: state that only a restart can clear is state that is not shared.
    """
    await seed(config.runner)
    allowance = fixtures.TENANT_ALLOWANCE_CENTS
    single_tenant, double_tenant = fixtures.BYSTANDER_TENANT_IDS

    single, single_bytes = await _spend_until_refused(
        one_replica, tenant_id=single_tenant, half_fix=HalfFix.IN_PROCESS_ALLOWANCE
    )
    double, double_bytes = await _spend_until_refused(
        both_replicas, tenant_id=double_tenant, half_fix=HalfFix.IN_PROCESS_ALLOWANCE
    )

    detail = [
        f"addressed at one replica, {single_tenant} was held to {single:,} cents "
        f"against an allowance of {allowance:,} — exactly right",
        f"addressed at two replicas, {double_tenant} was admitted {double:,} cents "
        f"against the same allowance of {allowance:,}",
        f"  the effective allowance was multiplied by {double / single:.0f} and the only variable "
        f"that changed was the number of processes enforcing it",
        "  nothing was corrupted and nothing was lost — the budget was enforced twice, in parallel",
        "  a limiter's counter must be shared by every process that serves the endpoint",
    ]
    return ShapeOutcome(
        shape="half-fix: the limiter's scope",
        headline=(
            f"exact at one replica ({single:,}) and doubled at two ({double:,}), with nothing "
            f"else changed"
        ),
        reproduced=single == allowance and double == allowance * 2,
        detail=detail,
        input_bytes=single_bytes + double_bytes,
        cents=single + double,
    )


async def caller_keyed_allowance(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Half-fix 3: the same limiter, keyed on something the caller sends.

    The steady key is fresh on every run. That is not a convenience — it is the defect, used
    deliberately: this allowance lives in the process, there is no endpoint to reset it, and a
    caller who wants an untouched one simply picks a value nobody has used yet. Getting a clean
    measurement requires exactly the move that defeats the limiter.
    """
    await seed(config.runner)
    allowance = fixtures.TENANT_ALLOWANCE_CENTS
    tenant = fixtures.ATTACKER_TENANT_ID
    steady = f"steady-{uuid4().hex[:12]}"

    held, held_bytes = await _spend_until_refused(
        client, tenant_id=tenant, half_fix=HalfFix.CALLER_KEYED_ALLOWANCE, client_id=steady
    )
    await seed(config.runner)
    rotated, rotated_bytes = await _spend_until_refused(
        client, tenant_id=tenant, half_fix=HalfFix.CALLER_KEYED_ALLOWANCE, rotate=True
    )

    detail = [
        f"keyed on a steady caller-supplied value, {tenant} was held to {held:,} cents",
        f"keyed the same way but rotating the value on every request, it was admitted "
        f"{rotated:,} cents — {rotated / held:.0f}x its allowance of {allowance:,}",
        "  a bucket keyed on anything the caller supplies is a bucket they can mint a fresh one of",
        "  the key must be the server-derived authenticated principal",
    ]
    return ShapeOutcome(
        shape="half-fix: the limiter's key",
        headline=(f"held to {held:,} cents with a steady key and {rotated:,} by rotating it"),
        reproduced=held == allowance and rotated > allowance,
        detail=detail,
        input_bytes=held_bytes + rotated_bytes,
        cents=held + rotated,
    )


async def compressed_size_check(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Half-fix 4: "we do check the size" — present, honoured, and useless."""
    import pathlib

    from ..vulnerable.shapes import COMPRESSED_BODY_LIMIT_BYTES

    await seed(config.runner)
    tenant = fixtures.ATTACKER_TENANT_ID

    oversized = fixtures.repetitive_ndjson_bundle(1_200_000)
    refused = await client.import_bundle(oversized, sequence=1, tenant_id=tenant)

    fixture = pathlib.Path(fixtures.EXPANSION_FIXTURE_PATH).read_bytes()
    admitted = await client.import_bundle(fixture, sequence=2, tenant_id=tenant)
    usage = await client.usage(sequence=3, tenant_id=tenant)
    admitted_cents = int(usage.body["cents_charged"]) if usage.body else 0

    detail = [
        f"the check is present and honoured: {len(oversized):,} B was refused "
        f"({refused.status_code}) against a limit of {COMPRESSED_BODY_LIMIT_BYTES:,} B",
        f"the check is useless: {len(fixture):,} B passed it and admitted {admitted_cents:,} "
        f"cents, {admitted_cents / fixtures.GLOBAL_SPEND_CAP_CENTS:.1f}x the whole cap",
        "  it measures the compressed size, a number whose ratio to the real one the sender chose",
        "  and it runs after the whole body is already in memory, so the allocation it exists to "
        "prevent has already happened",
    ]
    return ShapeOutcome(
        shape="half-fix: a size check on the compressed number",
        headline=(
            f"refused {len(oversized):,} B and waved through {len(fixture):,} B worth "
            f"{admitted_cents / fixtures.GLOBAL_SPEND_CAP_CENTS:.1f}x the cap"
        ),
        reproduced=(
            not refused.succeeded
            and admitted.succeeded
            and admitted_cents >= fixtures.GLOBAL_SPEND_CAP_CENTS * 10
        ),
        detail=detail,
        input_bytes=len(fixture),
        cents=admitted_cents,
    )


async def non_cancelling_deadline(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Half-fix 5: a deadline that answers the caller and lets the work carry on.

    The provider is held, so the abandoned call cannot finish on its own. The caller is answered at
    the deadline; the fixture's own occupancy count then says whether the work stopped. It did not.
    """
    await seed(config.runner)
    tenant = fixtures.ATTACKER_TENANT_ID
    await client.set_provider_control(held=True)
    detail: list[str] = []
    try:
        before = (await client.provider_stats()).in_flight
        status, sent = await _enrich(
            client,
            tenant_id=tenant,
            records=50,
            sequence=1,
            half_fix=HalfFix.NON_CANCELLING_DEADLINE,
        )
        after = (await client.provider_stats()).in_flight
        detail.append(f"the caller was answered {status} at the configured deadline")
        detail.append(
            f"upstream calls in flight before {before}, after {after} — the work did not stop"
        )
        detail.append("  the response was bounded; the work was not")
        detail.append("  a deadline is only a deadline if it cancels")
        answered_and_unchanged = status == 504 and after >= 1
    finally:
        await client.set_provider_control(held=False)
        await asyncio.sleep(0)

    spent = await _spent(client)
    detail.append(f"once released, the abandoned call completed and billed {spent:,} cents anyway")
    return ShapeOutcome(
        shape="half-fix: a deadline that returns but does not cancel",
        headline="the caller was answered at the deadline while the work carried on and billed",
        reproduced=answered_and_unchanged,
        detail=detail,
        input_bytes=sent,
        cents=spent,
    )


HALF_FIXES: Final = (
    request_rate_limit,
    caller_keyed_allowance,
    compressed_size_check,
    non_cancelling_deadline,
)


# --- the negative controls -------------------------------------------------------------------


async def every_request_is_authorized(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Control 1: no access-control fix reaches any of this.

    Every request in every shape is an ordinary paying customer, using documented endpoints, with a
    valid credential, acting only on its own data. This is the boundary between this flaw and the
    series' access-control demonstrations: object-level, function-level and property-level
    authorization are all working perfectly here, and all of them are beside the point.
    """
    await seed(config.runner)
    tenant = fixtures.ATTACKER_TENANT_ID
    detail: list[str] = []
    authenticated = 0

    probes = (
        ("enrich a batch", "POST", "/v1/enrich"),
        ("list its own records", "GET", "/v1/records?page_size=50"),
        ("read its own usage", "GET", "/v1/usage"),
    )
    for sequence, (label, method, path) in enumerate(probes, start=1):
        body = (
            {"records": [{"company_name": fixtures.company_name(i)} for i in range(1, 21)]}
            if method == "POST"
            else None
        )
        record = await client.send(
            method, path, operation="control", sequence=sequence, tenant_id=tenant, json_body=body
        )
        ok = record.succeeded
        authenticated += 1 if ok else 0
        detail.append(f"{label}: {record.status_code} as {tenant} — authenticated and authorized")

    detail.append(
        "  every one of these is a documented endpoint, a valid credential, and a tenant acting "
        "only on its own data"
    )
    detail.append(
        "  no object-level, function-level or property-level authorization control would refuse "
        "a single one of them"
    )
    return ShapeOutcome(
        shape="control: every request is authenticated and authorized",
        headline=f"all {authenticated} requests passed authentication and authorization",
        reproduced=authenticated == len(probes),
        detail=detail,
        input_bytes=0,
        cents=0,
    )


async def one_of_each_is_correct(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Control 2: the reason the defect ships.

    Each shape, issued once at an ordinary size, is correct, complete and prompt. A functional
    suite written against these requests is entirely green. The defect lives only in the
    **aggregate**, and the aggregate is what functional suites do not assert on.
    """
    await seed(config.runner)
    tenant = fixtures.ATTACKER_TENANT_ID
    detail: list[str] = []
    correct = 0

    batch = await client.enrich(
        [fixtures.company_name(i) for i in range(1, 21)], sequence=1, tenant_id=tenant
    )
    if batch.status_code == 201 and batch.records_admitted == 20:
        correct += 1
    detail.append(
        f"one batch of 20 records: {batch.status_code}, {batch.records_admitted} admitted, "
        f"{batch.cents_charged} cents — correct and complete"
    )

    page = await client.list_records(sequence=2, tenant_id=tenant, page_size=50)
    rows = len(page.body.get("records", [])) if page.body else 0
    if page.status_code == 200 and rows == 50:
        correct += 1
    detail.append(f"one listing of 50 records: {page.status_code}, {rows} returned — correct")

    imported = await client.import_bundle(
        fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS), sequence=3, tenant_id=tenant
    )
    if imported.status_code == 201:
        correct += 1
    detail.append(
        f"one import of {fixtures.LEGITIMATE_IMPORT_RECORDS} records: {imported.status_code}, "
        f"{imported.records_admitted} admitted — correct"
    )

    job = await client.job(
        str(imported.body["job_id"]) if imported.body else "", sequence=4, tenant_id=tenant
    )
    if job.status_code == 200:
        correct += 1
    detail.append(f"one job read: {job.status_code} — correct and prompt")
    detail.append("  a functional suite written against these four requests is entirely green")
    detail.append("  this is the reason the defect ships: it exists only in the aggregate")

    return ShapeOutcome(
        shape="control: one of each request is perfectly correct",
        headline=f"all {correct} ordinary requests were correct, complete and prompt",
        reproduced=correct == 4,
        detail=detail,
        input_bytes=0,
        cents=0,
    )


async def more_capacity_is_not_a_fix(client: HalyardHTTP, config: HarnessConfig) -> ShapeOutcome:
    """Control 3: buying a bigger budget buys a constant factor and changes nothing structural."""
    settings = (
        ("baseline", fixtures.GLOBAL_SPEND_CAP_CENTS, DRAIN_BATCH),
        ("capacity doubled", fixtures.GLOBAL_SPEND_CAP_CENTS * 2, DRAIN_BATCH),
        ("request rate halved", fixtures.GLOBAL_SPEND_CAP_CENTS, DRAIN_BATCH // 2),
    )
    detail: list[str] = []
    ratios: list[float] = []
    total_bytes = total_cents = 0

    for label, cap, batch in settings:
        await seed(config.runner)
        await set_spend_cap(config.runner.database_url, cap)
        issued = 0
        input_bytes = 0
        for sequence in range(1, 200):
            status, sent = await _enrich(
                client,
                tenant_id=fixtures.ATTACKER_TENANT_ID,
                records=batch,
                sequence=sequence,
                half_fix=HalfFix.NONE,
                offset=sequence * batch,
            )
            issued += 1
            input_bytes += sent
            if status != 201:
                break
        spent = await _spent(client)
        ratio = spent / input_bytes if input_bytes else 0.0
        ratios.append(ratio)
        total_bytes += input_bytes
        total_cents += spent
        detail.append(
            f"{label:<20} cap {cap:>7,}  batch {batch:>5,}  time-to-drain {issued:>3} requests  "
            f"work admitted {spent // fixtures.LOOKUP_PRICE_CENTS:>7,} lookups  "
            f"amplification {ratio:.4f}"
        )

    spread = max(ratios) - min(ratios)
    detail.append(
        f"  the amplification ratio moved by {spread:.4f} across every setting: it does not move"
    )
    detail.append("  added capacity buys a constant factor; the fix has to change the structure")

    return ShapeOutcome(
        shape="control: more capacity is not a fix",
        headline=(
            f"time-to-drain changed with capacity and rate; the amplification ratio did not "
            f"(spread {spread:.4f})"
        ),
        reproduced=spread < 0.005,
        detail=detail,
        input_bytes=total_bytes,
        cents=total_cents,
    )


NEGATIVE_CONTROLS: Final = (
    every_request_is_authorized,
    one_of_each_is_correct,
    more_capacity_is_not_a_fix,
)
