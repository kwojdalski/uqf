"""Inspection-only targets: uqs refuses every change to them (#2032).

Each refusal is checked to happen BEFORE anything reaches the server: the
seams that would - ssh (control.run), a push's driver, a rollback, a prune,
the config helper - are replaced with ones that fail the test if called.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs.cli import app, target_confirm
from uqs.cli import target as target_cli
from uqs.deploy import control, history, inspection, targets
from uqs.deploy.config import DeployError

REGISTRY = """\
[environments.production]
inspection = true

[targets.prod]
ssh = "deploy@prod"
dest = "/srv/uqf"
profile = "essential"
environment = "production"

[targets.uat]
ssh = "deploy@uat"
dest = "/srv/uqf-uat"
profile = "essential"
"""


def _never(*_a, **_k):
    raise AssertionError("an inspection-only target was reached")


@pytest.fixture
def declared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "deploy_targets.toml"
    path.write_text(REGISTRY)
    monkeypatch.setenv(targets.DECLARATION_ENV, str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    return path


@pytest.fixture
def no_ssh(monkeypatch: pytest.MonkeyPatch) -> list:
    """control.run recording what reached it, and refusing prod outright."""
    ran: list = []

    def run(r, args, **_):
        if r.name == "prod":
            _never()
        ran.append((r.name, list(args)))
        return 0

    monkeypatch.setattr(control, "run", run)
    return ran


# --- the registry ----------------------------------------------------------------


def test_the_key_reaches_a_target_through_its_environment(declared):
    found = targets.read(declared.parent)
    assert inspection.is_on(found["prod"]) and not inspection.is_on(found["uat"])


@pytest.mark.parametrize("value", ['"false"', '"yes"', "1"])
def test_a_value_that_is_not_a_boolean_is_refused_naming_its_line(declared, value):
    declared.write_text(REGISTRY.replace("inspection = true", f"inspection = {value}"))
    with pytest.raises(DeployError, match=r"line 2: .*inspection must be true or false"):
        targets.read(declared.parent)


def test_target_list_shows_the_column_only_when_a_target_is_inspection_only(declared):
    rows = {r["target"]: r for r in history.target_rows(declared.parent)}
    assert (rows["prod"]["inspection"], rows["uat"]["inspection"]) == (True, False)
    declared.write_text(REGISTRY.replace("inspection = true", "inspection = false"))
    assert all("inspection" not in r for r in history.target_rows(declared.parent))


def test_a_server_is_matched_by_its_ssh_and_dest_as_well_as_its_name(declared):
    found = targets.read(declared.parent)
    assert inspection.matching(found, name="prod") == "prod"
    assert inspection.matching(found, ssh="deploy@prod", dest="/srv/uqf/") == "prod"
    assert inspection.matching(found, ssh="deploy@prod", dest="/srv/other") is None
    assert inspection.matching(found, name="uat", ssh="deploy@uat", dest="/srv/uqf-uat") is None


# --- the same server under another name or spelling (#2068, #2074) ---------------------


ALIASES = """
[targets.prod-rw]
ssh = "deploy@prod"
dest = "/srv/uqf"
profile = "essential"
"""


@pytest.mark.parametrize(
    ("host", "dest"),
    [
        ("deploy@prod", "/srv/uqf"),
        ("prod", "/srv/uqf"),  # #2074: the login dropped
        ("PROD", "/srv/uqf"),
        ("root@prod", "/srv/uqf"),
        ("deploy@prod.example.com", "/srv/uqf"),  # a bare name is its FQDN's first label
        ("ssh://deploy@prod:2222", "/srv/uqf"),
        ("deploy@prod.", "/srv/uqf"),
        ("deploy@prod", "/srv//uqf"),
        ("deploy@prod", "/srv/uqf/"),
        ("deploy@prod", "/srv/uqf/."),
        ("deploy@prod", "//srv/uqf"),
        ("deploy@prod", "/srv/x/../uqf"),
    ],
)
def test_the_same_server_spelled_another_way_is_matched(declared, host, dest):
    found = targets.read(declared.parent)
    assert inspection.matching(found, ssh=host, dest=dest) == "prod"


@pytest.mark.parametrize(
    ("host", "dest"),
    [
        ("deploy@prod", "/srv/uqf-other"),
        ("deploy@prod-b", "/srv/uqf"),
        ("deploy@production", "/srv/uqf"),
        ("deploy@uat", "/srv/uqf"),
        ("deploy@prod", "/srv"),
    ],
)
def test_an_unrelated_server_is_not_matched(declared, host, dest):
    found = targets.read(declared.parent)
    assert inspection.matching(found, ssh=host, dest=dest) is None


def test_qualified_names_and_addresses_match_only_themselves():
    on = {"prod": {"ssh": "deploy@prod.eu.example", "dest": "/srv/uqf", "inspection": True}}
    assert inspection.matching(on, ssh="prod.us.example", dest="/srv/uqf") is None
    assert inspection.matching(on, ssh="PROD.EU.EXAMPLE", dest="/srv/uqf") == "prod"
    assert inspection.matching(on, ssh="prod", dest="/srv/uqf") == "prod"
    ip = {"prod": {"ssh": "deploy@10.0.0.1", "dest": "/srv/uqf", "inspection": True}}
    assert inspection.matching(ip, ssh="10.0.0.2", dest="/srv/uqf") is None
    assert inspection.matching(ip, ssh="10", dest="/srv/uqf") is None
    assert inspection.matching(ip, ssh="root@10.0.0.1", dest="/srv/uqf") == "prod"


def test_another_target_at_the_same_server_is_matched_by_its_name(declared):
    """#2068: a second declared target at prod's ssh and dest is prod, given
    its name alone - what `uqs target config restore` passes."""
    declared.write_text(REGISTRY + ALIASES)
    found = targets.read(declared.parent)
    assert inspection.matching(found, name="prod-rw") == "prod"
    assert inspection.matching(found, name="uat") is None
    with pytest.raises(inspection.InspectionRefused) as refused:
        inspection.refuse(declared.parent, "x", name="prod-rw")
    said = "target prod is in inspection mode (target prod-rw is the same server as target prod)"
    assert said in str(refused.value)


# --- uqs --target -------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["start"],
        ["stop", "rdb1"],
        ["restart", "--all"],
        ["data", "check", "--apply"],  # confirms on the server: still a change
    ],
)
def test_a_change_forwarded_to_an_inspection_target_is_refused(declared, no_ssh, args):
    with pytest.raises(inspection.InspectionRefused, match="target prod is in inspection mode"):
        target_cli.forward("prod", True, args, interactive=False)


@pytest.mark.parametrize("args", [["status"], ["stop", "--dry-run"], ["data", "check"], ["--help"]])
def test_a_read_forwarded_to_an_inspection_target_still_runs(declared, monkeypatch, args):
    ran: list = []
    monkeypatch.setattr(control, "run", lambda r, a, **_: ran.append((r.name, a)) or 0)
    assert target_cli.forward("prod", False, args, interactive=False) == 0
    assert ran and ran[0][0] == "prod"


def test_another_target_takes_the_same_change(declared, no_ssh):
    assert target_cli.forward("uat", True, ["stop", "rdb1"], interactive=False) == 0
    assert no_ssh == [("uat", ["stop", "rdb1"])]


def test_yes_does_not_get_past_inspection_mode(declared, no_ssh):
    """--yes answers a question; inspection mode is not one."""
    with pytest.raises(inspection.InspectionRefused):
        target_cli.forward("prod", True, ["stop"], interactive=False)


def test_the_refusal_says_ssh_is_not_made_read_only(declared, no_ssh):
    with pytest.raises(inspection.InspectionRefused) as caught:
        target_cli.forward("prod", True, ["stop"], interactive=False)
    assert "does not make ssh or the server read-only" in str(caught.value)


def test_may_change_the_server_counts_an_applied_server_side_confirmation():
    assert not target_confirm.changes_the_server(["data", "check", "--apply"])
    assert target_confirm.may_change_the_server(["data", "check", "--apply"])
    assert not target_confirm.may_change_the_server(["data", "check"])
    assert not target_confirm.may_change_the_server(["status"])
    assert target_confirm.may_change_the_server(["no-such-command"])


def test_target_shell_is_refused(declared, no_ssh, log_text):
    result = CliRunner().invoke(app, ["target", "shell", "prod"])
    assert result.exit_code != 0
    assert "inspection mode" in log_text()


def test_a_change_forwarded_to_another_target_at_the_same_server_is_refused(declared, no_ssh):
    """#2068: `uqs --target prod-rw stop` reaches prod's release."""
    declared.write_text(REGISTRY + ALIASES)
    with pytest.raises(inspection.InspectionRefused, match="target prod is in inspection mode"):
        target_cli.forward("prod-rw", True, ["stop"], interactive=False)
    assert no_ssh == []


