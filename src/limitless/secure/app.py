"""The secure application — every dimension of caller-named work bounded.

Both ``app-a`` and ``app-b`` run this module. What is left here, once the shared boundary in
``limitless.api`` is taken away, is the part that matters: the order the controls run in.

That order is the design. On every path that costs money, the sequence is always

    bound the input  →  reserve the money  →  take a slot  →  do the work  →  settle

and never any other. Each step refuses before the next one can allocate anything, so a request that
is going to be refused is refused at the cheapest possible moment — before the body is buffered,
before the budget is touched, before a worker is occupied, and before the provider is ever asked to
bill anyone. A control that runs after the work is not a control; it is a report.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Final
from uuid import NAMESPACE_URL, uuid4, uuid5

import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from pydantic import ValidationError

from .. import fixtures, store
from ..api import (
    add_common_routes,
    bad_request,
    install_refusal_handler,
    pool_of,
    require_tenant,
    stamp_requests,
)
from ..audit import RefusedOperation
from ..config import AppConfig
from ..db import Conn, ConnPool, make_pool
from ..models import (
    EnrichedRecord,
    EnrichRequest,
    EnrichResponse,
    ImportResponse,
    JobStatus,
    RecordPage,
    Reservation,
)
from ..providerclient import ProviderClient
from ..refusal import LimitReachedError, RefusalKind
from ..store import AllowanceExhaustedError
from .bounds import bounded_page_size, parse_json_object, read_bounded_body, require_batch_within
from .capacity import InFlightLimiter, deadline
from .expansion import ImportedRecord, read_bounded_bundle

VARIANT: Final = "secure"


def enriched_record_id(tenant_id: str, company_name: str) -> str:
    """A stable record identifier, so re-running the demonstration produces the same records."""
    digest = uuid5(NAMESPACE_URL, f"limitless:{tenant_id}:{company_name}").hex[:12]
    return f"{tenant_id}-ENR-{digest}"


def create_app(config: AppConfig | None = None) -> FastAPI:
    settings = config or AppConfig.from_env()
    bounds = settings.bounds

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(
            settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            timeout=settings.pool_timeout_seconds,
        )
        await pool.open(wait=True, timeout=60)
        provider = ProviderClient(
            settings.provider_url,
            # Generous next to the deadline that actually governs: the control is the cancelling
            # deadline in application code, not a transport default nobody can point at.
            timeout=bounds.upstream_deadline_seconds * 4,
        )
        app.state.pool = pool
        app.state.provider = provider
        app.state.limiter = InFlightLimiter(bounds.max_in_flight_upstream)
        try:
            yield
        finally:
            await provider.aclose()
            await pool.close()

    app = FastAPI(
        title=f"limitless — secure enrichment API ({settings.replica_name})",
        summary=(
            f"Fictional {fixtures.COMPANY_NAME} enrichment API that bounds every dimension of the "
            f"work a caller can name. Local educational material only."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = settings
    stamp_requests(app, settings.replica_name)
    install_refusal_handler(app, settings)
    add_common_routes(app, settings, variant=VARIANT, usage_source=store.read_usage)

    def limiter_of(request: Request) -> InFlightLimiter:
        limiter: InFlightLimiter = request.app.state.limiter
        return limiter

    def provider_of(request: Request) -> ProviderClient:
        provider: ProviderClient = request.app.state.provider
        return provider

    async def perform_lookups(
        request: Request,
        *,
        pool: ConnPool,
        tenant_id: str,
        company_names: list[str],
        reservation: Reservation,
        operation: RefusedOperation,
    ) -> tuple[list[EnrichedRecord], int, int]:
        """Do the admitted work under a slot and a cancelling deadline, then settle the money.

        No database connection is held across the upstream call, deliberately. A connection kept
        open for the duration of somebody else's I/O is one more bounded resource a caller can
        occupy, and holding the whole pool behind one slow provider would quietly make the pool the
        real limit and leave the in-flight cap a decoration. Connections are taken for the two short
        writes that need them and given straight back.

        Every exit that is not a completed settle releases the reservation, so money is never held
        for work that did not happen.
        """
        attempted = False
        try:
            async with (
                deadline(bounds.request_deadline_seconds, operation),
                limiter_of(request).slot(operation),
                deadline(bounds.upstream_deadline_seconds, operation),
            ):
                # From here on the provider may have been reached, and a provider that was reached
                # has already billed for what it was asked to do.
                attempted = True
                result = await provider_of(request).lookups(
                    tenant_id=tenant_id, company_names=company_names
                )
            entries = [
                (
                    enriched_record_id(tenant_id, item.company_name),
                    item.company_name,
                    item.registry_number,
                )
                for item in result.results
            ]
            # Built before the money is settled, deliberately. Settling is the last fallible thing
            # this function does, so the release below can never run against a completed charge.
            enriched = [
                EnrichedRecord(
                    record_id=record_id,
                    company_name=company_name,
                    registry_number=registry_number,
                )
                for record_id, company_name, registry_number in entries
            ]
            async with pool.connection() as conn:
                await store.upsert_records(
                    conn, tenant_id=tenant_id, entries=entries, enriched_at=datetime.now(UTC)
                )
                await store.settle(
                    conn,
                    reservation,
                    lookups_performed=result.lookups,
                    cents_charged=result.cents_charged,
                )
            return enriched, result.lookups, result.cents_charged
        except BaseException:
            async with pool.connection() as conn:
                if attempted:
                    # Fail closed. A cancelled call is not a free call: the deadline stopped *us*
                    # waiting, not necessarily the provider working, and the money may already be
                    # gone. Handing the reservation back would let this tenant spend it a second
                    # time, and the sum of those second chances is how an allowance quietly stops
                    # being an allowance. Charging for work that turns out not to have happened
                    # costs the tenant a little of its own budget; refunding work that did happen
                    # costs the company its cap.
                    await store.settle(
                        conn,
                        reservation,
                        lookups_performed=reservation.lookups,
                        cents_charged=reservation.cents,
                    )
                else:
                    await store.release(conn, reservation)
            raise

    async def reserve_or_refuse(
        conn: Conn, *, tenant_id: str, lookups: int, operation: RefusedOperation
    ) -> Reservation:
        try:
            return await store.reserve(
                conn,
                tenant_id=tenant_id,
                lookups=lookups,
                price_cents=settings.lookup_price_cents,
            )
        except AllowanceExhaustedError:
            raise LimitReachedError(RefusalKind.ALLOWANCE_EXHAUSTED, operation) from None

    @app.post("/v1/enrich", response_model=EnrichResponse, status_code=status.HTTP_201_CREATED)
    async def enrich(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> EnrichResponse:
        operation = RefusedOperation.ENRICH
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id

        # 1. how big — bounded while the stream is read, before anything is buffered.
        body = await read_bounded_body(
            request, max_bytes=bounds.max_body_bytes, operation=operation
        )
        try:
            payload = EnrichRequest.model_validate(parse_json_object(body))
        except (ValidationError, ValueError):
            raise bad_request() from None

        # 2. how many — each item is a metered lookup, so the count is a statement about money.
        require_batch_within(
            list(payload.records), max_items=bounds.max_batch_items, operation=operation
        )

        pool = pool_of(request)
        # 3. how much — the money is held before any of the work is performed.
        async with pool.connection() as conn:
            reservation = await reserve_or_refuse(
                conn, tenant_id=tenant_id, lookups=len(payload.records), operation=operation
            )
        results, lookups, cents = await perform_lookups(
            request,
            pool=pool,
            tenant_id=tenant_id,
            company_names=[record.company_name for record in payload.records],
            reservation=reservation,
            operation=operation,
        )
        return EnrichResponse(
            tenant_id=tenant_id,
            records_admitted=len(results),
            lookups_performed=lookups,
            cents_charged=cents,
            results=results,
        )

    @app.get("/v1/records", response_model=RecordPage)
    async def list_records(
        request: Request,
        page_size: Annotated[str | None, Query()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> RecordPage:
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id
        # A caller-supplied page size is refused against a server-side maximum, never clamped.
        size = bounded_page_size(page_size, max_page_size=bounds.max_page_size)
        async with pool_of(request).connection() as conn:
            return await store.read_records(conn, tenant_id=tenant_id, page_size=size)

    @app.post("/v1/imports", response_model=ImportResponse, status_code=status.HTTP_201_CREATED)
    async def import_bundle(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ImportResponse:
        operation = RefusedOperation.IMPORT_BUNDLE
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id

        # 1. how big, and how much it becomes — both bounded during decompression, mid-stream.
        try:
            bundle = await read_bounded_bundle(request, bounds=bounds, operation=operation)
        except ValueError:
            raise bad_request() from None

        records: list[ImportedRecord] = bundle.records
        job_id = uuid4()
        pool = pool_of(request)
        # 2. the money, held before the enrichment behind this bundle is performed.
        async with pool.connection() as conn:
            reservation = await reserve_or_refuse(
                conn, tenant_id=tenant_id, lookups=len(records), operation=operation
            )
        results, _lookups, cents = await perform_lookups(
            request,
            pool=pool,
            tenant_id=tenant_id,
            company_names=[record.company_name for record in records],
            reservation=reservation,
            operation=operation,
        )
        async with pool.connection() as conn:
            await store.insert_job(
                conn,
                job_id=job_id,
                tenant_id=tenant_id,
                records_admitted=len(results),
                cents_charged=cents,
                served_by=settings.replica_name,
            )
        return ImportResponse(
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.COMPLETED,
            records_admitted=len(results),
            cents_charged=cents,
        )

    @app.exception_handler(psycopg.OperationalError)
    async def on_database_unavailable(request: Request, exc: Exception) -> None:
        # Say so on the way out. The client still gets the same generic answer it always gets, but
        # an operator gets the class of failure — because a store problem that is indistinguishable
        # from a capacity refusal is a store problem nobody will find. This is the operator's
        # channel, not the caller's: no bound, allowance, or occupancy is disclosed to anyone.
        print(
            json.dumps(
                {
                    "event": "store_unavailable",
                    "replica": settings.replica_name,
                    "request_id": getattr(request.state, "request_id", "-"),
                    "error": type(exc).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            flush=True,
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail="service unavailable"
        ) from exc

    return app


app = create_app()
