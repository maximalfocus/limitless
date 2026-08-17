"""Shared test fixtures.

Every test that touches the store or the provider's bill gets freshly seeded state, because a test
that inherits another test's spending would be measuring the wrong thing — and in a project about
budgets, an inherited balance is the most misleading state there is.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from limitless import fixtures
from limitless.config import RunnerConfig
from limitless.db import connect
from limitless.httpclient import HalyardHTTP
from limitless.seed import seed


async def set_allowance(config: RunnerConfig, tenant_id: str, cents: int) -> None:
    """Narrow a tenant's partition, so a test can reach the boundary without buying its way there.

    This is *setup*, in the same category as seeding, and it is the only thing in the suite that
    writes to the store directly. Every outcome is still read back through the product's own
    boundary and the provider's own ledger.
    """
    async with connect(config.database_url) as conn:
        await conn.execute(
            "UPDATE tenant_allowances SET allowance_cents = %s WHERE tenant_id = %s",
            (cents, tenant_id),
        )


async def set_global_cap(config: RunnerConfig, cents: int) -> None:
    """Narrow the whole fictional company's cap, to prove it refuses independently."""
    async with connect(config.database_url) as conn:
        await conn.execute(
            "UPDATE spend_periods SET cap_cents = %s WHERE period_id = %s",
            (cents, fixtures.SPEND_PERIOD_ID),
        )


@pytest.fixture(scope="session")
def config() -> RunnerConfig:
    return RunnerConfig.from_env()


@pytest.fixture
async def fresh_state(config: RunnerConfig) -> None:
    """Fresh fixtures, an empty provider ledger, and no instrumentation."""
    await seed(config)


@pytest.fixture
async def client(config: RunnerConfig, fresh_state: None) -> AsyncIterator[HalyardHTTP]:
    async with HalyardHTTP(
        config.replica_urls,
        provider_url=config.provider_url,
        timeout=config.request_timeout_seconds,
    ) as http:
        await http.wait_until_ready()
        yield http


@pytest.fixture
async def single_replica_client(
    config: RunnerConfig, fresh_state: None
) -> AsyncIterator[HalyardHTTP]:
    """A client addressing exactly one replica, for assertions about a single process."""
    async with HalyardHTTP(
        config.replica_urls[:1],
        provider_url=config.provider_url,
        timeout=config.request_timeout_seconds,
    ) as http:
        await http.wait_until_ready()
        yield http