def test_target_shell_on_another_target_at_the_same_server_is_refused(declared, no_ssh, log_text):
    declared.write_text(REGISTRY + ALIASES)
    result = CliRunner().invoke(app, ["target", "shell", "prod-rw"])
    assert result.exit_code != 0
    assert "target prod is in inspection mode" in log_text()
    assert no_ssh == []


# --- deploy push, rollback, prune ---------------------------------------------------


@pytest.fixture
def no_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    from uqs.deploy import driver, prune, rollback

    monkeypatch.setattr(driver, "deploy", _never)
    monkeypatch.setattr(rollback, "rollback", _never)
    monkeypatch.setattr(prune, "prune", _never)


def _invoke(*argv: str):
    return CliRunner().invoke(app, list(argv))


def test_push_to_an_inspection_target_is_refused(declared, tmp_path, no_deploy, log_text):
    artifact = tmp_path / "uqf-release.tar.gz"
    artifact.write_bytes(b"")
    result = _invoke("deploy", "push", str(artifact), "prod")
    assert result.exit_code != 0
    assert "FAILED at inspection" in log_text()
    assert "target prod is in inspection mode" in log_text()


def test_a_list_with_one_inspection_target_pushes_to_none(declared, tmp_path, no_deploy, log_text):
    artifact = tmp_path / "uqf-release.tar.gz"
    artifact.write_bytes(b"")
    _invoke("deploy", "push", str(artifact), "uat", "prod")
    assert "FAILED at inspection" in log_text()


