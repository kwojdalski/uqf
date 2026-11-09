"""`uqs --target T data compress --report --hdb DIR`: a target with q and an HDB
but no uqf (#2219).

The remote side runs for real: a stand-in `ssh` on PATH runs the command the
real ssh would hand the target's login shell, with the script on stdin, here.
The target declares a `dest` with no release under it - no `current`, no .venv,
no deploy.env - so a report that needed the release could not run. The HDB is
test_data_compress_report.py's fixture, built by q; the target's q is this
machine's (QCMD in the account's environment, or the target's own q_cmd).

What each acceptance criterion is held to:
  1. no uqf there        test_a_target_without_uqf_reports_on_a_directory_there
  2. local == remote     test_the_remote_report_is_the_local_report
  3. nothing written     both, by a digest of every file before and after
  4. actionable errors   test_failures_say_what_to_fix_and_leave_nothing_behind
  5. sort unchecked      test_without_a_sort_csv_sorting_is_not_checked_*
  6. managed unchanged   test_without_hdb_the_release_runs_it_as_before, test_apply_*
  7. routing, quoting, inspection-only, JSON, cleanup - every case here
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from test_data_compress_report import BUILD_Q, _digest, run_uqs

from uqs.cli import hdb_scope
from uqs.cli import target as target_cli
from uqs.deploy import history, targets
from uqs.paths import UqsError, UqsUsageError
from uqs.stack.hdb_compression import SshHost

#: The stand-in ssh: logs its argv, refuses the host `unreachable` as ssh does
#: (255), and runs the remote command here with stdin passed through.
FAKE_SSH = """#!/bin/bash
echo "$*" >> "$FAKE_SSH_LOG"
cmd="${@: -1}"; host="${@: -2:1}"  # not ${args[-1]}: macOS's bash 3.2 has no negative index
[ "$host" = unreachable ] && { echo "ssh: connect to host unreachable: refused" >&2; exit 255; }
exec bash -c "$cmd"
"""

SORT_CSV = Path(__file__).resolve().parents[3] / "lib" / "torq" / "config" / "sort.csv"


@pytest.fixture
def hdb(q_binary, monkeypatch, tmp_path) -> Path:
    """test_data_compress_report.py's HDB, built by q; QCMD is the target's q too."""
    q, env = q_binary
    for key in ("QHOME", "QLIC"):
        if env.get(key):
            monkeypatch.setenv(key, env[key])
    monkeypatch.setenv("QCMD", q)
    root = tmp_path / "hdb"
    (tmp_path / "build.q").write_text(BUILD_Q)
    subprocess.run([q, str(tmp_path / "build.q"), "-q"], env={**env, "HDB": str(root)},
                   stdin=subprocess.DEVNULL, capture_output=True, check=True,
                   timeout=60)  # fmt: skip
    return root


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Targets on a stand-in ssh: `tp` (a TorQ server, no uqf), `prod` (the
    same, inspection-only), `noq` (its q_cmd names no q) and `down`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(FAKE_SSH)
    (bin_dir / "ssh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_SSH_LOG", str(tmp_path / "ssh.log"))
    # the target account's: its temporary directories land here, to be counted
    (tmp_path / "remote-tmp").mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "remote-tmp"))
    # a login shell reads the account's profile: an empty home has none
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    nothing = tmp_path / "opt" / "torq"  # no release: no current, no .venv, no deploy.env
    nothing.mkdir(parents=True)
    declared = tmp_path / "deploy_targets.toml"
    declared.write_text(
        f'[targets.tp]\nssh = "svc@tp.example"\ndest = "{nothing}"\n\n'
        f'[targets.prod]\nssh = "prod.example"\ndest = "{nothing}"\ninspection = true\n\n'
        f'[targets.noq]\nssh = "noq.example"\ndest = "{nothing}"\n'
        'q_cmd = "/nonexistent/q/bin/q"\n\n'
        '[targets.down]\nssh = "unreachable"\ndest = "/opt/uqf"\n'
    )
    monkeypatch.setenv(targets.DECLARATION_ENV, str(declared))
    monkeypatch.delenv(history.HISTORY_ENV, raising=False)
    monkeypatch.setenv("COLUMNS", "300")
    return tmp_path


@pytest.fixture
def logged():
    """What a refusal logged: loguru writes to the stderr it had at import."""
    from loguru import logger

    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="ERROR", format="{message}")
    yield lines
    logger.remove(sink)


def on_target(name: str, *args: str) -> int:
    """`uqs --target name args...`, as the entry point runs it."""
    return target_cli.forward(name, False, list(args), interactive=False)


def ssh_calls(server: Path) -> list[str]:
    log = server / "ssh.log"
    return log.read_text().splitlines() if log.exists() else []


def leftovers(server: Path) -> list[str]:
    return sorted(p.name for p in (server / "remote-tmp").iterdir())


def comparable(doc: dict) -> dict:
    return {k: doc[k] for k in ("columns", "largest_uncompressed", "unsorted", "sort_checked")}


def test_a_target_without_uqf_reports_on_a_directory_there(server, hdb, capsys):
    before = _digest(hdb)
    assert on_target("tp", "data", "compress", "--report", "--json", "--hdb", str(hdb)) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["target"] == "tp" and doc["hdb"] == str(hdb) and doc["present"]
    cols = {c["column"]: c for c in doc["columns"]}
    assert (cols["px"]["files"], cols["px"]["compressed"]) == (4, 2)
    # one ssh session, the script on stdin: no remote uqs, no release, no deploy.env
    [call] = ssh_calls(server)
    assert "BatchMode=yes" in call and call.endswith("svc@tp.example bash -l -s")
    assert not (server / "opt" / "torq" / "current").exists()
    assert _digest(hdb) == before, "the report wrote nothing under the HDB"
    assert leftovers(server) == [], "the helper's temporary directory is gone"


def test_the_remote_report_is_the_local_report(server, hdb, capsys):
    before = _digest(hdb)
    sort = ("--sort-csv", str(SORT_CSV))
    assert on_target("tp", "data", "compress", "--report", "--json", "--hdb", str(hdb), *sort) == 0
    remote = json.loads(capsys.readouterr().out)
    r = run_uqs("data", "compress", "--report", "--json", "--hdb", str(hdb), *sort)
    assert r.exit_code == 0, r.output
    local = json.loads(r.stdout)
    assert remote["sort_checked"] is True and remote["unsorted"], "sorting was judged"
    assert comparable(remote) == comparable(local)
    # and the runtime's own sort.csv, which the local --hdb report uses, is that file
    r = run_uqs("data", "compress", "--report", "--json", "--hdb", str(hdb))
    assert comparable(json.loads(r.stdout)) == comparable(local)
    assert _digest(hdb) == before


def test_without_a_sort_csv_sorting_is_not_checked_in_json(server, hdb, capsys):
    assert on_target("tp", "data", "compress", "--report", "--json", "--hdb", str(hdb)) == 0
    doc = json.loads(capsys.readouterr().out)
    # not [], which would read as "every partition sorted"
    assert doc["sort_checked"] is False and doc["unsorted"] is None and doc["sort_csv"] is None


def test_without_a_sort_csv_sorting_is_not_checked_in_text(server, hdb, capsys):
    assert on_target("tp", "data", "compress", "--report", "--hdb", str(hdb)) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "sorting not checked" in out
    assert "every partition is in its sort.csv order" not in out
    assert "substantial read I/O on target tp" in out
    assert f"compression by column - {hdb} on target tp" in out


def test_a_path_is_quoted_and_never_run_by_a_shell(server, hdb, capsys):
    odd = server / "a dir 'quoted' $(touch PWNED) ; x"
    shutil.copytree(hdb, odd)
    assert on_target("tp", "data", "compress", "--report", "--json", "--hdb", str(odd)) == 0
    assert json.loads(capsys.readouterr().out)["hdb"] == str(odd)
    assert not list(server.rglob("PWNED")) and not list(Path.cwd().glob("PWNED"))


def test_a_target_path_is_not_checked_on_this_machine(server, monkeypatch):
    token = hdb_scope.ON_TARGET.set("tp")
    try:
        handle = hdb_scope.resolve(None, "/only/on/the/target/hdb")
    finally:
        hdb_scope.ON_TARGET.reset(token)
    assert (handle.kind, handle.hdb) == ("target", "/only/on/the/target/hdb")
    assert handle.target is not None and handle.target.host == "svc@tp.example"
    assert ssh_calls(server) == [], "resolving reaches nothing"


def test_an_inspection_only_target_still_reports(server, hdb, capsys):
    before = _digest(hdb)
    assert on_target("prod", "data", "compress", "--report", "--json", "--hdb", str(hdb)) == 0
    assert json.loads(capsys.readouterr().out)["target"] == "prod"
    assert _digest(hdb) == before


@pytest.mark.parametrize("target", ["tp", "prod"])
def test_apply_on_a_directory_on_a_target_is_refused_before_ssh(server, tmp_path, target):
    with pytest.raises(UqsUsageError, match="`--apply` is not run on a directory --hdb names"):
        on_target(target, "data", "compress", "--apply", "--yes", "--hdb", str(tmp_path))
    assert ssh_calls(server) == []


def test_without_hdb_the_release_runs_it_as_before(server):
    """No --hdb: the release's own uqs, by the release's launch - refused here,
    as before, for want of one, and no helper is tried instead."""
    with pytest.raises(UqsError, match="has no deployed release"):
        on_target("tp", "data", "compress", "--report")
    [call] = ssh_calls(server)
    assert "bash -l -s" not in call and "current" in call


def test_another_hdb_command_still_refuses_hdb_under_target(server, tmp_path):
    with pytest.raises(UqsUsageError, match="give one of --target, --attachment and --hdb"):
        on_target("tp", "data", "alter", "trade", "--hdb", str(tmp_path))
    assert ssh_calls(server) == []


def _unreadable(hdb: Path) -> str:
    if os.geteuid() == 0:
        pytest.skip("root reads every file")
    f = hdb / "2026.01.03" / "trade" / "px"
    f.chmod(0)
    return str(f)


@pytest.mark.parametrize(
    ("target", "where", "expected"),
    [
        ("tp", "missing", "no directory"),
        ("tp", "unreadable", "cannot read"),  # the script's words: PeachQ's error is 'io
        ("noq", "hdb", "no q for"),
        ("down", "hdb", "cannot reach target down (unreachable) over ssh"),
    ],
)
def test_failures_say_what_to_fix_and_leave_nothing_behind(
    server, hdb, logged, capsys, target, where, expected
):
    path = str(server / "no-such-hdb") if where == "missing" else str(hdb)
    named = _unreadable(hdb) if where == "unreadable" else ""
    try:
        code = on_target(target, "data", "compress", "--report", "--hdb", path)
    finally:
        (hdb / "2026.01.03" / "trade" / "px").chmod(0o644)
    said = " ".join("".join(logged).split())
    assert code == 1 and expected in said, said
    assert named in said, "the file q could not read is named"
    assert leftovers(server) == [], "the helper's temporary directory is removed on failure"


def test_a_refusal_under_json_is_a_json_refusal(server, logged, capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["uqs", "--json"])
    missing = str(server / "no-such-hdb")
    assert on_target("tp", "data", "compress", "--report", "--json", "--hdb", missing) == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False and "no directory" in doc["error"]


def test_the_target_account_and_its_q_are_the_declared_ones():
    ssh = SshHost("t", "h.example", "svc", 7, "/opt/kx/q", "/opt/kx")
    assert ssh.argv() == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=7", "h.example",
        "sudo -n -iu svc bash -s",
    ]  # fmt: skip
    assert SshHost("t", "h").argv()[-1] == "bash -l -s"
