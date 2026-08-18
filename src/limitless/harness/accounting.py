"""The amplification accounting, and the bounded/unbounded verdict.

This is the output the whole demonstration exists to produce. Its central number is the
**amplification ratio** — fictional cents admitted per byte of input the caller supplied — because
the ratio between what an attack costs the attacker and what it costs the victim *is* the
vulnerability. Absolute capacity is a constant factor; the ratio is the structure.

Every figure here is **counted**, never timed. Bytes submitted, items admitted, provider lookups,
fictional cents from the provider's own ledger, occupied slots, refusals: all of them are integers
that two runs on two machines agree about. Nothing in this module observes a duration, and nothing
that depends on one may ever become a required assertion.

A **violation** is an observed breach of a bound. Zero of them, in every round, is what the secure
application is asserted to produce — and that assertion is also what proves the harness genuinely
generated load, since a harness that generated none would satisfy it without trying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

import httpx

from ..refusal import RefusalKind

REFUSAL_KIND_BY_STATUS: Final[dict[int, RefusalKind]] = {
    int(httpx.codes.REQUEST_ENTITY_TOO_LARGE): RefusalKind.INPUT_TOO_LARGE,
    int(httpx.codes.TOO_MANY_REQUESTS): RefusalKind.ALLOWANCE_EXHAUSTED,
    int(httpx.codes.SERVICE_UNAVAILABLE): RefusalKind.CAPACITY_SHED,
}


def refusal_kind(status_code: int) -> RefusalKind | None:
    """Which kind of limit refused this request, if one did."""
    return REFUSAL_KIND_BY_STATUS.get(status_code)


class ViolationKind(StrEnum):
    """The ways a bound can be observed to have failed."""

    OVER_LIMIT_ADMITTED = "over_limit_admitted"
    """A request naming more than a bound allows was served rather than refused."""

    ALLOWANCE_EXCEEDED = "allowance_exceeded"
    """A tenant was charged past its own partition of the fictional budget."""

    SPEND_CAP_BREACHED = "spend_cap_breached"
    """The whole fictional company's cap was spent past its floor."""

    BYSTANDER_AFFECTED = "bystander_affected"
    """A tenant that spent nothing was refused because another tenant spent."""

    CROSS_TENANT_CHARGE = "cross_tenant_charge"
    """Work was billed to a tenant that did not ask for any."""

    CHEAP_ENDPOINT_UNANSWERED = "cheap_endpoint_unanswered"
    """An endpoint that needs no provider, no allowance, and no slot failed to answer."""

    IN_FLIGHT_OVER_CAPACITY = "in_flight_over_capacity"
    """More upstream work was in flight at once than the configured cap permits."""

    UNACCOUNTED_SPEND = "unaccounted_spend"
    """The provider billed a tenant more than the application ever authorised for it.

    The sharpest check in the run. The application's allowance and the provider's bill are two
    independent records of the same fictional money, and the bill must never be the larger one. If
    it is, work was performed that no reservation covered, and the allowance is not a bound on
    spending at all — only a bound on the spending the application happens to know about.
    """

    LEGITIMATE_WORK_SHED = "legitimate_work_shed"
    """Valid work inside every bound was turned away.

    A capacity control that refuses ordinary concurrency is not protecting the budget; it is
    protecting it *from paying customers*, which is the failure mode the fix must not have.
    """


@dataclass(frozen=True, slots=True)
class Violation:
    kind: ViolationKind
    round_number: int
    detail: str

    def __str__(self) -> str:
        return f"round {self.round_number}: {self.kind.value} — {self.detail}"


@dataclass(frozen=True, slots=True)
class TenantFigures:
    """One tenant's side of the accounting, read from the provider's own bill."""

    tenant_id: str
    role: str
    """``attacker``, ``bystander``, or ``tenant`` — bystanders are reported separately."""

    input_bytes: int
    items_admitted: int
    lookups: int
    cents: int
    """What the provider billed this tenant, from the provider's own ledger."""

    cents_accounted: int = 0
    """What the application charged this tenant, from its own usage endpoint.

    Reported beside the provider's figure rather than instead of it: the two agreeing is the
    evidence, and the two disagreeing is the finding.
    """

    refusals: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioAccounting:
    """Everything one scenario observed, in the units the demonstration is measured in."""

    scenario: str
    description: str
    variant: str
    mode: str
    replicas: int
    concurrency: int
    rounds: int
    bound_in_effect: str

    input_bytes: int
    items_admitted: int
    lookups: int
    cents_total: int
    per_tenant: tuple[TenantFigures, ...]

    spend_cap_cents: int
    spend_cap_remaining: int
    spend_cap_breached: bool

    peak_in_flight: int
    in_flight_capacity: int

    cheap_endpoint_issued: int
    cheap_endpoint_answered: int

    refusals_by_kind: dict[str, int]
    violations: tuple[Violation, ...]

    @property
    def amplification_ratio(self) -> float:
        """Fictional cents admitted per byte of input. The demonstration's central number."""
        return self.cents_total / self.input_bytes if self.input_bytes else 0.0

    @property
    def verdict(self) -> str:
        return "bounded" if not self.violations else "unbounded"

    @property
    def bystanders(self) -> tuple[TenantFigures, ...]:
        """Reported separately, because in the unbounded case their loss is the point."""
        return tuple(figures for figures in self.per_tenant if figures.role == "bystander")


@dataclass(frozen=True, slots=True)
class RunAccounting:
    """Every scenario in one run."""

    variant: str
    mode: str
    scenarios: tuple[ScenarioAccounting, ...]

    @property
    def violations(self) -> tuple[Violation, ...]:
        return tuple(v for scenario in self.scenarios for v in scenario.violations)

    @property
    def verdict(self) -> str:
        return "bounded" if not self.violations else "unbounded"
