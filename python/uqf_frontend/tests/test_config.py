from __future__ import annotations

import pytest

from uqf_frontend.config import GATEWAY_PORT_OFFSET, Settings


def test_defaults_point_at_the_local_gateway():
    s = Settings()
    assert s.host == "localhost"
    assert s.max_rows > 0


def test_the_default_port_is_the_gateways_not_another_processes():
    """THE regression. The default was 6052, which is rdb1 - so every
    routed query and every ops view failed against a stack started with
    defaults, while /health reported up. Stated against process.csv's own
    arithmetic rather than the literal 6057, so a base-port change cannot
    make this test agree with a wrong answer.
    """
    s = Settings()
    assert s.port == s.base_port + GATEWAY_PORT_OFFSET
    assert s.port == 6057


def test_a_non_default_base_port_moves_the_gateway_with_it(monkeypatch):
    """The gateway is +7 from wherever the stack was started, so configuring
    the base port alone is enough - and is what someone running a second
    stack on another base actually sets.
    """
    monkeypatch.setenv("UQF_FRONTEND_BASE_PORT", "7000")
    s = Settings.from_env()
    assert (s.base_port, s.port) == (7000, 7007)


def test_an_explicit_gateway_port_still_wins_over_the_base_port(monkeypatch):
    """The escape hatch: a gateway that is not where process.csv puts it."""
    monkeypatch.setenv("UQF_FRONTEND_BASE_PORT", "7000")
    monkeypatch.setenv("UQF_FRONTEND_GATEWAY_PORT", "9999")
    assert Settings.from_env().port == 9999


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


# ------------------------------------------------- the write switch


def test_writes_are_off_when_the_variable_is_unset(monkeypatch):
    """The security default. A deployment that was never configured cannot
    be made to change anything."""
    monkeypatch.delenv("UQF_FRONTEND_ENABLE_WRITES", raising=False)
    assert Settings.from_env().enable_writes is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " True "])
def test_the_recognised_truthy_spellings_enable_writes(monkeypatch, raw):
    monkeypatch.setenv("UQF_FRONTEND_ENABLE_WRITES", raw)
    assert Settings.from_env().enable_writes is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
def test_the_recognised_falsy_spellings_leave_writes_off(monkeypatch, raw):
    monkeypatch.setenv("UQF_FRONTEND_ENABLE_WRITES", raw)
    assert Settings.from_env().enable_writes is False


def test_an_unrecognised_value_refuses_to_start(monkeypatch):
    """`ENABLE_WRITES=fasle` typed at 2am must not read as "writes are off,
    all is well". For a security switch, silently defaulting is the wrong
    failure - the value is quoted back and the server does not start."""
    monkeypatch.setenv("UQF_FRONTEND_ENABLE_WRITES", "fasle")
    with pytest.raises(ValueError, match="fasle"):
        Settings.from_env()


def test_the_stack_root_is_a_path_or_none(monkeypatch, tmp_path):
    monkeypatch.delenv("UQF_FRONTEND_STACK_ROOT", raising=False)
    assert Settings.from_env().stack_root is None
    monkeypatch.setenv("UQF_FRONTEND_STACK_ROOT", str(tmp_path))
    assert Settings.from_env().stack_root == tmp_path
