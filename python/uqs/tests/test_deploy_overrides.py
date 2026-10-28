"""Named, recorded policy overrides for `uqs deploy push` (#1557), with no server.

Three requirements, and an override for each one: expected_sha256 with
--override accept-digest, q_version with --override accept-q-version, and a .sha256 beside a
local artifact with --override allow-unpinned. Each override needs a reason. Each is
refused when there is nothing for it to relax. Each relaxes its own requirement
and nothing else.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from test_deploy import (
    _HEALTHY,
    SERVER,
    FakeRemote,
    _args,
    _artifact,
    _done,
    _verified,
    parse_args,
)

from uqs.deploy import artifact, driver, history, targets
from uqs.deploy.config import DeployError

OTHER = "ab" * 32


def _digest(path: Path) -> str:
    return Path(f"{path}.sha256").read_text().split()[0]


def _unpinned(tmp_path: Path) -> Path:
    path = _artifact(tmp_path)
    Path(f"{path}.sha256").unlink()
    return path


def _deploy(path: Path, *extra: str, q: str = "5.0", rules: dict | None = None):
    remote = FakeRemote(
        {
            **_HEALTHY,
            "uv python find": _done(SERVER + f"qversion={q}\ndata=present\n"),
            "uqs deploy verify --profile": _verified(True),
            **(rules or {}),
        }
    )
    out = io.StringIO()
    code = driver.deploy(parse_args(_args(path, *extra)), remote, out=out)
    return code, remote, out.getvalue()


def _report(out: str) -> dict:
    return json.loads(out)


# --- each override needs a reason, and something to relax ----------------------


@pytest.mark.parametrize(
    ("name", "needs"),
    [
        ("accept-digest", ["--expected-sha256", OTHER]),
        ("accept-q-version", ["--q-version", ">=4.1"]),
        ("allow-unpinned", []),
    ],
)
def test_each_override_needs_a_reason(name, needs):
    with pytest.raises(DeployError, match=f"--override {name} needs --reason"):
        parse_args(_args(Path("a.tar.gz"), *needs, "--override", name, "--reason", "  "))
    cfg = parse_args(_args(Path("a.tar.gz"), *needs, "--override", name, "--reason", " because "))
    assert getattr(cfg, name.replace("-", "_")) == "because"


def test_the_old_spelling_still_gives_the_override_and_its_reason():
    """`--accept-digest REASON` is `--override accept-digest --reason REASON`
    for one release (#1732)."""
    cfg = parse_args(_args(Path("a.tar.gz"), "--expected-sha256", OTHER, "--accept-digest", "ok"))
    assert cfg.accept_digest == "ok"


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (
            ["--override", "accept-digest", "--reason", "r"],
            r"--override accept-digest relaxes expected_sha256 \(or torq_runtime",
        ),
        (
            ["--override", "accept-q-version", "--reason", "r"],
            "--override accept-q-version relaxes q_version, and this",
        ),
        (
            ["--expected-sha256", OTHER, "--override", "allow-unpinned", "--reason", "r"],
            "already pins the artifact",
        ),
    ],
)
def test_an_override_with_nothing_to_relax_is_refused(args, said):
    with pytest.raises(DeployError, match=said):
        parse_args(_args(Path("a.tar.gz"), *args))


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (["--expected-sha256", "abc"], "not a sha256"),
        (["--q-version", "4.x"], "neither a minimum"),
        (["--q-version", "<4.1"], "neither a minimum"),
    ],
)
def test_a_malformed_requirement_is_refused(args, said):
    with pytest.raises(DeployError, match=said):
        parse_args(_args(Path("a.tar.gz"), *args))


@pytest.mark.parametrize(
    ("requirement", "version", "met"),
    [
        (">=4.1", "4.1", True),
        (">=4.1", "4.10", True),
        (">=4.1", "5.0", True),
        (">=4.1", "4.0", False),
        (">=4.1", "", False),
        ("4.1", "4.1", True),
        ("4.1", "4.1.0", True),
        ("4.1", "4.2", False),
    ],
)
def test_a_q_requirement_is_a_minimum_or_an_exact_version(requirement, version, met):
    assert artifact.meets(requirement, version) is met


def test_a_target_file_cannot_set_an_override(tmp_path, monkeypatch):
    path = tmp_path / "deploy_targets.toml"
    path.write_text(
        '[targets.a]\nhost = "h"\ndest = "/opt/uqf"\nprofile = "essential"\n'
        f'expected_sha256 = "{OTHER}"\naccept_digest = "it is fine"\n'
    )
    monkeypatch.setenv(targets.DECLARATION_ENV, str(path))
    with pytest.raises(DeployError, match="target a: accept_digest - an override"):
        targets.configs("a", "x", {}, False, tmp_path)


