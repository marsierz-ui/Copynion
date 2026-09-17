from __future__ import annotations

from pathlib import Path

import pytest

from copynion.categorize import Categoriser
from copynion.config import ObservationSettings
from copynion.models import Observation
from copynion.observer.backends import FakeBackend
from copynion.observer.sampler import Sampler
from copynion.privacy import Policy, Redactor, Vault
from copynion.storage import Store


@pytest.fixture
def vault() -> Vault:
    return Vault(Vault.generate_key())


@pytest.fixture
def store(tmp_path: Path, vault: Vault) -> Store:
    s = Store(tmp_path / "test.db", vault)
    yield s
    s.close()


@pytest.fixture
def settings() -> ObservationSettings:
    return ObservationSettings(
        poll_interval_seconds=5.0,
        idle_threshold_seconds=120.0,
        min_span_seconds=3.0,
        max_span_seconds=1800.0,
    )


@pytest.fixture
def full_policy() -> Policy:
    """A policy that captures titles for the apps used in tests."""
    return Policy(
        app_rules={app: "full" for app in ("code", "firefox", "localc", "thunderbird", "slack")}
    )


@pytest.fixture
def sampler(store, vault, settings, full_policy) -> Sampler:
    return Sampler(
        FakeBackend([]),
        store,
        policy=full_policy,
        redactor=Redactor(),
        vault=vault,
        categoriser=Categoriser(),
        settings=settings,
        clock=lambda: 0.0,
    )


def feed(sampler: Sampler, script, start: float = 10_000.0, step: float = 5.0) -> float:
    """Push ``(app, title[, idle])`` tuples through the sampler on a fake clock."""
    t = start
    for entry in script:
        app, title = entry[0], entry[1]
        idle = entry[2] if len(entry) > 2 else 0.0
        sampler.ingest(Observation(t, app, title, idle_seconds=idle, backend="fake"))
        t += step
    return t
