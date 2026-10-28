"""deploy_targets.toml changed as validated, atomic, logged transactions (#1756).

Each test works on a file the operator wrote by hand - comments, blank lines,
an order of their own - and holds the issue's acceptance: a change leaves the
old file or a valid new one, the rest of the file byte for byte; a hand edit is
picked up and recorded; every change is in the log and can be reverted.
`uqs target edit` runs a fake $EDITOR script; reaching a server runs the same
stand-in ssh as test_remote_targets.py, which executes the remote command here.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs.cli import app
from uqs.deploy import history, target_changelog, target_changes, target_file, targets
from uqs.deploy.config import DeployError

HAND_WRITTEN = """\
# Deployment targets for this workstation.
# Edited by hand - keep these comments.

[defaults]
profile = "essential"   # every target starts this

# production: careful
[targets.prod]
host = "deploy@prod"    # the bastion alias
dest = "/srv/uqf"
remote_user = "svc"

  # uat is rebuilt weekly
[targets.uat]
dest = "/srv/uat"
host = "deploy@uat"
labels = { tier = "uat" }


[environments.dr]
profile = "full"
"""

FAKE_SSH = """#!/bin/bash
cmd="${@: -1}"; host="${@: -2:1}"
[ "$host" = unreachable ] && { echo "ssh: connect to host unreachable" >&2; exit 255; }
exec bash -c "$cmd"
"""


@pytest.fixture
def declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "deploy_targets.toml"
    path.write_text(HAND_WRITTEN)
    monkeypatch.setenv(targets.DECLARATION_ENV, str(path))
    monkeypatch.delenv(history.HISTORY_ENV, raising=False)
    return path


@pytest.fixture
def ssh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in ssh on PATH that runs the remote command here."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


ROOT = Path(".")


def uqs(*argv: str, input: str | None = None):
    return CliRunner().invoke(app, list(argv), input=input)


@pytest.fixture
def capfd():
    """What `_die` logged: loguru writes to the stderr it was given at import,
    not CliRunner's, so a refusal is collected from a sink of its own."""
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="ERROR", format="{message}")
    yield lines
    logger.remove(sink)


def told(r, logged: list[str]) -> str:
    """What a command said, its refusals included."""
    return " ".join((r.output + "".join(logged)).split())


def _log(path: Path) -> list[dict]:
    return target_changelog.read_log(ROOT)


def _outside(old: str, new: str, block: str) -> tuple[str, str]:
    """The text of `old` and `new` outside the first occurrence of `block`
    in `old` (its replacement in `new` is whatever is left)."""
    i = old.index(block)
    head, tail = old[:i], old[i + len(block) :]
    assert new.startswith(head), "everything before the block is unchanged"
    assert new.endswith(tail), "everything after the block is unchanged"
    return head, tail


# --------------------------------------------------------------- transactions


def test_set_changes_one_value_and_nothing_else(declared):
    r = uqs("target", "set", "prod", "host=deploy@prod-2", "connect_timeout=20")
    assert r.exit_code == 0, r.output
    after = declared.read_text()
    assert after == HAND_WRITTEN.replace(
        'host = "deploy@prod"    # the bastion alias',
        'host = "deploy@prod-2"    # the bastion alias',
    ).replace('remote_user = "svc"\n', 'remote_user = "svc"\nconnect_timeout = 20\n')
    assert targets.read(ROOT)["prod"]["connect_timeout"] == 20


def test_remove_takes_its_block_and_its_own_comment_only(declared):
    block = (
        "# production: careful\n[targets.prod]\n"
        'host = "deploy@prod"    # the bastion alias\ndest = "/srv/uqf"\nremote_user = "svc"\n\n'
    )
    assert uqs("target", "remove", "prod").exit_code == 0
    assert declared.read_text() == HAND_WRITTEN.replace(block, "")
    assert "  # uat is rebuilt weekly\n[targets.uat]" in declared.read_text()