def test_push_to_the_same_server_by_ssh_and_dest_is_refused(
    declared, tmp_path, no_deploy, log_text
):
    artifact = tmp_path / "uqf-release.tar.gz"
    artifact.write_bytes(b"")
    _invoke(
        "deploy", "push", str(artifact), "--ssh", "deploy@prod", "--dest", "/srv/uqf",
        "--profile", "essential",
    )  # fmt: skip
    assert "FAILED at inspection" in log_text()
    assert "the server at deploy@prod:/srv/uqf is target prod" in log_text()


@pytest.mark.parametrize(
    "argv", [["deploy", "rollback", "prod"], ["deploy", "prune", "prod", "--keep", "2", "--yes"]]
)
def test_rollback_and_prune_of_an_inspection_target_are_refused(
    declared, no_deploy, argv, log_text
):
    result = _invoke(*argv)
    assert result.exit_code != 0
    assert "FAILED at inspection" in log_text()


def test_refuse_deploy_lets_a_dry_run_through(declared):
    inspection.refuse_deploy(
        declared.parent, "x", name="prod", ssh="deploy@prod", dest="/srv/uqf", dry_run=True
    )
    with pytest.raises(DeployError, match="inspection mode"):
        inspection.refuse_deploy(
            declared.parent, "x", name="prod", ssh="deploy@prod", dest="/srv/uqf", dry_run=False
        )


