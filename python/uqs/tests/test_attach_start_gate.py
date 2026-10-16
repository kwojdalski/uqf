"""The publisher start gate, from every route a start can take (#1441, #1442).

The gate (stack/attach_state.py) sits in start_policy.refuse_start, which
runtime.run_torq_sh applies first - so `uqs start`/`restart`, the MCP server,
the browser's lifecycle route and `uqs job attach` all reach it. Two
holes are pinned here:

  - #1441: `all` (and a bare `uqs start`/`restart`, which torq.sh reads as
    `all`) used to skip the gate, on the claim that a publisher is never a
    startwithall=1 row. A streaming job declared `start_with_all 1b` is one -
    `executions` is, so its publisher `executions1` is the real example here.
  - #1442: an attachment whose manifest (or attachment.json) cannot be read
    used to be skipped, which opened the gate for exactly that publisher.

Nothing here starts anything: torq.sh's launcher is replaced by one that
fails the test if it is ever reached.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from uqs import cli, mcp
from uqs.attach import config, service
from uqs.attach.config import AttachmentError
from uqs.attach.manifest import Manifest
from uqs.attach.publisher import UqsPublisher
from uqs.cli import shared
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import attach_state, listing, occupancy, qtree, runtime, start_policy

ROOT = repo_root()
PUBLISHER = "executions1"
STACK = 6000


@pytest.fixture
def state(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "attachments"
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(root))
    return root


@pytest.fixture
def att(state: Path) -> config.Attachment:
    attachment = service.create("ex-uat", "executions", None, STACK, ROOT)
    config.save(attachment)
    return attachment


@pytest.fixture(autouse=True)
def nothing_starts(monkeypatch):
    """Every refusal but the attach gate passes, and nothing reaches torq.sh."""
    monkeypatch.setattr(qtree, "refuse_unloadable", lambda *_a: None)
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)

    def launched(*_a, **_k):
        raise AssertionError("torq.sh was reached: the gate did not refuse the start")

    monkeypatch.setattr(runtime, "torq_launcher", launched)
    monkeypatch.setattr(runtime, "bootstrap", launched)


def with_state(att: config.Attachment, status: str) -> Path:
    m = Manifest.new(att, Manifest.path_for(att.name))
    m.set_state(status, "set by the test")
    return Manifest.path_for(att.name)


def test_the_example_publisher_is_a_startwithall_row():
    """The premise #1441 rests on: `all` launches this publisher."""
    rows = listing.list_items(default_paths(), "processes")
    assert {r["procname"]: r["startwithall"] for r in rows}[PUBLISHER] == "1"


# Every torq.sh argument list a front end produces for a start that launches
# the publisher: by name, `all`, and nothing named (which torq.sh reads as all).
STARTS = [
    ["start", PUBLISHER],
    ["start", "all"],
    ["start"],
    ["restart", PUBLISHER],
    ["restart", "all"],
    ["restart"],
    ["start", "rdb1", PUBLISHER],
    ["start", PUBLISHER, "-extras", "-x", "1"],
]


@pytest.mark.parametrize("args", STARTS, ids=" ".join)
@pytest.mark.parametrize("status", ["planned", "applied", "partial", "detached", "detaching"])
def test_an_unverified_publisher_is_refused_by_every_start_that_launches_it(att, args, status):
    if status != "planned":
        with_state(att, status)
    with pytest.raises(UqsError, match=f"publisher of attachment ex-uat .*which is {status}"):
        start_policy.refuse_start(default_paths(), args, STACK)


@pytest.mark.parametrize("args", STARTS, ids=" ".join)
@pytest.mark.parametrize("port", [None, 6100])
def test_a_verified_publisher_is_refused_under_another_stack(att, args, port):
    with_state(att, "verified")
    with pytest.raises(UqsError, match=f"on stack {STACK}.*--port {STACK}"):
        start_policy.refuse_start(default_paths(), args, port)


@pytest.mark.parametrize("args", STARTS, ids=" ".join)
def test_a_verified_publisher_starts_under_its_own_stack(att, args):
    with_state(att, "verified")
    start_policy.refuse_start(default_paths(), args, STACK)


def test_a_start_that_does_not_launch_the_publisher_is_not_gated(att):
    with_state(att, "applied")
    start_policy.refuse_start(default_paths(), ["start", "rdb1 wdb1"], None)
    start_policy.refuse_start(default_paths(), ["stop", PUBLISHER], None)
    start_policy.refuse_start(default_paths(), ["stop"], None)


# ------------------------------------------------------- unreadable state (#1442)


