"""Accepting an attachment blocker, explicitly and narrowly (#1435).

An override accepts a RISK; it never establishes that something works. These
tests hold the rules that keep it honest: scoped to one code on one component,
only codes the central policy allows, a mandatory reason, visible as an accepted
risk, void when the facts it was accepted against change, and confirmed
separately from --yes.
"""

from __future__ import annotations

import ast
import json
import socket
from pathlib import Path

import pytest
from attach_support import FakeStack, FaultIpc, make_bundle
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import apply as applying
from uqs.attach import config, facts, overrides, plan, preflight, service, verify
from uqs.attach.manifest import Manifest
from uqs.attach.overrides import OverrideError
from uqs.cli import shared
from uqs.paths import repo_root

ROOT = repo_root()
TABLE = "piggybank_general_ledger"
SITE = "4.2.0"  # the site's TorQ: untested by this procedure, and not for us to add to the list


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return make_bundle(tmp_path / "piggybank")


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


def att_for(bundle: Path, **kw) -> config.Attachment:
    kw.setdefault("runtime_only", True)
    return service.create("pb-uat", "pb_general_ledger", bundle, 6000, ROOT, **kw)


def accept(att, fake, specs, reason="validated against the site's TorQ 4.2.0 build"):
    return service.accept(att, fake, ROOT, specs, reason, operator="alice")


ALL_FOUR = [f"TORQ_VERSION:{p}" for p in ("discovery1", "stp1", "rdb1", "wdb1")]


# ------------------------------------------------------------------- policy


def test_every_code_the_preflight_raises_has_a_policy():
    """A code added to the preflight without a decision about overriding it would
    be silently non-overridable, or worse, unreviewed: this keeps the table whole."""
    found: set[str] = set()
    for name in ("preflight.py", "preflight_components.py", "topology.py"):
        tree = ast.parse((ROOT / "python/uqs/src/uqs/attach" / name).read_text())
        found |= {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.isupper()
            and "_" in n.value + "_"
            and len(n.value) > 3
        }
    unknown = (
        found - set(overrides.POLICY) - overrides.NOT_BLOCKERS - {"GENERATED", "SUPPORTED_TORQ"}
    )
    assert not unknown, f"finding codes with no override policy: {sorted(unknown)}"
    assert not (set(overrides.POLICY) & overrides.NOT_BLOCKERS)


def test_the_policy_allows_exactly_the_four_assessable_risks():
    allowed = {c for c, p in overrides.POLICY.items() if p.overridable}
    assert allowed == {"TORQ_VERSION", "SORT_CONFIG", "PORT_CONFLICT", "PUBLISHER_RUNNING"}
    for code in allowed:
        p = overrides.POLICY[code]
        assert p.risk and p.action, f"{code} must name its failure risk and the operator action"


@pytest.mark.parametrize(
    "code",
    [
        "SCHEMA_INCOMPATIBLE",
        "NOT_PUBLISHED",
        "STACK_MISMATCH",
        "LOGGING_MODE",
        "PERSIST_UNAVAILABLE",
    ],
)
def test_proven_incompatibility_ambiguous_ownership_and_restart_needs_are_not_overridable(
    code, bundle
):
    with pytest.raises(OverrideError, match=f"{code} cannot be overridden"):
        overrides.create([f"{code}:stp1"], "because", [], "alice")


def test_persisting_is_not_overridden_the_alternative_is_runtime_only():
    assert "--runtime-only" in overrides.POLICY["PERSIST_UNAVAILABLE"].risk


# ------------------------------------------------------------ what is refused


def test_a_spec_must_be_code_colon_component_and_there_is_no_blanket_form():
    for bad in (
        "TORQ_VERSION",
        "TORQ_VERSION:",
        ":stp1",
        "all",
        "TORQ_VERSION:stp1:x",
        "TORQ_VERSION: stp1",
    ):
        with pytest.raises(OverrideError, match="CODE:COMPONENT"):
            overrides.parse_spec(bad)
    assert overrides.parse_spec("torq_version:stp1") == ("TORQ_VERSION", "stp1")


def test_an_override_without_a_reason_is_refused(bundle):
    fake = FakeStack(6000, torq=SITE)
    for reason in ("", "   "):
        with pytest.raises(OverrideError, match="needs a reason"):
            accept(att_for(bundle), fake, ["TORQ_VERSION:stp1"], reason)