# --- target config restore ------------------------------------------------------------


def test_config_restore_on_an_inspection_target_writes_nothing(declared, monkeypatch, log_text):
    from uqf_frontend import remote_files

    asked: list[bool] = []

    def restore(r, name, push_id, *, dry_run, **_):
        asked.append(dry_run)
        if not dry_run:
            _never()
        return {"plan": [{"step": "write", "path": "a.csv"}]}

    monkeypatch.setattr(remote_files, "restore", restore)
    result = _invoke("target", "config", "restore", "prod", "20261104T145249000Z-0123abcd", "--yes")
    assert result.exit_code != 0
    assert "inspection mode" in log_text()
    assert asked == [True]  # the plan was read; nothing was written


def test_config_restore_on_a_target_at_an_inspection_server_writes_nothing(
    declared, monkeypatch, log_text
):
    """#2068: restore through a second target at prod's ssh and dest."""
    from uqf_frontend import remote_files

    declared.write_text(REGISTRY + ALIASES)
    asked: list[bool] = []

    def restore(r, name, push_id, *, dry_run, **_):
        asked.append(dry_run)
        if not dry_run:
            _never()
        return {"plan": [{"step": "write", "path": "a.csv"}]}

    monkeypatch.setattr(remote_files, "restore", restore)
    result = _invoke(
        "target", "config", "restore", "prod-rw", "20261104T145249000Z-0123abcd", "--yes"
    )
    assert result.exit_code != 0
    assert "target prod-rw is the same server as target prod" in log_text()
    assert asked == [True]


def test_push_to_the_same_server_spelled_another_way_is_refused(
    declared, tmp_path, no_deploy, log_text
):
    """#2074: --ssh prod for a target declared deploy@prod."""
    artifact = tmp_path / "uqf-release.tar.gz"
    artifact.write_bytes(b"")
    _invoke(
        "deploy", "push", str(artifact), "--ssh", "prod", "--dest", "/srv/uqf",
        "--profile", "essential",
    )  # fmt: skip
    assert "FAILED at inspection" in log_text()
    assert "the server at prod:/srv/uqf is target prod" in log_text()


def test_prune_of_the_same_server_spelled_another_way_is_refused(declared, no_deploy, log_text):
    result = _invoke(
        "deploy", "prune", "--ssh", "PROD", "--dest", "/srv/uqf/.", "--keep", "0", "--yes"
    )
    assert result.exit_code != 0
    assert "FAILED at inspection" in log_text()


# --- the gate: every command that changes a server asks inspection first ---------------

#: (module alias, function): each changes what a server holds. A CLI module that
#: calls one must also call inspection.refuse* - `target adopt --apply` did not,
#: and rearranged an inspection-only server reached under a new name (#2231).
SERVER_CHANGES = {
    ("adopt", "apply"),
    ("control", "run"),
    ("driver", "deploy"),
    ("prune", "prune"),
    ("remote_files", "restore"),
    ("rollback", "rollback"),
}
REFUSALS = {"refuse", "refuse_deploy", "refuse_push"}


def _calls(tree) -> set[tuple[str, str]]:
    import ast

    return {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }


def test_every_cli_module_that_changes_a_server_asks_inspection_first():
    import ast

    cli = Path(target_cli.__file__).parent
    changing: list[str] = []
    missing: list[str] = []
    for path in sorted(cli.glob("*.py")):
        calls = _calls(ast.parse(path.read_text()))
        if calls & SERVER_CHANGES:
            changing.append(path.name)
            if not {("inspection", r) for r in REFUSALS} & calls:
                missing.append(path.name)
    assert "target_adopt.py" in changing, "the gate no longer sees adopt.apply"
    assert not missing, f"change a server without inspection.refuse: {missing}"
