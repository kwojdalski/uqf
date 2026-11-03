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
from uqs.cli import shared, start_override_opts, target_confirm
from uqs.deploy import control
from uqs.paths import UqsError
from uqs.stack import occupancy, runtime, start_overrides, start_policy
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


def _cli_start(monkeypatch, paths, argv: list[str]) -> tuple[Overrides | None, list[dict]]:
    """`uqs <argv>` through the real CLI, start_policy and record, on `paths`:
    the Overrides that reached the launcher, and what was recorded (less its
    time). The launcher itself is never run."""
    reached: list[Overrides] = []

    def launcher(*_a, **_k):
        raise RuntimeError("reached the launcher")

    real_start, real_restart = runtime.start, runtime.restart

    def through(real):
        def run(p, names, **kw):
            reached.append(kw["skips"])
            try:
                real(p, names, **kw)
            except RuntimeError as exc:
                assert "reached the launcher" in str(exc)
            raise SystemExit(0)

        return run

    monkeypatch.setattr(stack_paths, "default_paths", lambda: paths)
    monkeypatch.setattr(runtime, "torq_launcher", launcher)
    monkeypatch.setattr(runtime, "start", through(real_start))
    monkeypatch.setattr(runtime, "restart", through(real_restart))
    (paths.torqdata / OVERRIDES_LOG).unlink(missing_ok=True)
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 0, (result.output, result.exception)
    return (reached[0] if reached else None), [
        {k: v for k, v in e.items() if k != "at"} for e in _record(paths)
    ]


@pytest.mark.parametrize("verb", ["start", "restart"])
def test_skip_q_version_check_is_its_override_through_policy_and_record(old_q, monkeypatch, verb):
    """#1918: `--skip-q-version-check --reason R` is `--override q-version-check
    --reason R` - the same Overrides reach the launcher, the same q-version
    refusal is lifted, and the same record is written, naming the risk."""
    why = "checking which jobs load on uat's 4.1"
    alias = _cli_start(
        monkeypatch, old_q, [verb, "rdb1", "--skip-q-version-check", "--reason", why]
    )
    canon = _cli_start(
        monkeypatch, old_q, [verb, "rdb1", "--override", "q-version-check", "--reason", why]
    )
    assert alias == canon
    skips, (entry,) = alias
    assert skips == Overrides(q_version=why)
    assert entry["override"] == "--override q-version-check" and entry["reason"] == why
    assert entry["risk"] == start_overrides.GUARDRAILS["q_version"].risk
    assert "nested context" in entry["risk"]


def test_skip_profile_budget_check_is_its_override_through_policy_and_record(old_q, monkeypatch):
    over = "the set needs 99 plant slots, past the budget"
    monkeypatch.setattr(start_policy.profiles, "over_budget_procs", lambda *_a: over)
    why = "full licence on this host"
    base = ["start", "--profile", "essential", "--reason", why]
    alias = _cli_start(monkeypatch, old_q, [*base, "--skip-profile-budget-check",
                                             "--skip-q-version-check"])  # fmt: skip
    canon = _cli_start(monkeypatch, old_q, [*base, "--override", "profile-budget-check",
                                             "--override", "q-version-check"])  # fmt: skip
    assert alias == canon
    skips, entries = alias
    assert skips == Overrides(q_version=why, profile_budget=why)
    assert {e["override"] for e in entries} == {
        "--override profile-budget-check", "--override q-version-check"
    }  # fmt: skip


def test_a_skip_alias_waives_only_its_own_check(old_q, monkeypatch):
    """--skip-profile-budget-check leaves the q-version refusal in place, and
    --skip-q-version-check the budget's; neither is a blanket skip."""
    said: list[str] = []

    class Log:
        def error(self, msg, *a):
            said.append(str(msg).format(*a))

        def __getattr__(self, _name):
            return lambda *a, **kw: None

    monkeypatch.setattr(shared, "log", Log())
    monkeypatch.setattr(stack_paths, "default_paths", lambda: old_q)
    monkeypatch.setattr(runtime, "torq_launcher", lambda *_a, **_k: pytest.fail("launched"))
    over = "the set needs 99 plant slots, past the budget"
    monkeypatch.setattr(start_policy.profiles, "over_budget_procs", lambda *_a: over)
    base = ["start", "--profile", "essential", "--reason", "r"]
    budget_only = runner.invoke(cli.app, [*base, "--skip-profile-budget-check"])
    assert budget_only.exit_code != 0 and any("no nested contexts" in m for m in said)
    said.clear()
    q_only = runner.invoke(cli.app, [*base, "--skip-q-version-check"])
    assert q_only.exit_code != 0 and any("past the budget" in m for m in said)


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (["--skip-q-version-check"], "--skip-q-version-check needs --reason"),
        (["--skip-q-version-check", "--reason", "  "], "--skip-q-version-check needs --reason"),
        # the old hidden spelling took the reason as its value; now it is refused, not guessed
        (["--skip-q-version-check", "old q on uat"], "--skip-q-version-check needs --reason"),
        (["--skip-q-version-check", "--override", "q-version-check", "--reason", "r",
          "--dry-run"], "a dry run starts nothing"),
        (["--skip-profile-budget-check", "--reason", "r"], "needs --profile"),
    ],
)  # fmt: skip
def test_a_skip_alias_is_refused_as_its_override_is(monkeypatch, args, says):
    said: list[str] = []

    class Log:
        def error(self, msg, *a):
            said.append(str(msg).format(*a))

        def __getattr__(self, _name):
            return lambda *a, **kw: None

    monkeypatch.setattr(shared, "log", Log())
    monkeypatch.setattr(runtime, "start", lambda *_a, **_k: pytest.fail("started"))
    result = runner.invoke(cli.app, ["start", "rdb1", *args])
    assert result.exit_code != 0
    assert any(says in m for m in said), said


def test_a_remote_start_forwards_the_reason_intact():
    reason = "uat's q is 4.1; $(not run)"
    args = ["start", "rdb1", "--override", "q-version-check", "--reason", reason]
    assert target_confirm.changes_the_server(args)
    r = control.Remote("uat", "h", "/srv/uqf", remote_user="svc")
    script = shlex.split(control.remote_command(r, args))[-1]  # what the account's bash runs
    assert shlex.split(script.rsplit("exec ", 1)[1]) == [".venv/bin/uqs", *args]


def test_the_front_ends_that_pass_no_override_keep_every_refusal():
    """The MCP server and the browser call runtime.start without overrides:
    the default skips nothing."""
    assert Overrides().given() == {}