def test_a_target_sets_the_requirements_and_the_flag_relaxes_one(tmp_path, monkeypatch):
    path = tmp_path / "deploy_targets.toml"
    path.write_text(
        '[targets.a]\nhost = "h"\ndest = "/opt/uqf"\nprofile = "essential"\n'
        f'expected_sha256 = "{OTHER}"\nq_version = ">=4.1"\n'
    )
    monkeypatch.setenv(targets.DECLARATION_ENV, str(path))
    (cfg,) = targets.configs("a", "x", {"accept_digest": "rebuilt"}, False, tmp_path)
    assert (cfg.expected_sha256, cfg.q_version, cfg.accept_digest) == (OTHER, ">=4.1", "rebuilt")
    assert cfg.accept_q_version is None
    assert cfg.sources["accept_digest"] == "--override accept-digest"


# --- --override accept-digest ---------------------------------------------------------------


def test_a_digest_other_than_expected_is_refused_before_the_server(tmp_path):
    remote = FakeRemote()
    path = _artifact(tmp_path)
    with pytest.raises(
        DeployError, match="not the expected_sha256.*--override accept-digest"
    ) as err:
        driver.deploy(parse_args(_args(path, "--expected-sha256", OTHER)), remote)
    assert err.value.stage == "artifact" and remote.scripts == []


def test_an_accepted_digest_records_the_actual_one_and_the_server_checks_it(tmp_path):
    path = _artifact(tmp_path)
    actual = _digest(path)
    code, remote, out = _deploy(
        path, "--expected-sha256", OTHER, "--override", "accept-digest", "--reason", "rebuilt"
    )
    report = _report(out)
    assert code == 0 and report["status"] == "deployed", report
    (e,) = report["exceptions"]
    assert e == {
        "override": "accept-digest",
        "reason": "rebuilt",
        "requirement": f"sha256 {OTHER}",
        "observed": f"sha256 {actual}",
        "applied": True,
    }
    # the server compares what arrived with the ACTUAL digest, never the expected one
    (check,) = [s for s in remote.stage("transfer") if "checksum mismatch" in s]
    assert f'"$got" != {actual}' in check and OTHER not in check


def test_a_digest_that_matches_records_the_override_as_not_needed(tmp_path):
    path = _artifact(tmp_path)
    code, _, out = _deploy(
        path,
        "--expected-sha256",
        _digest(path),
        "--override",
        "accept-digest",
        "--reason",
        "just in case",
    )
    (e,) = _report(out)["exceptions"]
    assert code == 0 and e["override"] == "accept-digest" and e["applied"] is False


def test_accepting_a_digest_never_excuses_the_sha256_beside_it(tmp_path):
    path = _artifact(tmp_path)
    Path(f"{path}.sha256").write_text("0" * 64 + f"  {path.name}\n")
    with pytest.raises(DeployError, match="does not match"):
        _deploy(
            path, "--expected-sha256", OTHER, "--override", "accept-digest", "--reason", "rebuilt"
        )


# --- --override allow-unpinned ----------------------------------------------------------------


def test_a_local_artifact_with_no_sha256_beside_it_is_refused(tmp_path):
    remote = FakeRemote()
    with pytest.raises(
        DeployError, match="no .*sha256 beside it.*--override allow-unpinned"
    ) as err:
        driver.deploy(parse_args(_args(_unpinned(tmp_path))), remote)
    assert err.value.stage == "artifact" and remote.scripts == []


def test_an_unpinned_artifact_deploys_with_allow_unpinned_and_its_digest_is_recorded(tmp_path):
    path = _unpinned(tmp_path)
    actual = artifact.read_artifact(path).sha256
    code, remote, out = _deploy(
        path, "--override", "allow-unpinned", "--reason", "a development build"
    )
    (e,) = _report(out)["exceptions"]
    assert code == 0 and (e["override"], e["applied"]) == ("allow-unpinned", True)
    assert actual in e["observed"]
    assert any(f'"$got" != {actual}' in s for s in remote.stage("transfer"))


def test_an_expected_sha256_pins_an_artifact_with_no_sha256_beside_it(tmp_path):
    path = _unpinned(tmp_path)
    actual = artifact.read_artifact(path).sha256
    code, _, out = _deploy(path, "--expected-sha256", actual)
    assert code == 0 and _report(out)["exceptions"] == []


