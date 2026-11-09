"""The stack facts the frontend reads from UQF_FRONTEND_STACK_ROOT (#1536).

Each is read from where uqs keeps it, so a fake stack root is laid out by
uqs.paths itself rather than by literals that could drift from it.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from uqf_frontend import stack
from uqf_frontend.config import Settings
from uqf_frontend.stack import DEFAULT_BASE_PORT, GATEWAY_PORT_OFFSET, Process, StackFacts
from uqs import paths as stack_paths

HEADER = "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd"


@pytest.fixture(autouse=True)
def _default_layout(monkeypatch):
    """The layout variables uqs.paths honours, unset: the checkout's own."""
    for var in ("UQS_RUNTIME", "UQS_DATA_ROOT", "TORQHOME", "TORQAPPHOME", "UQF_STATUS_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("UQF_FRONTEND_STACK_ROOT", raising=False)


def _root(tmp_path: Path) -> Path:
    """A directory uqs accepts as a stack root: both of its markers."""
    root = tmp_path / "stack"
    (root / "src" / "etl").mkdir(parents=True)
    (root / "python" / "uqs").mkdir(parents=True)
    return root


def _bootstrapped(root: Path, base_port: int, *rows: str) -> stack_paths.UqsPaths:
    """Write what bootstrap writes: setenv.sh and process.csv."""
    paths = stack_paths.paths_for_root(root)
    paths.torqdata.mkdir(parents=True)
    paths.generated_setenv.write_text(f'export TORQHOME="/x"\nexport KDBBASEPORT="{base_port}"\n')
    paths.generated_procs.write_text("\n".join((HEADER, *rows)) + "\n")
    return paths


def _row(host: str, port: str, proctype: str, procname: str, startwithall: str = "1") -> str:
    return f"{host},{port},{proctype},{procname},,1,0,,,x.q,{startwithall},,q"


def test_without_a_stack_root_every_fact_is_the_default():
    """No root, no guess: the gateway at the default stack's port, and the
    views that need the stack's files report themselves unconfigured."""
    assert stack.derive(None) == StackFacts()
    s = Settings.from_env()
    assert (s.host, s.port, s.base_port) == (
        "localhost",
        DEFAULT_BASE_PORT + GATEWAY_PORT_OFFSET,
        DEFAULT_BASE_PORT,
    )
    assert (s.process_csv, s.processes, s.web_dist, s.stack_root) == (None, (), None, None)


def test_without_a_stack_root_uqs_is_not_imported():
    """A read-only deployment with no stack root loads nothing of uqs.paths."""
    code = (
        "import sys; from uqf_frontend.config import Settings; Settings.from_env(); "
        "print('uqs.paths' in sys.modules)"
    )
    env = {"PATH": "/usr/bin:/bin"}
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert out.stdout.strip() == "False"


def test_a_root_that_is_not_the_repository_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("UQF_FRONTEND_STACK_ROOT", str(tmp_path))
    with pytest.raises(ValueError, match="does not look like the repository"):
        Settings.from_env()


def test_the_facts_come_from_the_bootstrapped_stack(tmp_path, monkeypatch):
    """Base port from setenv.sh, the gateway's host and port from its
    process.csv row, the fan-out from the rows `start all` starts."""
    root = _root(tmp_path)
    paths = _bootstrapped(
        root,
        7000,
        _row("localhost", "{KDBBASEPORT}+2", "rdb", "rdb1"),
        _row("gw.internal", "{KDBBASEPORT}+7", "gateway", "gateway1"),
        _row("localhost", "{KDBBASEPORT}+10", "tickerlogreplay", "tpreplay1", "0"),
        _row("localhost", "{UNKNOWN}+1", "hdb", "hdb9"),
    )
    monkeypatch.setenv("UQF_FRONTEND_STACK_ROOT", str(root))
    s = Settings.from_env()
    assert (s.host, s.port, s.base_port) == ("gw.internal", 7007, 7000)
    assert s.process_csv == paths.generated_procs
    assert s.processes == (
        Process("rdb1", "localhost", 7002),
        Process("gateway1", "gw.internal", 7007),
    )
    assert s.stack_root == root


def test_before_the_first_bootstrap_the_runtimes_base_port_is_used(tmp_path):
    """No setenv.sh, no process.csv: the runtime's declared base port, the
    gateway at its offset, and no processes to fan out to."""
    root = _root(tmp_path)
    declared = stack_paths.paths_for_root(root).runtime_declaration.base_port
    facts = stack.derive(root)
    assert (facts.base_port, facts.port, facts.processes) == (
        declared,
        declared + GATEWAY_PORT_OFFSET,
        (),
    )
    assert facts.process_csv == stack_paths.paths_for_root(root).generated_procs


def test_the_runtime_moves_every_fact_with_it(tmp_path, monkeypatch):
    """UQS_RUNTIME picks the data directory and the base port, for the
    frontend exactly as for the stack."""
    from uqs.runtimes import RUNTIMES

    monkeypatch.setenv("UQS_RUNTIME", "torq")
    root = _root(tmp_path)
    facts = stack.derive(root)
    assert facts.base_port == RUNTIMES["torq"].base_port
    assert facts.process_csv == root / "output" / RUNTIMES["torq"].data_dir / "process.csv"


def test_web_dist_is_served_only_once_it_is_built(tmp_path):
    root = _root(tmp_path)
    assert stack.derive(root).web_dist is None
    (root / "web" / "dist").mkdir(parents=True)
    assert stack.derive(root).web_dist == root / "web" / "dist"


def test_the_base_port_line_is_the_one_bootstrap_writes(tmp_path):
    """Read back with the format stack/runtime.py writes it in."""
    from uqs.stack.env import build_env

    root = _root(tmp_path)
    paths = stack_paths.paths_for_root(root)
    env = build_env(paths, base_port=7123)
    setenv = tmp_path / "setenv.sh"
    # As bootstrap writes it: one `export K=<shell-quoted V>` line per variable.
    setenv.write_text("\n".join(f"export {k}={shlex.quote(v)}" for k, v in env.items()) + "\n")
    assert stack.generated_base_port(setenv) == 7123
    assert stack.generated_base_port(tmp_path / "absent.sh") is None


def test_a_setting_is_read_back_unquoted_as_bash_would_read_it(tmp_path):
    """#2394: setenv.sh shell-quotes each value."""
    weird = "/data/st$HOME 'a' `x`"
    setenv = tmp_path / "setenv.sh"
    setenv.write_text(f"export UQF_STATUS_DIR={shlex.quote(weird)}\nexport QCMD=q\n")
    assert stack.generated_setting(setenv, "UQF_STATUS_DIR") == weird
    assert stack.generated_setting(setenv, "QCMD") == "q"
    assert stack.generated_setting(setenv, "ABSENT") is None


# ------------------------------------------- facts as current as their files (#2027)


def test_a_process_csv_written_after_startup_reaches_the_fan_out(tmp_path, monkeypatch):
    """The normal order on a fresh deploy: the frontend starts, then the stack's
    first bootstrap writes process.csv. The fan-out used to stay empty - and
    `/ops/usage` said `processes_configured: 0` - until the frontend restarted."""
    from fastapi.testclient import TestClient

    from uqf_frontend.app import create_app
    from uqf_frontend.fleet import KolaFleet
    from uqf_frontend.gateway import FakeGateway

    root = _root(tmp_path)
    monkeypatch.setenv("UQF_FRONTEND_STACK_ROOT", str(root))
    s = Settings.from_env()
    fleet = KolaFleet(s)
    assert fleet.processes == ()

    # A base port nothing listens on: the usage sweep below really connects.
    _bootstrapped(
        root,
        39900,
        _row("localhost", "{KDBBASEPORT}+2", "rdb", "rdb1"),
        _row("localhost", "{KDBBASEPORT}+7", "gateway", "gateway1"),
    )
    assert fleet.processes == ("rdb1", "gateway1")
    client = TestClient(create_app(gateway=FakeGateway(), settings=s, fleet=fleet))
    assert client.get("/ops/usage").json()["processes_configured"] == 2


def test_a_base_port_change_moves_the_gateway_and_the_fan_out(tmp_path, monkeypatch):
    """`uqs restart all --base-port 7050` rewrites setenv.sh under a running
    frontend; every address it uses follows."""
    from uqf_frontend.fleet import KolaFleet
    from uqf_frontend.gateway import KolaGateway

    root = _root(tmp_path)
    rows = (
        _row("localhost", "{KDBBASEPORT}+2", "rdb", "rdb1"),
        _row("localhost", "{KDBBASEPORT}+7", "gateway", "gateway1"),
    )
    paths = _bootstrapped(root, 6050, *rows)
    monkeypatch.setenv("UQF_FRONTEND_STACK_ROOT", str(root))
    s = Settings.from_env()
    assert s.facts().base_port == 6050

    paths.generated_setenv.write_text('export KDBBASEPORT="7050"\n')
    facts = s.facts()
    assert (facts.base_port, facts.port) == (7050, 7057)
    assert KolaGateway(s).address() == ("localhost", 7057)
    assert dict((p.name, p.port) for p in facts.processes) == {"rdb1": 7052, "gateway1": 7057}
    assert KolaFleet(s).processes == ("rdb1", "gateway1")


def test_facts_are_read_again_only_when_a_file_changes(tmp_path, monkeypatch):
    """Cheap per request: an unchanged stack is not re-parsed."""
    root = _root(tmp_path)
    _bootstrapped(root, 6050, _row("localhost", "{KDBBASEPORT}+2", "rdb", "rdb1"))
    calls: list[Path] = []
    real = stack.derive
    monkeypatch.setattr(stack, "derive", lambda r: calls.append(r) or real(r))
    first = stack.current(root)
    assert stack.current(root) is first
    assert len(calls) == 1


def test_without_a_stack_root_the_facts_are_the_settings_own(tmp_path):
    """Tests and the read-only deployment construct Settings directly."""
    s = Settings(host="h", port=1, base_port=2, processes=(Process("p", "h", 3),))
    facts = s.facts()
    assert (facts.host, facts.port, facts.base_port, facts.processes) == (
        "h",
        1,
        2,
        (Process("p", "h", 3),),
    )
