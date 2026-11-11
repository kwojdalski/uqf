"""An attached publisher starts without the whole-stack bootstrap (#1847).

The site here is a TorQ stack uqf does not manage: its own TORQHOME and
TorQApp, a schema, an HDB, encrypted credentials under KDBSECURE - and no
starter-pack `appconfig/passwords/accesslist.txt`. Starting the publisher of
an attachment to it used to build gateway1's and sctp1's access lists from
that file and fail with FileNotFoundError before anything launched.

Both routes are driven - `uqs job attachment add` and the direct start of a
saved attachment's publisher (`UqsPublisher.start`, `uqs start PROC`) - with
every refusal of a start real except the q-version probe, which needs a q
(it is spied on, so it is still seen to run). torq.sh is the one thing faked:
its launch is recorded, never run.
"""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from attach_support import FakeStack
from typer.testing import CliRunner
from which_support import fake_which

from uqs import cli
from uqs import paths as stack_paths
from uqs.attach import config, facts, live, service
from uqs.attach.config import AttachmentError
from uqs.attach.manifest import Manifest
from uqs.attach.publisher import UqsPublisher
from uqs.cli import shared
from uqs.model.pipelines import PIPELINE_OFFSETS
from uqs.paths import UqsError, repo_root
from uqs.stack import alive, attach_state, attached_publisher, gateway_access, qtree, runtime

ROOT = repo_root()
JOB, PUBLISHER, STACK = "executions", "executions1", 6000
REAL_ACCESS_WRITE = gateway_access.write
ANSI = re.compile(r"\x1b\[[0-9;]*m")
SITE_ROWS = (
    "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"
    "localhost,{KDBBASEPORT}+1,discovery,discovery1,,1,0,,,${KDBCODE}/processes/discovery.q,1,,q\n"
    "localhost,{KDBBASEPORT},segmentedtickerplant,stp1,,1,0,,,"
    "${KDBCODE}/processes/segmentedtickerplant.q,1,-schemafile ${TORQAPPHOME}/database.q,q\n"
)


@dataclass
class Site:
    torq: Path
    app: Path
    secure: Path
    data: Path
    #: (argv, env) of every torq.sh launch
    launched: list[tuple[list[str], dict[str, str]]] = field(default_factory=list)
    #: the whole-stack bootstrap steps that ran
    whole: list[str] = field(default_factory=list)
    #: the q-version probe, which needs a q, called
    probed: int = 0
    #: attachments the live table check was asked about
    live: list[str] = field(default_factory=list)

    def paths(self) -> stack_paths.UqsPaths:
        return stack_paths.paths_for_root(ROOT)

    def snapshot(self) -> dict[str, bytes]:
        """Every file of the stack's own trees: its TorQ, TorQApp (schema, process
        registry, HDB, settings) and its credentials."""
        return {
            str(p): p.read_bytes()
            for tree in (self.torq, self.app, self.secure)
            for p in sorted(tree.rglob("*"))
            if p.is_file()
        }