def test_an_unknown_code_is_refused_naming_what_can_be_accepted(bundle):
    with pytest.raises(OverrideError, match=r"NO_SUCH is not a finding code \(.*TORQ_VERSION"):
        accept(att_for(bundle), FakeStack(6000, torq=SITE), ["NO_SUCH:stp1"])


def test_there_must_be_a_finding_to_accept(bundle):
    """No standing waivers: an acceptance is for something the stack shows now."""
    with pytest.raises(OverrideError, match="nothing to accept"):
        accept(att_for(bundle), FakeStack(6000), ["TORQ_VERSION:stp1"])
    fake = FakeStack(6000, torq=SITE)
    with pytest.raises(
        OverrideError, match=r"no TORQ_VERSION finding for 'nope'.*it shows one for"
    ):
        accept(att_for(bundle), fake, ["TORQ_VERSION:nope"])


def test_a_non_overridable_blocker_present_on_the_stack_still_refuses(bundle):
    fake = FakeStack(6000)
    fake.named("stp1")["multilog"] = "singular"
    with pytest.raises(OverrideError, match="LOGGING_MODE cannot be overridden"):
        accept(att_for(bundle), fake, ["LOGGING_MODE:stp1"])


# ------------------------------------------------------- scoping and visibility


def test_an_override_covers_its_own_component_and_no_other(bundle):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ["TORQ_VERSION:stp1"])
    p = plan.build(att, fake, ROOT)
    assert not p.ok, "rdb1, wdb1 and discovery1 are still refused"
    refused = {f.component for f in p.findings if f.level == "blocker" and f.code == "TORQ_VERSION"}
    assert refused == {"discovery1", "rdb1", "wdb1"}
    assert [f.component for f in p.accepted] == ["stp1"]


def test_an_accepted_blocker_no_longer_stops_the_plan_but_stays_visible_as_a_risk(bundle):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    p = plan.build(att, fake, ROOT)
    assert p.ok
    assert {f.component for f in p.accepted} == {"discovery1", "stp1", "rdb1", "wdb1"}
    f = p.accepted[0]
    assert f.level == "accepted" and f.code == "TORQ_VERSION"
    assert "ACCEPTED RISK" in f.message and "alice" in f.message and "4.2.0" in f.message
    assert "does NOT add the version to the tested list" in f.message
    assert f.action and f.reason == "validated against the site's TorQ 4.2.0 build"
    data = p.as_dict()
    assert len(data["accepted_risks"]) == 4 and data["ok"] is True
    assert not any(x["level"] == "info" and x["code"] == "TORQ_VERSION" for x in data["findings"])


def test_accepting_a_version_does_not_add_it_to_the_tested_list(bundle):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    plan.build(att, fake, ROOT)
    assert SITE not in preflight.SUPPORTED_TORQ and preflight.SUPPORTED_TORQ == ("5.2.12",)


@pytest.mark.parametrize(
    ("code", "tamper", "needs"),
    [
        (
            "SORT_CONFIG",
            lambda fake: fake.named("wdb1").update(
                sortrows=[{"tabname": "default", "att": "p", "col": "sector", "sort": True}]
            ),
            ("end-of-day sort", "sort.csv row"),
        ),
        (
            "PUBLISHER_RUNNING",
            None,
            ("discarded or routed to some consumers only", "stop the publisher"),
        ),
        (
            "PORT_CONFLICT",
            None,
            ("cannot bind its port", "free the port"),
        ),
    ],
)
def test_sort_and_publisher_overrides_name_the_failure_risk_and_the_operator_action(
    bundle, monkeypatch, code, tamper, needs
):
    att0 = att_for(bundle)
    port = plan.publisher_port(att0, ROOT).port
    assert port is not None
    fake = FakeStack(6000)
    component = "wdb1"
    if tamper:
        tamper(fake)
    elif code == "PORT_CONFLICT":
        fake.named("discovery1")["services"].append(
            {"procname": "squatter1", "proctype": "feed", "hpup": f":testhost:{port}"}
        )
        component = att0.publisher.procname
    else:
        fake.named("discovery1")["services"].append(
            {"procname": att0.publisher.procname, "proctype": "feed", "hpup": f":testhost:{port}"}
        )
        monkeypatch.setattr(facts, "_tcp_open", lambda host, p: p == port)
        component = att0.publisher.procname
    att = accept(att0, fake, [f"{code}:{component}"])
    p = plan.build(att, fake, ROOT)
    assert p.ok and [f.code for f in p.accepted] == [code]
    text = p.accepted[0].message + " " + p.accepted[0].action
    for phrase in needs:
        assert phrase in text, text


