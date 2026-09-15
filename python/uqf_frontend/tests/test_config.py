from __future__ import annotations

import pytest

from uqf_frontend.config import Settings


def test_defaults_point_at_the_local_gateway():
    s = Settings()
    assert s.host == "localhost"
    assert s.max_rows > 0


def test_env_overrides_are_read(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_GATEWAY_HOST", "gw.internal")
    monkeypatch.setenv("UQF_FRONTEND_GATEWAY_PORT", "7001")
    monkeypatch.setenv("UQF_FRONTEND_MAX_ROWS", "250")
    s = Settings.from_env()
    assert (s.host, s.port, s.max_rows) == ("gw.internal", 7001, 250)


def test_a_malformed_numeric_setting_fails_loudly(monkeypatch):
    """Refuse to start rather than start misconfigured - the posture E-14
    takes on the q side.
    """
    monkeypatch.setenv("UQF_FRONTEND_GATEWAY_PORT", "not-a-port")
    with pytest.raises(ValueError, match="must be an integer"):
        Settings.from_env()


def test_empty_env_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_TIMEOUT", "")
    assert Settings.from_env().timeout == Settings().timeout


def test_credentials_are_not_in_the_default_settings():
    """F-14: credentials come from the server environment, never a default
    baked into the package.
    """
    s = Settings()
    assert s.user == "" and s.passwd == ""
