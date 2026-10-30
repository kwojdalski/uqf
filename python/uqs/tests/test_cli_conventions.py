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

Hidden commands and options are skipped: rule 6 ships each rename with the old
spelling as a hidden alias for one release, and an alias is on its way out, not
a command or an option to hold to the rules.
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
from uqs.cli import safety, shared
from uqs.cli.shared import app
from uqs.cli.target_confirm import effect_of

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
        # a hidden option is a renamed one's old spelling (rule 6): on its way out
        return [
            p
            for p in self.command.params
            if p.param_type_name == "option" and not getattr(p, "hidden", False)
        ]

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
    "restart": "the stack's lifecycle (also a connector's)",
    "exec": "run any other torq.sh verb, passed straight through",
    "logs": "read processes' logs",
    "clean": "delete the runtime state the stack wrote",
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
    "export": "write a record out for another machine",
    "approve": "accept a record another machine exported",
    "replay": "replay a tickerplant log into the HDB",
    "seed": "copy another runtime's HDB",
    "migrate": "migrate an HDB partition's schema",
    "alter": "change one table in every HDB partition holding it",
    "upgrade": "bring a store an older uqs wrote up to today's layout",
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
    # a target's own changes (#1756): the core verbs cover add, set and remove
    "unset": "remove a setting, so the layer beneath's value applies - `set` cannot say it",
    "rename": "give a thing a new name, keeping what is recorded under the old one",
    "edit": "change a file in $EDITOR, checked before it is saved",
    "log": "a record's change history - `logs` is processes' output, `history` deployments",
    "revert": "undo one recorded change, itself recorded - not `rollback`'s release",
    "adopt": "take over what was installed by hand, so uqs can drive it",
}

#: Rule 1: a group named after one of these is a verb with nouns under it.
GROUP_VERBS = set(CORE_VERBS) | {"start", "stop", "restart", "delete", "get", "create", "run"}


# ------------------------------------------------------------- rule 1: groups


#: group path -> the issue that renames it
RULE1_ALLOWLIST: dict[str, str] = {}


def test_rule1_no_group_is_named_after_a_verb():
    found = {path for path in GROUPS if path.split()[-1] in GROUP_VERBS}
    _hold("rule 1", found, RULE1_ALLOWLIST)


# ------------------------------------------------------------- group help


def _named(word: str, text: str) -> bool:
    """`word` in `text` as itself: `check` is not named by `hdb-check`."""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def test_every_group_help_names_every_command_in_it():
    """A group's one line of help, the one `uqs --help` shows beside it, names
    each command the group holds (#1737): `data`'s said "replay, seed,
    hdb-check" while it also held `migrate`. Hidden aliases are not held."""
    unnamed = {
        path: [n for n, sub in group.command.commands.items()
               if not getattr(sub, "hidden", False) and not _named(n, group.command.help or "")]
        for path, group in GROUPS.items()
    }  # fmt: skip
    unnamed = {path: names for path, names in unnamed.items() if names}
    assert not unnamed, f"group help that does not name these commands: {unnamed}"


# ------------------------------------------------- rule 2: what is acted on


#: The words a positional naming a process or a job is NOT called: one is PROC
#: (or PROCS), the other JOB (#1730).
NOT_THE_NAME = {"WORKER", "PROCNAME"}

#: Options naming a process or a job, kept because they filter rather than name
#: what the command acts on, with why.
RULE2_FILTERS = {
    ("logs", "--worker"): "with --attach: whose run in the attachment - a worker or a receiver",
}


def _metavar(param) -> str:
    return (param.metavar or param.name.upper()).strip("[].")


def test_rule2_a_process_or_a_job_is_a_positional_called_proc_or_job():
    misnamed = {
        (path, _metavar(p))
        for path, c in LEAVES.items()
        for p in c.command.params
        if p.param_type_name == "argument" and _metavar(p) in NOT_THE_NAME
    }
    assert not misnamed
    named = {
        (c.path, _long(p)) for c in TREE for p in c.options if _long(p) in ("--proc", "--worker")
    }
    assert named == set(RULE2_FILTERS)


# --------------------------------------------------------- rule 4: verb names


#: leaf path -> the issue that renames it
RULE4_ALLOWLIST: dict[str, str] = {}

