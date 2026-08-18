"""The two-mode reproduction contract: that the modes differ in behaviour, not in labelling.

`FR-012` and `FR-013` describe two modes. For five slices there was one mode with two labels: the
selected mode was rendered into the transcript and read nowhere else, so a `natural` run held the
provider exactly as a `deterministic` one did, and `scripts/verify.sh` ran a step it had labelled
`deterministic` in `natural`. Every figure in the two runs was byte-identical, which is precisely
why nothing caught it.

These tests are the ones whose absence allowed that. They assert the difference itself rather than
either mode's output: that the hold is reachable from one mode and not the other, that an
unproductive natural run is inconclusive rather than a pass or a failure, and that no step in the
verification boundary names a mode it does not select.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from limitless.config import HarnessConfig, ReproductionMode
from limitless.harness.__main__ import unreproduced_exit_code, unreproduced_notice
from limitless.harness.transcript import render_shapes
from limitless.harness.vulnerable import ShapeOutcome

ROOT = Path(__file__).resolve().parent.parent


def test_only_the_deterministic_mode_may_instrument() -> None:
    """The single property the whole contract now hangs on."""
    assert ReproductionMode.DETERMINISTIC.instrumented is True
    assert ReproductionMode.NATURAL.instrumented is False


def test_every_mode_answers_the_instrumentation_question() -> None:
    """A third mode added later must decide, rather than inherit an accident."""
    for mode in ReproductionMode:
        assert isinstance(mode.instrumented, bool)


# --- the hold is reachable from exactly one mode ------------------------------------------------


def _holds_issued(source: str) -> list[str]:
    """Every `set_provider_control(held=True)` in a source file, with its guard."""
    return [line.strip() for line in source.splitlines() if "held=True" in line]


@pytest.mark.parametrize(
    "module",
    ["src/limitless/harness/vulnerable.py", "src/limitless/harness/controls.py"],
)
def test_no_hold_is_issued_unconditionally(module: str) -> None:
    """Every hold sits behind the instrumentation question.

    Asserted against the source because the alternative — driving the live application in both
    modes and proving a negative about what it did *not* do — is exactly the kind of timing-shaped
    assertion `NFR-002` forbids.
    """
    source = (ROOT / module).read_text()
    assert _holds_issued(source), f"{module} no longer issues a hold at all; update this test"
    for block in source.split("async def ")[1:]:
        if "held=True" not in block:
            continue
        guard = block[: block.index("held=True")]
        assert "instrumented" in guard, (
            f"a hold in {module} is not guarded by the mode: {block.splitlines()[0]}"
        )


def test_the_natural_mode_reaches_for_the_slow_provider_instead() -> None:
    """It still has to occupy the upstream; it may just not arrange it."""
    source = (ROOT / "src/limitless/harness/vulnerable.py").read_text()
    assert "slow_mode=True" in source


# --- an unproductive natural run is inconclusive -------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(ReproductionMode.DETERMINISTIC, 1), (ReproductionMode.NATURAL, 0)],
)
def test_what_a_shape_that_did_not_reproduce_is_worth(
    mode: ReproductionMode, expected: int
) -> None:
    assert unreproduced_exit_code(["a shape"], mode) == expected


@pytest.mark.parametrize("mode", list(ReproductionMode))
def test_a_run_that_reproduced_everything_always_passes(mode: ReproductionMode) -> None:
    assert unreproduced_exit_code([], mode) == 0


def test_the_natural_notice_says_inconclusive_and_refuses_to_call_it_a_pass() -> None:
    notice = unreproduced_notice(["a shape"], ReproductionMode.NATURAL)
    assert "INCONCLUSIVE" in notice
    assert "not a pass" in notice
    assert "deterministic" in notice


def test_the_deterministic_notice_is_a_plain_failure() -> None:
    notice = unreproduced_notice(["a shape"], ReproductionMode.DETERMINISTIC)
    assert "INCONCLUSIVE" not in notice


def _outcome(*, reproduced: bool) -> ShapeOutcome:
    return ShapeOutcome(
        shape="a shape",
        headline="a headline",
        reproduced=reproduced,
        detail=[],
        input_bytes=10,
        cents=20,
    )


def _config(mode: ReproductionMode) -> HarnessConfig:
    return replace(HarnessConfig.from_env(), mode=mode)


def test_a_natural_transcript_reports_inconclusive_rather_than_failure() -> None:
    rendered = render_shapes((_outcome(reproduced=False),), _config(ReproductionMode.NATURAL))
    assert "INCONCLUSIVE" in rendered
    assert "DID NOT REPRODUCE" not in rendered
    assert "VERDICT: UNBOUNDED" not in rendered


def test_a_deterministic_transcript_reports_a_failure_rather_than_inconclusive() -> None:
    rendered = render_shapes((_outcome(reproduced=False),), _config(ReproductionMode.DETERMINISTIC))
    assert "DID NOT REPRODUCE" in rendered
    assert "INCONCLUSIVE" not in rendered


def test_the_transcripts_of_the_two_modes_differ_beyond_the_label() -> None:
    """The regression that started this: the runs used to differ by one word.

    Strip the mode name itself from both and they must still disagree, or the mode is decorative
    again.
    """
    outcomes = (_outcome(reproduced=False),)
    natural = render_shapes(outcomes, _config(ReproductionMode.NATURAL))
    deterministic = render_shapes(outcomes, _config(ReproductionMode.DETERMINISTIC))

    def strip_label(text: str) -> str:
        return re.sub(r"reproduction mode\s*:.*", "", text)

    assert strip_label(natural) != strip_label(deterministic)


def test_a_natural_transcript_tells_the_reader_its_figures_are_observed() -> None:
    rendered = render_shapes((_outcome(reproduced=True),), _config(ReproductionMode.NATURAL))
    assert "OBSERVED" in rendered
    assert "no instrumentation" in rendered


# --- the verification boundary runs the mode it names --------------------------------------------

VERIFY = (ROOT / "scripts/verify.sh").read_text()
HARNESS_CALL = re.compile(r"python -m limitless\.harness[^\n]*")


def _steps() -> list[tuple[str, str]]:
    """Each `step "..."` paired with the harness invocation that follows it, if any."""
    paired: list[tuple[str, str]] = []
    chunks = VERIFY.split('step "')[1:]
    for chunk in chunks:
        label = chunk[: chunk.index('"')]
        call = HARNESS_CALL.search(chunk)
        if call:
            paired.append((label, call.group(0)))
    return paired


def test_the_boundary_still_drives_the_harness() -> None:
    assert _steps(), "no harness invocations found in the verification boundary"


def test_every_harness_invocation_selects_its_mode_explicitly() -> None:
    """A run that inherits its mode from the environment is a run nobody chose."""
    for call in HARNESS_CALL.findall(VERIFY):
        assert "--mode" in call, f"this invocation inherits its mode: {call}"


@pytest.mark.parametrize("mode", [m.value for m in ReproductionMode])
def test_no_step_names_a_mode_it_does_not_select(mode: str) -> None:
    """The exact defect: a step labelled `deterministic mode` that ran `natural`."""
    for label, call in _steps():
        if f"{mode} mode" not in label:
            continue
        assert f"--mode {mode}" in call, (
            f"the step labelled {label!r} names {mode!r} but runs: {call}"
        )


def test_the_boundary_exercises_both_modes() -> None:
    """Deterministic carries the assertions; natural is the evidence they are not the hold."""
    calls = HARNESS_CALL.findall(VERIFY)
    for mode in ReproductionMode:
        assert any(f"--mode {mode.value}" in call for call in calls), (
            f"the verification boundary never runs {mode.value} mode"
        )


def test_the_container_default_mode_is_the_deterministic_one() -> None:
    """`FR-012` calls deterministic the default, and the supported path is the container one."""
    text = (ROOT / "docker-compose.yml").read_text()
    match = re.search(r'LIMITLESS_MODE:\s*"\$\{LIMITLESS_MODE:-([a-z]+)\}"', text)
    assert match is not None, "the harness service no longer declares a default mode"
    assert match.group(1) == ReproductionMode.DETERMINISTIC.value
