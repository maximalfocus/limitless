"""Entry point for the concurrent load harness.

Note what this command line does **not** accept: a target. There is no host, URL, or address
argument, and the destinations come from configuration that refuses anything but the demonstration's
own in-network services. That is a containment requirement, not an omission — this must never be
usable as a general-purpose load tool.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace
from pathlib import Path

from ..config import (
    MAX_CONCURRENCY,
    MAX_ROUNDS,
    HarnessConfig,
    ReproductionMode,
    parse_reproduction_mode,
)
from .engine import run
from .transcript import render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="limitless-harness",
        description=(
            "Drive the demonstration's own services with genuine concurrent load and report the "
            "amplification accounting. Accepts no target: it can only address this "
            "demonstration's own in-network services."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ReproductionMode],
        default=None,
        help="reproduction mode (default: natural — no instrumentation in any code path)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=f"simultaneous requests per round (maximum {MAX_CONCURRENCY})",
    )
    parser.add_argument(
        "--rounds", type=int, default=None, help=f"rounds per scenario (maximum {MAX_ROUNDS})"
    )
    parser.add_argument(
        "--transcript", type=Path, default=None, help="where to write the run transcript"
    )
    return parser


def _configure(args: argparse.Namespace) -> HarnessConfig:
    config = HarnessConfig.from_env()
    if args.mode is not None:
        mode = parse_reproduction_mode(args.mode)
        if mode is None:
            raise ValueError(f"unknown reproduction mode: {args.mode}")
        config = replace(config, mode=mode)
    if args.concurrency is not None:
        if not 1 <= args.concurrency <= MAX_CONCURRENCY:
            raise ValueError(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
        config = replace(config, concurrency=args.concurrency)
    if args.rounds is not None:
        if not 1 <= args.rounds <= MAX_ROUNDS:
            raise ValueError(f"--rounds must be between 1 and {MAX_ROUNDS}")
        config = replace(config, rounds=args.rounds)
    if args.transcript is not None:
        config = replace(config, transcript_path=args.transcript)
    return config


async def _amain(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = _configure(args)
    accounting = await run(config)
    transcript = render(accounting, config)
    print(transcript)
    try:
        config.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        config.transcript_path.write_text(transcript)
        print(f"transcript written to {config.transcript_path}")
    except OSError as exc:
        print(f"could not write the transcript to {config.transcript_path}: {exc}", file=sys.stderr)
        return 1

    if accounting.violations:
        print(
            f"\nthe secure application recorded {len(accounting.violations)} bound violation(s); "
            f"a secure-side violation is a genuine failure, never a flake",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