#: Leaves named by a vocabulary verb used with another meaning: a name test
#: cannot see the meaning, so each is listed until its rename removes the path.
RULE4_MISUSED: dict[str, str] = {}


def test_rule4_every_leaf_is_named_by_a_vocabulary_verb():
    verbs = set(CORE_VERBS) | set(DOMAIN_VERBS)
    found = {path for path in LEAVES if path.split()[-1] not in verbs}
    _hold("rule 4", found, RULE4_ALLOWLIST)


def test_rule4_a_misused_verb_is_listed_until_its_path_goes():
    _hold("rule 4 (misused verbs)", set(RULE4_MISUSED) & set(LEAVES), RULE4_MISUSED)


#: Groups that add or remove things but list none, with why (#1728).
UNLISTED = {
    "job checkpoint": "a worker has one checkpoint, or none: there is nothing to list",
}

#: The output flags every `list` takes (#1728).
LIST_FLAGS = ("--json", "--export", "--sort", "--reverse", "--interactive")


def test_every_noun_that_adds_or_removes_a_thing_lists_it():
    makers = {"add", "new", "remove"}
    found = set()
    for path in GROUPS:
        verbs = {leaf.split()[-1] for leaf in LEAVES if leaf.rsplit(" ", 1)[0] == path}
        if verbs & makers and "list" not in verbs:
            found.add(path)
    assert found == set(UNLISTED)


def test_every_list_takes_the_same_output_flags():
    lists = [c for p, c in LEAVES.items() if p.split()[-1] == "list"]
    missing = {
        c.path: [f for f in LIST_FLAGS if c.option(f) is None]
        for c in lists
        if any(c.option(f) is None for f in LIST_FLAGS)
    }
    assert set(missing) <= set(LIST_FLAGS_PENDING), missing


#: Lists that do not take every output flag yet, and the issue that adds them.
LIST_FLAGS_PENDING = {
    "job runs list": "#1731",  # the run ledger's own table: no --json/--export/--sort
    "job attachment contract list": "#1731",  # --json only
}


