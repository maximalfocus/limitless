"""Secure control D: in-flight work is capped and shed, and deadlines actually cancel.

Nothing here asserts on elapsed time. The cancellation test proves its property by catching the
``CancelledError`` inside the work itself, which is a fact about control flow rather than about how
long anything took, and the shedding test proves its property by refusing while the slots are still
demonstrably held.
"""

from __future__ import annotations

import asyncio

import pytest

from limitless.audit import RefusedOperation
from limitless.refusal import LimitReachedError, RefusalKind
from limitless.secure.capacity import InFlightLimiter, deadline

OPERATION = RefusedOperation.ENRICH


async def test_the_limiter_admits_exactly_its_capacity() -> None:
    limiter = InFlightLimiter(3)
    async with limiter.slot(OPERATION), limiter.slot(OPERATION), limiter.slot(OPERATION):
        assert limiter.in_flight == 3
        with pytest.raises(LimitReachedError) as raised:
            async with limiter.slot(OPERATION):
                pytest.fail("a fourth slot was handed out")
        assert raised.value.kind is RefusalKind.CAPACITY_SHED
        # Still exactly three: the refusal did not queue behind them and did not take a slot.
        assert limiter.in_flight == 3


async def test_a_slot_is_released_when_its_work_ends() -> None:
    limiter = InFlightLimiter(1)
    async with limiter.slot(OPERATION):
        assert limiter.in_flight == 1
    assert limiter.in_flight == 0
    async with limiter.slot(OPERATION):
        assert limiter.in_flight == 1


async def test_a_slot_is_released_even_when_its_work_raises() -> None:
    limiter = InFlightLimiter(1)
    with pytest.raises(RuntimeError):
        async with limiter.slot(OPERATION):
            raise RuntimeError("the work failed")
    assert limiter.in_flight == 0


async def test_excess_is_shed_rather_than_queued() -> None:
    """The refusal happens while the slots are held, not once one frees up.

    Queueing without bound is not a limit — it is the same unbounded resource under another name.
    """
    limiter = InFlightLimiter(1)
    holding = asyncio.Event()
    release = asyncio.Event()

    async def hold_a_slot() -> None:
        async with limiter.slot(OPERATION):
            holding.set()
            await release.wait()

    held = asyncio.create_task(hold_a_slot())
    await holding.wait()

    with pytest.raises(LimitReachedError):
        async with limiter.slot(OPERATION):
            pytest.fail("a slot was handed out while the cap was full")

    release.set()
    await held
    assert limiter.peak_in_flight == 1


async def test_peak_occupancy_is_tracked() -> None:
    limiter = InFlightLimiter(4)
    async with limiter.slot(OPERATION), limiter.slot(OPERATION):
        pass
    assert limiter.peak_in_flight == 2
    assert limiter.in_flight == 0


def test_a_capacity_below_one_is_not_a_capacity() -> None:
    with pytest.raises(ValueError):
        InFlightLimiter(0)


async def test_the_deadline_cancels_the_work_it_bounds() -> None:
    """The property that distinguishes a deadline from a timeout that merely answers the caller."""
    cancelled = False

    async def blocked_forever() -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    with pytest.raises(LimitReachedError) as raised:
        async with deadline(0.05, OPERATION):
            await blocked_forever()

    assert raised.value.kind is RefusalKind.CAPACITY_SHED
    assert cancelled, "the deadline returned without cancelling the work underneath it"


async def test_a_deadline_that_is_not_reached_changes_nothing() -> None:
    async with deadline(30.0, OPERATION):
        result = "completed"
    assert result == "completed"


async def test_a_cancelled_request_gives_its_slot_back() -> None:
    """A deadline firing inside a slot must not leak the slot it was holding."""
    limiter = InFlightLimiter(1)

    with pytest.raises(LimitReachedError):
        async with deadline(0.05, OPERATION), limiter.slot(OPERATION):
            await asyncio.Event().wait()

    assert limiter.in_flight == 0
    async with limiter.slot(OPERATION):
        assert limiter.in_flight == 1
