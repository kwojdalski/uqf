"""An even command surface (#1858): each `uqs list` kind under its noun, the
receiver a noun with two verbs, `config set`/`unset` as `target set`/`unset`,
`target edit NAME`, and the options one listing or range takes on every one."""

from __future__ import annotations

import json
import subprocess

from typer.testing import CliRunner

from uqs import cli
from uqs.attach import backfill_remote
from uqs.cli import shared, target_edit
from uqs.paths import UqsPaths
from uqs.stack import procs as stack_procs

runner = CliRunner()


def test_config_set_takes_several_field_value_pairs_and_unset_removes_one(monkeypatch, tmp_path):
    paths = UqsPaths.__new__(UqsPaths)
    monkeypatch.setattr("uqs.cli.config._paths", lambda: paths)
    monkeypatch.setattr(type(paths), "overrides_path", tmp_path / "o.csv", raising=False)
    monkeypatch.setattr(type(paths), "repo_root", tmp_path, raising=False)  # no bundle rows
    monkeypatch.setattr(type(paths), "orchestrator_dir", tmp_path, raising=False)
    monkeypatch.setattr(stack_procs, "list_process_names", lambda _p: ["rdb1"])
    ok = runner.invoke(cli.app, ["config", "set", "rdb1", "startwithall=0", "port=6061"])
    assert ok.exit_code == 0, ok.output
    text = (tmp_path / "o.csv").read_text()
    assert "rdb1,startwithall,0" in text and "rdb1,port,6061" in text
    gone = runner.invoke(cli.app, ["config", "unset", "rdb1", "port"])
    assert gone.exit_code == 0, gone.output
    assert "port" not in (tmp_path / "o.csv").read_text()
    refused = runner.invoke(cli.app, ["config", "unset", "rdb1", "port"])
    assert refused.exit_code == 1


def test_the_old_two_word_config_set_is_refused(monkeypatch):
    seen = []
    monkeypatch.setattr(stack_procs, "set_process_config", lambda *a: seen.append(a[1:4]))
    said = []
    errors = type("Errors", (), {"error": staticmethod(lambda f, *a: said.append(f.format(*a)))})
    monkeypatch.setattr(shared, "log", errors())
    result = runner.invoke(cli.app, ["config", "set", "rdb1", "startwithall", "1"])
    assert result.exit_code != 0 and seen == []
    assert said == ["'startwithall' is not FIELD=VALUE"]


def test_the_receiver_is_stopped_by_its_own_verb_and_a_dry_run_signals_nothing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        backfill_remote,
        "stop_receivers",
        lambda att, dry_run=False, kill=False: (
            calls.append(dry_run) or [{"worker": "d.receiver", "pid": 7, "outcome": "stopped"}]
        ),
    )
    dry = runner.invoke(cli.app, ["job", "receiver", "stop", "--attachment", "pb", "-n", "--json"])
    assert dry.exit_code == 0 and json.loads(dry.stdout)["dry_run"] is True
    assert calls == [True]


def test_target_edit_name_opens_the_file_at_that_targets_table(monkeypatch, tmp_path):
    decl = tmp_path / "deploy_targets.toml"
    decl.write_text('[defaults]\n\n[targets.uat]\nssh = "a@b"\ndest = "/srv"\n')
    monkeypatch.setattr(target_edit, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(target_edit.targets, "declaration_path", lambda _r: decl)
    monkeypatch.setenv("EDITOR", "vi")
    seen = []
    monkeypatch.setattr(
        target_edit.subprocess,
        "run",
        lambda argv, check=False: seen.append(argv) or subprocess.CompletedProcess(argv, 0),
    )
    result = runner.invoke(cli.app, ["target", "edit", "uat"])
    assert result.exit_code == 0, result.output
    assert seen[0][:2] == ["vi", "+3"]
    assert runner.invoke(cli.app, ["target", "edit", "nosuch"]).exit_code == 1


def test_schema_through_a_target_is_refused_beside_tunnel():
    from uqs.cli import target as target_cli

    try:
        target_cli.forward("uat", False, ["schema", "--tunnel", "uat"], interactive=False)
    except Exception as exc:  # noqa: BLE001 - the refusal is the point
        assert "give the target once" in str(exc)
    else:
        raise AssertionError("ran a schema through --target and --tunnel at once")
