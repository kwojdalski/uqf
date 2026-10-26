"""Deploy components, `existing` mode only (#1557, step 4): q, TorQ and Python
each in a mode, the mode layered like any key, a switch dropping the old
mode's fields, and each component's mode, path and observed version recorded
in the report, the history and the plan - while a source-only push sends the
server exactly what it did before."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest
from test_deploy import _HEALTHY, SERVER, FakeRemote, _args, _artifact, _done, _verified, parse_args

from uqs.deploy import driver, history, targets
from uqs.deploy.config import DeployError, make_config

GOLDEN = Path(__file__).parent / "golden" / "source_only_push.txt"
VERIFIED = {"uqs deploy verify --profile": _verified(True)}
BASE: dict[str, Any] = {"artifact": "x", "host": "h", "dest": "/srv/uqf", "profile": "essential"}


def _push(tmp_path: Path, *extra: str, facts: str = "") -> tuple[FakeRemote, dict]:
    """A healthy push with --torq-home /opt/torq: what the server was sent,
    and the report."""
    rules = {**_HEALTHY, **VERIFIED}
    rules["uv python find"] = _done(SERVER + facts + "data=present\n")
    remote = FakeRemote(rules)
    out = io.StringIO()
    cfg = parse_args(_args(_artifact(tmp_path), "--torq-home", "/opt/torq", *extra))
    assert driver.deploy(cfg, remote, out=out) == 0
    return remote, json.loads(out.getvalue())


def _normalised(remote: FakeRemote) -> str:
    """Every script the server ran, with what differs by run - the artifact's
    sha256, the lock's owner (who, where, when) and the report's body, which
    is data - masked."""
    s = "\n=====\n".join(f"[{stage}]\n{script}" for stage, script in remote.scripts)
    s = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", s)
    s = re.sub(r"printf '%s\\n' '[^']*' > (\S+/owner)", r"printf '%s\\n' <owner> > \1", s)
    body = r"<<'DEPLOYREPORT'\n.*?\nDEPLOYREPORT\n"
    s = re.sub(body, "<<'DEPLOYREPORT'\n<report>\nDEPLOYREPORT\n", s, flags=re.S)
    return s.rstrip("\n") + "\n"


# --- a source-only push is unchanged ------------------------------------------


@pytest.mark.parametrize(
    "modes",
    [(), ("--q-mode", "existing", "--torq-mode", "existing", "--python-mode", "existing")],
)
def test_a_source_only_push_sends_the_server_what_it_did_before(tmp_path, modes):
    """GOLDEN is every script a push sent before components existed: preflight,
    lock, transfer, prepare and its deploy.env, smoke, start, verify, activate -
    and, since #1664, the `running` report written before the start, and since
    #1557 step 7 preflight's host-contract checks (tools, python3 >= 3.8,
    hostname -I, the glibc it reports). Unset or spelt out, `existing` changes
    none of it."""
    remote, _ = _push(tmp_path, *modes)
    assert _normalised(remote) == GOLDEN.read_text()


def test_a_source_only_push_writes_the_same_deploy_env(tmp_path):
    remote, _ = _push(tmp_path)
    (prepare,) = remote.stage("prepare")
    env = prepare.split("<<'DEPLOYENV'\n")[1].split("\nDEPLOYENV")[0].splitlines()
    assert env[:6] == [
        "export TORQHOME=/opt/torq",
        "export QCMD='/opt/kx home/bin/q'",
        "export QHOME='/opt/kx home'",
        "export UQS_DATA_ROOT=/opt/uqf/shared/data",
        "export UQS_RUNTIME=uqf",
        "export UV_OFFLINE=1",
    ]


# --- modes ------------------------------------------------------------------------


def test_every_component_defaults_to_existing():
    cfg = make_config(**BASE)
    assert (cfg.q_mode, cfg.torq_mode, cfg.python_mode) == ("existing",) * 3


@pytest.mark.parametrize("component", ["q", "torq", "python"])
def test_bundled_needs_its_runtime(component):
    with pytest.raises(DeployError, match=f"{component}_mode is bundled, and needs "
                       f"{component}_runtime"):  # fmt: skip
        make_config(**BASE, **{f"{component}_mode": "bundled"})


def test_a_mode_that_is_not_one_is_refused():
    with pytest.raises(DeployError, match="q_mode 'portable' is not a mode - existing or bundled"):
        make_config(**BASE, q_mode="portable")


@pytest.mark.parametrize(
    ("component", "fields", "named"),
    [
        ("q", {"qcmd": "/opt/kx/bin/q"}, "qcmd - existing mode's - is set"),
        ("torq", {"torq_home": "/opt/torq", "torq_launcher": "/opt/t.sh"},
         "torq_home, torq_launcher - existing mode's - are set"),
    ],
)  # fmt: skip
def test_an_existing_field_beside_bundled_is_refused_before_bundled_is(component, fields, named):
    """The other mode's fields never ride along with bundled."""
    with pytest.raises(DeployError, match=f"{component}_mode is bundled, but {named}"):
        make_config(**BASE, **fields, **{f"{component}_mode": "bundled"})


def test_the_cli_takes_each_mode():
    cfg = parse_args(["a", "--host", "h", "--dest", "/srv/uqf", "--profile", "essential",
                      "--torq-mode", "existing", "--python-mode", "bundled",
                      "--python-runtime", "dist/py.tar.gz"])  # fmt: skip
    assert (cfg.torq_mode, cfg.python_mode) == ("existing", "bundled")
    assert cfg.python_runtime == "dist/py.tar.gz"