# ------------------------------------------------------------- persistence


def test_an_override_round_trips_with_who_when_why_and_the_facts(bundle, tmp_path):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ["TORQ_VERSION:stp1"])
    config.save(att, tmp_path)
    loaded = config.load("pb-uat", tmp_path)
    assert loaded == att
    (o,) = loaded.overrides
    assert (o.code, o.component, o.operator) == ("TORQ_VERSION", "stp1", "alice")
    assert o.facts == {"torq": SITE} and o.at.endswith("Z") and o.reason.startswith("validated")
    raw = json.loads((tmp_path / "pb-uat" / config.CONFIG_FILE).read_text())
    assert raw["overrides"][0]["facts"] == {"torq": SITE}


def test_an_attachment_saved_before_overrides_existed_still_loads(bundle, tmp_path):
    config.save(att_for(bundle), tmp_path)
    path = tmp_path / "pb-uat" / config.CONFIG_FILE
    data = json.loads(path.read_text())
    data.pop("overrides")
    path.write_text(json.dumps(data))
    assert config.load("pb-uat", tmp_path).overrides == ()


def test_accepting_again_replaces_the_same_code_and_component(bundle):
    fake = FakeStack(6000, torq=SITE)
    first = accept(att_for(bundle), fake, ["TORQ_VERSION:stp1"], "first")
    second = service.accept(first, fake, ROOT, ["TORQ_VERSION:stp1"], "second", operator="bob")
    assert [(o.reason, o.operator) for o in second.overrides] == [("second", "bob")]


# -------------------------------------------------------------- fact drift


def test_an_acceptance_is_void_when_the_facts_it_was_made_against_change(bundle):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    assert plan.build(att, fake, ROOT).ok
    for name in ("discovery1", "stp1", "rdb1", "wdb1"):
        fake.named(name)["torq"] = "4.2.1"
    p = plan.build(att, fake, ROOT)
    assert not p.ok and not p.accepted
    assert any("stale" in x and "4.2.0" in x and "4.2.1" in x for x in p.override_problems)
    assert any("an earlier acceptance no longer applies" in f.message for f in p.findings)


def test_an_acceptance_for_a_finding_that_is_gone_is_reported_not_silently_kept(bundle):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ["TORQ_VERSION:stp1"])
    for name in ("discovery1", "stp1", "rdb1", "wdb1"):
        fake.named(name)["torq"] = "5.2.12"
    p = plan.build(att, fake, ROOT)
    assert p.ok and not p.accepted
    assert any("no longer needed" in x for x in p.override_problems)


def test_a_sort_acceptance_goes_stale_when_the_missing_column_changes(bundle):
    fake = FakeStack(6000)
    row = {"tabname": "default", "att": "p", "col": "sector", "sort": True}
    fake.named("wdb1")["sortrows"] = [row]
    att = accept(att_for(bundle), fake, ["SORT_CONFIG:wdb1"])
    assert plan.build(att, fake, ROOT).ok
    fake.named("wdb1")["sortrows"] = [{**row, "col": "desk"}]
    p = plan.build(att, fake, ROOT)
    assert not p.ok and any("stale" in x for x in p.override_problems)


# ------------------------------------------------- confirmation and enforcement


def test_apply_with_accepted_risks_needs_its_own_confirmation(bundle, tmp_path):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    config.save(att, tmp_path)
    with pytest.raises(applying.RisksNotConfirmed, match=r"--yes alone does not"):
        applying.apply(att, fake, ROOT, state_dir=tmp_path)
    assert not [op for _, op, _ in fake.calls if op != "probe"], "nothing was changed"
    assert not Manifest.exists("pb-uat", tmp_path)
    result = applying.apply(att, fake, ROOT, state_dir=tmp_path, confirmed_risks="alice")
    assert result.state == "applied"
    m = Manifest.load("pb-uat", tmp_path)
    assert m.risk_acceptance["confirmed_by"] == "alice"
    assert {o["code"] for o in m.risk_acceptance["overrides"]} == {"TORQ_VERSION"}
    assert len(m.risk_acceptance["findings"]) == 4
    assert any(h["event"] == "risks accepted" for h in m.history)


