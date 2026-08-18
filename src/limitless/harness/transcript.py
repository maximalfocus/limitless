"""Rendering the accounting as something a person can read.

The transcript is the run artifact behind every claim this demonstration makes, so it is written for
a reader rather than for a parser: the amplification ratio is given the position it deserves, and
every figure that is an **observation** rather than an **assertion** says so where it appears.

Two things are deliberately absent. There is no duration, throughput, or latency figure
anywhere, because this project measures admitted work and charged cost and makes no performance
claim of any kind. And there is no credential: the transcript carries tenant identifiers, which are
fictional, and never a token.
"""

from __future__ import annotations

from typing import Final

from .. import fixtures
from ..config import HarnessConfig
from ..httpclient import replica_label
from .accounting import RunAccounting, ScenarioAccounting
from .vulnerable import ShapeOutcome

WIDTH: Final = 98
OBSERVED: Final = "(observed, not asserted)"


def _rule(character: str = "=") -> str:
    return character * WIDTH


def _heading(title: str) -> list[str]:
    return ["", _rule(), title, _rule()]


def render(accounting: RunAccounting, config: HarnessConfig) -> str:
    """The whole run, as a transcript."""
    lines: list[str] = [
        "",
        f"{fixtures.COMPANY_NAME} — concurrent load and amplification harness",
        "",
        f"  variant              : {accounting.variant}",
        f"  reproduction mode    : {accounting.mode}",
        f"  replicas addressed   : {len(config.runner.replica_urls)} "
        f"({', '.join(_labels(config))})",
        f"  concurrency          : {config.concurrency} simultaneous requests per round",
        f"  rounds               : {config.rounds}",
        f"  provider             : {fixtures.PROVIDER_NAME}, "
        f"{fixtures.LOOKUP_PRICE_CENTS} {fixtures.CURRENCY_LABEL} per lookup",
        f"  fictional cap        : {fixtures.GLOBAL_SPEND_CAP_CENTS}, partitioned into "
        f"{fixtures.TENANT_ALLOWANCE_CENTS} per tenant",
        "",
        "  This harness generates load only against the demonstration's own in-network services",
        "  and cannot be pointed anywhere else. It measures admitted work and charged cost, and",
        "  makes no throughput, latency, or capacity claim.",
    ]

    for scenario in accounting.scenarios:
        lines.extend(_scenario(scenario))

    lines.extend(_heading("RUN VERDICT"))
    if accounting.violations:
        lines.append(f"  {len(accounting.violations)} bound violation(s) observed:")
        lines.extend(f"    - {violation}" for violation in accounting.violations)
        lines.append("")
        lines.append("  VERDICT: UNBOUNDED")
    else:
        rounds = ", ".join(
            f"{scenario.scenario} in {scenario.rounds}" for scenario in accounting.scenarios
        )
        lines.append(
            f"  zero bound violations across {len(accounting.scenarios)} scenario(s): "
            f"{rounds} round(s) respectively."
        )
        lines.append("")
        lines.append("  VERDICT: BOUNDED")
    lines.append("")
    return "\n".join(lines) + "\n"


def _labels(config: HarnessConfig) -> tuple[str, ...]:
    return tuple(replica_label(url) for url in config.runner.replica_urls)


def _scenario(scenario: ScenarioAccounting) -> list[str]:
    lines = _heading(f"SCENARIO — {scenario.scenario}")
    lines.append(f"  {scenario.description}")
    lines.append("")
    lines.append(f"  bound in effect      : {scenario.bound_in_effect}")
    lines.append(
        f"  input submitted      : {scenario.input_bytes} B across every request in the scenario"
    )
    lines.append(f"  items admitted       : {scenario.items_admitted}")
    lines.append(f"  provider lookups     : {scenario.lookups}   (from the provider's own ledger)")
    lines.append(
        f"  fictional cents      : {scenario.cents_total}   (from the provider's own ledger)"
    )
    lines.append("")
    lines.append(
        f"  AMPLIFICATION RATIO  : {scenario.amplification_ratio:.4f} "
        f"{fixtures.CURRENCY_LABEL} admitted per byte of input"
    )
    lines.append(
        "                         the ratio between what the caller spends and what the service"
    )
    lines.append("                         spends is the vulnerability; capacity is only a factor.")
    lines.append("")
    lines.append(
        f"  spend cap            : {scenario.spend_cap_cents} with {scenario.spend_cap_remaining} "
        f"remaining · floor breached: {'YES' if scenario.spend_cap_breached else 'no'}"
    )
    lines.append(
        f"  peak in flight       : {scenario.peak_in_flight} against a capacity of "
        f"{scenario.in_flight_capacity} {OBSERVED}"
    )
    lines.append(
        f"  cheap endpoint       : {scenario.cheap_endpoint_answered} answered of "
        f"{scenario.cheap_endpoint_issued} issued"
    )
    lines.append(f"  refusals by kind     : {_refusals(scenario.refusals_by_kind)}")
    lines.append("")
    lines.append("  per tenant, from the provider's own bill:")
    lines.append(
        f"    {'tenant':<14} {'role':<10} {'input B':>10} {'items':>8} {'lookups':>9} "
        f"{'cents':>8}  refusals"
    )
    for figures in scenario.per_tenant:
        lines.append(
            f"    {figures.tenant_id:<14} {figures.role:<10} {figures.input_bytes:>10} "
            f"{figures.items_admitted:>8} {figures.lookups:>9} {figures.cents:>8}  "
            f"{_refusals(figures.refusals)}"
        )
    if scenario.bystanders:
        lines.append("")
        lines.append(
            "    the bystander rows are reported separately on purpose: when a budget is not"
        )
        lines.append(
            "    partitioned, their loss is the whole point, so their figures must never be"
        )
        lines.append("    averaged into somebody else's.")
    lines.append("")
    if scenario.violations:
        lines.append(f"  {len(scenario.violations)} violation(s):")
        lines.extend(f"    - {violation}" for violation in scenario.violations)
        lines.append(f"  VERDICT: {scenario.verdict.upper()}")
    else:
        lines.append(
            f"  no bound violation in any of {scenario.rounds} round(s)."
            f"   VERDICT: {scenario.verdict.upper()}"
        )
    return lines