@pytest.fixture
def site(tmp_path: Path, monkeypatch) -> Site:
    base = tmp_path / "site"
    s = Site(base / "torq", base / "TorQApp", base / "secure", tmp_path / "uqf-data")
    for d in (s.torq, s.app / "appconfig" / "settings", s.secure, s.data):
        d.mkdir(parents=True)
    (s.torq / "torq.q").write_text("")
    (s.torq / "torq.sh").write_text("#!/bin/sh\n")
    (s.torq / "torq.sh").chmod(0o755)
    (s.app / "database.q").write_text("quote:([]time:`timestamp$(); sym:`g#`symbol$())\n")
    (s.app / "appconfig" / "process.csv").write_text(SITE_ROWS)
    (s.app / "appconfig" / "settings" / "default.q").write_text(".pm.enabled:1b\n")
    part = s.app / "hdb" / "2026.10.01" / "quote"
    part.mkdir(parents=True)
    (part / ".d").write_bytes(b"\xff\x01time sym")
    (s.app / "dqe").mkdir()
    (s.secure / "stp1.enc").write_bytes(b"\x00encrypted\x01")
    for var, value in (
        ("TORQHOME", s.torq), ("TORQAPPHOME", s.app), ("UQS_DATA_ROOT", s.data),
        (attached_publisher.KDBSECURE_ENV, s.secure),
        (config.ATTACH_DIR_ENV, tmp_path / "attachments"),
    ):  # fmt: skip
        monkeypatch.setenv(var, str(value))
    monkeypatch.delenv(attached_publisher.SITE_AUTH_ENV, raising=False)
    monkeypatch.delenv("UQS_RUNTIME", raising=False)
    fake_which(monkeypatch, other="/usr/bin/true")

    def launch(cmd, env=None, **_kw):
        # subprocess is one module: only torq.sh's launch is recorded
        if cmd and Path(cmd[0]).name == "torq.sh":
            s.launched.append((list(cmd), dict(env or {})))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def step(name: str):
        def ran(*_a, **_k):
            s.whole.append(name)
            return ""

        return ran

    def probe(*_a) -> None:
        s.probed += 1

    monkeypatch.setattr(runtime.subprocess, "run", launch)
    monkeypatch.setattr(alive, "_command_lines", lambda timeout=None: [])
    monkeypatch.setattr(qtree, "refuse_unloadable", probe)
    monkeypatch.setattr(gateway_access, "write", step("gateway/subscriber access lists"))
    monkeypatch.setattr(runtime, "write_dqe_config", step("dqe config"))
    monkeypatch.setattr(runtime, "write_reporter_config", step("reporter config"))
    monkeypatch.setattr(runtime, "fill_hdb_partitions", step("hdb fill"))
    monkeypatch.setattr(runtime, "declared_schema", step("schema"))
    real_live = attach_state.live_check

    def live_check(name: str) -> None:
        s.live.append(name)
        if real_live is not None:
            real_live(name)

    monkeypatch.setattr(attach_state, "live_check", live_check)
    return s


def saved(state: str = "verified") -> config.Attachment:
    att = service.create("ex-site", JOB, None, STACK, ROOT)
    config.save(att)
    m = Manifest.new(att, Manifest.path_for(att.name))
    m.set_state(state, "set by the test")
    return att


def row(paths: stack_paths.UqsPaths, procname: str) -> dict[str, str]:
    with paths.generated_procs.open(newline="") as f:
        return next(r for r in csv.DictReader(f) if r["procname"] == procname)


def assert_only_the_publisher_was_prepared(site: Site, before: dict[str, bytes]) -> None:
    paths = site.paths()
    assert site.whole == [], f"whole-stack steps ran for a publisher: {site.whole}"
    assert site.snapshot() == before, "the stack's own files were changed"
    assert not paths.generated_gateway_access.exists()
    assert not (paths.torqdata / "subscriber_accesslist.txt").exists()
    assert not paths.generated_schema.exists() and not paths.hdb_dir.exists()
    assert paths.generated_procs.is_file() and paths.generated_setenv.is_file()


# ------------------------------------------------- the direct start of a saved one


def test_a_saved_attachments_publisher_starts_on_a_site_with_its_own_auth(site):
    saved()
    before = site.snapshot()
    UqsPublisher(site.paths()).start(config.load("ex-site"))

    [(argv, env)] = site.launched
    assert argv == [str(site.torq / "torq.sh"), "start", PUBLISHER]
    assert env["KDBBASEPORT"] == str(STACK)
    # the site's auth environment, as the site's processes see it, and uqf's
    # handlers told to leave its loader and .pm users alone
    assert env[attached_publisher.KDBSECURE_ENV] == str(site.secure)
    assert env[attached_publisher.SITE_AUTH_ENV] == "1"
    assert env["KDBAPPCODE"] == str(site.app / "code"), "the site's own code still loads"
    assert row(site.paths(), PUBLISHER)["U"] == "", "no -U naming a starter-pack list"
    assert_only_the_publisher_was_prepared(site, before)
    # every refusal of a start ran: q version, the gate's live table check
    assert site.probed == 1 and site.live == ["ex-site"]


