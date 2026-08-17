"""Four different credential failures produce one indistinguishable answer.

Authentication is not the subject of this project, and that is exactly why it has to be correct
here: the attacking tenant is authenticated and authorized throughout, so no access-control fix
reaches any of what follows. What matters is that a caller cannot learn *why* a credential failed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from limitless import fixtures
from limitless.auth import EXPIRED_TOKEN, TOKENS, UNKNOWN_TOKEN, authenticate, token_for
from limitless.httpclient import HalyardHTTP


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "NotBearer something",
        f"Basic {token_for(fixtures.ATTACKER_TENANT_ID)}",
        f"Bearer {UNKNOWN_TOKEN}",
        f"Bearer {EXPIRED_TOKEN}",
    ],
)
def test_every_failure_mode_returns_the_same_nothing(header: str | None) -> None:
    assert authenticate(header) is None


def test_a_valid_credential_yields_its_server_derived_tenant() -> None:
    for tenant_id in fixtures.BILLABLE_TENANT_IDS:
        assert authenticate(f"Bearer {token_for(tenant_id)}") == tenant_id


def test_expiry_is_a_fixed_instant_rather_than_a_clock_reading() -> None:
    """ "Expired" must mean the same thing on every machine and in every run."""
    before = datetime(2019, 1, 1, tzinfo=UTC)
    assert authenticate(f"Bearer {EXPIRED_TOKEN}", now=before) == fixtures.EXPIRED_TENANT_ID
    assert authenticate(f"Bearer {EXPIRED_TOKEN}") is None


def test_the_expired_tenant_is_the_only_expiring_credential() -> None:
    expiring = [token.tenant_id for token in TOKENS.values() if token.expires_at is not None]
    assert expiring == [fixtures.EXPIRED_TENANT_ID]


def test_tokens_are_conspicuously_fictional() -> None:
    for token in TOKENS:
        assert token.startswith("limitless-demo-token-")


async def test_the_api_answers_every_credential_failure_identically(client: HalyardHTTP) -> None:
    probes = [None, "NotBearer whatever", f"Bearer {UNKNOWN_TOKEN}", f"Bearer {EXPIRED_TOKEN}"]
    answers = set()
    for sequence, authorization in enumerate(probes, start=1):
        record = await client.probe_credential(sequence=sequence, authorization=authorization)
        answers.add((record.status_code, str(record.body)))
    assert len(answers) == 1, f"credential failures were distinguishable: {answers}"
    assert next(iter(answers))[0] == 401