def _refusals(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))


def render_shapes(
    outcomes: tuple[ShapeOutcome, ...], config: HarnessConfig, *, title: str = "unbounded ladder"
) -> str:
    """The unbounded ladder, as a transcript.

    The expectations here are the mirror image of the secure side's: each shape is supposed to
    reproduce, and a shape that does not is the failure. The verdict says so explicitly.
    """
    lines: list[str] = [
        "",
        f"{fixtures.COMPANY_NAME} — INTENTIONALLY VULNERABLE variant, {title}",
        "",
        f"  reproduction mode    : {config.mode.value}",
        f"  replicas addressed   : {len(config.runner.vulnerable_replica_urls)}",
        f"  provider             : {fixtures.PROVIDER_NAME}, "
        f"{fixtures.LOOKUP_PRICE_CENTS} {fixtures.CURRENCY_LABEL} per lookup",
        f"  fictional cap        : {fixtures.GLOBAL_SPEND_CAP_CENTS} for the whole company",
        "",
        "  The deterministic mode's hold/release control lives in the provider fixture and in",
        "  unbounded code paths only. It changes only *when* work is released, never whether the",
        "  application bounded it.",
    ]
    if config.mode.instrumented:
        lines.append(
            "  Run this with --mode natural for the same shapes with no instrumentation at all."
        )
    else:
        lines.extend(
            [
                "  This run uses no instrumentation and no provider hold. Its figures are",
                "  OBSERVED, not arranged: a shape that observes nothing here is INCONCLUSIVE,",
                "  never a pass and never evidence that the application bounded anything.",
            ]
        )

    for outcome in outcomes:
        lines.extend(_heading(outcome.shape.upper()))
        lines.append(f"  {outcome.headline}")
        lines.append("")
        for entry in outcome.detail:
            lines.append(f"  {entry}")
        lines.append("")
        if outcome.input_bytes:
            lines.append(
                f"  AMPLIFICATION RATIO  : {outcome.cost_per_input_byte:.4f} "
                f"{fixtures.CURRENCY_LABEL} per byte of input"
            )
        if outcome.reproduced:
            lines.append("  reproduced: YES   VERDICT: UNBOUNDED")
        elif config.mode.instrumented:
            lines.append("  reproduced: NO    VERDICT: DID NOT REPRODUCE")
        else:
            lines.append("  reproduced: NO    VERDICT: INCONCLUSIVE — observed nothing this run")

    missed = [o.shape for o in outcomes if not o.reproduced]
    lines.extend(_heading("RUN VERDICT"))
    if missed and config.mode.instrumented:
        lines.append(f"  {len(missed)} did not reproduce:")
        lines.extend(f"    - {shape}" for shape in missed)
        lines.append("")
        lines.append("  VERDICT: INCOMPLETE — a shape that does not reproduce proves nothing")
    elif missed:
        lines.append(f"  {len(missed)} observed nothing this run:")
        lines.extend(f"    - {shape}" for shape in missed)
        lines.append("")
        lines.append("  VERDICT: INCONCLUSIVE — a natural run that observes nothing proves")
        lines.append("  nothing either way. It is not a pass, and it is not a failure. Re-run")
        lines.append("  under --mode deterministic for the assertion that is allowed to fail.")
    else:
        lines.append(f"  all {len(outcomes)} reproduced.")
        lines.append("")
        if config.mode.instrumented:
            lines.append("  VERDICT: UNBOUNDED")
        else:
            lines.append("  VERDICT: UNBOUNDED (observed, with no instrumentation at all)")
    lines.append("")
    return "\n".join(lines) + "\n"
