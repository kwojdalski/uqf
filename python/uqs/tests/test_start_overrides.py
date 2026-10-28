"""Named, reasoned overrides of a start's refusals (#1643).

Each override skips exactly its own check, for one command, with a reason
that is warned and recorded; everything else a start refuses still refuses.
The real start_policy and runtime run here, against this checkout's own
process table, with torq.sh replaced by a stub.
"""

from __future__ import annotations

import dataclasses
import json
import shlex

import pytest
from typer.testing import CliRunner

from uqs import cli, interpreter
from uqs import paths as stack_paths
from uqs.cli import shared, start_override_opts
from uqs.deploy import control
from uqs.paths import UqsError
from uqs.stack import occupancy, runtime, start_policy
from uqs.stack.start_overrides import OVERRIDES_LOG, Overrides

runner = CliRunner()


@pytest.fixture
def old_q(monkeypatch, tmp_path):
    """A source-tree KDB-X runtime whose q answers 4.1, its data in tmp_path."""
    paths = dataclasses.replace(stack_paths.default_paths(), torqdata=tmp_path / "data")
    decl = dataclasses.replace(paths.runtime_declaration, q_tree="source")
    monkeypatch.setattr(type(paths), "runtime_declaration", property(lambda _s: decl))
    q = tmp_path / "q"
    # KDB-X when asked which q it is, 4.1 when asked its version.
    q.write_text("#!/bin/sh\n/usr/bin/grep -q load_natives \"$1\" && echo kdbx || echo '4.1'\n")
    q.chmod(0o755)
    monkeypatch.setenv("QCMD", str(q))
    monkeypatch.setattr(occupancy, "refuse_if_taken", lambda *_a: None)
    interpreter.q_version.cache_clear()
    yield paths
    interpreter.q_version.cache_clear()


def _record(paths) -> list[dict]:
    log = paths.torqdata / OVERRIDES_LOG
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


def test_the_q_version_refusal_holds_by_default(old_q):
    with pytest.raises(UqsError, match=r"kdb\+ 4\.1, which has no nested contexts"):
        start_policy.refuse_start(old_q, ["start", "rdb1"], None)
    assert _record(old_q) == []


def test_the_q_version_override_skips_only_that_check_and_records_it(old_q):
    start_policy.refuse_start(
        old_q, ["start", "rdb1"], None, Overrides(q_version="testing 4.1 compatibility")
    )
    (entry,) = _record(old_q)
    assert entry["override"] == "--override q-version-check"
    assert entry["reason"] == "testing 4.1 compatibility"
    assert (entry["command"], entry["processes"]) == ("start", "rdb1")
    assert entry["runtime"] == old_q.runtime


def test_mandatory_checks_still_refuse_under_an_override(old_q, monkeypatch):
    """A process-name typo and taken ports are never skipped."""
    q_skipped = Overrides(q_version="testing")
    with pytest.raises(UqsError, match="nosuchproc1"):
        start_policy.refuse_start(old_q, ["start", "nosuchproc1"], None, q_skipped)

    def taken(*_a):
        raise UqsError("ports held by another stack")

    monkeypatch.setattr(occupancy, "refuse_if_taken", taken)
    with pytest.raises(UqsError, match="ports held"):
        start_policy.refuse_start(old_q, ["start", "rdb1"], None, q_skipped)


def test_the_runtime_passes_the_override_through_to_the_policy(old_q, monkeypatch):
    def launched(*_a, **_k):
        raise RuntimeError("reached the launcher")

    monkeypatch.setattr(runtime, "torq_launcher", launched)
    with pytest.raises(UqsError, match="no nested contexts"):
        runtime.start(old_q, "rdb1")
    with pytest.raises(RuntimeError, match="reached the launcher"):
        runtime.start(old_q, "rdb1", skips=Overrides(q_version="testing"))


@pytest.mark.parametrize("reason", ["", "   "])
def test_an_override_needs_a_reason(reason):
    with pytest.raises(UqsError, match="--override q-version-check needs --reason"):
        Overrides(q_version=reason)


