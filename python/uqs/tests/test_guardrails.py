"""The guardrail declarations and their registry (#1825)."""

from __future__ import annotations

import pytest

from uqs import guardrails
from uqs.attach import overrides as attach
from uqs.cli import safety
from uqs.deploy import overrides as deploy
from uqs.guardrails import Guardrail
from uqs.stack import start_overrides as start


def test_the_catalog_lists_what_it_did_before_the_registry_in_the_same_order():
    """The rows `uqs override list` showed when safety.catalog built them by
    hand from the three owners' tables - same names, commands, effects, order."""
    before: list[tuple[str, tuple[str, ...], str]] = [
        (start.NAMES[field], ("start", "restart"), g.description)
        for field, g in start.GUARDRAILS.items()
    ]
    before += [(name, ("deploy push",), skips) for name, skips in deploy.SKIPS.items()]
    before += [
        (f"{code}:COMPONENT", ("job attachment add",), policy.risk)
        for code, policy in sorted(attach.POLICY.items())
        if policy.overridable
    ]
    assert [(g.name, g.commands, g.description) for g in safety.catalog()] == before
    assert [g.name for g in safety.catalog()] == [
        "q-version-check",
        "profile-budget-check",
        "accept-digest",
        "accept-q-version",
        "allow-unpinned",
        "accept-converted-release",
        "PORT_CONFLICT:COMPONENT",
        "PUBLISHER_RUNNING:COMPONENT",
        "SORT_CONFIG:COMPONENT",
        "TORQ_VERSION:COMPONENT",
    ]


def test_the_registry_keeps_every_attachment_code_and_its_policy():
    by_id = {g.id: g for g in safety.registry()}
    for code, policy in attach.POLICY.items():
        g = by_id[code]
        assert (g.overridable, g.risk, g.action) == (policy.overridable, policy.risk, policy.action)
        assert g.component_scoped and g.name == f"{code}:COMPONENT"
    assert not by_id["q-version-check"].component_scoped


def test_a_duplicate_id_is_refused():
    a = Guardrail("accept-digest", ("deploy push",), "one")
    b = Guardrail("accept-digest", ("start",), "another")
    with pytest.raises(ValueError, match="'accept-digest' is declared twice"):
        guardrails.aggregate([(a,), (b,)])


def test_a_duplicate_id_across_owners_fails_the_registry(monkeypatch):
    clash = Guardrail("q-version-check", ("deploy push",), "a second q-version-check")
    monkeypatch.setattr(deploy, "GUARDRAILS", (*deploy.GUARDRAILS, clash))
    with pytest.raises(ValueError, match="'q-version-check' is declared twice"):
        safety.registry()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"id": ""}, "is not a slug"),
        ({"id": "has space"}, "is not a slug"),
        ({"id": "trailing-"}, "is not a slug"),
        ({"id": "CODE:COMPONENT"}, "is not a slug"),
        ({"commands": ()}, "nonempty tuple"),
        ({"commands": "start"}, "nonempty tuple"),
        ({"commands": ("start", " ")}, "blank or padded"),
        ({"commands": ("start", "start")}, "named twice"),
        ({"description": "  "}, "description is empty"),
    ],
)
def test_an_invalid_declaration_is_refused(kwargs, message):
    fields = {"id": "q-version-check", "commands": ("start",), "description": "skips it"}
    with pytest.raises(ValueError, match=message):
        Guardrail(**(fields | kwargs))


def test_a_policy_table_adapts_into_guardrails_by_code():
    """The shape a finding-code table (attach's POLICY, #1827's) is adapted from."""
    table = {
        "B_CODE": attach.Policy(False, "cannot be accepted"),
        "A_CODE": attach.Policy(True, "may lose rows", "stop it first"),
    }
    a, b = guardrails.from_policy(table, ("job backfill",))
    assert (a.id, a.name, a.description, a.action, a.overridable) == (
        "A_CODE",
        "A_CODE:COMPONENT",
        "may lose rows",
        "stop it first",
        True,
    )
    assert (b.id, b.overridable) == ("B_CODE", False)
