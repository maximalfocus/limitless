"""The four unbounded shapes, written so they read as a diff against ``limitless.secure``.

Each function here has a counterpart in the secure application. The counterparts are not longer, or
cleverer, or slower — they simply ask *how much* before they do the work, and these do not. That is
the entire difference, and it is why the two variants are otherwise kept byte-for-byte identical.

    secure.bounds.read_bounded_body        →  read_whole_body
    secure.bounds.bounded_page_size        →  whatever_page_size_was_asked_for
    secure.expansion.read_bounded_bundle   →  decompress_completely
    secure.store.reserve                   →  store.charge_undivided_pool

Nothing in this module is correct. It is here to be run on purpose, on a network with no egress,
against fictional tenants and fictional money.
"""

from __future__ import annotations

import gzip
import json
import zlib
from dataclasses import dataclass
from typing import Any, Final

from fastapi import Request

COMPRESSED_BODY_LIMIT_BYTES: Final = 262_144
"""The size check that is wrong twice over.

It is wrong about **what** it measures. This is the *compressed* size, and the relationship
between it and the real one is chosen by whoever built the bundle: two hundred kilobytes of gzip
is fifty megabytes of records at an ordinary ratio, and the check cannot tell.

It is wrong about **when** it runs: by the time it is consulted, the whole body has already been
read into memory by ``read_whole_body`` below. The allocation this check exists to prevent has
already happened, so even when it refuses, it refuses too late to have helped.

Both mistakes are extremely common, and each one alone is enough to make the check useless.
"""


async def read_whole_body(request: Request) -> bytes:
    """Read the entire request body, however large it turns out to be.

    The secure counterpart counts bytes as they arrive and stops at its maximum. This one asks for
    all of it and finds out how much that was afterwards, which is the same as having no bound: the
    memory is already committed by the time anybody could object.
    """
    return await request.body()


def whatever_page_size_was_asked_for(raw: str | None, *, default: int) -> int:
    """Take the caller's page size at face value.

    Sixty bytes of query string is enough to ask for a million rows, and this will try.
    """
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(slots=True)
class DecompressedBundle:
    compressed_bytes: int
    decompressed_bytes: int
    records: int
    """Counted, not collected. Collecting them would run out of memory before the point was made."""

    @property
    def expansion_ratio(self) -> float:
        return self.decompressed_bytes / self.compressed_bytes if self.compressed_bytes else 0.0


def decompress_completely(compressed: bytes) -> DecompressedBundle:
    """Decompress the whole bundle before counting anything in it.

    The secure counterpart decompresses as a stream and aborts the moment either its byte ceiling or
    its ratio ceiling is crossed, so nothing beyond the ceiling is ever materialized. This one
    materializes the whole thing first and counts afterwards — by which time the caller has already
    been handed exactly as much of the machine as they asked for.
    """
    try:
        raw = gzip.decompress(compressed)
    except (OSError, zlib.error) as exc:
        raise ValueError("bundle is not a valid gzip stream") from exc
    return DecompressedBundle(
        compressed_bytes=len(compressed),
        decompressed_bytes=len(raw),
        records=sum(1 for line in raw.splitlines() if line.strip()),
    )


def parse_json_object(body: bytes) -> dict[str, Any]:
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("body is not a JSON object")
    return parsed
