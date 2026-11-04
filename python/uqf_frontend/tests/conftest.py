from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway


@pytest.fixture(autouse=True)
def _no_lifecycle_side_effects(monkeypatch):
    """A lifecycle action looks at this machine's process table before and
    after torq.sh (control._observe), and holds the stack's lock file around
    it (control._holding). Neither belongs in a test that did not ask: the
    table holds whatever stack the developer is running, and most tests hand
    control a stand-in for the paths. A test of either installs its own."""
    from contextlib import nullcontext

    from uqf_frontend import control

    monkeypatch.setattr(control, "_observe", lambda paths, base_port: {})
    monkeypatch.setattr(control, "_holding", lambda paths: nullcontext())


@pytest.fixture
def gw() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(gw: FakeGateway) -> TestClient:
    return TestClient(create_app(gateway=gw, settings=Settings(max_rows=5000)))


@pytest.fixture
def client_for():
    """Build a client around a specific FakeGateway, for tests that need to
    stage responses before the app is created.
    """

    def make(gateway: FakeGateway) -> TestClient:
        return TestClient(create_app(gateway=gateway, settings=Settings(max_rows=5000)))

    return make