# --- --override accept-q-version --------------------------------------------------------------


def test_a_q_below_q_version_is_refused_in_preflight(tmp_path):
    with pytest.raises(DeployError, match="q is 5.0, and q_version requires >=5.1") as err:
        _deploy(_artifact(tmp_path), "--q-version", ">=5.1")
    assert err.value.stage == "preflight"


def test_an_accepted_q_version_records_the_version_found(tmp_path):
    code, _, out = _deploy(
        _artifact(tmp_path),
        "--q-version",
        ">=5.1",
        "--override",
        "accept-q-version",
        "--reason",
        "site runs 5.0",
    )
    report = _report(out)
    assert code == 0 and report["qversion"] == "5.0"
    (e,) = report["exceptions"]
    assert (e["override"], e["requirement"], e["observed"], e["applied"]) == (
        "accept-q-version",
        ">=5.1",
        "5.0",
        True,
    )


def test_the_q_version_is_recorded_with_no_override_too(tmp_path):
    code, _, out = _deploy(_artifact(tmp_path), "--q-version", "5.0")
    report = _report(out)
    assert code == 0 and report["qversion"] == "5.0" and report["exceptions"] == []


def test_the_overrides_are_independent(tmp_path):
    """Accepting the q version does not excuse the digest, nor the other way round."""
    path = _artifact(tmp_path)
    with pytest.raises(DeployError, match="not the expected_sha256"):
        _deploy(path, "--expected-sha256", OTHER, "--q-version", ">=5.1",
                "--override", "accept-q-version", "--reason", "site runs 5.0")  # fmt: skip
    with pytest.raises(DeployError, match="q_version requires >=5.1"):
        _deploy(path, "--expected-sha256", OTHER, "--q-version", ">=5.1",
                "--override", "accept-digest", "--reason", "rebuilt")  # fmt: skip


# --- what no override bypasses ------------------------------------------------------

EVERY_OVERRIDE = ("--q-version", ">=5.1", "--override", "accept-q-version", "--reason", "r")


def _repacked(path: Path, extra: str) -> Path:
    """`path`'s members, plus one named `extra`, with no .sha256 beside it."""
    out = path.with_name("bad.tar.gz")
    with tarfile.open(path) as src, tarfile.open(out, "w:gz") as dst:
        for m in src:
            f = src.extractfile(m)
            dst.addfile(m, io.BytesIO(f.read()) if f else None)
        info = tarfile.TarInfo(extra)
        info.size = 1
        dst.addfile(info, io.BytesIO(b"x"))
    return out


@pytest.mark.parametrize(
    ("extra", "said"),
    [("../escape", "not a relative path"), ("src/extra.q", "in the archive but not the manifest")],
)
@pytest.mark.parametrize("pin", ["unpinned", "accepted digest"])
def test_an_unsafe_member_is_refused_under_every_override(tmp_path, extra, said, pin):
    path = _repacked(_artifact(tmp_path), extra)
    pins = (
        ("--override", "allow-unpinned", "--reason", "r")
        if pin == "unpinned"
        else ("--expected-sha256", OTHER, "--override", "accept-digest", "--reason", "r")
    )
    remote = FakeRemote()
    with pytest.raises(DeployError, match=said):
        driver.deploy(parse_args(_args(path, *pins, *EVERY_OVERRIDE)), remote)
    assert remote.scripts == []


@pytest.mark.parametrize(
    ("facts", "said"),
    [
        ("os=Darwin\narch=x86_64\npython=3.14\n", "not linux"),
        ("os=Linux\narch=aarch64\npython=3.14\n", "built for x86_64"),
        ("os=Linux\narch=x86_64\npython=3.13\n", "wheels are for 3.14"),
    ],
)
def test_the_platform_is_checked_under_every_override(tmp_path, facts, said):
    remote = FakeRemote({"uv python find": _done(facts + "qversion=5.0\ndata=present\n")})
    args = _args(
        _unpinned(tmp_path), "--override", "allow-unpinned", "--reason", "r", *EVERY_OVERRIDE
    )
    with pytest.raises(DeployError, match=said):
        driver.deploy(parse_args(args), remote)


