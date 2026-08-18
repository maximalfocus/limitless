"""The repairs that fail, the controls that mark the boundary, and the comparison across variants.

The arithmetic of each repair is tested directly, because that is where the lesson is and because a
pure test of it is repeatable in a way a live one cannot be: two of these repairs keep their state
**in the process**, and there is deliberately no endpoint to reset it. The live end-to-end
demonstrations run in the harness suite, against freshly started replicas.

Nothing here asserts on elapsed time.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest

from limitless import fixtures
from limitless.config import RunnerConfig
from limitless.httpclient import HalyardHTTP
from limitless.vulnerable.halffixes import (
    CLIENT_ID_HEADER,
    HALF_FIX_HEADER,
    REQUESTS_PER_WINDOW,
    HalfFix,
    HalfFixState,
    InProcessAllowance,
    RequestRateLimiter,
    parse_half_fix,
)

ALLOWANCE = fixtures.TENANT_ALLOWANCE_CENTS


# --- half-fix 1: the limit counts the wrong unit -----------------------------------------------


def test_the_rate_limiter_is_real() -> None:
    """It has to work, or its failure teaches nothing."""
    limiter = RequestRateLimiter()
    for index in range(REQUESTS_PER_WINDOW):
        assert limiter.allow("TEN-ORCHID", now=index * 0.001), f"refused request {index + 1}"
    assert not limiter.allow("TEN-ORCHID", now=0.1)
    assert limiter.report.allowed == REQUESTS_PER_WINDOW
    assert limiter.report.refused == 1


def test_the_rate_limiter_is_per_tenant() -> None:
    limiter = RequestRateLimiter()
    for index in range(REQUESTS_PER_WINDOW):
        limiter.allow("TEN-ORCHID", now=index * 0.001)
    assert limiter.allow("TEN-BASIL", now=0.1), "one tenant's traffic exhausted another's limit"


def test_the_rate_limiter_reports_zero_violations_while_the_budget_drains() -> None:
    """Sixty requests naming two thousand records each is 120 000 lookups, inside the limit."""
    limiter = RequestRateLimiter()
    admitted = 0
    for index in range(REQUESTS_PER_WINDOW):
        assert limiter.allow("TEN-ORCHID", now=index * 0.001)
        admitted += 2_000
    assert limiter.report.violations == 0
    assert admitted * fixtures.LOOKUP_PRICE_CENTS > fixtures.GLOBAL_SPEND_CAP_CENTS, (
        "a caller entirely inside the limit can still name more than the whole budget"
    )


def test_the_window_expires_so_the_limiter_is_a_rate_and_not_a_quota() -> None:
    limiter = RequestRateLimiter()
    for index in range(REQUESTS_PER_WINDOW):
        limiter.allow("TEN-ORCHID", now=index * 0.001)
    assert not limiter.allow("TEN-ORCHID", now=1.0)
    assert limiter.allow("TEN-ORCHID", now=1_000.0)


# --- half-fix 2 and 3: the limiter's scope and its key ------------------------------------------


def test_an_in_process_allowance_holds_exactly_in_one_process() -> None:
    allowance = InProcessAllowance()
    spent = 0
    while allowance.charge("TEN-ORCHID", 8_000):
        spent += 8_000
    assert spent == ALLOWANCE, "the allowance is correct, and correctly enforced, in one process"


def test_the_same_allowance_admits_double_across_two_processes() -> None:
    """The whole of `FR-017`, as arithmetic: two counters each enforce the whole allowance.

    Nothing is corrupted and nothing is lost. The budget is simply enforced twice, in parallel,
    because the only thing that changed is how many processes are doing the enforcing.
    """
    one_process = InProcessAllowance()
    single = 0
    while one_process.charge("TEN-ORCHID", 8_000):
        single += 8_000

    replicas = [InProcessAllowance(), InProcessAllowance()]
    double = 0
    for index in range(100):
        if replicas[index % 2].charge("TEN-ORCHID", 8_000):
            double += 8_000

    assert single == ALLOWANCE
    assert double == ALLOWANCE * 2
    assert double == single * 2


def test_a_caller_keyed_allowance_is_defeated_by_rotating_the_value() -> None:
    allowance = InProcessAllowance()
    steady = sum(8_000 for _ in iter(lambda: allowance.charge("steady", 8_000), False))
    rotated = sum(8_000 for index in range(100) if allowance.charge(f"rotated-{index}", 8_000))
    assert steady == ALLOWANCE
    assert rotated > ALLOWANCE * 10, "a fresh bucket is one header away"


def test_the_key_is_server_derived_unless_the_repair_asks_for_the_callers() -> None:
    state = HalfFixState()
    for half_fix in (HalfFix.NONE, HalfFix.REQUEST_RATE_LIMIT, HalfFix.IN_PROCESS_ALLOWANCE):
        assert state.key_for(half_fix, tenant_id="TEN-ORCHID", client_id="anything") == "TEN-ORCHID"
    assert (
        state.key_for(HalfFix.CALLER_KEYED_ALLOWANCE, tenant_id="TEN-ORCHID", client_id="mine")
        == "mine"
    )


@pytest.mark.parametrize("name", [half_fix.value for half_fix in HalfFix])
def test_every_repair_can_be_selected_by_name(name: str) -> None:
    assert parse_half_fix(name) is HalfFix(name)


def test_an_unknown_repair_is_an_error_rather_than_a_silent_default() -> None:
    assert parse_half_fix(None) is HalfFix.NONE
    assert parse_half_fix("") is HalfFix.NONE
    assert parse_half_fix("nonsense") is None


# --- the live comparison across variants ---------------------------------------------------------


def vulnerable_reachable(url: str) -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            return client.get(f"{url}/healthz").status_code == httpx.codes.OK
    except httpx.HTTPError:
        return False


@pytest.fixture
async def both_variants(
    config: RunnerConfig, fresh_state: None
) -> AsyncIterator[tuple[HalyardHTTP, HalyardHTTP]]:
    urls = config.vulnerable_replica_urls
    if not urls or not vulnerable_reachable(urls[0]):
        if os.environ.get("LIMITLESS_REQUIRE_VULNERABLE"):
            pytest.fail("LIMITLESS_REQUIRE_VULNERABLE is set but the vulnerable profile is not up")
        pytest.skip("the vulnerable opt-in profile is not running")
    async with (
        HalyardHTTP(
            config.replica_urls[:1], provider_url=config.provider_url, timeout=60
        ) as secure,
        HalyardHTTP(urls[:1], provider_url=config.provider_url, timeout=60) as unbounded,
    ):
        await secure.wait_until_ready()
        await unbounded.wait_until_ready()
        yield secure, unbounded


async def test_a_legitimate_request_gets_an_identical_answer_from_both_variants(
    both_variants: tuple[HalyardHTTP, HalyardHTTP],
) -> None:
    """`FR-010`: the fix changed the unbounded behaviour and nothing else.

    Same request, same payload, same state transition. A reader comparing the two variants on
    ordinary work should find nothing at all to look at — which is what makes the difference on
    *extraordinary* work the only difference there is.
    """
    secure, unbounded = both_variants
    names = [fixtures.company_name(i) for i in range(1, 21)]

    for tenant, client in (
        (fixtures.BYSTANDER_TENANT_IDS[0], secure),
        (fixtures.BYSTANDER_TENANT_IDS[1], unbounded),
    ):
        record = await client.enrich(names, sequence=1, tenant_id=tenant)
        assert record.status_code == 201
        assert record.records_admitted == 20
        assert record.cents_charged == 20 * fixtures.LOOKUP_PRICE_CENTS
        assert record.body is not None
        assert len(record.body["results"]) == 20

    secure_page = await secure.list_records(
        sequence=2, tenant_id=fixtures.ATTACKER_TENANT_ID, page_size=50
    )
    unbounded_page = await unbounded.list_records(
        sequence=2, tenant_id=fixtures.ATTACKER_TENANT_ID, page_size=50
    )
    assert secure_page.body == unbounded_page.body, (
        "the same legitimate listing must read identically from both variants"
    )


async def test_a_legitimate_import_is_identical_across_variants(
    both_variants: tuple[HalyardHTTP, HalyardHTTP],
) -> None:
    secure, unbounded = both_variants
    bundle = fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS)

    results = []
    for tenant, client in (
        (fixtures.BYSTANDER_TENANT_IDS[0], secure),
        (fixtures.BYSTANDER_TENANT_IDS[1], unbounded),
    ):
        record = await client.import_bundle(bundle, sequence=1, tenant_id=tenant)
        assert record.status_code == 201
        assert record.body is not None
        results.append((record.records_admitted, record.cents_charged))
    assert results[0] == results[1], "the same legitimate import must be admitted identically"


async def test_the_repairs_are_reachable_only_on_the_unbounded_variant(
    both_variants: tuple[HalyardHTTP, HalyardHTTP],
) -> None:
    """The secure application has no half-fix header, and offering it one changes nothing."""
    secure, _ = both_variants
    record = await secure.send(
        "POST",
        "/v1/enrich",
        operation="enrich",
        sequence=1,
        tenant_id=fixtures.BYSTANDER_TENANT_IDS[0],
        json_body={"records": [{"company_name": fixtures.company_name(1)}]},
        headers={HALF_FIX_HEADER: HalfFix.IN_PROCESS_ALLOWANCE.value, CLIENT_ID_HEADER: "mine"},
    )
    assert record.status_code == 201, "the secure application ignores the repair headers entirely"
