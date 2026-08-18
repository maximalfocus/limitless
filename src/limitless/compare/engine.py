"""The comparison engine: every scenario, reduced to one row each.

A plain function over a configuration that returns data. It renders nothing, prompts for nothing,
and is directly testable without simulating terminal input — which matters, because the table it
feeds is the demonstration's headline output and a table nobody can test is a table nobody should
trust.

Every row carries the same columns in the same units, so the secure application and the unbounded
one can be read across rather than argued about. The column that matters is the **amplification
ratio**: fictional cents admitted per byte of input the caller supplied. Absolute capacity is a
constant factor; the ratio is the structure, and it is the number a fix has to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import fixtures
from ..config import HarnessConfig
from ..harness import controls, vulnerable
from ..harness import engine as secure_engine
from ..harness.accounting import ScenarioAccounting
from ..harness.vulnerable import ShapeOutcome
from ..httpclient import HalyardHTTP, RequestRecord


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """One scenario, in the units every other scenario is also reported in."""

    scenario: str
    variant: str
    bound_in_effect: str
    mode: str
    replicas: int
    concurrency: int
    input_bytes: int
    items_admitted: int
    lookups: int
    cents: int
    cheap_issued: int
    cheap_answered: int
    refusals: dict[str, int]
    verdict: str
    records: tuple[RequestRecord, ...] = field(default_factory=tuple)

    @property
    def amplification_ratio(self) -> float:
        """Fictional cents admitted per byte of input. The demonstration's central number."""
        return self.cents / self.input_bytes if self.input_bytes else 0.0

    @property
    def cheap_endpoint(self) -> str:
        if not self.cheap_issued:
            return "—"
        return f"{self.cheap_answered}/{self.cheap_issued}"


def row_from_secure(accounting: ScenarioAccounting) -> ComparisonRow:
    return ComparisonRow(
        scenario=accounting.scenario,
        variant="secure",
        bound_in_effect=accounting.bound_in_effect,
        mode=accounting.mode,
        replicas=accounting.replicas,
        concurrency=accounting.concurrency,
        input_bytes=accounting.input_bytes,
        items_admitted=accounting.items_admitted,
        lookups=accounting.lookups,
        cents=accounting.cents_total,
        cheap_issued=accounting.cheap_endpoint_issued,
        cheap_answered=accounting.cheap_endpoint_answered,
        refusals=accounting.refusals_by_kind,
        verdict=accounting.verdict,
    )


def _verdict(outcome: ShapeOutcome) -> str:
    """What this row established.

    A negative control that held has not shown an unbounded path — it has ruled one out, which is a
    different claim and deserves a different word.
    """
    if not outcome.reproduced:
        return "did not reproduce"
    return "boundary held" if outcome.kind == "control" else "unbounded"


def row_from_shape(outcome: ShapeOutcome, *, config: HarnessConfig, replicas: int) -> ComparisonRow:
    return ComparisonRow(
        scenario=outcome.shape,
        variant="vulnerable",
        bound_in_effect=outcome.bound_in_effect,
        mode=config.mode.value,
        replicas=replicas,
        concurrency=1,
        input_bytes=outcome.input_bytes,
        items_admitted=outcome.items_admitted,
        lookups=outcome.lookups,
        cents=outcome.cents,
        cheap_issued=outcome.cheap_issued,
        cheap_answered=outcome.cheap_answered,
        refusals=outcome.refusals,
        verdict=_verdict(outcome),
        records=outcome.records,
    )


async def compare(config: HarnessConfig) -> tuple[ComparisonRow, ...]:
    """Run the full scenario set and return one row per scenario.

    The secure side runs first and must come out bounded; the unbounded side runs second and must
    come out unbounded. Either way round, a scenario that reported the *other* answer would mean the
    comparison is comparing something other than what it says it is.
    """
    rows: list[ComparisonRow] = []

    async with HalyardHTTP(
        config.runner.replica_urls,
        provider_url=config.runner.provider_url,
        timeout=config.runner.request_timeout_seconds,
    ) as secure:
        await secure.wait_until_ready()
        accounting = await secure_engine.run(config)
    rows.extend(row_from_secure(scenario) for scenario in accounting.scenarios)

    urls = config.runner.vulnerable_replica_urls
    if not urls:
        return tuple(rows)

    async with HalyardHTTP(
        urls[:1],
        provider_url=config.runner.provider_url,
        timeout=config.runner.request_timeout_seconds,
    ) as unbounded:
        await unbounded.wait_until_ready()
        for shape in vulnerable.SHAPES:
            rows.append(row_from_shape(await shape(unbounded, config), config=config, replicas=1))
        for half_fix in controls.HALF_FIXES:
            rows.append(
                row_from_shape(await half_fix(unbounded, config), config=config, replicas=1)
            )
        async with HalyardHTTP(
            urls,
            provider_url=config.runner.provider_url,
            timeout=config.runner.request_timeout_seconds,
        ) as both:
            await both.wait_until_ready()
            scope = await controls.in_process_allowance(unbounded, both, config)
        rows.append(row_from_shape(scope, config=config, replicas=len(urls)))
        for control in controls.NEGATIVE_CONTROLS:
            rows.append(row_from_shape(await control(unbounded, config), config=config, replicas=1))

    return tuple(rows)


def unbounded_rows(rows: tuple[ComparisonRow, ...]) -> tuple[ComparisonRow, ...]:
    return tuple(row for row in rows if row.variant == "vulnerable")


def worst_ratio(rows: tuple[ComparisonRow, ...]) -> float:
    """The largest amplification any scenario achieved. The headline of the headline."""
    return max((row.amplification_ratio for row in rows), default=0.0)


def cap_multiple(row: ComparisonRow) -> float:
    return row.cents / fixtures.GLOBAL_SPEND_CAP_CENTS