def test_the_budget_override_skips_only_the_estimate(old_q, monkeypatch):
    over = "the set needs 99 plant slots, past the budget"
    monkeypatch.setattr(start_policy.profiles, "over_budget_procs", lambda *_a: over)
    with pytest.raises(UqsError, match="past the budget"):
        start_policy.resolve_profiles(old_q, "essential")
    members, _ = start_policy.resolve_profiles(
        old_q, "essential", (), Overrides(profile_budget="full licence on this host")
    )
    assert members
    (entry,) = _record(old_q)
    assert entry["override"] == "--override profile-budget-check"
    # An undeclared profile is still refused, override or not.
    with pytest.raises(UqsError, match="nosuchprofile"):
        start_policy.resolve_profiles(
            old_q, "nosuchprofile", (), Overrides(profile_budget="testing")
        )


@pytest.mark.parametrize(
    ("kw", "says"),
    [
        (dict(names=["q-version-check"], print_only=True), "a dry run starts nothing"),
        (dict(names=["profile-budget-check"]), "needs --profile"),
        (dict(names=["no-such-check"]), "has no override 'no-such-check'"),
        (dict(names=["q-version-check"], reason=" "), "needs --reason"),
        (dict(names=[]), "none was given"),
    ],
)
def test_an_override_that_would_skip_nothing_is_refused(kw, says):
    with pytest.raises(UqsError, match=says):
        start_override_opts.overrides_from(
            "start", kw["names"], kw.get("reason", "x"), print_only=kw.get("print_only", False)
        )


def test_restart_takes_the_budget_override_as_start_does():
    """#1734 gave restart start's options, --profile among them."""
    got = start_override_opts.overrides_from("restart", ["profile-budget-check"], "x", profile="fx")
    assert got.profile_budget == "x"


def test_the_cli_refuses_an_irrelevant_combination_before_starting(monkeypatch):
    """`_die` reports through loguru, so the message is caught at its logger
    (see test_cli.py's _ErrorLog), not in CliRunner's output."""
    said: list[str] = []

    class Log:
        def error(self, msg, *a):
            said.append(str(msg).format(*a))

        def __getattr__(self, _name):
            return lambda *a, **kw: None

    monkeypatch.setattr(shared, "log", Log())
    monkeypatch.setattr(runtime, "start", lambda *_a, **_k: pytest.fail("started"))
    result = runner.invoke(
        cli.app, ["start", "rdb1", "--dry-run", "--override", "q-version-check", "--reason", "x"]
    )
    assert result.exit_code != 0
    assert any("a dry run starts nothing" in m for m in said)


def test_the_cli_hands_the_reason_to_the_stack(monkeypatch):
    seen = {}

    def start(_paths, names, **kw):
        seen.update(kw, names=names)
        raise SystemExit(0)

    monkeypatch.setattr(runtime, "start", start)
    args = ["start", "rdb1", "--override", "q-version-check", "--reason", "old q on uat"]
    runner.invoke(cli.app, args)
    assert seen["skips"] == Overrides(q_version="old q on uat")


def test_the_old_skip_flag_still_gives_the_override_for_one_release(monkeypatch):
    """`--skip-q-version-check REASON` is `--override q-version-check --reason
    REASON` (#1732), hidden, until the next release."""
    seen = {}

    def start(_paths, names, **kw):
        seen.update(kw)
        raise SystemExit(0)

    monkeypatch.setattr(runtime, "start", start)
    runner.invoke(cli.app, ["start", "rdb1", "--skip-q-version-check", "old q on uat"])
    assert seen["skips"] == Overrides(q_version="old q on uat")


def test_a_remote_start_forwards_the_reason_intact():
    reason = "uat's q is 4.1; $(not run)"
    args = ["start", "rdb1", "--override", "q-version-check", "--reason", reason]
    assert control.changes_the_server(args)
    r = control.Remote("uat", "h", "/srv/uqf", remote_user="svc")
    script = shlex.split(control.remote_command(r, args))[-1]  # what the account's bash runs
    assert shlex.split(script.rsplit("exec ", 1)[1]) == [".venv/bin/uqs", *args]


def test_the_front_ends_that_pass_no_override_keep_every_refusal():
    """The MCP server and the browser call runtime.start without overrides:
    the default skips nothing."""
    assert Overrides().given() == {}
