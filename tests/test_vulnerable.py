"""The unbounded ladder: the shapes, the generator, and the two gates in front of them.

The live shapes at the end run against the opt-in vulnerable replicas and are skipped when that
profile is not up — unless ``LIMITLESS_REQUIRE_VULNERABLE`` is set, which the verification boundary
does, so a skipped shape can never be mistaken for a passing one.

Here a defect that fails to reproduce is the failure. Nothing below asserts on elapsed time: the
deterministic mode holds the provider and counts, so every question is answered at a known instant.
"""

from __future__ import annotations

import gzip
import os
import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest

from limitless import fixtures
from limitless.config import HarnessConfig, ReproductionMode, RunnerConfig
from limitless.generate_expansion_fixture import (
    MAX_COMPRESSED_BYTES,
    MAX_DECOMPRESSED_BYTES,
    build,
    check,
    describe,
)
from limitless.harness.vulnerable import (
    SHAPES,
    client_names_the_work,
    expansion,
    unbounded_in_flight,
    undivided_budget,
)
from limitless.httpclient import HalyardHTTP
from limitless.vulnerable import shapes
from limitless.vulnerable.acknowledgement import REFUSAL, acknowledged, require_acknowledgement

# --- the gates ----------------------------------------------------------------------------------


def test_the_acknowledgement_is_exact_and_uninterpreted() -> None:
    for refused in ("", "1", "yes", "y", "on", "TRUE ", " true", "please"):
        assert not acknowledged({"ALLOW_VULNERABLE_DEMO": refused}) or refused.strip().lower() == (
            "true"
        )
    assert acknowledged({"ALLOW_VULNERABLE_DEMO": "true"})


def test_the_refusal_says_what_to_do_without_being_an_invitation() -> None:
    assert "never be deployed" in REFUSAL
    assert "two deliberate actions" in REFUSAL
    with pytest.raises(RuntimeError):
        require_acknowledgement({"ALLOW_VULNERABLE_DEMO": "no"})


# --- the build-time expansion fixture ------------------------------------------------------------


def test_the_generated_fixture_passes_its_own_containment_checks() -> None:
    assert check(build()) == []


def test_the_fixture_is_small_on_the_wire_and_ruinous_when_opened() -> None:
    bundle = build()
    raw = gzip.decompress(bundle)

    assert len(bundle) <= MAX_COMPRESSED_BYTES, "it must look like an unremarkable upload"
    assert len(raw) <= MAX_DECOMPRESSED_BYTES, "its worst case must stay contained"
    admitted = fixtures.EXPANSION_FIXTURE_RECORDS * fixtures.LOOKUP_PRICE_CENTS
    assert admitted >= fixtures.GLOBAL_SPEND_CAP_CENTS * 10


def test_the_fixture_is_one_layer_of_ordinary_records() -> None:
    """The expansion comes from repetition, never from nesting."""
    raw = gzip.decompress(build())
    assert raw.startswith(b'{"company_name"')
    assert not raw.startswith(b"\x1f\x8b")
    assert b"\x1f\x8b" not in raw[:4096]


def test_the_generator_reports_the_ratio_it_achieved() -> None:
    summary = describe(build())
    assert "single layer" in summary
    assert "the whole fictional monthly cap" in summary


def test_the_fixture_is_generated_and_never_committed() -> None:
    root = pathlib.Path(__file__).resolve().parent.parent
    committed = [
        path
        for pattern in ("*.gz", "*.zip", "*.bz2", "*.xz", "*.tar")
        for path in root.rglob(pattern)
        if ".git" not in path.parts and "artifacts" not in path.parts
    ]
    assert committed == []


# --- the shapes, as code -------------------------------------------------------------------------


def test_the_size_check_is_on_the_compressed_number() -> None:
    """It refuses a large *compressed* body and waves through what that body becomes."""
    bundle = build()
    assert len(bundle) <= shapes.COMPRESSED_BODY_LIMIT_BYTES, (
        "the fixture must pass the naive check, or it proves nothing about the check"
    )
    assert len(gzip.decompress(bundle)) > shapes.COMPRESSED_BODY_LIMIT_BYTES * 100


