"""One request timeout, owned by the server (#1370).

The browser used to hard-code 45 s (web/src/api.ts) while the server's own
timeout was UQF_FRONTEND_TIMEOUT, so raising the latter changed nothing the
operator saw, and a `start all` slower than 45 s read as "timed out" in the
browser while torq.sh was still running. Now `Settings.request_timeout` is the
authority and /health serves it; api.ts keeps only the value that applies
before the first /health answer, and this test holds that equal to the
server's default.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway

API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"


def test_the_browsers_fallback_timeout_is_the_servers_default():
    m = re.search(r"DEFAULT_REQUEST_TIMEOUT_MS\s*=\s*(\d+)\s*;", API_TS.read_text())
    assert m, "web/src/api.ts no longer declares DEFAULT_REQUEST_TIMEOUT_MS"
    assert int(m.group(1)) == Settings().request_timeout * 1000, (
        "web/src/api.ts's fallback differs from config.Settings.request_timeout"
    )


def test_health_serves_the_timeout_the_browser_must_use():
    client = TestClient(create_app(gateway=FakeGateway(), settings=Settings(timeout=100)))
    assert client.get("/health").json()["request_timeout_seconds"] == 115


def test_raising_the_server_timeout_raises_the_lifecycle_bound_under_the_browsers():
    slow = Settings(timeout=100)
    assert slow.request_timeout > Settings().request_timeout
    assert slow.lifecycle_timeout < slow.request_timeout, "the server gives up first"


def test_a_zero_gateway_timeout_does_not_collapse_the_browsers():
    assert Settings(timeout=0).request_timeout == Settings().request_timeout
