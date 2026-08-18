"""The publication surface: the license, the policies, and the claims the README makes.

This repository is published as public educational material, and three of the things that makes
true are documents rather than code: the license, the contribution guidance, and the security policy
that separates the vulnerability this project *demonstrates* from one it would want reported.

The rest of this file guards the README's claims. Those claims are the part of the publication
surface that rots: a sentence describing what "is here now" is accurate exactly until the next slice
lands, and a sentence promising nothing about production is accurate only while nobody adds a
promise. Both shapes had to be repaired before this repository could be published, so both are
asserted here rather than left to a reviewer's memory.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

LICENSE = (ROOT / "LICENSE").read_text()
README = (ROOT / "README.md").read_text()
SECURITY = (ROOT / "SECURITY.md").read_text()
CONTRIBUTING = (ROOT / "CONTRIBUTING.md").read_text()


def flat(text: str) -> str:
    """Collapse runs of whitespace, so a phrase assertion survives a line wrap.

    These documents are hard-wrapped prose. A required sentence is required whether or not it
    happens to straddle a line break, and a test that missed it for that reason would be asserting
    on the formatting rather than on the claim.
    """
    return re.sub(r"\s+", " ", text)


def test_the_license_is_the_canonical_mit_text() -> None:
    """Not "an MIT-style license" — the actual text, including the warranty disclaimer."""
    assert LICENSE.startswith("MIT License\n")
    for clause in (
        "Permission is hereby granted, free of charge",
        "The above copyright notice and this permission notice shall be included in all",
        'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND',
    ):
        assert clause in LICENSE


def test_the_license_carries_a_year_and_a_copyright_holder() -> None:
    holder = re.search(r"^Copyright \(c\) (\d{4}) (.+)$", LICENSE, re.MULTILINE)
    assert holder is not None, "the license states no copyright line"
    assert int(holder.group(1)) >= 2026
    assert holder.group(2).strip()


def test_the_package_metadata_agrees_with_the_license_file() -> None:
    """An SPDX identifier that disagreed with `LICENSE` would be worse than none at all."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert metadata["project"]["license"] == "MIT"


@pytest.mark.parametrize(
    "claim",
    [
        "educational",  # what it is for
        "no egress",  # the local-only operating boundary
        "ALLOW_VULNERABLE_DEMO=true",  # the intentionally vulnerable component, and its opt-in
        "docker compose",  # the supported workflow
        "cannot be redirected",  # the harness targets only this demo's own services
        "not deployed",  # no hosted service
        "MIT",  # the license
    ],
)
def test_the_readme_states_what_a_public_reader_needs(claim: str) -> None:
    assert claim.lower() in flat(README).lower(), f"the README no longer states {claim!r}"


def test_the_readme_promises_no_production_readiness() -> None:
    promise = "no** service-level, support-duration, compatibility, or production-readiness"
    assert promise in flat(README)


@pytest.mark.parametrize(
    "stale",
    [
        r"work in progress",
        r"what is here \*{0,2}now\*{0,2}",
        r"not (yet|currently) (implemented|present|built)",
        r"the secure side of the demonstration:",
        r"coming soon",
    ],
)
def test_the_readme_claims_nothing_it_has_outgrown(stale: str) -> None:
    """A sentence saying the project lacks something it now ships is a publication blocker.

    Every one of these was true of some earlier commit. None is true of this one, and the failure
    mode they share is that nobody notices when they stop being true.
    """
    found = re.search(stale, flat(README), re.IGNORECASE)
    assert found is None, f"the README still says {found.group(0)!r}"


def test_the_security_policy_separates_the_demonstration_from_a_defect() -> None:
    """The whole point of the file: this repository is vulnerable on purpose."""
    assert "on purpose" in flat(SECURITY)
    assert "do not report any of them" in flat(SECURITY).lower()
    assert "What *is* worth reporting" in flat(SECURITY)


def test_the_security_policy_gives_a_private_reporting_path_and_no_personal_address() -> None:
    assert "Report a vulnerability" in flat(SECURITY)
    assert "private advisory" in flat(SECURITY)
    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", SECURITY), (
        "the security policy exposes a contact address; the private advisory is the path"
    )


def test_the_contribution_guidance_names_the_gate_and_the_constraints() -> None:
    assert "bash scripts/verify.sh" in flat(CONTRIBUTING)
    for constraint in (
        "Everything is fictional",
        "The vulnerable code stays opt-in",
        "The secure application stays clean",
        "The harness takes no target",
        "No performance claims",
        "Nothing gets deployed",
    ):
        assert constraint in flat(CONTRIBUTING), f"the contribution guidance dropped {constraint!r}"


def test_the_public_documents_carry_no_contact_or_home_path() -> None:
    """Nothing published should carry a personal address or a path off somebody's machine."""
    for name, text in (
        ("README.md", README),
        ("SECURITY.md", SECURITY),
        ("CONTRIBUTING.md", CONTRIBUTING),
        ("LICENSE", LICENSE),
    ):
        assert not re.search(r"/(Users|home)/[a-z][a-z0-9_-]*/", text), (
            f"{name} carries a home path"
        )
        assert not re.search(
            r"[A-Za-z0-9._%+-]+@(gmail|outlook|hotmail|yahoo|icloud|proton(mail)?)\.[A-Za-z]{2,}",
            text,
        ), f"{name} carries a personal address"