def test_the_guide_lists_the_same_vocabulary():
    """The guide's verb table and the lists above are one vocabulary."""
    text = GUIDE.read_text()
    section = text.split("\n## CLI conventions\n", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
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
    "--apply": Meaning("make the changes; without it, a dry run", r"without it, a dry run"),
    "--arch": Meaning("the servers' CPU architecture", r"x86_64"),
    "--attach": Meaning("a saved attachment, by name", r"attach"),
    "--base-port": Meaning("the stack's base port", r"base port"),
    "--break-lock": Meaning("remove a stale deploy lock", r"deploy lock"),
    "--bundle": Meaning("a sidecar bundle folder", r"bundle"),
    "--cast": Meaning("cast a column's type", r"cast"),
    "--columns": Meaning("which columns of a table to show", r"`all`"),
    "--command-timeout": Meaning("seconds per remote step", r"remote step"),
    "--connect-timeout": Meaning("ssh/scp seconds", r"ssh/scp"),
    "--date": Meaning("one day of a time range: --from D --to D+1", r"--from D --to D\+1"),
    "--dataset": Meaning("the dataset a bounded worker fills", r"table it fills"),
    "--debug": Meaning("log at DEBUG", r"^Log at DEBUG\. Same as LOG_LEVEL"),
    "--dest": Meaning("the absolute directory on the server", r"absolute"),
    "--dry-run": Meaning("show what would change, change nothing", r"nothing|dry"),
    "--every": Meaning("re-read every N seconds", r"re-read"),
    "--export": Meaning("also write the rows to FILE", r"write output to FILE"),
    "--follow": Meaning("keep streaming log lines until Ctrl-C", r"stream"),
    "--force": Meaning("retired: each use gets its own name", None),
    "--from": Meaning("start of a time range", r"start"),
    "--hdb": Meaning("an HDB directory", r"HDB directory|this database"),
    "--home": Meaning("the ODBC home", r"ODBC home"),
    "--host": Meaning("where a q process listens", r"listen"),
    "--interactive": Meaning("browse the table", r"browse"),
    "--json": Meaning("one JSON document on stdout", r"JSON"),
    "--keep": Meaning("how many newest releases to keep", r"newest"),
    "--last": Meaning("a time range up to now", r"up to now"),
    "--level": Meaning("the lowest log level shown", r"level"),
    "--limit": Meaning("only the most recent N records", r"most recent"),
    "--lines": Meaning("how many lines of a log to show", r"lines"),
    "--live": Meaning("refuse fixtures", r"fixture"),
    "--mode": Meaning("backfill's validate/plan/run mode", r"validate"),
    "--odbc-home": Meaning("a private ODBC setup", r"ODBC"),
    "--output": Meaning("where to write the output", r"write"),
    "--overwrite": Meaning("replace what is already there", r"replace"),
    "--override": Meaning("bypass one named check", r"bypass"),
    "--passwd": Meaning("the q password", r"password"),
    "--port": Meaning("one process's port", r"directly"),
    "--print": Meaning("retired: --show-command", None),
    "--profile": Meaning("a start profile", r"profile"),
    "--show-command": Meaning("show the command, run nothing", r"show"),
    "--python": Meaning("the servers' Python version", r"major\.minor"),
    "--python-runtime": Meaning("a bundled CPython", r"CPython"),
    "--qhome": Meaning("a QHOME", r"QHOME"),
    "--raw": Meaning("retired: --no-gateway / --unresolved", None),
    "--reach": Meaning("reach the server first; save nothing it cannot reach", r"reach"),
    "--reason": Meaning("why an --override is acceptable", r"--override"),
    "--release": Meaning("a release on the server", r"release"),
    "--remote-user": Meaning("run as this account through sudo", r"sudo"),
    "--rename": Meaning("rename a column", r"rename"),
    "--reverse": Meaning("sort descending", r"descending"),
    "--schema": Meaning("tables' columns and their types", r"schema"),
    "--sort": Meaning("sort by a column", r"sort by"),
    "--ssh": Meaning("the ssh destination of a target", r"ssh destination"),
    "--table": Meaning("one table to act on", r"this table"),
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

#: A connector's start options, shared by `connector start` and `connector
#: restart` - one function's parameters, so one meaning by construction.
OPTIONS |= {
    name: Meaning(f"a connector's own start option ({name})", r"\((databento|kafka|crypto)")
    for name in (
        "--api-key", "--brokers", "--databento-dataset", "--group", "--interval-ms",
        "--oms-socket-path", "--poll-interval-ms", "--symbol", "--symbols", "--top-n-levels",
        "--topic", "--tp-credential", "--venues",
    )
}  # fmt: skip

#: Every short flag, and the one long name it abbreviates.
SHORT_FLAGS = {
    "-n": "--dry-run",
    "-y": "--yes",
    "-i": "--interactive",
    "-f": "--follow",
    "-o": "--output",
    "-c": "--columns",
    "-l": "--lines",
}

#: (command, option) -> the issue that renames the option there
RULE3_ALLOWLIST: dict[tuple[str, str], str] = {}


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

REPORTS, CONFIRMS, CHANGES = "reports", "confirms", "changes"

#: A command's kind follows from the Effect its Click class carries
#: (cli/safety.py, #1780), so a new command is classified by declaring it: a
#: read-only one reports, a confirms-on-server one reports its plan and, with
#: --apply, changes the server after asking, and a changes-state one changes.
BY_EFFECT = {
    safety.Effect.READ_ONLY: REPORTS,
    safety.Effect.CONFIRMS_ON_SERVER: CONFIRMS,
    safety.Effect.CHANGES_STATE: CHANGES,
}

#: The third kind, which no Effect states: a command whose output belongs to
#: something else, so --json and --dry-run do not apply. Each says why.
OWN_OUTPUT = {
    "exec": "passes any verb through to torq.sh",
    "job attachment contract export": "prints the contract, a document another machine approves",
    "setup odbc env": "prints shell exports, for eval",
    "target shell": "opens an interactive shell on the server",
}

#: Changes-state commands that report and take no --dry-run, with why: rule 5a
#: holds them, 5b does not.
NO_DRY_RUN = {
    "query": "runs any q on a process - so it may change it, which is its Effect - and "
    "what it changes is the expression's: there is nothing to check without running it",
}


def _kind(path: str) -> str | None:
    """`path`'s kind: OWN_OUTPUT's reason, or the one its Effect states."""
    if path in OWN_OUTPUT:
        return OWN_OUTPUT[path]
    return BY_EFFECT.get(effect_of(LEAVES[path].command))


#: Every command that asks before acting: ROOT asks before running a command on
#: a --target, a confirms-on-server command before --apply writes, and any
#: other that takes --yes says so by taking it.
ASKS = {c.path for c in TREE if c.path in LEAVES and _kind(c.path) == CONFIRMS} | {
    c.path for c in TREE if c.option("--yes") is not None
}

#: The cli modules that call a prompt. A new one means a command that asks:
#: give it --yes/-y, then list the module here.
PROMPTING_MODULES = {
    "attach", "hdb_scope", "install", "install_bundle", "remove", "target", "target_adopt",
    "target_edit",
}  # fmt: skip

#: leaf -> the issue that adds its --json. Empty since #1731: keep it so.
RULE5A_ALLOWLIST: dict[str, str] = {}

#: leaf -> the issue that gives it --dry-run/-n (or, for one that changes
#: nothing, takes its --dry-run away)
RULE5B_ALLOWLIST: dict[str, str] = {}

#: command -> the issue that gives its --yes a -y
RULE5C_ALLOWLIST: dict[str, str] = {}

#: (command, option) -> the issue that folds the bypass into --override NAME --reason
RULE5D_ALLOWLIST: dict[str, str] = {}


def _flag(cmd: Cmd, long: str, short: str | None = None) -> bool:
    param = cmd.option(long)
    return param is not None and (short is None or short in param.opts + param.secondary_opts)


def test_rule5_every_leaf_is_classified():
    """Every leaf has a kind - its Effect, or a row of the third kind - and
    every hand-kept row names a leaf of the kind it claims to except."""
    unmarked = sorted(p for p in LEAVES if _kind(p) is None)
    assert not unmarked, f"no Effect on: {unmarked} - give each its cls= from cli/safety.py"
    gone = sorted((set(OWN_OUTPUT) | set(NO_DRY_RUN)) - set(LEAVES))
    assert not gone, f"no such command any more - delete its row: {gone}"
    stale = sorted(p for p in NO_DRY_RUN if BY_EFFECT.get(effect_of(LEAVES[p].command)) != CHANGES)
    assert not stale, f"NO_DRY_RUN rows that no longer change state - delete them: {stale}"


def test_rule5a_every_reporting_command_has_json():
    """What reports - read-only, a confirms-on-server plan, a NO_DRY_RUN row."""
    found = {
        p
        for p, c in LEAVES.items()
        if (_kind(p) in (REPORTS, CONFIRMS) or p in NO_DRY_RUN) and not _flag(c, "--json")
    }
    _hold("rule 5a", found, RULE5A_ALLOWLIST)


def test_rule5b_dry_run_n_exactly_on_what_changes_state():
    """A confirms-on-server command is a dry run without --apply, so it may
    spell that out (`data alter --dry-run`) or not (`data check`): with -n."""
    found = set()
    for path, cmd in LEAVES.items():
        kind = _kind(path)
        if kind == CHANGES and path not in NO_DRY_RUN and not _flag(cmd, "--dry-run", "-n"):
            found.add(path)
        elif kind == CONFIRMS and _flag(cmd, "--dry-run") and not _flag(cmd, "--dry-run", "-n"):
            found.add(path)
        elif kind not in (CHANGES, CONFIRMS) and _flag(cmd, "--dry-run"):
            found.add(path)
        elif path in NO_DRY_RUN and _flag(cmd, "--dry-run"):
            found.add(path)
    _hold("rule 5b", found, RULE5B_ALLOWLIST)


def test_rule5c_yes_y_exactly_on_what_asks():
    assert ROOT in ASKS, "the root group asks before running a command on a --target"
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


def test_rule5d_every_override_is_named_where_uqs_override_list_finds_it():
    """A command that takes --override takes the names `uqs override list`
    shows, and every name listed is taken by the commands it names (#1732)."""
    from uqs.cli import safety

    taking = {c.path for c in TREE if c.option("--override") is not None}
    named = {command for b in safety.catalog() for command in b.commands}
    assert taking == named
    assert all(c.option("--reason") is not None for c in TREE if c.path in taking)


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