# --- layering: a switch drops the old mode's fields -----------------------------

LAYERED = """
[defaults]
profile = "essential"
qcmd = "/opt/kx/bin/q"
qhome = "/opt/kx"
torq_home = "/opt/torq"

[targets.kx]
host = "h"
dest = "/srv/uqf"

[targets.restated]
host = "h"
dest = "/srv/uqf"
q_mode = "existing"

[targets.switched]
host = "h"
dest = "/srv/uqf"
q_mode = "bundled"
q_runtime = "dist/q-runtime.tar.gz"

[targets.both]
host = "h"
dest = "/srv/uqf"
q_mode = "bundled"
qcmd = "/opt/kx/bin/q"
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.delenv(targets.DECLARATION_ENV, raising=False)
    (tmp_path / targets.DECLARATION).write_text(LAYERED)
    return tmp_path


def test_a_mode_is_layered_like_any_key(root):
    (cfg,) = targets.configs("restated", "x", {}, False, root)
    assert (cfg.q_mode, cfg.qcmd, cfg.qhome) == ("existing", "/opt/kx/bin/q", "/opt/kx")
    assert cfg.sources["q_mode"] == "target restated"
    assert cfg.sources["qcmd"] == "defaults"


def test_switching_drops_the_old_fields_and_keeps_every_other(root):
    """[defaults]' qcmd and qhome are existing mode's: the target that switches
    q to bundled does not inherit them - its q table is replaced whole."""
    (cfg,) = targets.configs("switched", "x", {}, False, root)
    assert (cfg.q_mode, cfg.qcmd, cfg.qhome) == ("bundled", None, None)
    assert "qcmd" not in cfg.sources and "qhome" not in cfg.sources
    assert cfg.torq_home == "/opt/torq"  # another component's: untouched
    # a flag switching back is a switch too, and drops nothing it does not own
    (cfg,) = targets.configs("switched", "x", {"q_mode": "existing"}, False, root)
    assert (cfg.q_mode, cfg.qcmd, cfg.q_runtime) == ("existing", None, None)


def test_a_flag_switching_a_mode_drops_the_targets_fields(root):
    """The flag drops [defaults]' qcmd and qhome, so the refusal is bundled's own."""
    with pytest.raises(DeployError, match="q_mode is bundled, and needs q_runtime"):
        targets.configs("kx", "x", {"q_mode": "bundled"}, False, root)
    flags = {"q_mode": "bundled", "q_runtime": "dist/q-runtime.tar.gz"}
    (cfg,) = targets.configs("kx", "x", flags, False, root)
    assert (cfg.qcmd, cfg.qhome, cfg.q_runtime) == (None, None, "dist/q-runtime.tar.gz")


def test_a_field_set_beside_the_switch_is_refused(root):
    with pytest.raises(DeployError, match="q_mode is bundled, but qcmd"):
        targets.configs("both", "x", {}, False, root)


# --- recorded -----------------------------------------------------------------------


def test_the_report_records_each_components_mode_path_and_version(tmp_path):
    _, report = _push(tmp_path, facts="qversion=5.0\n")
    c = report["components"]
    assert set(c) == {"q", "torq", "python"}
    assert {name: x["mode"] for name, x in c.items()} == dict.fromkeys(c, "existing")
    q = c["q"]
    assert (q["path"], q["home"], q["version"]) == ("/opt/kx home/bin/q", "/opt/kx home", "5.0")
    assert any("licence" in r for r in q["requires"])
    assert (c["torq"]["path"], c["torq"]["version"]) == ("/opt/torq", None)
    py = c["python"]
    assert py["version"] == "3.14" and py["path"].endswith("/.venv/bin/python")


def test_a_q_version_preflight_could_not_read_is_recorded_as_none(tmp_path):
    _, report = _push(tmp_path)
    assert report["components"]["q"]["version"] is None


def test_the_history_records_the_components(tmp_path, monkeypatch):
    monkeypatch.setenv(history.HISTORY_ENV, str(tmp_path / "history.json"))
    rules = {**_HEALTHY, **VERIFIED}
    rules["uv python find"] = _done(SERVER + "qversion=5.0\ndata=present\n")
    cfg = parse_args(_args(_artifact(tmp_path), "--torq-home", "/opt/torq"))
    out = io.StringIO()
    driver.deploy(
        cfg, FakeRemote(rules), out=out, on_report=lambda r: history.record(tmp_path, cfg, r)
    )
    (entry,) = history.read(tmp_path)
    assert entry["components"]["q"]["version"] == "5.0"
    assert entry["components"]["torq"]["mode"] == "existing"


def test_the_dry_run_shows_the_components(tmp_path):
    rules = {"uv python find": _done(SERVER + "qversion=5.0\ndata=present\n")}
    out = io.StringIO()
    cfg = parse_args(_args(_artifact(tmp_path), "--torq-home", "/opt/torq", "--dry-run"))
    assert driver.deploy(cfg, FakeRemote(rules), out=out) == 0
    shown = out.getvalue()
    assert "COMPONENTS - what the release runs on:" in shown
    assert "q      existing (default): /opt/kx home/bin/q, version 5.0" in shown
    assert "torq   existing (default): /opt/torq, version not read" in shown
    assert "python existing (default): " in shown and "licence" in shown
