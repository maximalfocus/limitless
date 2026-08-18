"""The comparison table, and the walkthrough's required content.

The engine is exercised directly, without simulating terminal input, because that is what makes the
demonstration's headline output testable at all.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from limitless import fixtures
from limitless.compare.engine import ComparisonRow, _verdict, cap_multiple, worst_ratio
from limitless.compare.table import render
from limitless.config import HarnessConfig
from limitless.harness.vulnerable import ShapeOutcome
from limitless.httpclient import RequestRecord

ROOT = pathlib.Path(__file__).resolve().parent.parent
WALKTHROUGH = (ROOT / "WALKTHROUGH.md").read_text()

BASE_ENV = {
    "LIMITLESS_REPLICA_URLS": "http://app-a:8000,http://app-b:8000",
    "LIMITLESS_PROVIDER_URL": "http://coastwise:8000",
}


def row(**overrides: object) -> ComparisonRow:
    defaults: dict[str, object] = {
        "scenario": "example",
        "variant": "secure",
        "bound_in_effect": "all five bounds",
        "mode": "deterministic",
        "replicas": 2,
        "concurrency": 24,
        "input_bytes": 1_000,
        "items_admitted": 200,
        "lookups": 200,
        "cents": 800,
        "cheap_issued": 12,
        "cheap_answered": 12,
        "refusals": {},
        "verdict": "bounded",
    }
    defaults.update(overrides)
    return ComparisonRow(**defaults)  # type: ignore[arg-type]


# --- the columns FR-026 requires ----------------------------------------------------------------


def test_every_required_column_appears_in_the_table() -> None:
    rendered = render((row(), row(variant="vulnerable", verdict="unbounded")), _config())
    for column in (
        "scenario",
        "variant",
        "mode",
        "rep",
        "conc",
        "input B",
        "items",
        "lookups",
        "cents",
        "ratio",
        "cheap",
        "refusals",
        "verdict",
    ):
        assert column in rendered, f"the comparison omits {column!r}"
    assert "bound in effect:" in rendered


def _config() -> HarnessConfig:
    return HarnessConfig.from_env(BASE_ENV)


def test_the_ratio_is_cents_per_byte_of_input() -> None:
    assert row(input_bytes=1_000, cents=800).amplification_ratio == 0.8
    assert row(input_bytes=0, cents=800).amplification_ratio == 0.0


def test_cheap_endpoint_availability_is_reported_as_a_fraction() -> None:
    assert row(cheap_issued=12, cheap_answered=11).cheap_endpoint == "11/12"
    assert row(cheap_issued=0).cheap_endpoint == "—"


def test_the_worst_ratio_is_the_headline() -> None:
    rows = (row(cents=800), row(variant="vulnerable", cents=800_000, verdict="unbounded"))
    assert worst_ratio(rows) == 800.0
    assert cap_multiple(rows[1]) == 800_000 / fixtures.GLOBAL_SPEND_CAP_CENTS


def test_a_control_that_held_is_not_called_unbounded() -> None:
    """Ruling a repair out and demonstrating an unbounded path are different claims."""
    control = ShapeOutcome(
        shape="control: something",
        headline="",
        reproduced=True,
        detail=[],
        input_bytes=0,
        cents=0,
        kind="control",
    )
    shape = ShapeOutcome(
        shape="a shape", headline="", reproduced=True, detail=[], input_bytes=0, cents=0
    )
    missed = ShapeOutcome(
        shape="a shape", headline="", reproduced=False, detail=[], input_bytes=0, cents=0
    )
    assert _verdict(control) == "boundary held"
    assert _verdict(shape) == "unbounded"
    assert _verdict(missed) == "did not reproduce"


def test_the_table_makes_no_performance_claim() -> None:
    rendered = render((row(),), _config()).lower()
    for forbidden in ("throughput", "latency", "elapsed", "per second", " ms"):
        offending = [
            line for line in rendered.splitlines() if forbidden in line and "makes no" not in line
        ]
        assert offending == [], f"the comparison reports {forbidden!r}: {offending}"


def test_the_table_says_what_the_unbounded_rows_are() -> None:
    rendered = render((row(),), _config())
    assert "must" in rendered and "never be deployed" in rendered
    assert "cannot be pointed at anything but" in rendered
    assert "fictional" in rendered


def test_verbose_mode_shows_the_per_request_records() -> None:
    record = RequestRecord(
        sequence=7,
        operation="enrich",
        tenant_id=fixtures.ATTACKER_TENANT_ID,
        addressed="vuln-a",
        served_by="vuln-a",
        status_code=201,
        request_id="enrich-00007",
        input_bytes=1_234,
        retry_after=None,
        body={"records_admitted": 20, "cents_charged": 80},
    )
    with_records = row(variant="vulnerable", verdict="unbounded", records=(record,))

    quiet = render((with_records,), _config())
    loud = render((with_records,), _config(), verbose=True)

    assert "PER-REQUEST RECORDS" not in quiet
    assert "PER-REQUEST RECORDS" in loud
    assert "enrich-00007" not in loud, "the table shows records, not request ids"
    assert "vuln-a" in loud
    assert "1,234" in loud


# --- the walkthrough FR-030 requires --------------------------------------------------------------


@pytest.mark.parametrize(
    "identifier", ["API4:2023", "CWE-770", "CWE-400", "CWE-799", "CWE-405", "CWE-409", "CWE-789"]
)
def test_the_walkthrough_carries_the_whole_taxonomy_mapping(identifier: str) -> None:
    assert identifier in WALKTHROUGH


def test_the_walkthrough_states_the_a04_caveat_in_the_required_terms() -> None:
    """`A04:2021` is claimed through `CWE-799` alone, and the text has to say why."""
    assert "A04:2021" in WALKTHROUGH
    assert "partial" in WALKTHROUGH
    assert "`CWE-799` alone" in WALKTHROUGH
    assert "published member of the A04:2021 CWE mapping" in WALKTHROUGH
    assert "appear in **no**\n2021 Top-10 category" in WALKTHROUGH
    assert "rather than implying whole-demo" in WALKTHROUGH


def test_the_walkthrough_names_llm10_as_a_relative_and_does_not_claim_it() -> None:
    assert "LLM10" in WALKTHROUGH
    assert "not**\nclaimed" in WALKTHROUGH or "is **not** claimed" in WALKTHROUGH


@pytest.mark.parametrize(
    "dimension", ["how big", "how many", "how much it becomes", "how often", "how many at once"]
)
def test_the_walkthrough_explains_all_five_dimensions(dimension: str) -> None:
    assert dimension in WALKTHROUGH


def test_the_walkthrough_explains_amplification_as_a_ratio() -> None:
    assert "Amplification is a ratio" in WALKTHROUGH
    assert "cents per input byte" in WALKTHROUGH
    assert "856" in WALKTHROUGH


@pytest.mark.parametrize(
    "shape",
    [
        "the client names the work",
        "unbounded repetition against an un-partitioned budget",
        "expansion, checked in the wrong place on the wrong number",
        "unbounded in-flight work and no deadline",
    ],
)
def test_the_walkthrough_carries_the_four_shape_ladder(shape: str) -> None:
    assert shape in WALKTHROUGH.lower() or shape.capitalize() in WALKTHROUGH


def test_every_shape_is_paired_with_the_repair_that_fails_against_it() -> None:
    assert WALKTHROUGH.count("The repair that fails") == 4


@pytest.mark.parametrize(
    "control",
    [
        "Every request is authenticated and authorized",
        "One of each request is perfectly correct",
        "More capacity is not a fix",
    ],
)
def test_the_walkthrough_carries_the_three_negative_controls(control: str) -> None:
    assert control in WALKTHROUGH


def test_the_walkthrough_carries_all_five_secure_controls_with_trade_offs() -> None:
    assert "The trade-off" in WALKTHROUGH or "the trade-off" in WALKTHROUGH
    for control in (
        "bounds while reading",
        "item and page bounds",
        "expansion bounds",
        "a cost-based quota",
        "bounded concurrency and cancelling deadlines",
    ):
        assert control in WALKTHROUGH
    assert "Why each belongs at the edge" in WALKTHROUGH


def test_the_walkthrough_names_the_out_of_scope_neighbours() -> None:
    for neighbour in ("CWE-1333", "CWE-776", "API6:2023", "service mesh", "circuit breaker"):
        assert neighbour.lower() in WALKTHROUGH.lower(), neighbour


def test_the_walkthrough_carries_the_instrumentation_note() -> None:
    assert "changes only *when* work is released" in WALKTHROUGH
    assert "never changes *whether*" in WALKTHROUGH


def test_the_walkthrough_carries_the_cross_references() -> None:
    assert "atomic conditional write" in WALKTHROUGH
    assert "known-correct tool" in WALKTHROUGH
    assert "two-mode reproduction contract" in WALKTHROUGH
    assert "client-side page-size cap is not a server-side bound" in WALKTHROUGH


def test_the_walkthrough_warns_conspicuously_and_repeatedly() -> None:
    assert WALKTHROUGH.lower().count("never be deployed") >= 2
    assert WALKTHROUGH.startswith("# limitless")
    assert "deliberately vulnerable" in WALKTHROUGH.lower()


def test_the_walkthrough_makes_no_performance_claim() -> None:
    """It may name performance only to disclaim it, never to report it."""
    disclaiming = ("no ", "none", "not ", "never", "makes no", "out of scope", "deliberately")
    for term in ("throughput", "latency", "benchmark", "requests per second", "faster"):
        for line in WALKTHROUGH.lower().splitlines():
            if term in line:
                assert any(marker in line for marker in disclaiming), (
                    f"the walkthrough reports {term!r}: {line.strip()!r}"
                )
    assert "makes no" in WALKTHROUGH and "performance claim" in WALKTHROUGH


def test_the_walkthrough_is_wholly_fictional() -> None:
    """No real company, provider, or credential may appear."""
    assert fixtures.COMPANY_NAME in WALKTHROUGH or "fictional" in WALKTHROUGH
    assert "limitless-demo-token-" not in WALKTHROUGH
    assert not re.search(r"https?://(?!github\\.com)", WALKTHROUGH), "it links to an outside host"
