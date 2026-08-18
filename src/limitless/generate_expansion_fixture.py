"""Generate the compressed import fixture, at image build time.

The fixture is **generated, never committed**. A repository that carries a highly compressible
archive around in its history hands one to everybody who clones it, forever, including people who
only wanted to read the documentation. Generating it at build time means the repository contains
the *recipe* — repetitive fictional records, compressed once — and the recipe is unremarkable.

What it produces is ordinary NDJSON, repeated, through one gzip stream. The expansion comes from
repetition and nothing else: it is not an archive inside an archive, it is not recursive, and it
does not refer to itself. It is sized so that even fully expanded it stays well inside the
application container's declared memory limit, which is what keeps it contained rather than small.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path
from typing import Final

from . import fixtures

MAX_COMPRESSED_BYTES: Final = 200_000
"""The fixture must stay inside the size an unremarkable upload would be, or it proves nothing."""

MAX_DECOMPRESSED_BYTES: Final = 128 * 1024 * 1024
"""A hard containment ceiling, far below the application container's declared memory limit."""


def build() -> bytes:
    return fixtures.repetitive_ndjson_bundle(fixtures.EXPANSION_FIXTURE_RECORDS)


def describe(bundle: bytes) -> str:
    decompressed = len(gzip.decompress(bundle))
    cents = fixtures.EXPANSION_FIXTURE_RECORDS * fixtures.LOOKUP_PRICE_CENTS
    return (
        f"{len(bundle):,} B compressed -> {decompressed:,} B decompressed "
        f"({decompressed / len(bundle):.1f}:1, single layer), "
        f"{fixtures.EXPANSION_FIXTURE_RECORDS:,} records, "
        f"{cents:,} {fixtures.CURRENCY_LABEL} "
        f"({cents / fixtures.GLOBAL_SPEND_CAP_CENTS:.1f}x the whole fictional monthly cap)"
    )


def check(bundle: bytes) -> list[str]:
    """Containment checks, run every time the fixture is built rather than trusted once."""
    failures: list[str] = []
    raw = gzip.decompress(bundle)
    if len(bundle) > MAX_COMPRESSED_BYTES:
        failures.append(f"compressed fixture is {len(bundle)} B, over {MAX_COMPRESSED_BYTES} B")
    if len(raw) > MAX_DECOMPRESSED_BYTES:
        failures.append(f"decompressed fixture is {len(raw)} B, over {MAX_DECOMPRESSED_BYTES} B")
    if raw.startswith(b"\x1f\x8b"):
        failures.append("the fixture decompresses into another compressed stream")
    if not raw.startswith(b'{"company_name"'):
        failures.append("the fixture does not decompress into fictional NDJSON records")
    admitted = fixtures.EXPANSION_FIXTURE_RECORDS * fixtures.LOOKUP_PRICE_CENTS
    if admitted < fixtures.GLOBAL_SPEND_CAP_CENTS * 10:
        failures.append(
            f"the fixture only admits {admitted} against a cap of "
            f"{fixtures.GLOBAL_SPEND_CAP_CENTS}; it must exceed it by an order of magnitude"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="limitless-generate-expansion-fixture",
        description="Build the compressed import fixture from repetitive fictional records.",
    )
    parser.add_argument("--output", type=Path, required=True, help="where to write the bundle")
    args = parser.parse_args(argv)

    bundle = build()
    failures = check(bundle)
    if failures:
        print("expansion fixture FAILED its containment checks:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bundle)
    print(f"wrote {args.output}: {describe(bundle)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
