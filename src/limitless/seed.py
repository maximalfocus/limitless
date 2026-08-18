"""Create the schema and (re)seed the fictional fixtures — including the provider's empty bill.

Seeding is *setup*, not observation: it is the only place in the project that touches the database
directly on behalf of the demonstration, and the only place that speaks to the provider fixture's
control endpoints. Every claim the demonstration goes on to make is read back through the
application's own HTTP boundary and the provider's own ledger instead.

Every run starts from nothing. The tables are truncated and rebuilt, the provider's ledger is
emptied, and its instrumentation is switched off, so no run can inherit another run's state and no
result can quietly depend on one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from . import fixtures, schema
from .config import RunnerConfig
from .db import Conn, connect


async def create_schema(conn: Conn) -> None:
    """Create every table, index, and constraint. Idempotent."""
    await conn.execute(schema.CREATE_SCHEMA)


async def reset_fixtures(conn: Conn) -> None:
    """Discard all state and rebuild the fictional fixtures exactly as they are defined."""
    async with conn.transaction():
        await conn.execute(schema.TRUNCATE_ALL)
        await conn.cursor().executemany(
            "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s)",
            [(tenant.tenant_id, tenant.display_name) for tenant in fixtures.TENANTS],
        )
        await conn.execute(
            "INSERT INTO spend_periods (period_id, cap_cents) VALUES (%s, %s)",
            (fixtures.SPEND_PERIOD_ID, fixtures.GLOBAL_SPEND_CAP_CENTS),
        )
        await conn.cursor().executemany(
            "INSERT INTO tenant_allowances (tenant_id, period_id, allowance_cents)"
            " VALUES (%s, %s, %s)",
            [
                (tenant_id, fixtures.SPEND_PERIOD_ID, fixtures.TENANT_ALLOWANCE_CENTS)
                for tenant_id in fixtures.BILLABLE_TENANT_IDS
            ],
        )
        await conn.cursor().executemany(
            "INSERT INTO admitted_work (tenant_id) VALUES (%s)",
            [(tenant_id,) for tenant_id in fixtures.BILLABLE_TENANT_IDS],
        )
        await conn.cursor().executemany(
            "INSERT INTO records (record_id, tenant_id, company_name) VALUES (%s, %s, %s)",
            [
                (record.record_id, record.tenant_id, record.company_name)
                for record in fixtures.seed_records()
            ],
        )


async def reset_provider(provider_url: str, *, timeout: float = 30.0) -> None:
    """Empty the provider's bill and switch its instrumentation off."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        await _wait_for_provider(client, provider_url)
        (await client.post(f"{provider_url}/ledger/reset")).raise_for_status()
        (
            await client.post(f"{provider_url}/control", json={"slow_mode": False, "held": False})
        ).raise_for_status()


async def _wait_for_provider(
    client: httpx.AsyncClient, provider_url: str, *, attempts: int = 60, delay: float = 1.0
) -> None:
    for attempt in range(1, attempts + 1):
        try:
            if (await client.get(f"{provider_url}/healthz")).status_code == httpx.codes.OK:
                return
        except httpx.HTTPError:
            pass
        if attempt == attempts:
            raise RuntimeError(f"provider fixture never became ready: {provider_url}")
        await asyncio.sleep(delay)


async def seed(config: RunnerConfig, *, create: bool = True) -> None:
    async with connect(config.database_url) as conn:
        if create:
            await create_schema(conn)
        await reset_fixtures(conn)
    await reset_provider(config.provider_url)


async def _amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="limitless-seed",
        description="Create the limitless schema and reset its fictional fixtures.",
    )
    parser.add_argument(
        "--fixtures-only",
        action="store_true",
        help="reset fixture rows without re-running the schema DDL",
    )
    args = parser.parse_args(argv)
    config = RunnerConfig.from_env()
    await seed(config, create=not args.fixtures_only)
    print(
        f"seeded {fixtures.COMPANY_NAME}: {len(fixtures.BILLABLE_TENANT_IDS)} fictional tenants "
        f"at {fixtures.TENANT_ALLOWANCE_CENTS} {fixtures.CURRENCY_LABEL} each, "
        f"a {fixtures.GLOBAL_SPEND_CAP_CENTS}-cent fictional cap for {fixtures.SPEND_PERIOD_ID}, "
        f"{fixtures.RECORDS_PER_TENANT} records per tenant, "
        f"and an empty {fixtures.PROVIDER_NAME} ledger"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