def test_uqs_start_of_the_publisher_takes_the_same_path(site):
    saved()
    before = site.snapshot()
    result = CliRunner().invoke(
        cli.app, ["start", PUBLISHER, "--base-port", str(STACK)], catch_exceptions=False
    )
    assert result.exit_code == 0, ANSI.sub("", result.output)
    [(argv, env)] = site.launched
    assert argv[1:] == ["start", PUBLISHER] and env[attached_publisher.SITE_AUTH_ENV] == "1"
    assert_only_the_publisher_was_prepared(site, before)


def test_a_starter_pack_stack_keeps_its_access_list_and_uqfs_loader(site, monkeypatch):
    """KDBSECURE unset and the starter pack's list there: as before #1847 -
    the row's -U names the list, and nothing tells uqf's loader to stand down."""
    monkeypatch.delenv(attached_publisher.KDBSECURE_ENV)
    access = site.app / attached_publisher.STARTER_ACCESS_LIST
    access.parent.mkdir(parents=True)
    access.write_text("admin:admin\n")
    saved()
    before = site.snapshot()
    UqsPublisher(site.paths()).start(config.load("ex-site"))
    [(_argv, env)] = site.launched
    assert attached_publisher.SITE_AUTH_ENV not in env
    assert row(site.paths(), PUBLISHER)["U"] == "${TORQAPPHOME}/appconfig/passwords/accesslist.txt"
    assert_only_the_publisher_was_prepared(site, before)


def test_an_auth_it_cannot_tell_is_refused_by_name_not_given_demo_credentials(site, monkeypatch):
    monkeypatch.delenv(attached_publisher.KDBSECURE_ENV)
    saved()
    with pytest.raises(AttachmentError) as refused:
        UqsPublisher(site.paths()).start(config.load("ex-site"))
    said = " ".join(str(refused.value).split())
    assert f"{PUBLISHER} did not start: {PUBLISHER} cannot be started" in said
    assert "has no appconfig/passwords/accesslist.txt and KDBSECURE is not set" in said
    assert "uqf will not fall back to TorQ's demo credentials" in said
    assert "export KDBSECURE as the site's processes see it" in said
    assert site.launched == [] and site.whole == []


def test_kdbsecure_naming_no_directory_is_refused(site, monkeypatch):
    monkeypatch.setenv(attached_publisher.KDBSECURE_ENV, str(site.secure / "missing"))
    saved()
    with pytest.raises(AttachmentError, match=r"KDBSECURE=.*missing' is not a directory"):
        UqsPublisher(site.paths()).start(config.load("ex-site"))
    assert site.launched == []


def test_an_unverified_attachment_still_refuses_the_launch(site):
    saved("applied")
    with pytest.raises(AttachmentError, match="ex-site .*which is applied: it may start once"):
        UqsPublisher(site.paths()).start(config.load("ex-site"))
    assert site.launched == [] and not site.paths().generated_procs.exists()


def test_an_occupied_port_still_refuses_the_launch(site, monkeypatch, tmp_path):
    saved()
    other = tmp_path / "other-stack"
    other.mkdir()
    (other / "process.csv").write_text(
        "host,port,proctype,procname\n"
        f"localhost,{{KDBBASEPORT}}+{PIPELINE_OFFSETS[PUBLISHER]},rdb,squatter\n"
    )
    line = f"q x.q -stackid {STACK} -procname squatter -procfile {other / 'process.csv'}"
    monkeypatch.setattr(alive, "_command_lines", lambda timeout=None: [(4242, line)])
    with pytest.raises(AttachmentError, match=r"already using ports .*squatter .*pid 4242"):
        UqsPublisher(site.paths()).start(config.load("ex-site"))
    assert site.launched == []


def test_stopping_the_publisher_needs_no_auth_and_no_stack_preparation(site, monkeypatch):
    monkeypatch.delenv(attached_publisher.KDBSECURE_ENV)
    saved()
    UqsPublisher(site.paths()).stop(config.load("ex-site"))
    # Found by command line and signalled directly (stack/stopping.py, #2689): no
    # launcher runs, and nothing of the stack is prepared for it.
    assert site.launched == [] and site.whole == []


# ------------------------------------------------------- `job attachment add`