def test_a_stale_acceptance_cannot_be_confirmed_into_an_apply(bundle, tmp_path):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    config.save(att, tmp_path)
    for name in ("discovery1", "stp1", "rdb1", "wdb1"):
        fake.named(name)["torq"] = "4.2.1"
    with pytest.raises(applying.ApplyRefused, match="TORQ_VERSION"):
        applying.apply(att, fake, ROOT, state_dir=tmp_path, confirmed_risks="alice")


def test_an_attachment_without_accepted_risks_needs_no_such_confirmation(bundle, tmp_path):
    fake = FakeStack(6000)
    att = att_for(bundle)
    config.save(att, tmp_path)
    assert applying.apply(att, fake, ROOT, state_dir=tmp_path).state == "applied"
    assert Manifest.load("pb-uat", tmp_path).risk_acceptance == {}


def test_verify_passes_on_a_valid_acceptance_and_fails_once_it_is_stale(bundle, tmp_path):
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    config.save(att, tmp_path)
    applying.apply(att, fake, ROOT, state_dir=tmp_path, confirmed_risks="alice")
    ok = verify.verify(att, fake, tmp_path)
    assert ok.state == "verified"
    assert any(
        "accepted risk TORQ_VERSION:stp1 still holds as accepted" in c.name for c in ok.checks
    )
    for name in ("discovery1", "stp1", "rdb1", "wdb1"):
        fake.named(name)["torq"] = "4.2.1"
    bad = verify.verify(att, fake, tmp_path)
    assert not bad.ok and bad.state == "applied", "and the start gate closes with it"
    assert any(
        "accepted risk is still valid" in c.name and "stale" in c.detail
        for c in bad.checks
        if not c.ok
    )


# -------------------------------------------------------------------- the CLI


class _ErrorLog:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


@pytest.fixture
def world(monkeypatch, tmp_path, bundle):
    fake = FakeStack(6000, torq=SITE)
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path / "attachments"))
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    fake.errors = errors.messages
    return fake


def invoke(*argv: str, **kw):
    return CliRunner().invoke(cli.app, ["job", "attach", *argv], catch_exceptions=False, **kw)


def plan_args(bundle: Path) -> list[str]:
    return ["pb-uat", "--job", "pb_general_ledger", "--bundle", str(bundle)] + [
        "--stack-id", "6000", "--runtime-only",
    ]  # fmt: skip


def overrides_args() -> list[str]:
    args = []
    for spec in ALL_FOUR:
        args += ["--override", spec]
    return [*args, "--override-reason", "Validated against the site's TorQ 4.2.0 build"]


def mutations(world) -> list[str]:
    return [op for _, op, _ in world.calls if op != "probe"]


def test_a_dry_run_without_the_override_is_blocked_and_with_it_shows_the_accepted_risk(
    world, bundle
):
    blocked = invoke(*plan_args(bundle), "--json", "--dry-run")
    assert json.loads(blocked.output)["plan"]["ok"] is False
    ok = invoke(*plan_args(bundle), *overrides_args(), "--json", "--dry-run")
    data = json.loads(ok.output)["plan"]
    assert data["ok"] is True and len(data["accepted_risks"]) == 4
    human = invoke(*plan_args(bundle), *overrides_args(), "--dry-run")
    assert human.exit_code == 0 and "ACCEPTED RISK" in human.output.replace("\n", " ")


def test_an_override_needs_a_reason_a_known_code_and_an_overridable_one(world, bundle):
    no_reason = invoke(*plan_args(bundle), "--override", "TORQ_VERSION:stp1", "--dry-run")
    assert no_reason.exit_code != 0 and any("needs a reason" in m for m in world.errors)
    unknown = invoke(
        *plan_args(bundle), "--override", "NOPE:stp1", "--override-reason", "x", "--dry-run"
    )
    assert unknown.exit_code != 0 and any("not a finding code" in m for m in world.errors)
    world.named("stp1")["multilog"] = "singular"
    fixed = invoke(
        *plan_args(bundle), "--override", "LOGGING_MODE:stp1", "--override-reason", "x", "--dry-run"
    )
    assert fixed.exit_code != 0 and any("cannot be overridden" in m for m in world.errors)


def test_yes_alone_does_not_confirm_accepted_risks_but_accept_risks_does(world, bundle):
    refused = invoke(*plan_args(bundle), *overrides_args(), "--yes", "--no-start")
    assert refused.exit_code != 0, "the second confirmation was asked for, and not given"
    assert mutations(world) == []
    as_json = invoke(*plan_args(bundle), *overrides_args(), "--yes", "--no-start", "--json")
    assert as_json.exit_code == 1 and "--accept-risks" in json.loads(as_json.output)["error"]
    assert mutations(world) == []
    done = invoke(*plan_args(bundle), *overrides_args(), "--yes", "--accept-risks", "--no-start")
    assert done.exit_code == 0, done.output
    assert world.named("stp1")["subscribers"][TABLE] == 2


