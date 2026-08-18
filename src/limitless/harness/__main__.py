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
from .controls import HALF_FIXES, NEGATIVE_CONTROLS, in_process_allowance
from .engine import run
from .transcript import render, render_shapes
from .vulnerable import SHAPES


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
        "--variant",
        choices=["secure", "vulnerable", "half-fixes", "controls"],
        default="secure",
        help=(
            "what to drive: the secure application, the unbounded ladder, the repairs that fail, "
            "or the negative controls (default: secure)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ReproductionMode],
        default=None,
        help=(
            "reproduction mode (default: deterministic — the provider's hold/release control "
            "makes occupancy arithmetic; natural uses no instrumentation at all and reports "
            "observed figures, so it can be inconclusive)"
        ),
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
    if args.variant in ("vulnerable", "half-fixes", "controls"):
        return await _drive_unbounded(config, args.variant)
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


TITLES = {
    "vulnerable": "unbounded ladder",
    "half-fixes": "repairs that fail",
    "controls": "negative controls",
}


async def _drive_unbounded(config: HarnessConfig, variant: str) -> int:
    """Drive one of the unbounded suites.

    Here the expectations are the mirror image of the secure side's: a shape, a repair, or a control
    that fails to reproduce is the failure, because it has not been demonstrated.
    """
    from ..httpclient import HalyardHTTP

    urls = config.runner.vulnerable_replica_urls
    if not urls:
        print("no vulnerable replicas are configured", file=sys.stderr)
        return 1

    async with HalyardHTTP(
        urls[:1],
        provider_url=config.runner.provider_url,
        timeout=config.runner.request_timeout_seconds,
    ) as client:
        await client.wait_until_ready()
        if variant == "vulnerable":
            outcomes = tuple([await shape(client, config) for shape in SHAPES])
        elif variant == "controls":
            outcomes = tuple([await control(client, config) for control in NEGATIVE_CONTROLS])
        else:
            outcomes = tuple([await half_fix(client, config) for half_fix in HALF_FIXES])
            # The scope half-fix is the one that needs both counts, so it needs both clients.
            async with HalyardHTTP(
                urls,
                provider_url=config.runner.provider_url,
                timeout=config.runner.request_timeout_seconds,
            ) as both:
                await both.wait_until_ready()
                outcomes = (*outcomes, await in_process_allowance(client, both, config))

    transcript = render_shapes(outcomes, config, title=TITLES[variant])
    print(transcript)
    _write_transcript(config, transcript)

    missed = [outcome.shape for outcome in outcomes if not outcome.reproduced]
    if missed:
        print(unreproduced_notice(missed, config.mode), file=sys.stderr)
    return unreproduced_exit_code(missed, config.mode)


def unreproduced_exit_code(missed: list[str], mode: ReproductionMode) -> int:
    """What an unbounded run that failed to reproduce something is worth.

    `FR-012` and `FR-013` answer this differently on purpose. The deterministic mode carries the
    required vulnerable-side assertions, so a shape it cannot reproduce is a failure. The natural
    mode reports **observed** figures under genuine load, so a shape it did not observe is
    **inconclusive** — not a pass, and not a failure either, because absence of observation proves
    nothing. Reporting it as a failure would make a green run depend on winning a race, which is
    exactly what `NFR-002` forbids.
    """
    if not missed:
        return 0
    return 1 if mode.instrumented else 0


def unreproduced_notice(missed: list[str], mode: ReproductionMode) -> str:
    if mode.instrumented:
        return f"\n{len(missed)} did not reproduce: {missed}"
    return (
        f"\n{len(missed)} observed nothing this run: {missed}\n"
        f"INCONCLUSIVE — not a pass. Re-run with --mode deterministic to assert on them."
    )


def _write_transcript(config: HarnessConfig, transcript: str) -> None:
    config.transcript_path.parent.mkdir(parents=True, exist_ok=True)
    config.transcript_path.write_text(transcript)
    print(f"transcript written to {config.transcript_path}")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
