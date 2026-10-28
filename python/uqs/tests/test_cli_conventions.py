"""The `uqs` CLI conventions (#1738), held over the real Typer tree.

docs/guides/uqs.md#cli-conventions states six rules; this walks every command
`uqs.cli.entry` registers and enforces the four a tree can show:

- rule 1: no group is named after a verb;
- rule 3: one option name has one meaning, and one short flag one long name;
- rule 4: every leaf is named by a verb from the vocabulary;
- rule 5: `--json` on what reports, `--dry-run/-n` on what changes state,
  `--yes/-y` on what asks, and no bypass but `--override NAME --reason`.

Each rule has an ALLOWLIST of today's exceptions, each naming the issue that
removes it (#1727 to #1740). A rule fails on a violation that is not listed AND
on a listed one that is no longer a violation, so the lists only shrink - the
way tests/q/peachq_known_gaps.txt does for PeachQ. When a sub-issue lands, the
test names the rows to delete.

Hidden commands are skipped: rule 6 ships each rename with the old spelling as
a hidden alias for one release, and an alias is on its way out, not a command
to hold to the rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer.main

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs.cli import shared
from uqs.cli.shared import app

ROOT = "uqs"  # the label the root group's own options are reported under
GUIDE = Path(__file__).resolve().parents[3] / "docs" / "guides" / "uqs.md"

#: The issues allowed to hold an exception open: #1738's sub-issues, and those
#: the audit that built these lists filed beside them.
ISSUES = {f"#{n}" for n in range(1727, 1741)}


# --------------------------------------------------------------------------- tree


@dataclass(frozen=True)
class Cmd:
    path: str  # "job runs list"; ROOT for the root group
    is_group: bool
    command: Any  # the click command typer built (typer vendors its own click)

    @property
    def options(self) -> list:
        return [p for p in self.command.params if p.param_type_name == "option"]

    def option(self, name: str):
        return next((p for p in self.options if name in p.opts + p.secondary_opts), None)


def _walk(command, path: list[str]) -> Iterator[Cmd]:
    if getattr(command, "hidden", False):
        return
    # typer vendors its own click, so a group is told by its commands, not its class
    is_group = hasattr(command, "commands")
    yield Cmd(" ".join(path) or ROOT, is_group, command)
    if is_group:
        for name, sub in command.commands.items():
            yield from _walk(sub, [*path, name])


TREE = list(_walk(typer.main.get_command(app), []))
LEAVES = {c.path: c for c in TREE if not c.is_group}
GROUPS = {c.path: c for c in TREE if c.is_group and c.path != ROOT}


def _long(param) -> str:
    return next(o for o in param.opts + param.secondary_opts if o.startswith("--"))


def _hold(rule: str, found: set, allowed: dict) -> None:
    """The violations found must be exactly the allowlisted ones."""
    new = sorted(found - set(allowed), key=str)
    stale = sorted(set(allowed) - found, key=str)
    lines = []
    if new:
        lines.append(f"{rule}: new violations - fix them (or, for today's tree, list them):")
        lines += [f"  {v!r}" for v in new]
    if stale:
        lines.append(f"{rule}: no longer violations - delete these allowlist rows:")
        lines += [f"  {v!r}: {allowed[v]!r}" for v in stale]
    assert not lines, "\n".join(lines)


# --------------------------------------------------------------------- vocabulary

#: Rule 4's core verbs, each meaning one thing (#1733).
CORE_VERBS = {
    "status": "the state of something running or installed",
    "check": "actively probe; exit non-zero on a problem",
    "list": "many records",
    "show": "one record",
    "set": "change one setting",
    "add": "create one thing",
    "remove": "delete one thing",
}

#: Rule 4's domain verbs: the words a domain needs that the core does not cover,
#: including the few nouns kept as views of a running stack (graph, schema, ...).
DOMAIN_VERBS = {
    "start": "the stack's lifecycle (also a connector's)",
    "stop": "the stack's lifecycle (also a connector's)",
    "restart": "the stack's lifecycle",
    "logs": "read processes' logs",
    "query": "run a q expression on a process",
    "graph": "draw the process graph",
    "schema": "describe a process's tables",
    "new": "scaffold a job into the tree",
    "install": "place files built elsewhere",
    "backfill": "run a bounded worker over a range",
    "preview": "fetch a feed's rows without publishing them",
    "gaps": "a job's missing ranges",
    "audit": "one window across runs",
    "receive": "take an extractor's batches into an HDB",
    "attach": "put a job on a running TorQ stack",
    "detach": "take it off again",
    "export": "write a record out for another machine",
    "approve": "accept a record another machine exported",
    "replay": "replay a tickerplant log into the HDB",
    "seed": "copy another runtime's HDB",
    "migrate": "migrate an HDB partition's schema",
    "diff": "compare two runtimes",
    "prepare": "install the bundles a runtime declares",
    "stub": "add a placeholder row",
    "env": "print the environment to source",
    "build": "build a release",
    "push": "deploy a release onto a server",
    "rollback": "return to the previous release or version",
    "prune": "remove old releases",
    "history": "what was deployed",
    "shell": "open a shell on a target",
}

#: Rule 1: a group named after one of these is a verb with nouns under it.
GROUP_VERBS = set(CORE_VERBS) | {"start", "stop", "restart", "delete", "get", "create", "run"}


# ------------------------------------------------------------- rule 1: groups


#: group path -> the issue that renames it
RULE1_ALLOWLIST = {
    "remove": "#1727",  # remove output / remove checkpoint -> clean, job checkpoint ...
}


def test_rule1_no_group_is_named_after_a_verb():
    found = {path for path in GROUPS if path.split()[-1] in GROUP_VERBS}
    _hold("rule 1", found, RULE1_ALLOWLIST)


# --------------------------------------------------------- rule 4: verb names


#: leaf path -> the issue that renames it
RULE4_ALLOWLIST = {
    "up": "#1734",  # -> start --follow
    "raw": "#1734",  # torq.sh pass-through
    "summary": "#1733",  # -> status
    "remove output": "#1727",  # -> clean
    "remove checkpoint": "#1727",  # -> job checkpoint ...
    "data hdb-check": "#1733",  # -> data check
    "config get": "#1733",  # -> show
    "deploy verify": "#1733",  # -> deploy check
    "deploy build-runtime": "#1740",  # a verb glued to a noun
}

#: Leaves named by a vocabulary verb used with another meaning: a name test
#: cannot see the meaning, so each is listed until its rename removes the path.
RULE4_MISUSED = {
    "list": "#1728",  # one `list KIND` for eleven kinds; each noun owns its list
    "job runs status": "#1733",  # unfinished runs, a list - not a state
    "job runs migrate": "#1737",  # the ledger's upgrade, not `data migrate`'s HDB schema
}


def test_rule4_every_leaf_is_named_by_a_vocabulary_verb():
    verbs = set(CORE_VERBS) | set(DOMAIN_VERBS)
    found = {path for path in LEAVES if path.split()[-1] not in verbs}
    _hold("rule 4", found, RULE4_ALLOWLIST)


def test_rule4_a_misused_verb_is_listed_until_its_path_goes():
    _hold("rule 4 (misused verbs)", set(RULE4_MISUSED) & set(LEAVES), RULE4_MISUSED)


def test_the_guide_lists_the_same_vocabulary():
    """The guide's verb table and the lists above are one vocabulary."""
    text = GUIDE.read_text()
    section = text.split("\n## CLI conventions\n", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\s*\|\s*(`[^|]*`)\s*\|", section, flags=re.M)
    documented = {w for row in rows for w in re.findall(r"`([a-z-]+)`", row)}
    assert documented == set(CORE_VERBS) | set(DOMAIN_VERBS)


# ----------------------------------------------------- rule 3: option meaning


@dataclass(frozen=True)
class Meaning:
    says: str  # the one meaning, for the reader of a failure
    pattern: str | None  # matches (ignoring case) the help text that says it; None: retired


#: Every option name more than one command uses, with its one meaning. A help
#: text that does not match the pattern is a second meaning.
OPTIONS = {
    "--allow-dirty": Meaning("accept uncommitted changes", r"uncommitted"),
    "--arch": Meaning("the servers' CPU architecture", r"x86_64"),
    "--attach": Meaning("a saved attachment, by name", r"attach"),
    "--base-port": Meaning("the stack's base port", r"base port"),
    "--break-lock": Meaning("remove a stale deploy lock", r"deploy lock"),
    "--bundle": Meaning("a sidecar bundle folder", r"bundle"),
    "--columns": Meaning("which columns of a table to show", r"`all`"),
    "--command-timeout": Meaning("seconds per remote step", r"remote step"),
    "--connect-timeout": Meaning("ssh/scp seconds", r"ssh/scp"),
    "--dataset": Meaning("the dataset a bounded worker fills", r"table it fills"),
    "--debug": Meaning("log at DEBUG", r"^Log at DEBUG\. Same as LOG_LEVEL"),
    "--dest": Meaning("the absolute directory on the server", r"absolute"),
    "--dry-run": Meaning("show what would change, change nothing", r"nothing|dry"),
    "--every": Meaning("re-read every N seconds", r"re-read"),
    "--export": Meaning("also write the rows to FILE", r"write output to FILE"),
    "--force": Meaning("retired: each use gets its own name", None),
    "--from": Meaning("start of a time range", r"start"),
    "--home": Meaning("the ODBC home", r"ODBC home"),
    "--host": Meaning("where a q process listens", r"listen"),
    "--interactive": Meaning("browse the table", r"browse"),
    "--json": Meaning("one JSON document on stdout", r"JSON"),
    "--keep": Meaning("how many newest releases to keep", r"newest"),
    "--level": Meaning("the lowest log level shown", r"level"),
    "--live": Meaning("refuse fixtures", r"fixture"),
    "--mode": Meaning("backfill's validate/plan/run mode", r"validate"),
    "--odbc-home": Meaning("a private ODBC setup", r"ODBC"),
    "--output": Meaning("where to write the output", r"write"),
    "--overwrite": Meaning("replace what is already there", r"replace"),
    "--passwd": Meaning("the q password", r"password"),
    "--port": Meaning("one process's port", r"directly"),
    "--print": Meaning("retired: --show-command", None),
    "--proc": Meaning("one process, by name", r"process|tickerplant"),
    "--profile": Meaning("a start profile", r"profile"),
    "--python": Meaning("the servers' Python version", r"major\.minor"),
    "--python-runtime": Meaning("a bundled CPython", r"CPython"),
    "--qhome": Meaning("a QHOME", r"QHOME"),
    "--raw": Meaning("retired: --no-gateway / --unresolved", None),
    "--remote-user": Meaning("run as this account through sudo", r"sudo"),
    "--reverse": Meaning("sort descending", r"descending"),
    "--skip-profile-budget-check": Meaning("bypass the budget check", r"budget"),
    "--skip-q-version-check": Meaning("bypass the q version check", r"q too old"),
    "--sort": Meaning("sort by a column", r"sort by"),
    "--tables": Meaning("a set of tables", r"tables"),
    "--target": Meaning("run this command on a deployed target", r"instead of this machine"),
    "--timeout": Meaning("seconds for the whole command", r"seconds"),
    "--to": Meaning("end of a time range, exclusive", r"exclusive"),
    "--trace": Meaning("log every query the source is sent", r"quer"),
    "--user": Meaning("the q user", r"user|admin"),
    "--verify-timeout": Meaning("readiness deadline", r"deadline"),
    "--version": Meaning("print uqs's version", r"uqs's version"),
    "--yes": Meaning("do not ask", r"ask"),
}

#: Every short flag, and the one long name it abbreviates.
SHORT_FLAGS = {
    "-n": "--dry-run",
    "-y": "--yes",
    "-i": "--interactive",
    "-f": "--follow",
    "-o": "--output",
    "-c": "--columns",
}

#: (command, option) -> the issue that renames the option there
RULE3_ALLOWLIST = {
    # --port as the stack's base port, or a listen port (#1729)
    **{
        (c, "--port"): "#1729"
        for c in (
            "start",
            "up",
            "stop",
            "restart",
            "raw",
            "summary",
            "graph",
            "list",
            "data replay",
            "config get",
            "job receive",
            "job backfill",
            "connector start",
            "deploy verify",
        )
    },
    ("query", "--target"): "#1729",
    ("deploy push", "--target"): "#1729",
    ("stop", "--force"): "#1729",
    ("job backfill", "--force"): "#1729",
    ("job remove", "--force"): "#1729",
    ("start", "--print"): "#1729",
    ("logs", "--print"): "#1729",
    ("query", "--raw"): "#1729",
    ("config get", "--raw"): "#1729",
    ("summary", "--debug"): "#1729",
    ("job backfill", "--debug"): "#1729",
    ("logs", "-n"): "#1729",  # lines, not --dry-run
    ("data seed", "--from"): "#1739",
    ("deploy rollback", "--to"): "#1739",
    ("logs", "--columns"): "#1739",
    ("job new", "--columns"): "#1739",
    ("connector start", "--dataset"): "#1739",
    ("job install", "--mode"): "#1739",
    ("job backfill", "--version"): "#1739",
    **{(f"deploy {c}", "--host"): "#1739" for c in ("push", "status", "rollback", "prune")},
}


def _option_uses() -> dict[str, dict[str, str]]:
    """option name -> {command path: help text}, over every command and group."""
    uses: dict[str, dict[str, str]] = {}
    for cmd in TREE:
        for param in cmd.options:
            uses.setdefault(_long(param), {})[cmd.path] = " ".join((param.help or "").split())
    return uses


def test_rule3_every_shared_option_name_has_a_registered_meaning():
    shared = {name for name, by in _option_uses().items() if len(by) > 1}
    unregistered = sorted(shared - set(OPTIONS))
    assert not unregistered, (
        f"option names now used by more than one command: {unregistered} - "
        "give each its one meaning in OPTIONS"
    )


def test_rule3_one_option_name_has_one_meaning():
    found = set()
    for name, by in _option_uses().items():
        meaning = OPTIONS.get(name)
        if meaning is None:
            continue
        for path, text in by.items():
            if meaning.pattern is None or not re.search(meaning.pattern, text, re.I):
                found.add((path, name))
    found |= _short_flag_violations()
    _hold("rule 3", found, RULE3_ALLOWLIST)


def _short_flag_violations() -> set[tuple[str, str]]:
    found = set()
    for cmd in TREE:
        for param in cmd.options:
            for short in (o for o in param.opts + param.secondary_opts if not o.startswith("--")):
                if SHORT_FLAGS.get(short) != _long(param):
                    found.add((cmd.path, short))
    return found


def test_rule3_every_option_says_what_it_means():
    """An option with no help text cannot be held to one meaning."""
    silent = sorted(
        f"{c.path} {_long(p)}" for c in TREE for p in c.options if not (p.help or "").strip()
    )
    assert not silent


# ------------------------------------------------------ rule 5: uniform flags

REPORTS, CHANGES = "reports", "changes"

#: Every visible leaf, as reporting or changing state - decided by hand, because
#: a heuristic over names would be wrong. A third kind is a string saying why
#: the command is neither: its output belongs to something else.
KIND = {
    "start": CHANGES,
    "up": CHANGES,
    "stop": CHANGES,
    "restart": CHANGES,
    "raw": "passes any verb through to torq.sh",
    "summary": REPORTS,
    "graph": REPORTS,
    "query": REPORTS,
    "schema": REPORTS,
    "list": REPORTS,
    "logs": REPORTS,
    "remove output": CHANGES,
    "remove checkpoint": CHANGES,
    "data replay": CHANGES,
    "data seed": CHANGES,
    "data migrate": CHANGES,
    "data hdb-check": REPORTS,  # --fix writes, opt-in: #1732 settles --apply-style modes
    "config get": REPORTS,
    "config set": CHANGES,
    "config sources stub": CHANGES,
    "config sources check": REPORTS,
    "runtime diff": REPORTS,
    "runtime prepare": CHANGES,
    "job receive": CHANGES,
    "job backfill": CHANGES,
    "job gaps": REPORTS,
    "job preview": REPORTS,
    "job new": CHANGES,
    "job remove": CHANGES,
    "job install": CHANGES,
    "job attach": CHANGES,
    "job detach": CHANGES,
    "job runs status": REPORTS,
    "job runs list": REPORTS,
    "job runs migrate": CHANGES,
    "job runs show": REPORTS,
    "job runs audit": REPORTS,
    "job contract export": "prints the contract, a document another machine approves",
    "job contract approve": CHANGES,
    "job contract list": REPORTS,
    "connector start": CHANGES,
    "connector stop": CHANGES,
    "connector status": REPORTS,
    "setup odbc install": CHANGES,
    "setup odbc rollback": CHANGES,
    "setup odbc status": REPORTS,
    "setup odbc env": "prints shell exports, for eval",
    "deploy build": CHANGES,
    "deploy push": CHANGES,
    "deploy build-runtime": CHANGES,
    "deploy status": REPORTS,
    "deploy rollback": CHANGES,
    "deploy prune": CHANGES,
    "deploy verify": REPORTS,
    "deploy history": REPORTS,
    "target add": CHANGES,
    "target check": REPORTS,
    "target shell": "opens an interactive shell on the server",
    "target remove": CHANGES,
}

#: Every command that asks before acting. ROOT asks before running a command
#: on a --target.
ASKS = {ROOT, "job remove", "job install", "job attach", "job detach"}

#: The cli modules that call a prompt. A new one means a command that asks:
#: put it in ASKS, then here.
PROMPTING_MODULES = {"attach", "install", "install_bundle", "remove", "target"}

#: leaf -> the issue that adds its --json. Empty since #1731: keep it so.
RULE5A_ALLOWLIST: dict[str, str] = {}

#: leaf -> the issue that gives it --dry-run/-n (or, for one that changes
#: nothing, takes its --dry-run away)
RULE5B_ALLOWLIST = {
    c: "#1732"
    for c in (
        "start",
        "up",
        "stop",
        "restart",
        "remove checkpoint",
        "data replay",
        "data seed",
        "data migrate",  # --apply: dry by default
        "config set",
        "config sources stub",
        "runtime prepare",
        "job receive",
        "job backfill",  # --mode validate/plan
        "job preview",  # changes nothing, yet has --dry-run
        "job new",
        "job remove",
        "job install",
        "job attach",
        "job detach",
        "job runs migrate",
        "job contract approve",
        "connector start",
        "connector stop",
        "setup odbc install",
        "setup odbc rollback",
        "deploy build",
        "deploy build-runtime",
        "deploy push",
        "deploy rollback",
        "deploy prune",
        "target add",
        "target remove",
    )
}

#: command -> the issue that gives its --yes a -y
RULE5C_ALLOWLIST = {ROOT: "#1732", "job attach": "#1732", "job detach": "#1732"}

#: (command, option) -> the issue that folds the bypass into --override NAME --reason
RULE5D_ALLOWLIST = {
    ("start", "--skip-q-version-check"): "#1732",
    ("start", "--skip-profile-budget-check"): "#1732",
    ("up", "--skip-q-version-check"): "#1732",
    ("up", "--skip-profile-budget-check"): "#1732",
    ("restart", "--skip-q-version-check"): "#1732",
    ("job attach", "--override-reason"): "#1732",
    ("deploy push", "--accept-converted-release"): "#1732",
    ("deploy push", "--accept-digest"): "#1732",
    ("deploy push", "--accept-q-version"): "#1732",
    ("deploy push", "--allow-unpinned"): "#1732",
}


def _flag(cmd: Cmd, long: str, short: str | None = None) -> bool:
    param = cmd.option(long)
    return param is not None and (short is None or short in param.opts + param.secondary_opts)


def test_rule5_every_leaf_is_classified():
    assert set(KIND) == set(LEAVES), (
        f"unclassified: {sorted(set(LEAVES) - set(KIND))}; gone: {sorted(set(KIND) - set(LEAVES))}"
    )


def test_rule5a_every_reporting_command_has_json():
    found = {p for p, c in LEAVES.items() if KIND.get(p) == REPORTS and not _flag(c, "--json")}
    _hold("rule 5a", found, RULE5A_ALLOWLIST)


def test_rule5b_dry_run_n_exactly_on_what_changes_state():
    found = set()
    for path, cmd in LEAVES.items():
        changes = KIND.get(path) == CHANGES
        if changes and not _flag(cmd, "--dry-run", "-n"):
            found.add(path)
        if not changes and _flag(cmd, "--dry-run"):
            found.add(path)
    _hold("rule 5b", found, RULE5B_ALLOWLIST)


def test_rule5c_yes_y_exactly_on_what_asks():
    with_yes = {c.path for c in TREE if _flag(c, "--yes")}
    assert with_yes <= ASKS, f"--yes on a command not in ASKS: {sorted(with_yes - ASKS)}"
    found = {c.path for c in TREE if c.path in ASKS and not _flag(c, "--yes", "-y")}
    _hold("rule 5c", found, RULE5C_ALLOWLIST)


def test_rule5c_the_commands_that_ask_are_the_known_ones():
    cli_dir = Path(shared.__file__).parent
    prompting = {
        f.stem
        for f in cli_dir.glob("*.py")
        if re.search(r"typer\.confirm\(|Confirm\.ask\(|\.prompt\(", f.read_text())
    }
    assert prompting == PROMPTING_MODULES


def test_rule5d_a_bypass_is_override_name_reason():
    found = set()
    for cmd in TREE:
        for param in cmd.options:
            name = _long(param)
            reason_taking = (param.metavar or "").upper() == "REASON" and name != "--reason"
            if name.startswith("--skip-") or name == "--override-reason" or reason_taking:
                found.add((cmd.path, name))
    _hold("rule 5d", found, RULE5D_ALLOWLIST)


# ------------------------------------------------------------- the lists


@pytest.mark.parametrize(
    "allowlist",
    [
        RULE1_ALLOWLIST,
        RULE3_ALLOWLIST,
        RULE4_ALLOWLIST,
        RULE4_MISUSED,
        RULE5A_ALLOWLIST,
        RULE5B_ALLOWLIST,
        RULE5C_ALLOWLIST,
        RULE5D_ALLOWLIST,
    ],
)
def test_every_exception_names_the_issue_that_removes_it(allowlist):
    assert set(allowlist.values()) <= ISSUES
