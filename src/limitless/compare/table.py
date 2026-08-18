"""Rendering the comparison for a reader.

The narrative comes first and is short, because a table nobody has been told how to read is a table
nobody reads. Then every scenario in one grid, in the same columns and the same units, so the two
variants can be read across rather than argued about.

The **amplification ratio** is given the last word. Its units are fictional cents admitted per byte
of input the caller supplied, and it is the only column where the secure and unbounded sides differ
by orders of magnitude rather than by degree — which is the entire lesson, in one number.

There is no duration anywhere in this output. This project measures admitted work and charged cost;
it makes no throughput, latency, or capacity claim of any kind.
"""

from __future__ import annotations

from typing import Final

from .. import fixtures
from ..config import HarnessConfig
from .engine import ComparisonRow, cap_multiple, unbounded_rows, worst_ratio

WIDTH: Final = 190

NARRATIVE: Final = """\
A request names work, and the request's own size tells you nothing about how much.

Every row below is the same fictional API, the same fictional provider at four fictional cents a
lookup, and the same fictional 250 000-cent monthly budget. What changes between the rows is
whether anything on the request path asks *how much*. Read the last two columns together: the
ratio is what the caller bought per byte they sent, and the verdict is whether anything stopped it.

The secure rows bound all five dimensions — how big, how many, how much it becomes, how often, and
how many at once. The unbounded rows bound none of them, and the rows in between are the repairs
that look like they do."""


def _cell(value: object, width: int, *, right: bool = True) -> str:
    text = f"{value:,}" if isinstance(value, int) else str(value)
    return text.rjust(width) if right else text.ljust(width)


def render(rows: tuple[ComparisonRow, ...], config: HarnessConfig, *, verbose: bool = False) -> str:
    """The whole comparison, as a table with a short narrative in front of it."""
    lines: list[str] = [
        "",
        f"{fixtures.COMPANY_NAME} — unrestricted resource consumption, every scenario side by side",
        "=" * WIDTH,
        "",
        NARRATIVE,
        "",
        "=" * WIDTH,
    ]

    header = (
        f"{'scenario':<52} {'variant':<11} {'mode':<14} {'rep':>3} {'conc':>4} "
        f"{'input B':>11} {'items':>10} {'lookups':>10} {'cents':>11} {'ratio':>10} "
        f"{'cheap':>7} {'refusals':>28} {'verdict':<16}"
    )
    lines.append(header)
    lines.append("-" * WIDTH)

    for row in rows:
        lines.append(
            f"{row.scenario[:52]:<52} {row.variant:<11} {row.mode:<14} "
            f"{row.replicas:>3} {row.concurrency:>4} "
            f"{_cell(row.input_bytes, 11)} {_cell(row.items_admitted, 10)} "
            f"{_cell(row.lookups, 10)} {_cell(row.cents, 11)} "
            f"{row.amplification_ratio:>10.4f} {row.cheap_endpoint:>7} "
            f"{_refusals(row.refusals):>28} {row.verdict:<16}"
        )
        lines.append(f"{'':<52} bound in effect: {row.bound_in_effect}")

    lines.append("-" * WIDTH)
    lines.extend(_closing(rows))

    if verbose:
        lines.extend(_records(rows))
    lines.append("")
    return "\n".join(lines) + "\n"


def _refusals(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))


def _closing(rows: tuple[ComparisonRow, ...]) -> list[str]:
    secure = tuple(row for row in rows if row.variant == "secure")
    unbounded = unbounded_rows(rows)
    worst = max(unbounded, key=lambda row: row.amplification_ratio, default=None)

    lines = [
        "",
        f"  secure scenarios     : {len(secure)}, every one bounded, "
        f"worst amplification {worst_ratio(secure):.4f} cents per input byte",
        f"  unbounded scenarios  : {len(unbounded)}, worst amplification "
        f"{worst_ratio(unbounded):.4f} cents per input byte",
    ]
    if worst is not None:
        lines.append(
            f"  the worst of them    : {worst.scenario} — {worst.cents:,} "
            f"{fixtures.CURRENCY_LABEL} from {worst.input_bytes:,} B of input, "
            f"{cap_multiple(worst):.1f}x the whole monthly cap"
        )
    if worst_ratio(secure) > 0:
        lines.append(
            f"  the ratio between them: {worst_ratio(unbounded) / worst_ratio(secure):,.0f}x"
        )
    lines.extend(
        [
            "",
            "  The ratio is the vulnerability. Doubling the budget doubles what an attacker can",
            "  spend and changes that number by nothing at all; bounding the work changes it by",
            "  orders of magnitude. That is why the fix is a bound and not a bigger budget.",
            "",
            "  The unbounded rows come from a service that is local educational material and must",
            "  never be deployed. It does not start without two deliberate actions, this harness",
            "  cannot be pointed at anything but the demonstration's own services, and every",
            "  tenant, provider, price and budget above is fictional.",
        ]
    )
    return lines


def _records(rows: tuple[ComparisonRow, ...]) -> list[str]:
    """The per-request records underlying the table, for a reader who wants to check it."""
    lines = ["", "=" * WIDTH, "PER-REQUEST RECORDS", "=" * WIDTH]
    for row in rows:
        if not row.records:
            continue
        lines.append("")
        lines.append(f"  {row.scenario}")
        lines.append(
            f"    {'#':>4} {'operation':<14} {'tenant':<14} {'addressed':<10} {'served by':<10} "
            f"{'status':>6} {'input B':>10} {'admitted':>9} {'cents':>9}"
        )
        for record in row.records:
            lines.append(
                f"    {record.sequence:>4} {record.operation:<14} {record.tenant_id:<14} "
                f"{record.addressed:<10} {(record.served_by or '?'):<10} "
                f"{record.status_code:>6} {record.input_bytes:>10,} "
                f"{record.records_admitted:>9,} {record.cents_charged:>9,}"
            )
    return lines
