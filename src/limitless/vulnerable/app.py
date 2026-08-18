"""The intentionally vulnerable application.

**This service has no bounds on the work a caller may name.** It is local educational material.
It must never be deployed or exposed, and it will not start without two deliberate actions.

Read it as a diff against ``limitless.secure.app``. The routes are the same, the payloads are the
same, the credentials are the same, and the refusal responses are the same. Four things are missing,
and each missing thing is one of the demonstration's shapes:

1. **nothing asks how much.** The body is read whole, the batch length is whatever arrived, and the
   page size is whatever was asked for.
2. **the budget is not partitioned.** Every tenant spends from one undivided pool, so the first
   tenant to drain it decides what is left for the others.
3. **expansion is measured after the fact, on the wrong number.** The bundle is decompressed to
   completion and then checked against its *compressed* size.
4. **nothing bounds work in flight, and no deadline cancels anything.** A database connection is
   held for the whole duration of the upstream call, so a slow provider takes the connection pool
   with it — and endpoints that need nothing but a connection stop being served.

The fourth is worth being explicit about, because it is the one that takes down the innocent
endpoint. ``GET /v1/jobs/{job_id}`` touches no provider, no budget, and no expensive path. It fails
anyway, because something else was allowed to hold every connection there was.
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
from ..db import ConnPool, make_pool
from ..models import (
    EnrichedRecord,
    EnrichRequest,
    EnrichResponse,
    ImportResponse,
    JobStatus,
    RecordPage,
)
from ..providerclient import ProviderClient
from ..refusal import LimitReachedError, RefusalKind
from .acknowledgement import require_acknowledgement
from .shapes import (
    COMPRESSED_BODY_LIMIT_BYTES,
    decompress_completely,
    parse_json_object,
    read_whole_body,
    whatever_page_size_was_asked_for,
)

VARIANT: Final = "vulnerable"

DEFAULT_PAGE_SIZE: Final = 200
ADMIT_FLUSH_RECORDS: Final = 50_000
"""How often admitted work is written down while a bundle is being taken on.

Small enough that a process which dies partway through still leaves an honest record of most of what
it had already committed to, which is the only reason anybody would find out.
"""

ENRICH_CHUNK_RECORDS: Final = 5_000
"""How many records are sent to the provider at a time. Not a bound — the loop has no end condition
except running out of records or running out of the company's money."""


def enriched_record_id(tenant_id: str, company_name: str) -> str:
    """Identical to the secure application's. The record identity is not what differs."""
    digest = uuid5(NAMESPACE_URL, f"limitless:{tenant_id}:{company_name}").hex[:12]
    return f"{tenant_id}-ENR-{digest}"


