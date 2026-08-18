"""Secure control A: the bound is enforced *while reading*, not after buffering.

The distinction this file exists to prove is the whole point of the control. A size check written
after ``await request.body()`` also refuses an over-large request — and by then the process has
already allocated exactly the memory the caller asked for. The test below therefore does not merely
assert that a large body is refused; it counts how much of the body was ever pulled off the wire.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from starlette.requests import Request

from limitless.audit import RefusedOperation
from limitless.config import BOUNDS, MAX_CONCURRENCY, SecureBounds
from limitless.refusal import LimitReachedError, RefusalKind
from limitless.secure.bounds import (
    bounded_page_size,
    parse_json_object,
    read_bounded_body,
    require_batch_within,
)

Receive = Callable[[], Awaitable[dict[str, Any]]]


def make_request(
    chunks: list[bytes], headers: dict[str, str] | None = None
) -> tuple[Request, list[bytes]]:
    """A request whose body arrives in known chunks, recording which ones were actually read."""
    delivered: list[bytes] = []
    queue = list(chunks)
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/enrich",
        "raw_path": b"/v1/enrich",
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("test", 1234),
        "server": ("test", 8000),
    }

    async def receive() -> dict[str, Any]:
        if not queue:
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = queue.pop(0)
        delivered.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": bool(queue)}

    return Request(scope, receive), delivered


async def test_a_body_within_the_bound_is_returned_whole() -> None:
    request, delivered = make_request([b"abc", b"def", b"ghi"])
    body = await read_bounded_body(request, max_bytes=64, operation=RefusedOperation.ENRICH)
    assert body == b"abcdefghi"
    assert len(delivered) == 3


async def test_an_over_large_body_is_refused_without_being_read() -> None:
    """The control: reading stops the moment the bound is passed."""
    chunks = [b"x" * 1024 for _ in range(200)]
    request, delivered = make_request(chunks)

    with pytest.raises(LimitReachedError) as raised:
        await read_bounded_body(request, max_bytes=4096, operation=RefusedOperation.ENRICH)

    assert raised.value.kind is RefusalKind.INPUT_TOO_LARGE
    # Five 1 KiB chunks is what it takes to pass a 4 KiB bound. Anything close to 200 would mean
    # the whole body had been accepted into the process before anyone objected.
    assert len(delivered) == 5
    assert sum(len(chunk) for chunk in delivered) <= 4096 + 1024


async def test_a_declared_over_large_length_is_refused_before_reading_anything() -> None:
    request, delivered = make_request([b"x" * 8192], {"content-length": "8192"})

    with pytest.raises(LimitReachedError):
        await read_bounded_body(request, max_bytes=4096, operation=RefusedOperation.ENRICH)

    assert delivered == [], "a declared over-large body should cost us nothing at all"


async def test_a_lying_content_length_does_not_defeat_the_bound() -> None:
    """The declared length is a courtesy. The byte counter is the control."""
    chunks = [b"x" * 1024 for _ in range(200)]
    request, delivered = make_request(chunks, {"content-length": "10"})

    with pytest.raises(LimitReachedError):
        await read_bounded_body(request, max_bytes=4096, operation=RefusedOperation.ENRICH)

    assert len(delivered) == 5


async def test_a_chunked_body_with_no_declared_length_is_still_bounded() -> None:
    chunks = [b"y" * 512 for _ in range(100)]
    request, delivered = make_request(chunks, {"transfer-encoding": "chunked"})

    with pytest.raises(LimitReachedError):
        await read_bounded_body(request, max_bytes=2048, operation=RefusedOperation.ENRICH)

    assert len(delivered) == 5


def test_a_batch_within_the_bound_is_admitted() -> None:
    require_batch_within(
        list(range(BOUNDS.max_batch_items)),
        max_items=BOUNDS.max_batch_items,
        operation=RefusedOperation.ENRICH,
    )


def test_a_batch_one_item_over_the_bound_is_refused() -> None:
    with pytest.raises(LimitReachedError) as raised:
        require_batch_within(
            list(range(BOUNDS.max_batch_items + 1)),
            max_items=BOUNDS.max_batch_items,
            operation=RefusedOperation.ENRICH,
        )
    assert raised.value.kind is RefusalKind.INPUT_TOO_LARGE


@pytest.mark.parametrize("requested", [1, 50, 200])
def test_a_page_size_within_the_bound_is_served_as_asked(requested: int) -> None:
    assert bounded_page_size(str(requested), max_page_size=200) == requested


def test_an_absent_page_size_defaults_to_the_maximum() -> None:
    assert bounded_page_size(None, max_page_size=200) == 200
    assert bounded_page_size("", max_page_size=200) == 200


@pytest.mark.parametrize("requested", ["201", "1000000", "0", "-5", "not-a-number"])
def test_an_unacceptable_page_size_is_refused_rather_than_clamped(requested: str) -> None:
    """Refused, not quietly answered with something smaller.

    Serving 200 rows to a caller who asked for a million hides the bound from them and from the
    reader, and leaves nothing in the log to notice.
    """
    with pytest.raises(LimitReachedError) as raised:
        bounded_page_size(requested, max_page_size=200)
    assert raised.value.kind is RefusalKind.INPUT_TOO_LARGE


def test_the_documented_bounds_are_the_ones_in_force() -> None:
    assert SecureBounds() == BOUNDS
    assert BOUNDS.max_body_bytes == 65_536
    assert BOUNDS.max_batch_items == 500
    assert BOUNDS.max_page_size == 200
    assert BOUNDS.max_import_body_bytes == 262_144
    assert BOUNDS.max_decompressed_bytes == 4_194_304
    assert BOUNDS.max_expansion_ratio == 25
    assert BOUNDS.max_in_flight_upstream == 48


def test_a_non_object_body_is_a_bad_request_not_a_refusal() -> None:
    with pytest.raises(ValueError):
        parse_json_object(b"[1, 2, 3]")
    with pytest.raises(ValueError):
        parse_json_object(b"not json at all")


def test_the_in_flight_cap_can_absorb_the_highest_load_the_harness_can_generate() -> None:
    """A capacity control that sheds ordinary concurrency would refuse valid work.

    The harness cannot be configured above ``MAX_CONCURRENCY``, and every one of those requests may
    land on a single replica when only one is addressed. If the per-replica cap were below that, a
    heavy but legitimate tenant could be turned away — which is exactly what the fix must not do.
    """
    assert BOUNDS.max_in_flight_upstream >= MAX_CONCURRENCY
