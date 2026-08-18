"""The concurrent load harness: its bounds, its concurrency, its accounting, and its transcript.

The live scenarios at the end are the substance of this slice. Everything before them exists so
that those runs mean something: a harness whose load is not actually concurrent, or whose accounting
reads its figures from the wrong place, would satisfy the secure application's zero-violation
assertion without ever having tested it.

Nothing here asserts on elapsed time.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from limitless import fixtures
from limitless.config import (
    BOUNDS,
    DEFAULT_CONCURRENCY,
    MAX_CONCURRENCY,
    MAX_ROUNDS,
    HarnessConfig,
    ReproductionMode,
    RunnerConfig,
    parse_reproduction_mode,
)
from limitless.harness.accounting import (
    RunAccounting,
    ScenarioAccounting,
    TenantFigures,
    Violation,
    ViolationKind,
    refusal_kind,
)
from limitless.harness.burst import (
    MIN_CONCURRENCY,
    OVER_LIMIT_PROBES,
    compose,
    simultaneously,
)
from limitless.harness.engine import run
from limitless.harness.transcript import render
from limitless.httpclient import RequestRecord
from limitless.refusal import RefusalKind

BASE_ENV = {
    "LIMITLESS_REPLICA_URLS": "http://app-a:8000,http://app-b:8000",
    "LIMITLESS_PROVIDER_URL": "http://coastwise:8000",
}


def record(**overrides: object) -> RequestRecord:
    defaults: dict[str, object] = {
        "sequence": 1,
        "operation": "enrich",
        "tenant_id": fixtures.ATTACKER_TENANT_ID,
        "addressed": "app-a",
        "served_by": "app-a",
        "status_code": 201,
        "request_id": "enrich-00001",
        "input_bytes": 100,
        "retry_after": None,
        "body": None,
    }
    defaults.update(overrides)
    return RequestRecord(**defaults)  # type: ignore[arg-type]


# --- the load is bounded by explicit configured maxima ----------------------------------------


def test_concurrency_and_rounds_are_bounded() -> None:
    for value in (str(MAX_CONCURRENCY + 1), "0", "-4", "1000000", "not-a-number"):
        with pytest.raises(ValueError):
            HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_CONCURRENCY": value})
    for value in (str(MAX_ROUNDS + 1), "0", "-1"):
        with pytest.raises(ValueError):
            HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_ROUNDS": value})


def test_the_defaults_sit_inside_the_ceilings() -> None:
    config = HarnessConfig.from_env(BASE_ENV)
    assert MIN_CONCURRENCY <= config.concurrency <= MAX_CONCURRENCY
    assert config.concurrency == DEFAULT_CONCURRENCY
    assert 1 <= config.rounds <= MAX_ROUNDS
    assert config.batch_records <= BOUNDS.max_batch_items


def test_the_harness_cannot_be_pointed_at_another_host() -> None:
    """The containment property, checked at the layer that would have to be defeated."""
    with pytest.raises(ValueError):
        HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_REPLICA_URLS": "http://example.com:8000"})
    with pytest.raises(ValueError):
        HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_PROVIDER_URL": "http://example.com:8000"})


def test_an_unknown_reproduction_mode_is_refused() -> None:
    with pytest.raises(ValueError):
        HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_MODE": "deterministic"})
    assert parse_reproduction_mode(None) is ReproductionMode.NATURAL
    assert parse_reproduction_mode("natural") is ReproductionMode.NATURAL
    assert parse_reproduction_mode("nonsense") is None


def test_this_slice_ships_the_natural_mode_only() -> None:
    """The deterministic mode and the provider hold it uses belong to later work."""
    assert [mode.value for mode in ReproductionMode] == ["natural"]


def test_in_flight_capacity_follows_the_replicas_addressed() -> None:
    two = HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_REPLICAS": "2"})
    one = HarnessConfig.from_env({**BASE_ENV, "LIMITLESS_REPLICAS": "1"})
    assert two.in_flight_capacity == BOUNDS.max_in_flight_upstream * 2
    assert one.in_flight_capacity == BOUNDS.max_in_flight_upstream


# --- a round is composed, and the parts add up ------------------------------------------------


@pytest.mark.parametrize("concurrency", list(range(MIN_CONCURRENCY, MAX_CONCURRENCY + 1)))
def test_a_round_never_exceeds_its_concurrency_budget(concurrency: int) -> None:
    composition = compose(concurrency)
    assert composition.total == concurrency
    assert composition.over_limit == OVER_LIMIT_PROBES
    assert composition.cheap >= 4
    assert composition.work >= 4


def test_a_concurrency_too_small_to_compose_is_refused() -> None:
    with pytest.raises(ValueError):
        compose(MIN_CONCURRENCY - 1)


# --- the load is genuinely concurrent ----------------------------------------------------------


async def test_every_request_in_a_round_is_released_at_the_same_instant() -> None:
    """The property the whole harness rests on.

    If these ran one after another, the secure application would never be asked to hold anything
    under contention, and every assertion about it would be vacuous.
    """
    in_flight = 0
    peak = 0

    async def factory() -> RequestRecord:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return record()

    count = 16
    released = await simultaneously([factory for _ in range(count)])

    assert len(released) == count
    assert peak == count, f"only {peak} of {count} requests were ever in flight together"


async def test_an_empty_round_is_not_an_error() -> None:
    assert await simultaneously([]) == []


# --- the accounting -----------------------------------------------------------------------------


def figures(tenant_id: str, role: str, **overrides: int) -> TenantFigures:
    defaults = {"input_bytes": 0, "items_admitted": 0, "lookups": 0, "cents": 0}
    defaults.update(overrides)
    return TenantFigures(tenant_id=tenant_id, role=role, **defaults)  # type: ignore[arg-type]


def accounting(**overrides: object) -> ScenarioAccounting:
    defaults: dict[str, object] = {
        "scenario": "example",
        "description": "an example",
        "variant": "secure",
        "mode": "natural",
        "replicas": 2,
        "concurrency": 24,
        "rounds": 3,
        "bound_in_effect": "all five bounds",
        "input_bytes": 1_000,
        "items_admitted": 200,
        "lookups": 200,
        "cents_total": 800,
        "per_tenant": (
            figures(fixtures.ATTACKER_TENANT_ID, "attacker", cents=800, lookups=200),
            figures(fixtures.BYSTANDER_TENANT_IDS[0], "bystander"),
        ),
        "spend_cap_cents": fixtures.GLOBAL_SPEND_CAP_CENTS,
        "spend_cap_remaining": fixtures.GLOBAL_SPEND_CAP_CENTS - 800,
        "spend_cap_breached": False,
        "peak_in_flight": 20,
        "in_flight_capacity": 96,
        "cheap_endpoint_issued": 12,
        "cheap_endpoint_answered": 12,
        "refusals_by_kind": {},
        "violations": (),
    }
    defaults.update(overrides)
    return ScenarioAccounting(**defaults)  # type: ignore[arg-type]


def test_the_amplification_ratio_is_cents_per_byte_of_input() -> None:
    assert accounting(input_bytes=1_000, cents_total=800).amplification_ratio == 0.8
    assert accounting(input_bytes=0, cents_total=800).amplification_ratio == 0.0


def test_the_verdict_follows_the_violations() -> None:
    assert accounting().verdict == "bounded"
    clean = accounting()
    assert (
        accounting(
            violations=(Violation(ViolationKind.SPEND_CAP_BREACHED, 1, "over"),),
        ).verdict
        == "unbounded"
    )
    assert clean.verdict == "bounded"


def test_bystanders_are_reported_separately() -> None:
    """When a budget is not partitioned their loss is the point, so it cannot be averaged away."""
    reported = accounting().bystanders
    assert [f.tenant_id for f in reported] == [fixtures.BYSTANDER_TENANT_IDS[0]]


def test_a_run_is_unbounded_if_any_scenario_is() -> None:
    run_accounting = RunAccounting(
        variant="secure",
        mode="natural",
        scenarios=(
            accounting(),
            accounting(violations=(Violation(ViolationKind.BYSTANDER_AFFECTED, 2, "refused"),)),
        ),
    )
    assert run_accounting.verdict == "unbounded"
    assert len(run_accounting.violations) == 1


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (413, RefusalKind.INPUT_TOO_LARGE),
        (429, RefusalKind.ALLOWANCE_EXHAUSTED),
        (503, RefusalKind.CAPACITY_SHED),
        (201, None),
        (200, None),
        (401, None),
        (404, None),
    ],
)
def test_refusals_are_classified_by_kind(status: int, kind: RefusalKind | None) -> None:
    assert refusal_kind(status) is kind


# --- the transcript ------------------------------------------------------------------------------


def test_the_transcript_leads_with_the_ratio_and_carries_no_credential() -> None:
    config = HarnessConfig.from_env(BASE_ENV)
    text = render(RunAccounting("secure", "natural", (accounting(),)), config)

    assert "AMPLIFICATION RATIO" in text
    assert "VERDICT: BOUNDED" in text
    assert fixtures.PROVIDER_NAME in text
    # Occupancy is an observation, and the transcript has to say so where it appears.
    assert "(observed, not asserted)" in text
    assert "limitless-demo-token-" not in text

    # The only place a performance word may appear is the sentence disclaiming it. Anywhere else
    # would be this project reporting a figure it has no business measuring.
    for forbidden in ("throughput", "latency", "elapsed", "per second", "seconds", " ms"):
        offending = [
            line
            for line in text.lower().splitlines()
            if forbidden in line and "makes no" not in line
        ]
        assert offending == [], f"the transcript reports {forbidden!r}: {offending}"


def test_the_transcript_names_every_violation_it_found() -> None:
    config = HarnessConfig.from_env(BASE_ENV)
    violation = Violation(ViolationKind.BYSTANDER_AFFECTED, 2, "TEN-BASIL was refused with 429")
    text = render(
        RunAccounting("secure", "natural", (accounting(violations=(violation,)),)), config
    )
    assert "VERDICT: UNBOUNDED" in text
    assert "bystander_affected" in text
    assert "TEN-BASIL was refused with 429" in text


# --- the live runs -------------------------------------------------------------------------------


def live_config(concurrency: int, rounds: int, replicas: int = 2) -> HarnessConfig:
    runner = RunnerConfig.from_env()
    config = HarnessConfig.from_env()
    return replace(
        config,
        runner=replace(runner, replica_urls=runner.replica_urls[:replicas]),
        concurrency=concurrency,
        rounds=rounds,
    )


async def _assert_secure_side_is_clean(config: HarnessConfig) -> RunAccounting:
    result = await run(config)

    assert result.verdict == "bounded", [str(v) for v in result.violations]
    assert result.violations == ()
    for scenario in result.scenarios:
        assert scenario.cheap_endpoint_issued > 0
        assert scenario.cheap_endpoint_answered == scenario.cheap_endpoint_issued
        assert not scenario.spend_cap_breached
        assert scenario.peak_in_flight <= scenario.in_flight_capacity
        assert scenario.lookups > 0, "the harness did not generate any load at all"
        for tenant in scenario.per_tenant:
            assert tenant.cents <= fixtures.TENANT_ALLOWANCE_CENTS
    return result


async def test_the_secure_application_holds_at_the_highest_configured_concurrency(
    fresh_state: None,
) -> None:
    """Acceptance, at two replicas: zero violations in every round."""
    result = await _assert_secure_side_is_clean(live_config(MAX_CONCURRENCY, 3, replicas=2))
    names = [scenario.scenario for scenario in result.scenarios]
    assert names == ["concurrent_legitimate", "allowance_isolation"]


async def test_the_secure_application_holds_at_one_replica_too(fresh_state: None) -> None:
    """Acceptance, at one replica: the replica count is a real run parameter."""
    await _assert_secure_side_is_clean(live_config(MAX_CONCURRENCY, 3, replicas=1))


async def test_the_burst_reaches_every_replica_it_addresses(fresh_state: None) -> None:
    result = await run(live_config(MAX_CONCURRENCY, 1, replicas=2))
    assert result.scenarios[0].replicas == 2


async def test_a_spending_tenant_consumes_its_whole_partition_and_no_more(
    fresh_state: None,
) -> None:
    """FR-010 under concurrency: the heavy legitimate tenant is served, then bounded."""
    result = await run(live_config(MAX_CONCURRENCY, 3, replicas=2))
    isolation = next(s for s in result.scenarios if s.scenario == "allowance_isolation")

    attacker = next(f for f in isolation.per_tenant if f.role == "attacker")
    one_request = BOUNDS.max_batch_items * fixtures.LOOKUP_PRICE_CENTS

    assert attacker.cents <= fixtures.TENANT_ALLOWANCE_CENTS
    assert fixtures.TENANT_ALLOWANCE_CENTS - attacker.cents < one_request
    assert isolation.refusals_by_kind.get("allowance_exhausted", 0) > 0
    assert "capacity_shed" not in isolation.refusals_by_kind, (
        "valid work was turned away by the capacity control"
    )

    for bystander in isolation.bystanders:
        assert bystander.cents > 0, "a bystander did no work, so nothing was proved about it"
        assert bystander.refusals == {}, f"{bystander.tenant_id} was refused"


async def test_every_over_limit_probe_is_refused_under_load(fresh_state: None) -> None:
    result = await run(live_config(MAX_CONCURRENCY, 2, replicas=2))
    for scenario in result.scenarios:
        assert scenario.refusals_by_kind.get("input_too_large", 0) > 0, (
            "the over-limit probes were never refused, so the bounds were not exercised"
        )
