from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway


@pytest.fixture
def gw() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def client(gw: FakeGateway) -> TestClient:
    return TestClient(create_app(gateway=gw, settings=Settings(max_rows=5000)))
