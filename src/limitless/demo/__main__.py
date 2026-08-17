"""Entry point for the sequential demonstration."""

from __future__ import annotations

import asyncio
import sys

from ..config import RunnerConfig
from .sequential import run


async def _amain() -> int:
    counter = await run(RunnerConfig.from_env())
    if counter.failures:
        print("\nthe sequential demonstration FAILED:", file=sys.stderr)
        for failure in counter.failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nevery sequential expectation held.")
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
