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
    """Refuse to start rather than start misconfigured - the posture ETL-14
    takes on the q side.
    """
    monkeypatch.setenv("UQF_FRONTEND_GATEWAY_PORT", "not-a-port")
    with pytest.raises(ValueError, match="must be an integer"):
        Settings.from_env()


def test_empty_env_value_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_TIMEOUT", "")
    assert Settings.from_env().timeout == Settings().timeout


def test_credentials_are_not_in_the_default_settings():
    """FE-14: credentials come from the server environment, never a default
    baked into the package.
    """
    s = Settings()
    assert s.user == "" and s.passwd == ""


# --- process registry (FE-04 fan-out) --------------------------------------


def test_processes_default_to_empty():
    """Empty, not a guessed list: the usage view then reports it has nothing
    configured rather than showing an empty log as an idle fleet.
    """
    assert Settings().processes == ()


def test_processes_parse_name_and_port(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_PROCESSES", "rdb1:6052,hdb1:6053")
    procs = Settings.from_env().processes
    assert [(p.name, p.host, p.port) for p in procs] == [
        ("rdb1", "localhost", 6052),
        ("hdb1", "localhost", 6053),
    ]


def test_processes_parse_an_explicit_host(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_PROCESSES", "rdb1:db.internal:6052")
    p = Settings.from_env().processes[0]
    assert (p.name, p.host, p.port) == ("rdb1", "db.internal", 6052)


def test_processes_tolerate_whitespace_and_trailing_commas(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_PROCESSES", " rdb1:6052 , hdb1:6053 ,")
    assert len(Settings.from_env().processes) == 2


def test_a_malformed_process_entry_fails_loudly(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_PROCESSES", "rdb1")
    with pytest.raises(ValueError, match="name:port or name:host:port"):
        Settings.from_env()


def test_a_non_integer_process_port_fails_loudly(monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_PROCESSES", "rdb1:not-a-port")
    with pytest.raises(ValueError, match="non-integer port"):
        Settings.from_env()