@pytest.mark.parametrize(
    ("rules", "stage"),
    [
        ({"deploy_smoke.q": _done("", rc=1)}, "smoke"),
        ({"uqs deploy verify --profile": _verified(False, "rdb1 did not answer")}, "verify"),
    ],
)
def test_smoke_and_verify_run_under_every_override(tmp_path, rules, stage):
    args = ("--override", "allow-unpinned", "--reason", "r", *EVERY_OVERRIDE)
    code, remote, out = _deploy(_unpinned(tmp_path), *args, rules=rules)
    report = _report(out)
    assert code == 1 and (report["status"], report["stage"]) == ("failed", stage)
    assert not remote.ran(".current.new"), "never activated"
    assert {e["override"] for e in report["exceptions"]} == {"allow-unpinned", "accept-q-version"}


# --- the plan and the history -------------------------------------------------------


def test_the_plan_opens_with_the_exceptions(tmp_path):
    path = _artifact(tmp_path)
    _, _, out = _deploy(
        path, "--dry-run", "--expected-sha256", OTHER, "--q-version", ">=5.1",
        "--override", "accept-digest", "--override", "accept-q-version",
        "--reason", "rebuilt; site runs 5.0",
    )  # fmt: skip
    lines = out.splitlines()
    assert lines[0] == "EXCEPTIONS - policy relaxed for this deployment:"
    block = "\n".join(lines[: lines.index("")])
    assert (
        f"--override accept-digest: sha256 {OTHER} required, sha256 {_digest(path)} found" in block
    )
    assert "server still checks the bytes" in block and "reason: rebuilt" in block
    assert "--override accept-q-version: >=5.1 required, 5.0 found; reason: rebuilt; site" in block


def test_a_plan_with_no_override_has_no_exceptions_block(tmp_path):
    _, _, out = _deploy(_artifact(tmp_path), "--dry-run")
    assert "EXCEPTIONS" not in out


def test_the_converted_release_override_is_in_the_plans_exceptions(tmp_path):
    path = _artifact(tmp_path, q="4.0")
    _, _, out = _deploy(
        path,
        "--dry-run",
        "--override",
        "accept-converted-release",
        "--reason",
        "smoke-tested",
        q="3.6",
    )
    assert out.startswith("EXCEPTIONS")
    assert "--override accept-converted-release: recorded in the release once its smoke test" in out


def test_the_history_records_the_exceptions(tmp_path, monkeypatch):
    monkeypatch.setenv(history.HISTORY_ENV, str(tmp_path / "h.json"))
    monkeypatch.setenv(targets.DECLARATION_ENV, str(tmp_path / "t.toml"))
    path = _unpinned(tmp_path)
    remote = FakeRemote({**_HEALTHY, "uv python find": _done(SERVER + "data=present\n"),
                         "uqs deploy verify --profile": _verified(True)})  # fmt: skip
    cfg = parse_args(_args(path, "--override", "allow-unpinned", "--reason", "dev build"))
    code = driver.deploy(
        cfg, remote, out=io.StringIO(), on_report=lambda r: history.record(tmp_path, cfg, r)
    )
    (entry,) = history.read(tmp_path)
    assert code == 0 and entry["status"] == "deployed"
    (e,) = entry["exceptions"]
    assert (e["override"], e["reason"], e["applied"]) == ("allow-unpinned", "dev build", True)


# --- the artifact's digest is recorded whether or not anything pinned it -------------


def test_the_report_records_the_artifacts_digest_with_no_pin_or_override(tmp_path):
    path = _artifact(tmp_path)
    code, remote, out = _deploy(path)
    report = _report(out)
    assert code == 0 and report["exceptions"] == []
    assert report["artifact"] == {"name": path.name, "sha256": _digest(path)}
    # the digest recorded is the one the server checked the bytes against
    assert any(f'"$got" != {_digest(path)}' in s for s in remote.stage("transfer"))


def test_the_history_records_the_artifacts_digest(tmp_path, monkeypatch):
    monkeypatch.setenv(history.HISTORY_ENV, str(tmp_path / "h.json"))
    monkeypatch.setenv(targets.DECLARATION_ENV, str(tmp_path / "t.toml"))
    path = _artifact(tmp_path)
    remote = FakeRemote({**_HEALTHY, "uv python find": _done(SERVER + "data=present\n"),
                         "uqs deploy verify --profile": _verified(True)})  # fmt: skip
    cfg = parse_args(_args(path))
    code = driver.deploy(
        cfg, remote, out=io.StringIO(), on_report=lambda r: history.record(tmp_path, cfg, r)
    )
    (entry,) = history.read(tmp_path)
    assert code == 0 and entry["artifact"] == {"name": path.name, "sha256": _digest(path)}
