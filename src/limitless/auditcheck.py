"""Assert the shape and count of the rejection audit events in a captured log stream.

The application writes its audit events to standard output, so proving the audit surface means
reading the containers' logs back and checking four things at once:

1. **exactly one** event per refusal — no more, no fewer;
2. every event carries the allowed keys and **only** the allowed keys, so no run can quietly start
   disclosing which bound was crossed, how much allowance remains, or how many slots are occupied;
3. every event carries the **same** reason, so a body one byte too large and an exhausted budget are
   indistinguishable in the log as well as in the response; and
4. no demonstration bearer token appears **anywhere** in the captured stream.

Reads the log stream on standard input, so the caller needs nothing but Docker and a pipe.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from typing import Any, Final

from .audit import EVENT_NAME, REFUSAL_REASON
from .auth import TOKEN_PREFIX

ALLOWED_KEYS: Final = frozenset(
    {
        "event",
        "request_id",
        "replica",
        "operation",
        "tenant_id",
        "outcome",
        "reason",
    }
)


def _events(lines: Iterable[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("event") == EVENT_NAME:
            found.append(parsed)
    return found


def check(stream: str, *, expected: int) -> list[str]:
    """Return a list of failures; empty means the audit surface is exactly as required."""
    failures: list[str] = []
    events = _events(stream.splitlines())

    if len(events) != expected:
        failures.append(f"expected exactly {expected} refusal audit events, found {len(events)}")

    for index, event in enumerate(events, start=1):
        extra = sorted(set(event) - ALLOWED_KEYS)
        missing = sorted(ALLOWED_KEYS - set(event))
        if extra:
            failures.append(f"event {index} discloses unexpected fields: {extra}")
        if missing:
            failures.append(f"event {index} is missing required fields: {missing}")
        if event.get("reason") != REFUSAL_REASON:
            failures.append(
                f"event {index} reason is {event.get('reason')!r}, "
                f"which distinguishes refusals that must be indistinguishable"
            )
        if event.get("outcome") != "refused":
            failures.append(f"event {index} outcome is {event.get('outcome')!r}")

    if TOKEN_PREFIX in stream:
        failures.append("a demonstration bearer token appeared in the captured log stream")

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="limitless-auditcheck",
        description="Verify the rejection audit events in a captured log stream on stdin.",
    )
    parser.add_argument(
        "--expected", type=int, required=True, help="number of refusals the run produced"
    )
    args = parser.parse_args(argv)

    failures = check(sys.stdin.read(), expected=args.expected)
    if failures:
        print("audit gate FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print(
        f"audit gate passed: exactly {args.expected} generic refusal events, "
        f"no disclosed detail, no token in the log stream"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
