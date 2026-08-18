"""The repairs that fail.

Every one of these is a repair a competent engineer reaches for, every one is **genuinely
implemented and genuinely honoured**, and every one fails anyway. That they work exactly as
advertised is the whole point — a broken repair teaches nothing, and the reason these are worth
building is that they are the ones people actually ship.

    a request-count rate limit      counts the wrong unit
    an in-process allowance         is one allowance per process, not one allowance
    a caller-keyed allowance        is a bucket the caller can mint a fresh one of
    a deadline that returns         bounds the response, and not the work

The size check that measures the compressed number after buffering the body lives in ``shapes`` with
the shape it belongs to, because there the check *is* the defect rather than a repair for one.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from .. import fixtures


class HalfFix(StrEnum):
    """Which repair is in effect. Selected per request, so one service can show them all."""

    NONE = "none"
    REQUEST_RATE_LIMIT = "request_rate_limit"
    IN_PROCESS_ALLOWANCE = "in_process_allowance"
    CALLER_KEYED_ALLOWANCE = "caller_keyed_allowance"
    NON_CANCELLING_DEADLINE = "non_cancelling_deadline"


HALF_FIX_HEADER: Final = "X-Limitless-Half-Fix"
CLIENT_ID_HEADER: Final = "X-Limitless-Client-Id"
"""A caller-supplied identifier. That a limiter may be keyed on it at all is the defect."""

REQUESTS_PER_WINDOW: Final = 60
WINDOW_SECONDS: Final = 60.0
"""Sixty requests a minute per tenant — a limit nobody would call unreasonable."""

NON_CANCELLING_DEADLINE_SECONDS: Final = 1.0
"""When the caller is answered. Not when the work stops, because it does not stop."""


def parse_half_fix(raw: str | None) -> HalfFix | None:
    """Parse a repair name. ``None``/empty means no repair; an unknown value is an error."""
    if raw is None or raw == "":
        return HalfFix.NONE
    try:
        return HalfFix(raw.strip().lower())
    except ValueError:
        return None


@dataclass(slots=True)
class RateLimiterReport:
    """What the limiter believes about itself, which is where the trouble starts."""

    allowed: int = 0
    refused: int = 0

    @property
    def violations(self) -> int:
        """How many callers exceeded the limit.

        It will say zero, and it will be telling the truth.
        """
        return self.refused


@dataclass(slots=True)
class RequestRateLimiter:
    """Half-fix 1: sixty requests per minute per tenant, honoured exactly.

    It is a real limiter and it does its job. Its job is the wrong job: it counts **requests**, and
    the thing that runs out is **lookups**. A caller who stays politely inside sixty requests a
    minute can still name three million units of work a minute, because nothing here has any opinion
    about how much work a request is allowed to name.
    """

    max_requests: int = REQUESTS_PER_WINDOW
    window_seconds: float = WINDOW_SECONDS
    report: RateLimiterReport = field(default_factory=RateLimiterReport)
    _seen: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Whether this request is inside the limit. Honoured; simply about the wrong quantity."""
        moment = time.monotonic() if now is None else now
        recent = [at for at in self._seen[key] if moment - at < self.window_seconds]
        if len(recent) >= self.max_requests:
            self._seen[key] = recent
            self.report.refused += 1
            return False
        recent.append(moment)
        self._seen[key] = recent
        self.report.allowed += 1
        return True


@dataclass(slots=True)
class InProcessAllowance:
    """Half-fixes 2 and 3: the right allowance, in the wrong place, on the wrong key.

    The allowance is correct. The arithmetic is correct. It is enforced exactly — **per process**.
    Put a second replica behind the same endpoint, change nothing else at all, and "forty thousand
    cents a month" quietly becomes eighty thousand, because there are now two counters and each of
    them is enforcing the whole allowance by itself. Nothing is corrupted and nothing is lost; the
    budget is simply enforced twice, in parallel.

    Keyed on a caller-supplied value it is defeated more cheaply still: the caller changes the value
    and gets a fresh allowance, as many times as they care to.
    """

    allowance_cents: int = fixtures.TENANT_ALLOWANCE_CENTS
    spent: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def charge(self, key: str, cents: int) -> bool:
        """Spend against this process's own idea of the allowance."""
        if self.spent[key] + cents > self.allowance_cents:
            return False
        self.spent[key] += cents
        return True


@dataclass(slots=True)
class HalfFixState:
    """Everything the repairs remember, which is precisely the problem with two of them."""

    rate_limiter: RequestRateLimiter = field(default_factory=RequestRateLimiter)
    in_process: InProcessAllowance = field(default_factory=InProcessAllowance)
    caller_keyed: InProcessAllowance = field(default_factory=InProcessAllowance)

    def key_for(self, half_fix: HalfFix, *, tenant_id: str, client_id: str | None) -> str:
        """The key each repair counts against — a server-derived tenant, or whatever arrived."""
        if half_fix is HalfFix.CALLER_KEYED_ALLOWANCE:
            return client_id or tenant_id
        return tenant_id
