"""Secure control D — bounded in-flight work, and deadlines that cancel.

Two limits live here, and the second one is the subtle one.

**Bounded in-flight work, shed rather than queued.** The number of upstream calls in flight at once
is capped explicitly. When the cap is reached the next request is refused **immediately** instead of
being queued behind the others. Queueing without bound is not a limit — it is the same unbounded
resource wearing a different name, and it converts a spending problem into a memory problem while
leaving the caller's leverage untouched. Shedding keeps the failure cheap, immediate, and local to
the caller who caused it, which is what lets endpoints that need none of these resources go on being
served.

**A deadline that cancels.** ``asyncio.timeout`` cancels the operation it wraps: when the deadline
passes, the work underneath actually stops. A timeout that merely returns a response to the caller
while the server keeps the upstream call and its worker has bounded the *response* and nothing else
— the work goes on accumulating behind it, the slot stays occupied, and the money still gets spent.
A deadline is only a deadline if it cancels, and a later part of this demonstration is built on the
difference.

Nothing in this module is exposed to a client. Occupancy is state a caller must not be able to read:
knowing how full the service is now is the beginning of knowing exactly when to push.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..audit import RefusedOperation
from ..refusal import LimitReachedError, RefusalKind


class InFlightLimiter:
    """An explicit cap on concurrent upstream work, with excess shed at once."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError(f"in-flight capacity must be at least 1; got {capacity}")
        self._capacity = capacity
        self._in_flight = 0
        self._peak_in_flight = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def peak_in_flight(self) -> int:
        """The high-water mark since start. Used by tests and never served to a client."""
        return self._peak_in_flight

    @asynccontextmanager
    async def slot(self, operation: RefusedOperation) -> AsyncIterator[None]:
        """Occupy one slot for the duration, or refuse immediately if there is none.

        The check and the claim below are a single step with no ``await`` between them, so on one
        event loop they cannot interleave: two requests can never both find the last slot free.
        """
        if self._in_flight >= self._capacity:
            raise LimitReachedError(RefusalKind.CAPACITY_SHED, operation)
        self._in_flight += 1
        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        try:
            yield
        finally:
            self._in_flight -= 1


@asynccontextmanager
async def deadline(seconds: float, operation: RefusedOperation) -> AsyncIterator[None]:
    """Bound the enclosed work in time, cancelling it when the bound is reached.

    The cancellation is the control. On expiry the enclosed operation is cancelled, its slot is
    released by the ``finally`` above, and the caller is refused — rather than the caller being
    answered while the work carries on.
    """
    try:
        async with asyncio.timeout(seconds):
            yield
    except TimeoutError:
        raise LimitReachedError(RefusalKind.CAPACITY_SHED, operation) from None
