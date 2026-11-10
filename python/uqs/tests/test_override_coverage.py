"""The guardrail registry against what enforces it (#1830, the last part of #1824).

What the per-part tests leave open: is every guardrail `uqs override list` names
enforced by the module that declares it, and does every command that takes a generated
`--skip-<id>` hand it to that enforcement, offer it in help and completion, and forward it to a
target as the override it spells? test_backfill_override_cli.py drives the real `uqs job
backfill` with the waiver; this holds the registry and the documentation to it.
"""

from __future__ import annotations

import ast
import dataclasses
import re
import shlex
from pathlib import Path

import pytest
from plain_cli import PlainRunner
from test_attach_backfill_eod_override import REASON
from test_attach_backfill_findings import _emitted_codes
from test_backfill_override_cli import ARGV

from uqs import cli
from uqs.attach import backfill_forced, backfill_policy
from uqs.attach import overrides as attach
from uqs.cli import safety, skip_options, target_confirm
from uqs.cli import target as target_cli
from uqs.deploy import control, portable
from uqs.deploy import overrides as deploy
from uqs.paths import UqsError, repo_root
from uqs.scaffold import remove_policy as remove
from uqs.stack import start_overrides as start

SRC = Path(__file__).resolve().parents[1] / "src" / "uqs"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


# ------------------------------------------------------------ registry completeness


def test_every_guardrail_belongs_to_an_owner_that_enforces_it():
    """The registry is its owners' declarations and nothing else."""
    owned = [
        *start.GUARDRAILS.values(),
        *deploy.GUARDRAILS,
        *attach.GUARDRAILS,
        *backfill_policy.GUARDRAILS,
        *backfill_forced.GUARDRAILS,
        *portable.GUARDRAILS,
        *remove.GUARDRAILS,
    ]
    assert list(safety.registry()) == owned


