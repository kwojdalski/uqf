"""The deploy and target commands hold to one contract (#2123, #2124, #2125).

- deploy_targets.toml is the flags written down: one name per setting, every
  example the help and the docstring give is accepted, and `deploy prune`
  honours a target's `keep` as `deploy push` does.
- `deploy push`, `rollback` and `prune` print text, or with --json one
  document through emit - a push to two targets, a dry run and a push that
  registers a target included - and everything else goes to stderr.
- An unknown target is one refusal, exit 1 and one phrasing, on every
  command; an unreadable registry is reported as unreadable.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs.cli import deploy_server
from uqs.deploy import driver, history, targets
from uqs.deploy import report as deploy_report
from uqs.deploy.config import DeployError
from uqs.deploy.report import Report

FILE = """\
[targets.a]
ssh = "deploy@a"
dest = "/srv/uqf"
profile = "essential"
keep = 3
launch_mode = "source"
launch_python = "/opt/py/bin/python3"
launch_env_file = "/opt/uqf/env.sh"

[targets.b]
ssh = "deploy@b"
dest = "/srv/uqf"
profile = "essential"
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    declared = tmp_path / targets.DECLARATION
    declared.write_text(FILE)
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    monkeypatch.setenv(history.HISTORY_ENV, str(tmp_path / "history.json"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _uqs(*argv: str):
    return CliRunner().invoke(cli.app, list(argv))


# ------------------------------------------------- #2123: the file is the flags


def test_prune_takes_the_targets_keep_as_push_does(root, monkeypatch):
    kept = []
    monkeypatch.setattr(
        deploy_server.prune,
        "prune",
        lambda cfg, remote, keep, **_k: (kept.append(keep) or 0, {"ok": True}),
    )
    monkeypatch.setattr(deploy_server.prune, "render", lambda doc: "pruned")
    assert _uqs("deploy", "prune", "a", "--yes").exit_code == 0
    assert _uqs("deploy", "prune", "a", "--keep", "1", "--yes").exit_code == 0
    assert kept == [3, 1], "the target's keep, and a flag given over it"


def test_prune_with_no_keep_anywhere_is_a_usage_error(root, log_text):
    r = _uqs("deploy", "prune", "b", "--yes")
    assert r.exit_code == 2
    assert "--keep is required: the target sets no keep" in log_text()


def test_every_target_set_help_example_is_a_key_the_file_takes(root):
    """`uqs target set --help` gave `host=deploy@prod`, which the file refuses
    (#2123): each example, set on its own, is accepted."""
    import typer.main

    command = typer.main.get_command(cli.app).commands["target"].commands["set"]  # ty: ignore[unresolved-attribute]
    (pairs,) = [p for p in command.params if p.name == "pairs"]
    examples = re.findall(r"""(\S+=(?:'[^']*'|\S+))""", pairs.help.split("VALUE is")[0])
    assert examples and examples[0].startswith("ssh=")
    for pair in examples:
        value = pair.split("=", 1)[1].strip("'")
        r = _uqs("target", "set", "a", f"{pair.split('=', 1)[0]}={value}", "--dry-run")
        assert r.exit_code == 0, (pair, r.output)


def test_the_docstrings_sample_file_is_a_valid_file():
    """targets.py's own sample spelt the ssh destination `host` (#2123)."""
    doc = targets.__doc__ or ""
    sample = doc[doc.index("    [targets.") :].split("\n\n")[0]
    text = "\n".join(line.removeprefix("    ") for line in sample.splitlines()) + "\n"
    merged = targets.validate(text, Path("sample.toml"))
    assert merged["prod-a"]["ssh"] == "deploy@prod-a"


def test_an_old_key_is_refused_naming_the_new_one():
    text = '[targets.a]\nhost = "h"\ndest = "/d"\nprofile = "p"\n'
    with pytest.raises(DeployError, match="host is ssh - rename the key") as caught:
        targets.validate(text, Path("t.toml"))
    assert caught.value.stage == targets.TARGET


def test_a_target_is_read_under_the_keys_the_file_spells():
    """No second key space (#2123): what read() returns is the file's keys,
    which are make_config's parameters and the Config's fields."""
    text = '[targets.a]\nssh = "h"\ndest = "/d"\nprofile = "p"\nq_cmd = "/opt/q"\n'
    assert targets.validate(text, Path("t.toml"))["a"] == {
        "ssh": "h",
        "dest": "/d",
        "profile": "p",
        "q_cmd": "/opt/q",
    }


@pytest.mark.parametrize("key", ["ssh", "host"])
def test_a_report_on_a_server_is_read_whichever_release_wrote_it(key):
    """deploy-report.json names the ssh destination `ssh` now; one an older
    release left on a server says `host`, and is read the same."""
    read = deploy_report.read({"release": "r1", key: "svc@box", "dest": "/srv"})
    assert read == {"release": "r1", "ssh": "svc@box", "dest": "/srv"}
    fields = {f for f in Report.__dataclass_fields__}
    assert "ssh" in fields and "host" not in fields


# ------------------------------------------- #2125: one document, under --json


def _report(cfg, status: str = "deployed") -> Report:
    return Report(
        release="20261008T000000Z-0123456789ab",
        revision="0" * 16,
        artifact={"name": "x.tar.gz", "sha256": "ab" * 32},
        dirty=False,
        ssh=cfg.ssh,
        dest=cfg.dest,
        profile=cfg.profile,
        status=status,
    )


@pytest.fixture
def pushed(root, monkeypatch):
    """`deploy push` with the server replaced: a dry run prints its plan to
    `out`; a run records itself in the history (so a new server registers)
    and ends; a target named in `fail` is refused at preflight."""
    fail: list[str] = []

    def deploy(cfg, remote, *, out, on_report, on_result):
        if cfg.target in fail:
            raise DeployError("preflight", f"{cfg.ssh} did not answer")
        if cfg.dry_run:
            print(f"PLAN for {cfg.ssh}", file=out)
            report = _report(cfg, "planned")
        else:
            report = _report(cfg)
            on_report(report)
        on_result(report)
        return 0

    monkeypatch.setattr(driver, "deploy", deploy)
    return fail


def _document(r) -> dict:
    """stdout, parsed as one JSON document - nothing before or after it."""
    return json.loads(r.stdout)


def test_a_two_target_push_is_one_document_with_a_row_each(pushed):
    r = _uqs("deploy", "push", "x.tar.gz", "a", "b", "--json")
    assert r.exit_code == 0, r.output
    doc = _document(r)
    assert doc["ok"] is True
    assert [(d["target"], d["ssh"], d["status"]) for d in doc["deployments"]] == [
        ("a", "deploy@a", "deployed"),
        ("b", "deploy@b", "deployed"),
    ]
    assert "host" not in doc["deployments"][0]


def test_a_dry_run_is_one_document_with_its_plan_and_note_on_stderr(pushed):
    r = _uqs("deploy", "push", "x.tar.gz", "a", "--dry-run", "--json")
    assert r.exit_code == 0, r.output
    (row,) = _document(r)["deployments"]
    assert row["status"] == "planned"
    assert "PLAN for deploy@a" in r.stderr and "dry run" in r.stderr.lower()


def test_a_push_that_registers_a_target_is_one_document(pushed, root):
    argv = ["--ssh", "svc@new.example", "--dest", "/srv/new", "--profile", "essential"]
    r = _uqs("deploy", "push", "x.tar.gz", *argv, "--json")
    assert r.exit_code == 0, r.output
    (row,) = _document(r)["deployments"]
    assert row["ssh"] == "svc@new.example"
    assert "registered target new-example-new" in r.stderr
    assert "new-example-new" in targets.read(root)


def test_a_push_refused_part_way_names_what_it_pushed(pushed, monkeypatch):
    pushed.append("b")
    argv = ["deploy", "push", "x.tar.gz", "a", "b", "--json"]
    # a refusal's document reads --json off the command line (json_requested)
    monkeypatch.setattr("sys.argv", ["uqs", *argv])
    r = _uqs(*argv)
    assert r.exit_code == 1
    doc = _document(r)
    assert doc["ok"] is False and "FAILED at preflight" in doc["error"]
    assert [d["target"] for d in doc["deployments"]] == ["a"]


def test_without_json_a_push_prints_text(pushed):
    r = _uqs("deploy", "push", "x.tar.gz", "a")
    assert r.exit_code == 0, r.output
    assert r.stdout.startswith(
        "deployed release 20261008T000000Z-0123456789ab to deploy@a:/srv/uqf"
    )
    with pytest.raises(ValueError):
        json.loads(r.stdout)


@pytest.mark.parametrize("command", ["push", "rollback", "prune"])
def test_push_rollback_and_prune_take_json(command):
    """They printed JSON always, and refused --json (#2125)."""
    import typer.main

    deploy = typer.main.get_command(cli.app).commands["deploy"]  # ty: ignore[unresolved-attribute]
    assert "--json" in {o for p in deploy.commands[command].params for o in p.opts}


# ----------------------------------------- #2124: one refusal for a bad name


#: Every command that names a declared target, and how it is asked for one
#: that is not declared.
UNKNOWN = [
    ["deploy", "push", "x.tar.gz", "nosuch"],
    ["deploy", "status", "nosuch"],
    ["deploy", "rollback", "nosuch"],
    ["deploy", "prune", "nosuch", "--keep", "1"],
    ["target", "check", "nosuch"],
    ["target", "edit", "nosuch"],
    ["target", "log", "nosuch"],
    ["target", "config", "history", "nosuch"],
    ["target", "set", "nosuch", "ssh=h"],
    ["target", "unset", "nosuch", "jobs"],
]


@pytest.mark.parametrize("argv", UNKNOWN, ids=" ".join)
def test_an_unknown_target_is_one_refusal_exiting_1(root, argv, log_text, monkeypatch):
    monkeypatch.setenv("EDITOR", "false")
    r = _uqs(*argv)
    said = r.output + log_text()
    assert r.exit_code == 1, said
    declared = f"{root / targets.DECLARATION} declares: a, b; `uqs target list` names them"
    assert re.search(r"no target( or deployed server)? nosuch - ", said), said
    assert declared in said, said


def test_target_log_reports_an_unreadable_registry_as_unreadable(root, log_text):
    """It answered `no target 'a'` for a declared target in a file that does
    not load (#2124)."""
    (root / targets.DECLARATION).write_text(FILE + "not_a_setting = 1\n")
    r = _uqs("target", "log", "a")
    said = r.output + log_text()
    assert r.exit_code == 1
    assert "unknown setting(s) not_a_setting" in said and "no target" not in said