def test_the_risk_prompt_can_be_answered_interactively(world, bundle):
    done = invoke(*plan_args(bundle), *overrides_args(), "--yes", "--no-start", input="y\n")
    assert done.exit_code == 0 and "ACCEPTED RISK(S)" in done.output.replace("\n", " ")


def test_clear_overrides_withdraws_every_acceptance_and_saves_the_withdrawal(world, bundle):
    args = [*plan_args(bundle), *overrides_args()]
    assert invoke(*args, "--yes", "--accept-risks", "--no-start").exit_code == 0
    assert len(config.load("pb-uat").overrides) == 4
    preview = invoke("pb-uat", "--clear-overrides", "--json", "--dry-run")
    assert json.loads(preview.output)["plan"]["ok"] is False
    assert len(config.load("pb-uat").overrides) == 4, "a dry run withdraws nothing"
    cleared = invoke("pb-uat", "--clear-overrides", "--yes")
    assert cleared.exit_code == 1, "blocked again, so nothing is applied"
    assert config.load("pb-uat").overrides == ()


def test_only_the_cli_and_the_attach_package_reach_the_attach_workflow():
    """MCP and the browser do not start or change attachments; if one ever does it
    must go through uqs.attach (service.accept, plan.build, apply) - and this list
    is where that is noticed."""
    importers = set()
    for root in (ROOT / "python/uqs/src", ROOT / "python/uqf_frontend/src"):
        for path in root.rglob("*.py"):
            text = path.read_text()
            if "uqs.attach" in text or "from uqs import attach" in text:
                importers.add(str(path.relative_to(ROOT / "python")))
    assert not [p for p in importers if "mcp" in p or "uqf_frontend" in p], importers


def test_the_start_gate_still_needs_verify_even_with_accepted_risks(bundle, tmp_path, monkeypatch):
    from uqs.paths import UqsError, default_paths
    from uqs.stack import attach_state

    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path))
    fake = FakeStack(6000, torq=SITE)
    att = accept(att_for(bundle), fake, ALL_FOUR)
    config.save(att, tmp_path)
    applying.apply(att, fake, ROOT, state_dir=tmp_path, confirmed_risks="alice")
    with pytest.raises(UqsError, match="which is applied"):
        attach_state.refuse_unverified_start(default_paths(), att.publisher.procname, 6000)
    verify.verify(att, fake, tmp_path)
    attach_state.refuse_unverified_start(default_paths(), att.publisher.procname, 6000)


# --------------------------------------------------------- the local-host finding


def _own_address() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent
            address = s.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") else address


def test_a_host_given_as_an_address_this_machine_owns_is_local():
    h = facts.Host()
    assert h.is_local("127.0.0.1") and h.is_local("127.0.0.2") and h.is_local("localhost")
    assert not h.is_local("192.0.2.77") and not h.is_local("no-such-host.invalid")
    own = _own_address()
    if own is None:
        pytest.skip("this machine has only loopback")
    assert h.is_local(own), "the machine's own LAN address is local, not 'another machine'"


def test_a_schema_file_on_this_disk_is_persistable_with_the_stack_host_given_as_an_ip(
    bundle, tmp_path
):
    schema = tmp_path / "database.q"
    schema.write_text("trade:([] time:`timestamp$())\n")
    fake = FakeStack(6000, schema_file=str(schema))
    for address in ("127.0.0.1", "127.0.0.2", _own_address()):
        if address is None:
            continue
        att = att_for(bundle, host=address, runtime_only=False)
        p = plan.build(att, fake, ROOT, preflight.Facts(host=facts.Host(lambda h, port: False)))
        assert "PERSIST_UNAVAILABLE" not in {f.code for f in p.findings}, address


def test_wire_faults_in_a_plan_do_not_leak_into_overrides(bundle):
    """A lost connection is not a finding anyone can accept."""
    fake = FakeStack(6000, torq=SITE)
    cut = FaultIpc(fake, [("probe", 0, "before")])
    with pytest.raises(OverrideError, match="nothing to accept"):
        service.accept(att_for(bundle), cut, ROOT, ["TORQ_VERSION:stp1"], "x", operator="a")
