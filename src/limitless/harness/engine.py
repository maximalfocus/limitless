"""The scenario engine.

Two scenarios run against the secure application, and between them they establish everything this
slice claims:

**concurrent_legitimate** — all three fictional tenants working at once, well inside their
allowances. Every legitimate request must succeed, every over-limit probe must be refused, the
cheap endpoint must answer every time, and no tenant may be billed for another's work. This is the
scenario that shows the bounds cost a paying customer nothing.

**allowance_isolation** — one tenant deliberately spends its entire partition of the fictional
budget with the largest batches the bound allows, while two tenants that did nothing keep working
alongside it. The spending tenant must be able to consume its **whole** allowance without a single
piece of valid work being turned away, must then be refused, and must never reach a cent beyond it —
and the bystanders must be completely unaffected. This is the scenario that shows the partition is
the control.

The engine is a plain function over a configuration that returns data. It is directly testable
without simulating terminal input, and it renders nothing itself.

Nothing here asserts on elapsed time. Every check below is a comparison between counted integers,
read from the application's own responses, its own usage endpoint, and the provider fixture's own
ledger and occupancy — which is why two runs on two machines agree about the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Final

from .. import fixtures
from ..config import BOUNDS, MAX_LOOKUPS_PER_ROUND, MAX_ROUNDS, HarnessConfig
from ..httpclient import HalyardHTTP, RequestRecord
from ..seed import seed
from .accounting import (
    RunAccounting,
    ScenarioAccounting,
    TenantFigures,
    Violation,
    ViolationKind,
    refusal_kind,
)
from .burst import RoundBuilder, RoundRecords, WorkSlot, compose

VARIANT: Final = "secure"

SETUP_IMPORT_RECORDS: Final = 10
"""A tiny legitimate import, so the cheap-endpoint probes have a real job to read."""

BYSTANDER_SLOTS_EACH: Final = 2
"""How many concurrent slots each uninvolved tenant keeps working in, in every round."""


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    number: int
    records: RoundRecords
    violations: tuple[Violation, ...]


async def run(config: HarnessConfig) -> RunAccounting:
    """Drive every scenario against the secure application and return what was counted."""
    async with HalyardHTTP(
        config.runner.replica_urls,
        provider_url=config.runner.provider_url,
        timeout=config.runner.request_timeout_seconds,
    ) as client:
        await client.wait_until_ready()
        scenarios = (
            await _concurrent_legitimate(client, config),
            await _allowance_isolation(client, config),
        )
    return RunAccounting(variant=VARIANT, mode=config.mode.value, scenarios=scenarios)


async def _prepare(client: HalyardHTTP, config: HarnessConfig, *, probe_tenant: str) -> str:
    """Fresh fixtures, an empty provider bill, no remembered occupancy, and a job to read.

    Every scenario starts from nothing, so one scenario's spending can never be mistaken for
    another's.
    """
    await seed(config.runner)
    imported = await client.import_bundle(
        fixtures.ndjson_bundle(SETUP_IMPORT_RECORDS), sequence=1, tenant_id=probe_tenant
    )
    if imported.body is None or imported.status_code != 201:
        raise RuntimeError(f"could not create the setup job: {imported.status_code}")
    return str(imported.body["job_id"])


def _check_common(
    round_number: int,
    records: RoundRecords,
    *,
    peak_in_flight: int,
    capacity: int,
    provider_cents: int,
) -> list[Violation]:
    """The checks that hold in every scenario, whatever the load was meant to show."""
    violations: list[Violation] = []

    for record in records.over_limit:
        if record.succeeded:
            violations.append(
                Violation(
                    ViolationKind.OVER_LIMIT_ADMITTED,
                    round_number,
                    f"{record.operation} naming more than a bound allows returned "
                    f"{record.status_code}",
                )
            )

    for record in records.cheap:
        if not record.succeeded:
            violations.append(
                Violation(
                    ViolationKind.CHEAP_ENDPOINT_UNANSWERED,
                    round_number,
                    f"an endpoint that needs no provider, allowance, or slot returned "
                    f"{record.status_code}",
                )
            )

    for record in records.work:
        if record.status_code == 503:
            violations.append(
                Violation(
                    ViolationKind.LEGITIMATE_WORK_SHED,
                    round_number,
                    f"valid work from {record.tenant_id} was turned away with 503",
                )
            )

    if peak_in_flight > capacity:
        violations.append(
            Violation(
                ViolationKind.IN_FLIGHT_OVER_CAPACITY,
                round_number,
                f"{peak_in_flight} upstream calls were in flight at once against a capacity of "
                f"{capacity}",
            )
        )

    if provider_cents > fixtures.GLOBAL_SPEND_CAP_CENTS:
        violations.append(
            Violation(
                ViolationKind.SPEND_CAP_BREACHED,
                round_number,
                f"{provider_cents} {fixtures.CURRENCY_LABEL} spent against a cap of "
                f"{fixtures.GLOBAL_SPEND_CAP_CENTS}",
            )
        )

    return violations


async def _accounted_cents(
    client: HalyardHTTP, tenant_ids: tuple[str, ...], sequence: int
) -> dict[str, int]:
    """What the application itself says each tenant has spent, from its own usage endpoint."""
    accounted: dict[str, int] = {}
    for offset, tenant_id in enumerate(tenant_ids):
        record = await client.usage(sequence=sequence + offset, tenant_id=tenant_id)
        if record.body is None:
            raise RuntimeError(f"could not read usage for {tenant_id}: {record.status_code}")
        accounted[tenant_id] = int(record.body["cents_charged"])
    return accounted


def _budget_violations(
    round_number: int,
    *,
    accounted: dict[str, int],
    billed: dict[str, int],
    allowance: int,
) -> list[Violation]:
    """The two money checks, made against the two independent records of it."""
    violations: list[Violation] = []
    for tenant_id, cents in accounted.items():
        if cents > allowance:
            violations.append(
                Violation(
                    ViolationKind.ALLOWANCE_EXCEEDED,
                    round_number,
                    f"{tenant_id} was charged {cents} against a partition of {allowance}",
                )
            )
    for tenant_id, cents in billed.items():
        authorised = accounted.get(tenant_id, 0)
        if cents > authorised:
            violations.append(
                Violation(
                    ViolationKind.UNACCOUNTED_SPEND,
                    round_number,
                    f"the provider billed {tenant_id} {cents} while the application accounted for "
                    f"{authorised}",
                )
            )
    return violations


def _refusals_by_kind(records: tuple[RequestRecord, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        kind = refusal_kind(record.status_code)
        if kind is not None:
            counts[kind.value] = counts.get(kind.value, 0) + 1
    return counts


async def _accounting(
    client: HalyardHTTP,
    config: HarnessConfig,
    *,
    scenario: str,
    description: str,
    bound_in_effect: str,
    rounds: tuple[RoundOutcome, ...],
    roles: dict[str, str],
) -> ScenarioAccounting:
    """Turn what the rounds observed into the accounting.

    Money comes from the provider's own bill, never from the application under study.
    """
    all_records = tuple(record for outcome in rounds for record in outcome.records.all_records)
    ledger = await client.provider_ledger()
    stats = await client.provider_stats()
    accounted = await _accounted_cents(client, tuple(roles), 90_000)

    per_tenant: list[TenantFigures] = []
    billed = {entry.tenant_id: entry for entry in ledger.per_tenant}
    for tenant_id, role in roles.items():
        tenant_records = tuple(r for r in all_records if r.tenant_id == tenant_id)
        entry = billed.get(tenant_id)
        per_tenant.append(
            TenantFigures(
                tenant_id=tenant_id,
                role=role,
                input_bytes=sum(r.input_bytes for r in tenant_records),
                items_admitted=sum(r.records_admitted for r in tenant_records),
                lookups=entry.lookups if entry else 0,
                cents=entry.cents if entry else 0,
                cents_accounted=accounted.get(tenant_id, 0),
                refusals=_refusals_by_kind(tenant_records),
            )
        )

    violations = [v for outcome in rounds for v in outcome.violations]

    # Anyone billed who never appeared in this scenario is being charged for somebody else's work.
    for tenant_id in billed:
        if tenant_id not in roles:
            violations.append(
                Violation(
                    ViolationKind.CROSS_TENANT_CHARGE,
                    rounds[-1].number if rounds else 0,
                    f"{tenant_id} was billed {billed[tenant_id].cents} for work it never asked for",
                )
            )

    cheap = tuple(r for outcome in rounds for r in outcome.records.cheap)
    return ScenarioAccounting(
        scenario=scenario,
        description=description,
        variant=VARIANT,
        mode=config.mode.value,
        replicas=len(config.runner.replica_urls),
        concurrency=config.concurrency,
        rounds=len(rounds),
        bound_in_effect=bound_in_effect,
        input_bytes=sum(r.input_bytes for r in all_records),
        items_admitted=sum(r.records_admitted for r in all_records),
        lookups=ledger.total_lookups,
        cents_total=ledger.total_cents,
        per_tenant=tuple(per_tenant),
        spend_cap_cents=fixtures.GLOBAL_SPEND_CAP_CENTS,
        spend_cap_remaining=fixtures.GLOBAL_SPEND_CAP_CENTS - ledger.total_cents,
        spend_cap_breached=ledger.total_cents > fixtures.GLOBAL_SPEND_CAP_CENTS,
        peak_in_flight=stats.peak_in_flight,
        in_flight_capacity=config.in_flight_capacity,
        cheap_endpoint_issued=len(cheap),
        cheap_endpoint_answered=sum(1 for r in cheap if r.succeeded),
        refusals_by_kind=_refusals_by_kind(all_records),
        violations=tuple(violations),
    )


async def _concurrent_legitimate(client: HalyardHTTP, config: HarnessConfig) -> ScenarioAccounting:
    """Every tenant working at once, comfortably inside every bound."""
    composition = compose(config.concurrency)
    probe_tenant = fixtures.ATTACKER_TENANT_ID
    job_id = await _prepare(client, config, probe_tenant=probe_tenant)
    builder = RoundBuilder(client, probe_tenant_id=probe_tenant)

    tenants = fixtures.BILLABLE_TENANT_IDS
    slots = [
        WorkSlot(tenant_id=tenants[index % len(tenants)], records=config.batch_records)
        for index in range(composition.work)
    ]

    sequence = 100
    outcomes: list[RoundOutcome] = []
    for round_number in range(1, config.rounds + 1):
        records = await builder.run(
            slots=slots,
            cheap_probes=composition.cheap,
            job_id=job_id,
            start_sequence=sequence,
            name_offset=round_number * 10_000,
        )
        sequence += composition.total
        ledger = await client.provider_ledger()
        stats = await client.provider_stats()
        violations = _check_common(
            round_number,
            records,
            peak_in_flight=stats.peak_in_flight,
            capacity=config.in_flight_capacity,
            provider_cents=ledger.total_cents,
        )
        # Nothing here is anywhere near an allowance, so any refused work at all is a finding.
        for record in records.work:
            if not record.succeeded and record.status_code != 503:
                violations.append(
                    Violation(
                        ViolationKind.LEGITIMATE_WORK_SHED,
                        round_number,
                        f"valid work from {record.tenant_id}, well inside every bound, returned "
                        f"{record.status_code}",
                    )
                )
        violations.extend(
            _budget_violations(
                round_number,
                accounted=await _accounted_cents(
                    client, fixtures.BILLABLE_TENANT_IDS, 80_000 + round_number * 10
                ),
                billed={e.tenant_id: e.cents for e in ledger.per_tenant},
                allowance=fixtures.TENANT_ALLOWANCE_CENTS,
            )
        )
        outcomes.append(RoundOutcome(round_number, records, tuple(violations)))

    return await _accounting(
        client,
        config,
        scenario="concurrent_legitimate",
        description=(
            "every fictional tenant working at once, well inside every bound: valid work is "
            "served, over-limit input is refused, and the cheap endpoint answers throughout"
        ),
        bound_in_effect="all five bounds",
        rounds=tuple(outcomes),
        roles=dict.fromkeys(fixtures.BILLABLE_TENANT_IDS, "tenant"),
    )


async def _allowance_isolation(client: HalyardHTTP, config: HarnessConfig) -> ScenarioAccounting:
    """One tenant spends its whole partition; the tenants that spent nothing lose nothing."""
    composition = compose(config.concurrency)
    attacker = fixtures.ATTACKER_TENANT_ID
    bystanders = fixtures.BYSTANDER_TENANT_IDS
    job_id = await _prepare(client, config, probe_tenant=attacker)
    builder = RoundBuilder(client, probe_tenant_id=attacker)

    bystander_slots = [
        WorkSlot(tenant_id=tenant_id, records=config.batch_records)
        for tenant_id in bystanders
        for _ in range(BYSTANDER_SLOTS_EACH)
    ]
    attacker_slot_count = composition.work - len(bystander_slots)
    if attacker_slot_count < 1:
        raise ValueError(
            f"concurrency {config.concurrency} leaves no room for the spending tenant; "
            f"raise it or lower the bystander slots"
        )
    # Bound the *work* a round names, not merely the number of requests it sends. Nineteen requests
    # of five hundred records each would ask a single-CPU fixture for nine and a half thousand
    # lookups simultaneously, and whether that finished inside a deadline would then depend on the
    # host — which is exactly the kind of result this project refuses to assert on.
    attacker_records = max(
        1, min(BOUNDS.max_batch_items, MAX_LOOKUPS_PER_ROUND // attacker_slot_count)
    )
    slots = [
        *(
            WorkSlot(tenant_id=attacker, records=attacker_records)
            for _ in range(attacker_slot_count)
        ),
        *bystander_slots,
    ]

    # Enough rounds to spend the whole partition and then be refused, since that is what this
    # scenario exists to show. Bounded by the same documented ceiling as everything else.
    round_cents = attacker_slot_count * attacker_records * fixtures.LOOKUP_PRICE_CENTS
    rounds_needed = math.ceil(fixtures.TENANT_ALLOWANCE_CENTS / round_cents) + 1
    rounds = min(MAX_ROUNDS, max(config.rounds, rounds_needed))

    sequence = 5_000
    outcomes: list[RoundOutcome] = []
    for round_number in range(1, rounds + 1):
        records = await builder.run(
            slots=slots,
            cheap_probes=composition.cheap,
            job_id=job_id,
            start_sequence=sequence,
            name_offset=round_number * 500_000,
        )
        sequence += composition.total
        ledger = await client.provider_ledger()
        stats = await client.provider_stats()
        violations = _check_common(
            round_number,
            records,
            peak_in_flight=stats.peak_in_flight,
            capacity=config.in_flight_capacity,
            provider_cents=ledger.total_cents,
        )
        # The spending tenant may be refused — that is the control working. A tenant that spent
        # nothing may not be, and that is the partition working.
        for record in records.work:
            if record.tenant_id in bystanders and not record.succeeded:
                violations.append(
                    Violation(
                        ViolationKind.BYSTANDER_AFFECTED,
                        round_number,
                        f"{record.tenant_id} spent nothing and was refused with "
                        f"{record.status_code}",
                    )
                )
        violations.extend(
            _budget_violations(
                round_number,
                accounted=await _accounted_cents(
                    client, fixtures.BILLABLE_TENANT_IDS, 80_000 + round_number * 10
                ),
                billed={e.tenant_id: e.cents for e in ledger.per_tenant},
                allowance=fixtures.TENANT_ALLOWANCE_CENTS,
            )
        )
        outcomes.append(RoundOutcome(round_number, records, tuple(violations)))

    accounting = await _accounting(
        client,
        config,
        scenario="allowance_isolation",
        description=(
            "one tenant spends its entire partition of the fictional budget with the largest "
            "batches allowed, while the tenants that spent nothing keep working untouched"
        ),
        bound_in_effect="partitioned per-tenant allowance, charged in provider lookups",
        rounds=tuple(outcomes),
        roles={
            attacker: "attacker",
            **dict.fromkeys(bystanders, "bystander"),
        },
    )

    # Two facts make this scenario mean what it says, and both are only checkable when the load
    # actually asked for more than the partition holds. At a low concurrency or a single round it
    # may not have, and in that case the boundary was never approached — which is not a finding.
    one_request = attacker_records * fixtures.LOOKUP_PRICE_CENTS
    requested = attacker_slot_count * one_request * rounds
    if requested <= fixtures.TENANT_ALLOWANCE_CENTS:
        return accounting

    extra: list[Violation] = []
    spent = next(f.cents for f in accounting.per_tenant if f.tenant_id == attacker)
    if fixtures.TENANT_ALLOWANCE_CENTS - spent >= one_request:
        # It asked for more than its partition and finished with room for another whole request:
        # something inside every bound was turned away.
        extra.append(
            Violation(
                ViolationKind.LEGITIMATE_WORK_SHED,
                rounds,
                f"{attacker} finished on {spent} of its {fixtures.TENANT_ALLOWANCE_CENTS}-cent "
                f"partition with room for another full request, so valid work was lost",
            )
        )
    if not accounting.refusals_by_kind.get("allowance_exhausted"):
        extra.append(
            Violation(
                ViolationKind.ALLOWANCE_EXCEEDED,
                rounds,
                f"{attacker} asked for {requested} against a partition of "
                f"{fixtures.TENANT_ALLOWANCE_CENTS} and was never refused",
            )
        )
    if not extra:
        return accounting
    return replace(accounting, violations=accounting.violations + tuple(extra))
