"""Entry point for the comparison.

Like the harness, this takes no target. Its destinations are the demonstration's own in-network
services and there is no argument that could change that.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..config import HarnessConfig
from .engine import compare
from .table import render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="limitless-compare",
        description=(
            "Run every scenario against both applications and print one comparison table. "
            "Accepts no target: it can only address this demonstration's own services."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="also print the per-request records underlying every row",
    )
    parser.add_argument("--output", type=Path, default=None, help="also write the table to a file")
    return parser


async def _amain(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = HarnessConfig.from_env()
    rows = await compare(config)
    if not rows:
        print("no scenarios ran", file=sys.stderr)
        return 1

    table = render(rows, config, verbose=args.verbose)
    print(table)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(table)
        print(f"comparison written to {args.output}")

    wrong = [
        row.scenario
        for row in rows
        if row.verdict == "did not reproduce"
        or (row.variant == "secure" and row.verdict != "bounded")
    ]
    if wrong:
        print(f"\n{len(wrong)} scenario(s) reported the wrong verdict: {wrong}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
