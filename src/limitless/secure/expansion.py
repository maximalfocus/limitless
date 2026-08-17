"""Secure control B — expansion bounded during decompression.

A compressed upload is the one input whose size on the wire says almost nothing about its size in
memory. 36 KB of gzip is 10 MB of NDJSON when the content repeats, and the submitter chose the
ratio. So this module never decides anything from the compressed size. It decompresses **as a
stream** and enforces two ceilings *while* it does:

* an **absolute** ceiling on decompressed bytes, because a bound has to exist in the unit that
  actually consumes the machine; and
* a **ratio** ceiling, because an absolute bound alone lets a submitter sit just under it every
  time, and because a wildly disproportionate ratio is the signature of the attack rather than of a
  large legitimate import.

Both abort **mid-stream**, the moment they are crossed. Nothing beyond the ceiling is ever
materialized: ``zlib`` is asked for a bounded amount of output at a time, so even a single small
input chunk cannot expand into an unbounded allocation between two checks.

The ratio is not evaluated until enough has been decompressed for it to mean anything. Early in a
gzip stream, a few hundred compressed bytes can legitimately produce several kilobytes as the
dictionary fills, and a ratio computed on that sample is noise rather than evidence. The floor is
documented, is far below either real ceiling, and does not weaken the absolute bound, which applies
from the first byte.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from typing import Final

from fastapi import Request

from ..audit import RefusedOperation
from ..config import SecureBounds
from ..refusal import LimitReachedError, RefusalKind

GZIP_WBITS: Final = 16 + zlib.MAX_WBITS
"""Decode a gzip stream — a single layer of it. This project never unwraps an archive."""

OUTPUT_CHUNK_BYTES: Final = 65_536
"""How much decompressed output is produced per step. The step *is* the enforcement interval."""

RATIO_EVALUATION_FLOOR_BYTES: Final = 65_536
"""Below this much output, the observed ratio is noise and is not evaluated."""


@dataclass(slots=True)
class ImportedRecord:
    company_name: str


@dataclass(slots=True)
class BundleResult:
    """What a bundle turned out to be, once it was safely bounded."""

    compressed_bytes: int
    decompressed_bytes: int
    records: list[ImportedRecord] = field(default_factory=list)

    @property
    def expansion_ratio(self) -> float:
        return self.decompressed_bytes / self.compressed_bytes if self.compressed_bytes else 0.0


class _LineAssembler:
    """Turns decompressed chunks into whole NDJSON records without buffering the whole stream."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.records: list[ImportedRecord] = []

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        while (newline := self._buffer.find(b"\n")) != -1:
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            self._take(line)

    def finish(self) -> None:
        if self._buffer:
            self._take(bytes(self._buffer))
            self._buffer.clear()

    def _take(self, line: bytes) -> None:
        stripped = line.strip()
        if not stripped:
            return
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return
        if isinstance(parsed, dict) and isinstance(parsed.get("company_name"), str):
            self.records.append(ImportedRecord(company_name=parsed["company_name"]))


async def read_bounded_bundle(
    request: Request, *, bounds: SecureBounds, operation: RefusedOperation
) -> BundleResult:
    """Stream, decompress, and bound a compressed import bundle.

    Refuses the moment the compressed body, the decompressed byte ceiling, or the expansion ratio is
    crossed — never after. The bundle is returned only when the whole of it fitted inside every one
    of those bounds.
    """
    decompressor = zlib.decompressobj(GZIP_WBITS)
    assembler = _LineAssembler()
    compressed_bytes = 0
    decompressed_bytes = 0

    def refuse() -> LimitReachedError:
        return LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation)

    def account(produced: bytes) -> None:
        nonlocal decompressed_bytes
        decompressed_bytes += len(produced)
        if decompressed_bytes > bounds.max_decompressed_bytes:
            raise refuse()
        if (
            decompressed_bytes >= RATIO_EVALUATION_FLOOR_BYTES
            and decompressed_bytes > compressed_bytes * bounds.max_expansion_ratio
        ):
            raise refuse()
        assembler.feed(produced)

    async for chunk in request.stream():
        compressed_bytes += len(chunk)
        if compressed_bytes > bounds.max_import_body_bytes:
            # The compressed body has its own bound, enforced while reading, exactly as every other
            # body is. It is not the expansion control and cannot stand in for it.
            raise refuse()
        pending = chunk
        while True:
            try:
                produced = decompressor.decompress(pending, max_length=OUTPUT_CHUNK_BYTES)
            except zlib.error as exc:
                raise ValueError("bundle is not a valid gzip stream") from exc
            account(produced)
            pending = decompressor.unconsumed_tail
            if not pending:
                break

    try:
        account(decompressor.flush())
    except zlib.error as exc:
        raise ValueError("bundle is not a valid gzip stream") from exc
    if not decompressor.eof:
        raise ValueError("bundle is a truncated gzip stream")
    assembler.finish()

    return BundleResult(
        compressed_bytes=compressed_bytes,
        decompressed_bytes=decompressed_bytes,
        records=assembler.records,
    )