def unreadable_mode(path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    path.chmod(0o000)


@pytest.fixture
def chmod_back():
    changed: list[Path] = []
    yield changed
    for path in changed:
        path.chmod(0o755 if path.is_dir() else 0o644)


MANIFESTS = {
    "mode 000": None,
    "truncated": lambda text: text[: len(text) // 2],
    "no state key": lambda _text: json.dumps({"schema": 1}),
    "state null": lambda _text: json.dumps({"state": None}),
    "not an object": lambda _text: "[1, 2]",
    "empty": lambda _text: "",
}


@pytest.mark.parametrize("damage", list(MANIFESTS))
@pytest.mark.parametrize(
    "args", [["start", PUBLISHER], ["start", "all"], ["restart"]], ids=" ".join
)
def test_an_unreadable_manifest_refuses_its_publisher(att, damage, args, chmod_back):
    path = with_state(att, "verified")
    rewrite = MANIFESTS[damage]
    if rewrite is None:
        unreadable_mode(path)
        chmod_back.append(path)
    else:
        path.write_text(rewrite(path.read_text()))
    with pytest.raises(UqsError) as caught:
        start_policy.refuse_start(default_paths(), args, STACK)
    text = str(caught.value)
    assert f"{PUBLISHER} is the publisher of attachment ex-uat" in text
    assert "whose manifest is unreadable" in text and str(path) in text
    assert "uqs job attach ex-uat` has verified it" in text
    # ...and only its publisher: a start that does not launch it is not stopped.
    start_policy.refuse_start(default_paths(), ["start", "rdb1"], None)


def test_an_unreadable_manifest_is_reported_as_such(att, chmod_back):
    path = with_state(att, "verified")
    path.write_text("{")
    (pub,) = attach_state.publishers()
    assert pub.state == attach_state.UNREADABLE and str(path) in pub.problem


@pytest.mark.parametrize("damage", ["mode 000", "truncated", "no publisher", "folder mode 000"])
def test_an_unreadable_attachment_config_refuses_every_start(att, state, damage, chmod_back):
    """Its publisher cannot be known, so no start can be shown to be unrelated."""
    with_state(att, "verified")
    path = config.directory(att.name) / config.CONFIG_FILE
    if damage == "mode 000":
        unreadable_mode(path)
        chmod_back.append(path)
    elif damage == "folder mode 000":
        unreadable_mode(path.parent)
        chmod_back.append(path.parent)
    elif damage == "truncated":
        path.write_text(path.read_text()[:40])
    else:
        path.write_text(json.dumps({"stack": {"id": STACK}}))
    for args in (["start", "rdb1"], ["start", "all"], ["restart", PUBLISHER]):
        with pytest.raises(UqsError, match="an attachment cannot be read") as caught:
            start_policy.refuse_start(default_paths(), args, STACK)
        assert "Nothing was started" in str(caught.value) and str(state) in str(caught.value)


def test_a_folder_that_is_not_an_attachment_stops_nothing(att, state):
    with_state(att, "verified")
    (state / "scratch").mkdir()
    (state / ".DS_Store").write_text("")
    start_policy.refuse_start(default_paths(), ["start", "all"], STACK)


# ------------------------------------------------------------ the front ends


@pytest.fixture
def unverified(att):
    with_state(att, "applied")
    return att


@pytest.mark.parametrize(
    "call",
    [
        lambda: runtime.start(default_paths()),
        lambda: runtime.start(default_paths(), "all", base_port=STACK),
        lambda: runtime.restart(default_paths()),
        lambda: runtime.restart(default_paths(), PUBLISHER, base_port=STACK),
    ],
    ids=["start", "start all", "restart", "restart name"],
)
def test_the_service_layer_refuses(unverified, call):
    with pytest.raises(UqsError, match="publisher of attachment ex-uat"):
        call()


@pytest.mark.parametrize(
    "argv",
    [["start"], ["start", "all"], ["start", PUBLISHER], ["restart"], ["restart", "all"]],
    ids=" ".join,
)
def test_the_cli_refuses(unverified, argv, monkeypatch):
    # The refusal goes through loguru (cli.shared._die), which CliRunner does
    # not capture: record what `_die` logs instead (test_cli._ErrorLog).
    said: list[str] = []
    monkeypatch.setattr(
        shared, "log", SimpleNamespace(error=lambda m, *a: said.append(m.format(*a)))
    )
    result = CliRunner().invoke(cli.app, [*argv, "--port", str(STACK)])
    assert result.exit_code == 1
    assert any("publisher of attachment ex-uat" in line for line in said), said


@pytest.mark.parametrize(
    "call",
    [
        lambda: mcp.uqs_start(),
        lambda: mcp.uqs_start("all", port=STACK),
        lambda: mcp.uqs_restart(),
        lambda: mcp.uqs_start(PUBLISHER, port=STACK),
    ],
    ids=["start", "start all", "restart", "start name"],
)
def test_the_mcp_server_refuses(unverified, call):
    said = call()
    assert said.startswith("ERROR:") and "publisher of attachment ex-uat" in said


@pytest.mark.parametrize("action", ["start", "restart"])
@pytest.mark.parametrize("procs", ["all", PUBLISHER])
def test_the_browser_lifecycle_route_refuses(unverified, action, procs):
    control = pytest.importorskip("uqf_frontend.control")
    from uqf_frontend.config import Settings
    from uqf_frontend.errors import ValidationFailed

    with pytest.raises(ValidationFailed, match="publisher of attachment ex-uat"):
        control.lifecycle(Settings(enable_writes=True, base_port=STACK), action, procs)


def test_uqs_job_attach_start_refuses(unverified):
    with pytest.raises(AttachmentError, match="did not start: .*publisher of attachment ex-uat"):
        UqsPublisher(default_paths()).start(unverified)


def test_a_backfill_style_start_is_gated_too(unverified):
    """`uqs job backfill`/`replay`/`raw` call run_torq_sh with their own args."""
    with pytest.raises(UqsError, match="publisher of attachment ex-uat"):
        runtime.run_torq_sh(default_paths(), ["start", PUBLISHER, "-extras", "-x"], STACK)


def test_stop_is_never_gated(unverified, monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(runtime, "torq_launcher", lambda _p: Path("/bin/true"))
    monkeypatch.setattr(runtime, "bootstrap", lambda *_a, **_k: {})
    monkeypatch.setattr(
        subprocess, "run", lambda cmd, **_k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0)
    )
    runtime.stop(default_paths(), "all")
    assert seen and seen[0][1:] == ["stop", "all"]


def test_list_attachments_shows_a_detaching_state_as_it_is(att, state):
    with_state(att, "detaching")
    row = next(r for r in attach_state.inventory(state) if r["attachment"] == "ex-uat")
    assert row["state"] == "detaching"
