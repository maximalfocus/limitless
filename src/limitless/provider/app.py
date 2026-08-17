"""Coastwise Registry — the fictional metered provider, as an in-network service.

This is a **fixture**. There is no real provider, no real registry, no real API, and no real money.
It exists so the demonstration can charge someone for work, keep the bill somewhere the application
under study does not control, and show that bill to the reader afterwards.

It answers enrichment lookups, charges a fixed fictional price for each one, and keeps its own
ledger. It reaches nothing outside the demonstration's network — it makes no outbound request of any
kind — and it is never presented as, or connected to, any real service.

Two documented controls exist here and nowhere else, because instrumentation belongs in the fixture
rather than in the application whose behaviour is under study:

* **slow mode** delays each lookup by a fixed configured amount, which later work uses to occupy the
  application's workers; and
* **hold / release** blocks lookups until they are released, which later work uses to make occupancy
  *arithmetic* — a configured number of held calls occupies a configured number of slots — instead
  of a race with the clock.

Both change only **when** this fixture answers. Neither changes whether the application under study
bounded its own work, which is the only thing the demonstration ever asserts on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Final

from fastapi import Body, FastAPI, Request
from pydantic import BaseModel

from .. import fixtures
from ..config import ProviderConfig
from ..models import (
    HealthResponse,
    LedgerView,
    LookupRequest,
    LookupResponse,
    LookupResult,
    ProviderControlView,
)
from .ledger import Ledger


class ControlRequest(BaseModel):
    """Instrumentation only. Absent, this fixture answers immediately and charges normally."""

    slow_mode: bool | None = None
    held: bool | None = None


NO_CONTROL_CHANGE: Final = ControlRequest()
"""A module-level default, so the request signature performs no call of its own."""


class ProviderState:
    """The fixture's mutable state: its books and its two instrumentation controls."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.ledger = Ledger(price_cents_per_lookup=config.lookup_price_cents)
        self.slow_mode = False
        # Set means "not held". Starting released is what makes the control opt-in.
        self._released = asyncio.Event()
        self._released.set()

    @property
    def held(self) -> bool:
        return not self._released.is_set()

    def hold(self) -> None:
        self._released.clear()

    def release(self) -> None:
        self._released.set()

    async def await_release(self) -> None:
        await self._released.wait()

    def control_view(self) -> ProviderControlView:
        return ProviderControlView(
            slow_mode=self.slow_mode,
            held=self.held,
            slow_delay_seconds=self.config.slow_mode_delay_seconds,
        )


def create_app(config: ProviderConfig | None = None) -> FastAPI:
    settings = config or ProviderConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.provider = ProviderState(settings)
        yield

    app = FastAPI(
        title=f"{fixtures.PROVIDER_NAME} — fictional metered provider fixture",
        summary=(
            f"A fictional company-records provider that charges "
            f"{settings.lookup_price_cents} {fixtures.CURRENCY_LABEL} per lookup and keeps its own "
            f"ledger. Local educational material only; no real provider, API, or money."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    def state_of(request: Request) -> ProviderState:
        provider: ProviderState = request.app.state.provider
        return provider

    @app.get("/healthz", response_model=HealthResponse)
    async def healthz(request: Request) -> HealthResponse:
        return HealthResponse(status="ok", replica=fixtures.PROVIDER_NAME, variant="provider")

    @app.post("/v1/lookups", response_model=LookupResponse)
    async def lookups(payload: LookupRequest, request: Request) -> LookupResponse:
        """Perform the named lookups, charge for them, and answer.

        Note what this fixture does *not* do: it does not bound the batch. A provider bills for the
        work it is asked to do, and asking it for too much is the caller's defect to prevent. That
        is precisely why the bound has to live in the application.
        """
        state = state_of(request)
        # Instrumentation, in this order: a hold blocks until released, then slow mode delays.
        await state.await_release()
        if state.slow_mode:
            await asyncio.sleep(state.config.slow_mode_delay_seconds)
        cents = state.ledger.charge(payload.tenant_id, len(payload.items))
        return LookupResponse(
            lookups=len(payload.items),
            cents_charged=cents,
            results=[
                LookupResult(
                    company_name=item.company_name,
                    registry_number=_registry_number_for(item.company_name),
                )
                for item in payload.items
            ],
        )

    @app.get("/ledger", response_model=LedgerView)
    async def ledger(request: Request) -> LedgerView:
        return state_of(request).ledger.view()

    @app.post("/ledger/reset", response_model=LedgerView)
    async def reset_ledger(request: Request) -> LedgerView:
        """Start a run from an empty bill. Setup, not observation."""
        state = state_of(request)
        state.ledger.reset()
        return state.ledger.view()

    @app.post("/control", response_model=ProviderControlView)
    async def control(
        request: Request,
        payload: Annotated[ControlRequest, Body()] = NO_CONTROL_CHANGE,
    ) -> ProviderControlView:
        """Set the fixture's instrumentation. Documented, visible, and provider-side only."""
        state = state_of(request)
        if payload.slow_mode is not None:
            state.slow_mode = payload.slow_mode
        if payload.held is not None:
            state.hold() if payload.held else state.release()
        return state.control_view()

    @app.get("/control", response_model=ProviderControlView)
    async def read_control(request: Request) -> ProviderControlView:
        return state_of(request).control_view()

    return app


def _registry_number_for(company_name: str) -> str:
    """A stable fictional registry number derived from the fictional company name.

    Deterministic so two runs of the same scenario are comparable, and derived rather than stored so
    the fixture answers for any company name a caller invents.
    """
    digest = 0
    for character in company_name:
        digest = (digest * 131 + ord(character)) % 1_000_000
    return f"CR-{digest % 10:01d}{digest:06d}"


app = create_app()