def test_unset_set_and_rename_keep_the_rest_byte_for_byte(declared):
    uat = HAND_WRITTEN[HAND_WRITTEN.index("  # uat") : HAND_WRITTEN.index("[environments")]
    assert uqs("target", "unset", "uat", "labels.tier").exit_code == 0
    assert uqs("target", "set", "uat", "labels.owner=desk", "live=true").exit_code == 0
    assert uqs("target", "rename", "uat", "staging").exit_code == 0
    after = declared.read_text()
    _outside(HAND_WRITTEN, after, uat)
    assert targets.read(ROOT)["staging"] == {
        "profile": "essential", "dest": "/srv/uat", "host": "deploy@uat",
        "labels": {"owner": "desk"}, "live": True,
    }  # fmt: skip
    assert "uat" not in targets.read(ROOT)


def test_add_appends_a_block_and_a_remove_takes_it_away_again(declared):
    r = uqs("target", "add", "dr", "--ssh", "svc@dr", "--dest", "/srv/dr", "--label", "site=b")
    assert r.exit_code == 0, r.output
    assert declared.read_text().startswith(HAND_WRITTEN)
    assert '[targets.dr]\nhost = "svc@dr"\ndest = "/srv/dr"\nlabels = {site = "b"}\n' in (
        declared.read_text()
    )
    assert uqs("target", "remove", "dr").exit_code == 0
    assert declared.read_text() == HAND_WRITTEN


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (["set", "prod", "hots=x"], r"line 12: target prod: unknown setting\(s\) hots"),
        (["set", "prod", "launch_mode=source"], "needs launch_python and launch_env_file"),
        (
            ["set", "prod", "launch_mode=source", "launch_python=/opt/py/bin/python3"],
            "needs launch_env_file",
        ),
        (["set", "prod", "launch_mode=venv"], "launch_mode 'venv' is not one of"),
        (["set", "prod", "accept_digest=x"], "an override is given on the command line"),
        (["set", "prod", "connect_timeout=soon"], "wrong type"),
        (["set", "prod", "environment=nowhere"], "environment 'nowhere' is not declared"),
        (["set", "prod", "dest=relative"], "dest 'relative' must be an absolute path"),
        (["add", "uat", "--ssh", "h", "--dest", "/d"], "target uat is already declared"),
        (["add", "Bad", "--ssh", "h", "--dest", "/d"], "target name 'Bad'"),
        (["rename", "prod", "uat"], "target uat is already declared"),
        (["remove", "nope"], "no target 'nope'.*declares: prod, uat"),
        (["unset", "prod", "jobs"], "target prod sets no jobs"),
    ],
)  # fmt: skip
def test_a_refused_change_leaves_the_file_byte_identical(declared, argv, said, capfd):
    r = uqs("target", *argv)
    assert r.exit_code == 1
    said_ = told(r, capfd)
    assert re.search(said, said_), said_
    assert declared.read_text() == HAND_WRITTEN
    assert _log(declared) == []


