"""The fictional fixtures are deterministic, and the expansion fixture is what it claims to be."""

from __future__ import annotations

import gzip
from pathlib import Path

from limitless import fixtures


def test_identifiers_are_stable_across_calls() -> None:
    assert fixtures.company_name(42) == fixtures.company_name(42)
    assert fixtures.record_id("TEN-ORCHID", 7) == "TEN-ORCHID-REC-00007"
    assert fixtures.registry_number(3) == fixtures.registry_number(3)


def test_seed_records_cover_every_billable_tenant() -> None:
    records = fixtures.seed_records()
    assert len(records) == len(fixtures.BILLABLE_TENANT_IDS) * fixtures.RECORDS_PER_TENANT
    for tenant_id in fixtures.BILLABLE_TENANT_IDS:
        assert sum(1 for r in records if r.tenant_id == tenant_id) == fixtures.RECORDS_PER_TENANT


def test_the_expired_tenant_holds_no_allowance() -> None:
    assert fixtures.EXPIRED_TENANT_ID not in fixtures.BILLABLE_TENANT_IDS


def test_the_partition_cannot_reach_the_global_cap() -> None:
    """Three tenants at their full share must still sit inside the whole fictional budget.

    This is the property that makes one tenant's exhaustion cost the others nothing.
    """
    total = fixtures.TENANT_ALLOWANCE_CENTS * len(fixtures.BILLABLE_TENANT_IDS)
    assert total <= fixtures.GLOBAL_SPEND_CAP_CENTS


def test_bundles_are_byte_identical_across_calls() -> None:
    assert fixtures.ndjson_bundle(50) == fixtures.ndjson_bundle(50)
    assert fixtures.repetitive_ndjson_bundle(50) == fixtures.repetitive_ndjson_bundle(50)


def test_the_legitimate_bundle_is_an_ordinary_import() -> None:
    bundle = fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS)
    expanded = gzip.decompress(bundle)
    ratio = len(expanded) / len(bundle)
    assert len(expanded.splitlines()) == fixtures.LEGITIMATE_IMPORT_RECORDS
    assert ratio < 25, f"the legitimate fixture must sit inside the ratio bound; got {ratio:.1f}"


def test_the_expansion_fixture_is_single_layer_and_contained() -> None:
    """Repetitive, single-layer, and small enough that its worst case is contained."""
    bundle = fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS)
    expanded = gzip.decompress(bundle)
    ratio = len(expanded) / len(bundle)

    assert ratio > 100, f"the expansion fixture is supposed to expand; got {ratio:.1f}"
    # Far inside the application container's 512 MB limit, deliberately.
    assert len(expanded) < 32 * 1024 * 1024
    # One layer: what comes out is NDJSON text, not another compressed stream.
    assert expanded.startswith(b'{"company_name"')
    assert not expanded.startswith(b"\x1f\x8b")


def test_no_bomb_artifact_is_committed() -> None:
    """The expansion fixture is generated, never stored. Nothing compressed is checked in."""
    root = Path(__file__).resolve().parent.parent
    archives = [
        path
        for pattern in ("*.gz", "*.zip", "*.bz2", "*.xz", "*.tar")
        for path in root.rglob(pattern)
        if ".git" not in path.parts
    ]
    assert archives == [], f"a compressed artifact is committed: {archives}"