@pytest.fixture
def stack(site, monkeypatch) -> FakeStack:
    fake = FakeStack(STACK)
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    monkeypatch.setattr(live, "ipc_for", lambda att: fake)
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: port in fake.procs)
    monkeypatch.setattr(shared, "configure_logging", lambda **kw: None)
    return fake


def test_attachment_add_prepares_and_launches_only_the_publisher(site, stack):
    before = site.snapshot()
    result = CliRunner().invoke(
        cli.app,
        ["job", "attachment", "add", "ex-site", "--job", JOB, "--base-port", str(STACK),
         "--transient", "--yes"],
        catch_exceptions=False,
    )  # fmt: skip
    output = ANSI.sub("", result.output)
    assert result.exit_code == 0, output
    assert Manifest.load("ex-site").state == "verified"
    [(argv, env)] = site.launched
    assert argv[1:] == ["start", PUBLISHER] and env["KDBBASEPORT"] == str(STACK)
    assert env[attached_publisher.SITE_AUTH_ENV] == "1"
    assert_only_the_publisher_was_prepared(site, before)
    assert site.probed == 1 and site.live == ["ex-site"], "the runtime-only table re-checked"


def test_attachment_add_that_fails_verification_never_launches(site, stack, monkeypatch):
    from uqs.attach import verify as verifying

    def failed(*_a, **_k):
        raise AttachmentError("the tickerplant lost the table")

    monkeypatch.setattr(verifying, "verify", failed)
    result = CliRunner().invoke(
        cli.app,
        ["job", "attachment", "add", "ex-site", "--job", JOB, "--base-port", str(STACK),
         "--transient", "--yes"],
        catch_exceptions=False,
    )  # fmt: skip
    assert result.exit_code != 0
    assert "was not started" in " ".join(ANSI.sub("", result.output).split())
    assert site.launched == []


# --------------------------------------------------------- the whole stack


def test_publishers_alone_are_the_only_command_that_skips_the_stack(site):
    saved()
    assert attached_publisher.publishers(["start", PUBLISHER, "-extras", "-x"]) == (PUBLISHER,)
    assert attached_publisher.publishers(["stop", PUBLISHER]) == (PUBLISHER,)
    for args in (["start"], ["start", "all"], ["start", "rdb1", PUBLISHER], ["start", "rdb1"]):
        assert attached_publisher.publishers(args) == (), args


def _start_all(paths: stack_paths.UqsPaths):
    return runtime.run_torq_sh(paths, ["start", "all"], capture=True)


def test_a_whole_stack_start_still_builds_and_checks_the_access_lists(site, monkeypatch):
    """A stack uqf manages - the vendored starter pack - started whole: both
    access lists are written from its accesslist.txt, and the stack's other
    steps run, before torq.sh is launched."""
    for var in ("TORQHOME", "TORQAPPHOME", attached_publisher.KDBSECURE_ENV):
        monkeypatch.delenv(var)
    monkeypatch.setenv("UQS_LICENCE_CONNECTIONS", "16")  # monitor1's budget, without asking q
    monkeypatch.setattr(gateway_access, "write", REAL_ACCESS_WRITE)
    paths = site.paths()
    _start_all(paths)
    assert "admin:admin" in paths.generated_gateway_access.read_text().splitlines()
    assert (paths.torqdata / "subscriber_accesslist.txt").is_file()
    assert {"schema", "dqe config", "reporter config", "hdb fill"} <= set(site.whole)
    [(argv, env)] = site.launched
    assert argv[1:] == ["start", "all"] and attached_publisher.SITE_AUTH_ENV not in env


def test_a_whole_stack_start_without_the_access_list_fails_by_name(site, monkeypatch):
    """The same start against the site with no starter-pack list is refused,
    naming the file - not a FileNotFoundError, and not skipped."""
    monkeypatch.setattr(gateway_access, "write", REAL_ACCESS_WRITE)
    with pytest.raises(UqsError) as refused:
        _start_all(site.paths())
    said = " ".join(str(refused.value).split())
    assert f"{site.app}/appconfig/passwords/accesslist.txt does not exist" in said
    assert "gateway1's and sctp1's access lists start from the starter pack's logins" in said
    assert "A publisher attached to a stack uqf does not manage needs neither list" in said
    assert site.launched == []
