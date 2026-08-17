"""Secure control A — input bounds enforced at the edge, while reading.

Three dimensions are bounded here, and one of them is bounded in a specific *place* that matters
more than the number:

**How big — enforced while reading the stream.** ``read_bounded_body`` counts bytes as they arrive
and stops the moment the maximum is passed, so an over-large body is refused **without ever being
allocated**. A size check written after ``await request.body()`` is not this control: by the time it
runs, the allocation it exists to prevent has already happened, and the caller has already been
handed exactly the memory they asked for. That mistake is common enough that a later part of this
demonstration is built around it.

The declared ``Content-Length`` is consulted first, but only as a courtesy early-out. It is not the
control and cannot be: the caller writes that header, and a chunked request has none at all. The
byte counter below is what actually holds.

**How many — the batch.** Each item in a batch is a metered provider lookup, so the item count is a
direct statement of how much money the request will spend. It is bounded explicitly.

**How many — the page.** A caller-supplied page size is bounded by a server-side maximum rather than
clamped silently, so an over-large request is refused rather than quietly answered with something
else. A cap that lives only in a client is not a bound at all.
"""

from __future__ import annotations

import json
from typing import Any, Final

from fastapi import Request

from ..audit import RefusedOperation
from ..refusal import LimitReachedError, RefusalKind

CONTENT_LENGTH_HEADER: Final = "content-length"


async def read_bounded_body(
    request: Request, *, max_bytes: int, operation: RefusedOperation
) -> bytes:
    """Read at most ``max_bytes`` from the request stream, refusing the moment it is exceeded.

    Returns the body only when the whole of it fitted. Nothing larger is ever assembled.
    """
    declared = request.headers.get(CONTENT_LENGTH_HEADER)
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        # A courtesy: refuse before reading anything at all when the caller admits the size.
        raise LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation)

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            # The authoritative check. We stop reading here; the rest of the body is never
            # accepted into this process, so the allocation never happens.
            raise LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation)
        chunks.append(chunk)
    return b"".join(chunks)


def parse_json_object(body: bytes) -> dict[str, Any]:
    """Parse a JSON object from an already-bounded body."""
    try:
        parsed = json.loads(body)
    except ValueError as exc:
        raise ValueError("body is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("body is not a JSON object")
    return parsed


def require_batch_within(items: list[Any], *, max_items: int, operation: RefusedOperation) -> None:
    """Refuse a batch naming more items — and so more metered lookups — than are allowed."""
    if len(items) > max_items:
        raise LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation)


def bounded_page_size(raw: str | None, *, max_page_size: int) -> int:
    """Resolve a caller-supplied page size against the server-side maximum.

    Absent, it defaults to the maximum. Present and larger, it is **refused** rather than clamped:
    silently serving something other than what was asked for hides the bound from the caller and
    from the reader.
    """
    operation = RefusedOperation.LIST_RECORDS
    if raw is None or raw == "":
        return max_page_size
    try:
        requested = int(raw)
    except ValueError:
        raise LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation) from None
    if requested < 1 or requested > max_page_size:
        raise LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation)
    return requested