def test_a_change_killed_before_the_replace_leaves_the_original(declared, monkeypatch):
    def killed(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(target_changes.os, "replace", killed)
    with pytest.raises(KeyboardInterrupt):
        target_changes.set_keys(ROOT, "prod", {"dest": "/srv/new"}, "test")
    assert declared.read_text() == HAND_WRITTEN
    assert sorted(p.name for p in declared.parent.iterdir() if ".tmp" in p.name) == []
    assert _log(declared) == []


def test_the_write_replaces_the_file_in_one_rename(declared, monkeypatch):
    calls = []
    real = os.replace
    monkeypatch.setattr(
        target_changes.os, "replace", lambda a, b: calls.append((a, b)) or real(a, b)
    )
    target_changes.set_keys(ROOT, "prod", {"dest": "/srv/new"}, "test")
    ((src, dst),) = calls
    assert Path(dst) == declared and Path(src).parent == declared.parent


def test_a_dry_run_shows_the_change_and_writes_nothing(declared):
    r = uqs("target", "set", "prod", "dest=/srv/next", "--dry-run")
    assert r.exit_code == 0, r.output
    assert '+dest = "/srv/next"' in r.output and "nothing written" in r.output
    assert declared.read_text() == HAND_WRITTEN and _log(declared) == []


def test_reach_refuses_a_target_it_cannot_reach_and_saves_nothing(declared, ssh, capfd):
    r = uqs("target", "add", "gone", "--ssh", "unreachable", "--dest", "/srv/x", "--reach")
    assert r.exit_code == 1
    assert "not saved: could not reach target gone" in told(r, capfd)
    assert declared.read_text() == HAND_WRITTEN and _log(declared) == []


def test_reach_saves_a_target_that_answers(declared, ssh, tmp_path):
    release = tmp_path / "srv" / "releases" / "r1"
    release.mkdir(parents=True)
    (tmp_path / "srv" / "current").symlink_to(release)
    r = uqs("target", "add", "here", "--ssh", "h", "--dest", str(tmp_path / "srv"), "--reach")
    assert r.exit_code == 0, r.output
    assert "release r1" in r.output and "here" in targets.read(ROOT)


def test_two_changes_never_hold_the_lock_at_once(declared):
    with target_changes.locked(ROOT):
        with pytest.raises(DeployError, match="being changed by another uqs"):
            with target_changes.locked(ROOT, wait=0.1):
                pass


# ------------------------------------------------------------------ hand edits


def test_a_hand_edit_is_picked_up_and_logged_with_its_diff(declared):
    assert uqs("target", "set", "prod", "dest=/srv/a").exit_code == 0
    by_hand = declared.read_text().replace('dest = "/srv/uat"', 'dest = "/srv/uat2"')
    declared.write_text(by_hand)
    assert targets.read(ROOT)["uat"]["dest"] == "/srv/uat2", "read as it is"
    assert "edited outside uqs" in uqs("target", "log").output
    r = uqs("target", "set", "prod", "dest=/srv/b")
    assert r.exit_code == 0 and "edited outside uqs" in r.output
    entries = _log(declared)
    assert [e["kind"] for e in entries] == ["set", target_changelog.OUTSIDE, "set"]
    outside = entries[1]
    assert '-dest = "/srv/uat"\n+dest = "/srv/uat2"' in outside["diff"]
    assert [t["table"] for t in outside["tables"]] == ["targets.uat"]
    assert target_changelog.pending_outside_edit(ROOT) is None


def test_an_invalid_hand_edit_is_refused_on_read_naming_its_line(declared):
    declared.write_text(HAND_WRITTEN.replace('remote_user = "svc"', 'remote_usr = "svc"'))
    with pytest.raises(DeployError, match=r"deploy_targets.toml, line 11: target prod: unknown"):
        targets.read(ROOT)
    declared.write_text(HAND_WRITTEN.replace('dest = "/srv/uat"', "dest = /srv/uat"))
    with pytest.raises(DeployError, match=r"not TOML .*line 15"):
        targets.read(ROOT)


def _editor(tmp_path: Path, monkeypatch, *steps: str) -> Path:
    """A fake $EDITOR: its Nth run applies the Nth sed program to the file."""
    script = tmp_path / "editor.sh"
    count = tmp_path / "editor.count"
    count.write_text("0")
    cases = "".join(f'  {i}) sed -i.bak {json.dumps(s)} "$1" ;;\n' for i, s in enumerate(steps))
    script.write_text(
        "#!/bin/bash\n"
        f'n=$(cat {count}); echo $((n + 1)) > {count}; cp "$1" {tmp_path}/seen.$n\n'
        f"case $n in\n{cases}esac\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(script))
    monkeypatch.delenv("VISUAL", raising=False)
    return count


def test_edit_saves_a_valid_copy(declared, tmp_path, monkeypatch):
    _editor(tmp_path, monkeypatch, "s|/srv/uat|/srv/uat9|")
    r = uqs("target", "edit")
    assert r.exit_code == 0, r.output
    assert declared.read_text() == HAND_WRITTEN.replace("/srv/uat", "/srv/uat9")
    assert _log(declared)[-1]["kind"] == "edit"


def test_edit_refuses_an_invalid_copy_and_reopens_it_with_the_reason(
    declared, tmp_path, monkeypatch
):
    count = _editor(tmp_path, monkeypatch, "s|remote_user|remote_usr|", "s|remote_usr|remote_user|")
    r = uqs("target", "edit", input="y\n")
    assert r.exit_code == 0, r.output
    assert count.read_text().strip() == "2"
    reopened = (tmp_path / "seen.1").read_text()
    assert reopened.startswith("# uqs: ") and "unknown setting(s) remote_usr" in reopened
    assert declared.read_text() == HAND_WRITTEN, "fixed back to what it was: nothing changed"


def test_edit_discards_an_invalid_copy_on_request(declared, tmp_path, monkeypatch, capfd):
    _editor(tmp_path, monkeypatch, "s|remote_user|remote_usr|")
    r = uqs("target", "edit", input="n\n")
    assert r.exit_code == 1 and "edit discarded" in told(r, capfd)
    assert declared.read_text() == HAND_WRITTEN and _log(declared) == []


def test_edit_refuses_to_overwrite_a_change_made_meanwhile(declared, tmp_path, monkeypatch, capfd):
    script = tmp_path / "editor.sh"
    script.write_text(
        f'#!/bin/bash\nsed -i.bak "s|/srv/uat|/srv/x|" "$1"\necho "# meanwhile" >> {declared}\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("EDITOR", str(script))
    r = uqs("target", "edit")
    assert r.exit_code == 1 and "changed while it was being edited" in told(r, capfd)
    assert declared.read_text() == HAND_WRITTEN + "# meanwhile\n"


# ------------------------------------------------------------------- change log


def test_every_change_appends_one_entry_and_log_lists_them(declared):
    steps = [
        ("add", "dr", "--ssh", "svc@dr", "--dest", "/srv/dr"),
        ("set", "dr", "profile=full"),
        ("unset", "dr", "profile"),
        ("rename", "dr", "dr2"),
        ("remove", "dr2"),
    ]
    for n, argv in enumerate(steps, 1):
        assert uqs("target", *argv).exit_code == 0
        assert len(_log(declared)) == n
    entry = _log(declared)[1]
    assert entry["command"] == "uqs target set dr profile=full"
    assert {"at", "user", "host", "hash", "file"} <= set(entry)
    assert entry["hash"] == target_changelog.digest(entry["file"])
    listed = json.loads(uqs("target", "log", "--json").output)
    assert [c["kind"] for c in listed["changes"]] == ["remove", "rename", "unset", "set", "add"]
    assert listed["changes"][3]["tables"][0]["keys"] == ["+profile"]
    assert listed["edited_outside"] is False
    only = json.loads(uqs("target", "log", "dr", "--json").output)["changes"]
    assert [c["number"] for c in only] == [4, 3, 2, 1]
    table = CliRunner().invoke(app, ["target", "log"], terminal_width=200).output
    assert "+profile" in table and "unset" in table


def test_revert_last_restores_the_previous_block_exactly(declared):
    assert uqs("target", "set", "prod", "dest=/srv/new", "remote_user=efx").exit_code == 0
    r = uqs("target", "revert", "last")
    assert r.exit_code == 0, r.output
    assert declared.read_text() == HAND_WRITTEN
    assert [e["kind"] for e in _log(declared)] == ["set", "revert"]


def test_revert_brings_back_a_removed_target_with_its_comment(declared):
    assert uqs("target", "remove", "prod").exit_code == 0
    assert uqs("target", "revert", "1").exit_code == 0
    assert targets.read(ROOT)["prod"]["remote_user"] == "svc"
    assert "# production: careful\n[targets.prod]\n" in declared.read_text()


def test_revert_is_refused_when_the_table_changed_since(declared, capfd):
    assert uqs("target", "set", "prod", "dest=/srv/a").exit_code == 0
    assert uqs("target", "set", "prod", "dest=/srv/b").exit_code == 0
    r = uqs("target", "revert", "1")
    assert r.exit_code == 1 and "targets.prod has changed since change 1" in told(r, capfd)
    assert targets.read(ROOT)["prod"]["dest"] == "/srv/b"


def test_rename_keeps_the_deployments_recorded_under_the_old_name(declared):
    history._write(ROOT, [{"target": "prod", "host": "deploy@prod", "status": "deployed"}])
    assert uqs("target", "rename", "prod", "production").exit_code == 0
    assert [e["target"] for e in history.read(ROOT)] == ["production"]


# ------------------------------------------------------------------------ pushes


def _cfg(host: str, dest: str):
    from types import SimpleNamespace

    return SimpleNamespace(host=host, dest=dest, remote_user=None, target=None, runtime="uqf")


def _deployed():
    from types import SimpleNamespace

    return SimpleNamespace(as_dict=lambda: {"release": "r1", "status": "deployed"})


def test_a_push_registers_through_the_transaction_and_prints_the_block(declared):
    said: list[str] = []
    history.record(ROOT, _cfg("svc@new.example", "/srv/new"), _deployed(), say=said.append)
    (entry,) = _log(declared)
    assert entry["kind"] == target_changelog.REGISTERED and entry["target"] == "new-example-new"
    assert declared.read_text().startswith(HAND_WRITTEN), "appended, the rest untouched"
    (message,) = said
    assert '[targets.new-example-new]\nhost = "svc@new.example"' in message
    assert "change 1" in message and "--no-register" in message


def test_a_push_with_no_register_registers_nothing(declared):
    history.record(ROOT, _cfg("svc@new.example", "/srv/new"), _deployed(), register=False)
    assert declared.read_text() == HAND_WRITTEN and _log(declared) == []
    (entry,) = history.read(ROOT)
    assert entry["target"] is None and entry["status"] == "deployed"


def test_push_takes_no_register(declared, monkeypatch):
    from uqs.cli import deploy as cli_deploy
    from uqs.deploy import driver

    def deploy(cfg, remote, *, on_report=None, **_k):
        assert on_report is not None
        on_report(_deployed())
        return 0

    monkeypatch.setattr(cli_deploy, "repo_root", lambda: ROOT)
    monkeypatch.setattr(driver, "deploy", deploy)
    argv = ["deploy", "push", "x.tar.gz", "--ssh", "svc@n.example", "--dest", "/srv/n"]
    r = uqs(*argv, "--profile", "essential", "--no-register")
    assert r.exit_code == 0, r.output
    assert declared.read_text() == HAND_WRITTEN
    r = uqs(*argv, "--profile", "essential")
    assert r.exit_code == 0 and "registered target n-example-n" in r.output
    assert "n-example-n" in targets.read(ROOT)


# ------------------------------------------------------------- the text itself


def test_an_inline_target_is_changed_through_tomlkit(tmp_path):
    text = '[targets]\na = { host = "h", dest = "/d" }  # inline\nb = { host = "g", dest = "/e" }\n'
    key = ("targets", "a")
    assert target_file.block(text, key) is None
    removed = target_file.remove(text, key)
    assert removed == '[targets]\nb = { host = "g", dest = "/e" }\n'
    renamed = target_file.rename(text, key, ("targets", "c"))
    assert set(tomllib.loads(renamed)["targets"]) == {"b", "c"}


def test_a_sub_table_moves_with_its_target():
    text = '[targets.a]\nhost = "h"\n\n[targets.a.labels]\nx = "1"\n\n[targets.b]\nhost = "g"\n'
    assert target_file.remove(text, ("targets", "a")) == '[targets.b]\nhost = "g"\n'
    assert target_file.rename(text, ("targets", "a"), ("targets", "z")).startswith(
        '[targets.z]\nhost = "h"\n\n[targets.z.labels]\n'
    )


@pytest.mark.parametrize(
    ("given", "value"),
    [("20", 20), ("true", True), ('["a", "b"]', ["a", "b"]), ("deploy@prod", "deploy@prod"),
     ('"20"', "20"), ("/srv/uqf", "/srv/uqf")],
)  # fmt: skip
def test_a_value_is_toml_when_it_is_one_else_text(given, value):
    assert target_file.parse_value(given) == value


def test_edit_mends_a_file_a_hand_edit_left_unreadable(declared, tmp_path, monkeypatch, capfd):
    broken = HAND_WRITTEN.replace('dest = "/srv/uat"', "dest = /srv/uat")
    declared.write_text(broken)
    r = uqs("target", "set", "prod", "dest=/srv/x")
    assert r.exit_code == 1 and "mend it with `uqs target edit`" in told(r, capfd)
    _editor(tmp_path, monkeypatch, 's|dest = /srv/uat|dest = "/srv/uat"|')
    r = uqs("target", "edit")
    assert r.exit_code == 0, r.output
    assert declared.read_text() == HAND_WRITTEN
