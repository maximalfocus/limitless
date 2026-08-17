"""Secure control B: expansion is bounded *during* decompression, and aborts mid-stream.

Two ceilings are proved separately here, because each catches something the other cannot. The
absolute ceiling bounds what a bundle may become in the unit that actually consumes the machine. The
ratio ceiling catches the submission whose compressed size is unremarkable and whose expansion is
not — the one a check on the compressed number would wave straight through.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from limitless import fixtures
from limitless.audit import RefusedOperation
from limitless.config import BOUNDS
from limitless.refusal import LimitReachedError, RefusalKind
from limitless.secure.expansion import read_bounded_bundle

from .test_bounds import make_request

CHUNK = 4096
OPERATION = RefusedOperation.IMPORT_BUNDLE


def chunked(payload: bytes) -> list[bytes]:
    return [payload[i : i + CHUNK] for i in range(0, len(payload), CHUNK)] or [b""]


async def test_a_legitimate_bundle_is_admitted_whole() -> None:
    bundle = fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS)
    request, _ = make_request(chunked(bundle))

    result = await read_bounded_bundle(request, bounds=BOUNDS, operation=OPERATION)

    assert len(result.records) == fixtures.LEGITIMATE_IMPORT_RECORDS
    assert result.compressed_bytes == len(bundle)
    assert result.expansion_ratio < BOUNDS.max_expansion_ratio
    assert result.records[0].company_name == fixtures.company_name(1)


async def test_the_absolute_ceiling_aborts_mid_stream() -> None:
    """The bundle is refused before anything past the ceiling is ever produced."""
    bundle = fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS)
    chunks = chunked(bundle)
    # Ratio effectively disabled, so only the absolute ceiling can be what refuses this.
    bounds = replace(BOUNDS, max_decompressed_bytes=1_048_576, max_expansion_ratio=1_000_000)
    request, delivered = make_request(chunks)

    with pytest.raises(LimitReachedError) as raised:
        await read_bounded_bundle(request, bounds=bounds, operation=OPERATION)

    assert raised.value.kind is RefusalKind.INPUT_TOO_LARGE
    assert len(delivered) < len(chunks), "the whole bundle was read before it was refused"


async def test_the_ratio_ceiling_refuses_what_the_absolute_ceiling_would_admit() -> None:
    """A bundle comfortably under the byte ceiling, and wildly over the ratio."""
    bundle = fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS)
    # Absolute ceiling far above anything this bundle produces; only the ratio can refuse it.
    bounds = replace(BOUNDS, max_decompressed_bytes=64 * 1024 * 1024, max_expansion_ratio=25)
    chunks = chunked(bundle)
    request, delivered = make_request(chunks)

    with pytest.raises(LimitReachedError) as raised:
        await read_bounded_bundle(request, bounds=bounds, operation=OPERATION)

    assert raised.value.kind is RefusalKind.INPUT_TOO_LARGE
    assert len(delivered) < len(chunks)


async def test_the_compressed_body_has_its_own_bound_too() -> None:
    bundle = fixtures.ndjson_bundle(20_000)
    bounds = replace(BOUNDS, max_import_body_bytes=1024)
    request, delivered = make_request(chunked(bundle))

    with pytest.raises(LimitReachedError):
        await read_bounded_bundle(request, bounds=bounds, operation=OPERATION)

    assert len(delivered) == 1


async def test_the_real_expansion_fixture_is_refused_by_the_real_bounds() -> None:
    """No custom bounds: the shipped fixture against the shipped ceilings."""
    bundle = fixtures.repetitive_ndjson_bundle(fixtures.OVER_EXPANDING_IMPORT_RECORDS)
    assert len(bundle) < BOUNDS.max_import_body_bytes, (
        "the fixture must sit inside the compressed body bound, so that the *expansion* control "
        "is what refuses it"
    )
    request, _ = make_request(chunked(bundle))

    with pytest.raises(LimitReachedError):
        await read_bounded_bundle(request, bounds=BOUNDS, operation=OPERATION)


async def test_a_body_that_is_not_gzip_is_a_bad_request() -> None:
    request, _ = make_request([b"this is not a gzip stream at all"])
    with pytest.raises(ValueError):
        await read_bounded_bundle(request, bounds=BOUNDS, operation=OPERATION)


async def test_a_truncated_bundle_is_a_bad_request() -> None:
    bundle = fixtures.ndjson_bundle(fixtures.LEGITIMATE_IMPORT_RECORDS)
    request, _ = make_request(chunked(bundle[: len(bundle) // 2]))
    with pytest.raises(ValueError):
        await read_bounded_bundle(request, bounds=BOUNDS, operation=OPERATION)


async def test_an_empty_bundle_is_a_bad_request() -> None:
    request, _ = make_request([b""])
    with pytest.raises(ValueError):
        await read_bounded_bundle(request, bounds=BOUNDS, operation=OPERATION)