def test_every_start_guardrail_is_an_overrides_field_the_start_policy_checks():
    fields = {f.name for f in dataclasses.fields(start.Overrides)}
    assert set(start.GUARDRAILS) == fields
    checked = {
        node.args[2].value
        for node in ast.walk(_tree(SRC / "stack" / "start_policy.py"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skipped"
        and isinstance(node.args[2], ast.Constant)
    }
    assert checked == fields, f"declared but never checked: {sorted(fields - checked)}"


def test_every_deploy_guardrail_is_a_config_field_the_deployment_reads():
    from uqs.deploy.config import Config

    fields = {g.id.replace("-", "_") for g in deploy.GUARDRAILS}
    assert fields <= {f.name for f in dataclasses.fields(Config)}
    read = {
        node.attr
        for path in (SRC / "deploy").glob("*.py")
        if path.name != "config.py"
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load)
    }
    assert fields <= read, f"declared but never read: {sorted(fields - read)}"


def test_every_attachment_guardrail_is_a_code_the_attach_workflow_raises():
    """The reverse of test_attach_overrides' scan: each declared code is raised somewhere in
    the attach workflow outside its own policy table - sort_plan.py included."""
    raised = {
        node.value
        for path in (SRC / "attach").glob("*.py")
        if path.name != "overrides.py"
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    declared = {g.id for g in attach.GUARDRAILS}
    assert declared == set(attach.POLICY)
    assert declared <= raised, f"declared but never raised: {sorted(declared - raised)}"


def test_every_backfill_guardrail_is_a_finding_coordination_emits_and_may_accept():
    emitted = _emitted_codes()
    for g in backfill_policy.GUARDRAILS:
        assert g.id in emitted, f"{g.id} is declared but coordination never emits it"
        assert backfill_policy.POLICY[g.id].overridable


# --------------------------------------------------------- CLI to enforcement


def _given_literals() -> set[str]:
    """The commands each `safety.given("<command>", ...)` call in cli/ names."""
    found: set[str] = set()
    for path in (SRC / "cli").glob("*.py"):
        for node in ast.walk(_tree(path)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner, first = node.func.value, node.args[0] if node.args else None
            if (
                node.func.attr == "given"
                and isinstance(owner, ast.Name)
                and owner.id == "safety"
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                found.add(first.value)
    return found


def test_every_command_with_a_generated_skip_reads_it_under_its_own_name():
    """skip_options hands a skip only to `safety.given(<the command's own name>)`: a body that
    asked under another name would accept the flag and silently ignore it."""
    skipping = {c for g in safety.registry() if g.skip_flag for c in g.commands}
    assert skipping, "no generated skip: this test would hold nothing"
    assert skipping <= _given_literals() | set(VERB_READERS)


#: Commands whose safety.given call takes the command name as a variable, not a
#: literal - start and restart share `_launch(verb, ...)`, which hands `verb` to
#: start_override_opts.overrides_from - each with the call that proves it.
VERB_READERS = {
    "start": '_launch(\n        "start",',
    "restart": '_launch(\n        "restart",',
}


def test_the_start_verbs_read_their_skips_under_their_own_name():
    """#1918: `start` and `restart` reach safety.given under their own name: their
    bodies call `_launch` with it, and `_launch` passes it to overrides_from, which
    calls safety.given with it. test_start_overrides.py proves it at run time."""
    lifecycle = (SRC / "cli" / "lifecycle.py").read_text()
    opts = (SRC / "cli" / "start_override_opts.py").read_text()
    for call in VERB_READERS.values():
        assert call in lifecycle, call
    assert "_overrides(verb, override, reason" in lifecycle
    assert "given = safety.given(command, names, reason)" in opts


# ------------------------------------------------------------- help and completion


def _plain(text: str) -> str:
    return " ".join(text.replace("''", "'").replace("\\\\:", ":").split())


def test_completion_offers_the_skip_with_its_declared_help():
    [g] = skip_options.declared("job backfill")
    words = "uqs job backfill duckdb_deals_backfill --skip-"
    zsh = PlainRunner().invoke(
        cli.app, [], prog_name="uqs",
        env={"_UQS_COMPLETE": "complete_zsh", "_TYPER_COMPLETE_ARGS": words},
    )  # fmt: skip
    assert f'"--skip-eod-fence":"{_plain(skip_options._help(g))}"' in _plain(zsh.output)
    bash = PlainRunner().invoke(
        cli.app, [], prog_name="uqs",
        env={"_UQS_COMPLETE": "complete_bash", "COMP_WORDS": words, "COMP_CWORD": "4"},
    )  # fmt: skip
    assert bash.output.split() == ["--skip-eod-fence"]


def test_help_carries_the_declaration_risk_and_action():
    [g] = skip_options.declared("job backfill")
    # Rich wraps a long help line inside its panel, so drop the panel's side
    # borders: the text then reads as one run however the line wrapped.
    shown = _plain(
        PlainRunner()
        .invoke(cli.app, ["job", "backfill", "--help"], env={"COLUMNS": "300"})
        .output.replace("│", " ")
    )
    for part in (g.description, g.risk, g.action):
        assert _plain(part).rstrip(".")[1:80] in shown


# ------------------------------------------------------------------ uqs --target


@pytest.fixture
def sent(monkeypatch):
    out: list[list[str]] = []
    monkeypatch.setattr(control, "remote_for", lambda name, root: control.Remote(name, "h", "/d"))
    monkeypatch.setattr(control, "run", lambda r, args, tty: out.append(list(args)) or 0)
    return out


@pytest.mark.parametrize("mode", ["plan", "dry-run", "run"])
def test_a_target_receives_the_skip_as_its_override(sent, mode):
    given = [*ARGV, "--mode", mode, "--skip-eod-fence", "--reason", REASON]
    assert target_cli.forward("uat", True, given, interactive=False) == 0
    assert sent == [[*ARGV, "--mode", mode, "--override", "eod-fence", "--reason", REASON]]


def test_a_target_asks_first_and_shows_the_override_it_would_send(sent):
    with pytest.raises(UqsError, match="may change uat") as info:
        target_cli.forward(
            "uat", False, [*ARGV, "--skip-eod-fence", "--reason", "r"], interactive=False
        )
    assert "--override eod-fence --reason r" in str(info.value) and sent == []


# ---------------------------------------------------------------- the documentation


GUIDE = repo_root() / "docs" / "guides" / "attach-existing-torq.md"
SECTION = "### Waiving the end-of-day fence"


def _documented() -> list[list[str]]:
    """Each `uqs ...` command in the guide's waiver section: its fenced blocks and inline code."""
    text = GUIDE.read_text().split(f"\n{SECTION}\n", 1)[1].split("\n#", 1)[0]
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.S)
    lines = " ".join(blocks).replace("\\\n", " ").splitlines()
    lines += re.findall(r"`(uqs [^`]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    out = []
    for line in lines:
        words = shlex.split(line)[1:] if line.strip().startswith("uqs ") else []
        if words[:1] == ["--target"]:  # the entry point's, before any command parses
            words = words[2:]
        if words:
            out.append(words)
    return out


def test_every_command_the_waiver_section_documents_parses_against_the_tree():
    commands = _documented()
    assert any("--skip-eod-fence" in c for c in commands), commands
    for words in commands:
        assert target_confirm.parse(words) is not None, f"`uqs {' '.join(words)}` does not parse"
