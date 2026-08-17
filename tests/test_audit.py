"""The audit surface: one generic event per refusal, and nothing a caller could map limits with."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from limitless import audit
from limitless.audit import EVENT_NAME, REFUSAL_REASON, RefusedOperation, emit_refusal
from limitless.auditcheck import ALLOWED_KEYS, check
from limitless.auth import TOKEN_PREFIX


def emit(**overrides: str) -> dict[str, object]:
    payload = {
        "request_id": "enrich-00001",
        "replica": "app-a",
        "operation": RefusedOperation.ENRICH,
        "tenant_id": "TEN-ORCHID",
    }
    payload.update(overrides)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        emit_refusal(**payload)  # type: ignore[arg-type]
    parsed: dict[str, object] = json.loads(buffer.getvalue())
    return parsed


def test_an_event_carries_the_allowed_keys_and_only_those() -> None:
    assert set(emit()) == ALLOWED_KEYS


def test_an_event_carries_no_quantity_at_all() -> None:
    """No number, anywhere.

    Every quantity the event could carry is one a caller wants: the bound, the overage, the
    remaining allowance, the occupancy. The simplest way to disclose none of them is to carry no
    number at all, which is a property that can be checked rather than reviewed.
    """
    for value in emit().values():
        assert isinstance(value, str), f"the audit event carries a quantity: {value!r}"


def test_an_event_never_names_the_bound_that_refused() -> None:
    """ "A limit was reached" is the whole message. Which one is not disclosed."""
    naming_a_specific_bound = (
        "body",
        "batch",
        "page",
        "decompress",
        "expansion",
        "ratio",
        "allowance",
        "quota",
        "remaining",
        "reset",
        "occupancy",
        "in_flight",
        "capacity",
        "cents",
        "bytes",
    )
    # The *values* are what an event discloses; the key names are fixed and already asserted
    # elsewhere to be exactly the allowed set.
    disclosed = " ".join(str(value) for value in emit().values()).lower()
    for term in naming_a_specific_bound:
        assert term not in disclosed, f"the audit event names {term!r}"


def test_every_refusal_carries_the_same_reason() -> None:
    """A body one byte too large and an exhausted budget must be indistinguishable in the log."""
    reasons = {str(emit(operation=operation)["reason"]) for operation in RefusedOperation}
    assert reasons == {REFUSAL_REASON}


def test_the_gate_counts_events_exactly() -> None:
    stream = "\n".join(json.dumps(emit()) for _ in range(3))
    assert check(stream, expected=3) == []
    assert check(stream, expected=2) != []
    assert check(stream, expected=4) != []


def test_the_gate_ignores_ordinary_log_lines() -> None:
    stream = "\n".join(
        ["INFO: application startup complete", json.dumps(emit()), 'GET /v1/usage 200 - "-"']
    )
    assert check(stream, expected=1) == []


def test_the_gate_rejects_a_disclosed_field() -> None:
    leaky = emit()
    leaky["remaining_cents"] = 1200
    failures = check(json.dumps(leaky), expected=1)
    assert any("unexpected fields" in failure for failure in failures)


def test_the_gate_rejects_a_distinguishing_reason() -> None:
    distinguishing = emit()
    distinguishing["reason"] = "batch_too_large"
    failures = check(json.dumps(distinguishing), expected=1)
    assert any("indistinguishable" in failure for failure in failures)


def test_the_gate_rejects_a_token_anywhere_in_the_stream() -> None:
    stream = f"{json.dumps(emit())}\nAuthorization: Bearer {TOKEN_PREFIX}ten-orchid\n"
    failures = check(stream, expected=1)
    assert any("bearer token" in failure for failure in failures)


def test_the_event_name_is_stable() -> None:
    assert audit.EVENT_NAME == EVENT_NAME == "limit_refusal"
