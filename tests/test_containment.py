"""Containment: no egress, nothing published, bounded resources, and no arbitrary target.

These are safety properties of the demonstration rather than features of the product, so they are
asserted against the files that actually configure it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from limitless import fixtures
from limitless.config import (
    ALLOWED_TARGET_HOSTS,
    RunnerConfig,
    require_allowed_target,
)

ROOT = Path(__file__).resolve().parent.parent


def compose() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    return parsed


def services() -> dict[str, Any]:
    found: dict[str, Any] = compose()["services"]
    return found


def test_the_network_is_internal_so_there_is_no_egress() -> None:
    assert compose()["networks"]["demo"]["internal"] is True


def test_no_service_publishes_a_port() -> None:
    published = {
        name: service["ports"] for name, service in services().items() if "ports" in service
    }
    assert published == {}, f"these services publish ports: {published}"


def test_every_service_declares_an_explicit_memory_and_cpu_limit() -> None:
    for name, service in services().items():
        limits = service.get("deploy", {}).get("resources", {}).get("limits", {})
        assert limits.get("memory"), f"{name} declares no memory limit"
        assert limits.get("cpus"), f"{name} declares no CPU limit"


def test_every_service_is_hardened() -> None:
    for name, service in services().items():
        assert service.get("cap_drop") == ["ALL"], f"{name} keeps Linux capabilities"
        assert "no-new-privileges:true" in service.get("security_opt", []), f"{name} may escalate"
        assert service.get("read_only") is True, f"{name} has a writable root filesystem"


def test_the_database_keeps_its_data_on_disposable_storage() -> None:
    tmpfs = services()["db"]["tmpfs"]
    assert any(entry.startswith("/var/lib/postgresql/data") for entry in tmpfs)
    assert "volumes" not in services()["db"], "the database must not persist across runs"


def test_the_topology_is_two_replicas_over_one_database() -> None:
    names = set(services())
    assert {"app-a", "app-b", "db", "coastwise"} <= names
    assert sum(1 for name in names if name.startswith("app-")) == 2
    assert sum(1 for name in names if name == "db") == 1


def test_no_vulnerable_service_exists_yet() -> None:
    """The harness exists now. A deliberately unbounded application still does not."""
    assert not {"vuln-a", "vuln-b"} & set(services())
    modules = {path.parent.name for path in (ROOT / "src").rglob("*.py")}
    assert "vulnerable" not in modules


def test_the_harness_writes_only_to_the_artifacts_directory() -> None:
    """Its root filesystem is read-only; the transcript goes to a bind mount and nowhere else."""
    harness = services()["harness"]
    assert harness["read_only"] is True
    assert harness["volumes"] == ["./artifacts:/artifacts"]


def test_the_harness_command_takes_no_target() -> None:
    """There is no host, URL, or address argument to give it."""
    command = services()["harness"]["command"]
    assert command == ["python", "-m", "limitless.harness"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "http://example.com:8000",
        "https://app-a:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://app-a.example.com:8000",
        "file:///etc/passwd",
    ],
)
def test_no_host_but_our_own_can_be_targeted(url: str) -> None:
    """The demonstration must never become a general-purpose load or stress tool."""
    with pytest.raises(ValueError):
        require_allowed_target(url)


@pytest.mark.parametrize("host", sorted(ALLOWED_TARGET_HOSTS))
def test_our_own_services_are_reachable(host: str) -> None:
    assert require_allowed_target(f"http://{host}:8000") == f"http://{host}:8000"


def test_configuration_cannot_redirect_the_runner_at_another_host() -> None:
    with pytest.raises(ValueError):
        RunnerConfig.from_env(
            {"LIMITLESS_REPLICA_URLS": "http://app-a:8000,http://attacker.example.com:8000"}
        )
    with pytest.raises(ValueError):
        RunnerConfig.from_env({"LIMITLESS_PROVIDER_URL": "http://attacker.example.com:8000"})


def test_the_replica_count_is_a_bounded_run_parameter() -> None:
    assert len(RunnerConfig.from_env({"LIMITLESS_REPLICAS": "1"}).replica_urls) == 1
    assert len(RunnerConfig.from_env({"LIMITLESS_REPLICAS": "2"}).replica_urls) == 2
    for bad in ("0", "3", "-1"):
        with pytest.raises(ValueError):
            RunnerConfig.from_env({"LIMITLESS_REPLICAS": bad})


def test_nothing_in_the_repository_describes_building_an_archive_bomb() -> None:
    """The expansion fixture is one layer of ordinary gzip, and the docs say only that."""
    forbidden = ("zip bomb", "archive bomb", "billion laughs", "quine", "recursive archive")
    for path in [*(ROOT / "src").rglob("*.py"), ROOT / "README.md"]:
        text = path.read_text().lower()
        for term in forbidden:
            assert term not in text, f"{path.name} contains {term!r}"


def test_the_currency_is_labelled_fictional_wherever_it_appears() -> None:
    assert "fictional" in fixtures.CURRENCY_LABEL