def create_app(config: AppConfig | None = None) -> FastAPI:
    require_acknowledgement()
    settings = config or AppConfig.from_env()
    price = settings.lookup_price_cents

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = make_pool(
            settings.database_url,
            min_size=settings.pool_min_size,
            max_size=settings.pool_max_size,
            timeout=settings.pool_timeout_seconds,
        )
        await pool.open(wait=True, timeout=60)
        # No timeout on the provider. A deadline is one of the things this variant does not have.
        provider = ProviderClient(settings.provider_url, timeout=None)
        app.state.pool = pool
        app.state.provider = provider
        try:
            yield
        finally:
            await provider.aclose()
            await pool.close()

    app = FastAPI(
        title=f"limitless — INTENTIONALLY VULNERABLE enrichment API ({settings.replica_name})",
        summary=(
            "Deliberately unbounded variant of the fictional enrichment API. No bound on body "
            "size, batch length, page size, expansion, spend partition, in-flight work, or "
            "deadlines. Local educational material only — never deploy this."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = settings
    stamp_requests(app, settings.replica_name)
    install_refusal_handler(app, settings)
    add_common_routes(app, settings, variant=VARIANT, usage_source=store.read_admitted_usage)

    def provider_of(request: Request) -> ProviderClient:
        provider: ProviderClient = request.app.state.provider
        return provider

    def pool_drained(operation: RefusedOperation) -> LimitReachedError:
        """The only refusal this variant has: the company's money ran out.

        Not the tenant's money — the company's. There is no per-tenant partition to run out of.
        """
        return LimitReachedError(RefusalKind.ALLOWANCE_EXHAUSTED, operation)

    @app.post("/v1/enrich", response_model=EnrichResponse, status_code=status.HTTP_201_CREATED)
    async def enrich(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> EnrichResponse:
        operation = RefusedOperation.ENRICH
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id

        # Shape 1: the client names the work. Nothing here asks how much of it there is.
        body = await read_whole_body(request)
        try:
            payload = EnrichRequest.model_validate(parse_json_object(body))
        except (ValidationError, ValueError):
            raise bad_request() from None
        lookups = len(payload.records)

        pool: ConnPool = pool_of(request)
        # Shape 4: the connection is held for the whole upstream call. Every request that is waiting
        # on the provider is also sitting on a connection that nothing else can have.
        async with pool.connection() as conn:
            # Shape 2: one undivided pool, with no notion of whose money this is.
            if not await store.charge_undivided_pool(conn, lookups=lookups, price_cents=price):
                raise pool_drained(operation)
            await store.record_admitted_work(
                conn, tenant_id=tenant_id, records=lookups, cents=lookups * price
            )
            result = await provider_of(request).lookups(
                tenant_id=tenant_id, company_names=[r.company_name for r in payload.records]
            )
            entries = [
                (
                    enriched_record_id(tenant_id, item.company_name),
                    item.company_name,
                    item.registry_number,
                )
                for item in result.results
            ]
            await store.upsert_records(
                conn, tenant_id=tenant_id, entries=entries, enriched_at=datetime.now(UTC)
            )
        return EnrichResponse(
            tenant_id=tenant_id,
            records_admitted=len(entries),
            lookups_performed=result.lookups,
            cents_charged=result.cents_charged,
            results=[
                EnrichedRecord(
                    record_id=record_id, company_name=company_name, registry_number=number
                )
                for record_id, company_name, number in entries
            ],
        )

    @app.get("/v1/records", response_model=RecordPage)
    async def list_records(
        request: Request,
        page_size: Annotated[str | None, Query()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> RecordPage:
        tenant_id = require_tenant(authorization)
        request.state.tenant_id = tenant_id
        # Shape 1, second sink: about sixty bytes of query string names the whole result set.
        size = whatever_page_size_was_asked_for(page_size, default=DEFAULT_PAGE_SIZE)
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

        # Shape 3, mistake one: the whole compressed body is in memory before anything is checked.
        compressed = await read_whole_body(request)
        # Shape 3, mistake two: the check is on the compressed size, and it runs far too late.
        if len(compressed) > COMPRESSED_BODY_LIMIT_BYTES:
            raise LimitReachedError(RefusalKind.INPUT_TOO_LARGE, operation)
        try:
            bundle = decompress_completely(compressed)
        except ValueError:
            raise bad_request() from None

        job_id = uuid4()
        pool: ConnPool = pool_of(request)

        # Every record in the bundle is now admitted work: the application has committed itself to
        # billing for all of it. That commitment is written down as it is made, in flushes, so a
        # process that does not survive the bundle still leaves behind what it had taken on.
        remaining = bundle.records
        async with pool.connection() as conn:
            while remaining > 0:
                flush = min(ADMIT_FLUSH_RECORDS, remaining)
                await store.record_admitted_work(
                    conn, tenant_id=tenant_id, records=flush, cents=flush * price
                )
                remaining -= flush

        # ...and then it starts paying for it, until the company's money runs out. There is no
        # condition on this loop other than the bundle ending or the pool refusing.
        performed = 0
        charged = 0
        drained = False
        async with pool.connection() as conn:
            while performed < bundle.records and not drained:
                chunk = min(ENRICH_CHUNK_RECORDS, bundle.records - performed)
                # Already admitted above; this only spends. Recording it again would count the
                # same obligation twice, and an accounting that flatters itself is no accounting.
                if not await store.charge_undivided_pool(conn, lookups=chunk, price_cents=price):
                    drained = True
                    break
                result = await provider_of(request).lookups(
                    tenant_id=tenant_id,
                    company_names=[fixtures.company_name(i) for i in range(1, chunk + 1)],
                    return_results=False,
                )
                performed += result.lookups
                charged += result.cents_charged
            await store.insert_job(
                conn,
                job_id=job_id,
                tenant_id=tenant_id,
                records_admitted=bundle.records,
                cents_charged=charged,
                served_by=settings.replica_name,
            )
        return ImportResponse(
            job_id=job_id,
            tenant_id=tenant_id,
            status=JobStatus.COMPLETED,
            records_admitted=bundle.records,
            cents_charged=charged,
        )

    @app.exception_handler(psycopg.OperationalError)
    async def on_database_unavailable(request: Request, exc: Exception) -> None:
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