def test_decompressing_completely_counts_everything_it_finds() -> None:
    result = shapes.decompress_completely(fixtures.ndjson_bundle(1_000))
    assert result.records == 1_000
    assert result.expansion_ratio > 1


def test_a_page_size_is_taken_at_face_value() -> None:
    assert shapes.whatever_page_size_was_asked_for("1000000", default=200) == 1_000_000
    assert shapes.whatever_page_size_was_asked_for(None, default=200) == 200


def test_a_bad_bundle_is_still_a_bad_request() -> None:
    with pytest.raises(ValueError):
        shapes.decompress_completely(b"not gzip at all")


# --- the live ladder ------------------------------------------------------------------------------


def vulnerable_reachable(url: str) -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            return client.get(f"{url}/healthz").status_code == httpx.codes.OK
    except httpx.HTTPError:
        return False


@pytest.fixture
async def vulnerable_client(config: RunnerConfig) -> AsyncIterator[HalyardHTTP]:
    """One vulnerable replica, so the connection arithmetic in shape four is exact."""
    urls = config.vulnerable_replica_urls
    if not urls or not vulnerable_reachable(urls[0]):
        if os.environ.get("LIMITLESS_REQUIRE_VULNERABLE"):
            pytest.fail(
                "LIMITLESS_REQUIRE_VULNERABLE is set but the vulnerable profile is not running"
            )
        pytest.skip("the vulnerable opt-in profile is not running")
    async with HalyardHTTP(
        urls[:1], provider_url=config.provider_url, timeout=config.request_timeout_seconds
    ) as client:
        await client.wait_until_ready()
        yield client


@pytest.fixture
def harness_config() -> HarnessConfig:
    config = HarnessConfig.from_env()
    assert config.mode is ReproductionMode.DETERMINISTIC, (
        "the default mode is the deterministic one"
    )
    return config


async def test_the_client_names_the_work(
    vulnerable_client: HalyardHTTP, harness_config: HarnessConfig
) -> None:
    """One request naming fifty thousand records bills four fifths of the whole cap."""
    outcome = await client_names_the_work(vulnerable_client, harness_config)
    assert outcome.reproduced, outcome.detail
    share = outcome.cents / fixtures.GLOBAL_SPEND_CAP_CENTS
    assert 0.7 <= share <= 0.9, f"expected about four fifths of the cap, got {share:.2f}"
    assert outcome.cost_per_input_byte > 0


async def test_an_un_partitioned_budget_costs_the_bystanders(
    vulnerable_client: HalyardHTTP, harness_config: HarnessConfig
) -> None:
    """The tenants who spent nothing are the ones who find out."""
    outcome = await undivided_budget(vulnerable_client, harness_config)
    assert outcome.reproduced, outcome.detail
    assert any("REFUSED" in line for line in outcome.detail)
    assert outcome.cents == fixtures.GLOBAL_SPEND_CAP_CENTS


async def test_expansion_admits_work_worth_many_times_the_cap(
    vulnerable_client: HalyardHTTP, harness_config: HarnessConfig
) -> None:
    outcome = await expansion(vulnerable_client, harness_config)
    assert outcome.reproduced, outcome.detail
    assert outcome.input_bytes <= 200_000
    assert outcome.cents >= fixtures.GLOBAL_SPEND_CAP_CENTS * 10

    # The obligation is durable: it is readable after the request that took it on has gone.
    usage = await vulnerable_client.usage(sequence=9999, tenant_id=fixtures.ATTACKER_TENANT_ID)
    assert usage.body is not None
    assert int(usage.body["cents_charged"]) == outcome.cents


async def test_unbounded_in_flight_work_takes_down_an_innocent_endpoint(
    vulnerable_client: HalyardHTTP, harness_config: HarnessConfig
) -> None:
    """The endpoint that fails needs no provider, no budget, and no expensive path."""
    outcome = await unbounded_in_flight(vulnerable_client, harness_config)
    assert outcome.reproduced, outcome.detail
    assert any("answered 5" in line or "answered 4" in line for line in outcome.detail)


async def test_every_shape_in_the_ladder_reproduces(
    vulnerable_client: HalyardHTTP, harness_config: HarnessConfig
) -> None:
    """A shape that does not reproduce has not been demonstrated, whatever the code looks like."""
    assert len(SHAPES) == 4
