"""The shared store's schema, written as explicit SQL.

Two things here are load-bearing rather than incidental.

**The allowance lives in the store, not in a process.** ``tenant_allowances`` is a table because a
quota enforced in process memory is not one quota when two processes serve the endpoint — it is one
quota per process. Every replica reserves against these same rows.

**The two ``within`` constraints are a database-enforced backstop.**
``tenant_allowances_within_allowance`` and ``spend_periods_within_cap`` state, in the store itself,
the invariant the application's reservations already maintain: committed plus reserved money never
exceeds the budget. A correct application never reaches them. They exist so that if it ever did, the
store refuses the write instead of quietly letting a tenant spend past its partition.

Money is held in two columns rather than one because a reservation and a charge are different
facts. ``reserved_cents`` is money held for work that has been admitted but not yet performed;
``committed_cents`` is money actually spent. The allowance bounds their *sum*, so concurrent
requests cannot each see room that only one of them can have.
"""

from __future__ import annotations

from typing import Final

CREATE_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id    TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS records (
    record_id       TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants (tenant_id),
    company_name    TEXT NOT NULL,
    registry_number TEXT,
    enriched_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS records_tenant_id_idx ON records (tenant_id, record_id);

CREATE TABLE IF NOT EXISTS jobs (
    job_id           UUID PRIMARY KEY,
    tenant_id        TEXT   NOT NULL REFERENCES tenants (tenant_id),
    status           TEXT   NOT NULL,
    records_admitted INTEGER NOT NULL DEFAULT 0,
    cents_charged    BIGINT  NOT NULL DEFAULT 0,
    served_by        TEXT   NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT jobs_records_admitted_non_negative CHECK (records_admitted >= 0),
    CONSTRAINT jobs_cents_charged_non_negative CHECK (cents_charged >= 0)
);

CREATE INDEX IF NOT EXISTS jobs_tenant_id_idx ON jobs (tenant_id);

-- One tenant's partition of the fictional monthly budget, and what it has spent against it.
CREATE TABLE IF NOT EXISTS tenant_allowances (
    tenant_id         TEXT   PRIMARY KEY REFERENCES tenants (tenant_id),
    period_id         TEXT   NOT NULL,
    allowance_cents   BIGINT NOT NULL,
    reserved_cents    BIGINT NOT NULL DEFAULT 0,
    committed_cents   BIGINT NOT NULL DEFAULT 0,
    lookups_performed BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT tenant_allowances_reserved_non_negative CHECK (reserved_cents >= 0),
    CONSTRAINT tenant_allowances_committed_non_negative CHECK (committed_cents >= 0),
    CONSTRAINT tenant_allowances_within_allowance
        CHECK (committed_cents + reserved_cents <= allowance_cents)
);

-- The whole fictional company's budget for the period. Partitioned into the allowances above.
CREATE TABLE IF NOT EXISTS spend_periods (
    period_id       TEXT   PRIMARY KEY,
    cap_cents       BIGINT NOT NULL,
    reserved_cents  BIGINT NOT NULL DEFAULT 0,
    committed_cents BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT spend_periods_reserved_non_negative CHECK (reserved_cents >= 0),
    CONSTRAINT spend_periods_committed_non_negative CHECK (committed_cents >= 0),
    CONSTRAINT spend_periods_within_cap
        CHECK (committed_cents + reserved_cents <= cap_cents)
);
"""

TRUNCATE_ALL: Final = """
TRUNCATE TABLE jobs, records, tenant_allowances, spend_periods, tenants RESTART IDENTITY CASCADE;
"""

BACKSTOP_CONSTRAINTS: Final = (
    "tenant_allowances_within_allowance",
    "spend_periods_within_cap",
)
"""The two constraints that state the budget invariant in the store itself."""

PRESENT_BACKSTOP_CONSTRAINTS: Final = """
SELECT conname FROM pg_constraint WHERE conname = ANY(%s)
"""
