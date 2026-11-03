"""The `uqs` CLI conventions (#1738), held over the real Typer tree.

docs/guides/uqs.md#cli-conventions states six rules; this walks every command
`uqs.cli.entry` registers and enforces the four a tree can show:

- rule 1: no group is named after a verb;
- rule 3: one option name has one meaning and one value type, one short flag
  one long name, one word in an option name one meaning, and one concept one
  option name (#1855);
- rule 4: every leaf is named by a verb from the vocabulary;
- rule 5: `--json` on what reports, `--dry-run/-n` on what changes state,
  `--yes/-y` on what asks, and no bypass but `--override NAME --reason` - or
  the `--skip-<id>` the guardrail registry generates as its other spelling
  (cli/skip_options.py, #1826), never one written by hand.

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
from uqs.cli import safety, shared, skip_options, vocabulary
from uqs.cli.shared import app
from uqs.cli.target_confirm import effect_of

ROOT = "uqs"  # the label the root group's own options are reported under
GUIDE = Path(__file__).resolve().parents[3] / "docs" / "guides" / "uqs.md"

#: The issues allowed to hold an exception open: #1738's sub-issues, those the
#: audit that built these lists filed beside them, and the naming audit's
#: (#1855-#1859).
ISSUES = {f"#{n}" for n in (*range(1727, 1741), *range(1855, 1860))}


# --------------------------------------------------------------------------- tree


@dataclass(frozen=True)
class Cmd:
    path: str  # "job runs list"; ROOT for the root group
    is_group: bool
    command: Any  # the click command typer built (typer vendors its own click)

    @property
    def options(self) -> list:
        # a hidden option is a renamed one's old spelling (rule 6): on its way out;
        # a leaf's generated --skip-<id>s are its options as much as its signature's
        generated = (
            skip_options.generated(self.command, self.path)
            if isinstance(self.command, skip_options.SkipOptions)
            else ()
        )
        return [
            p
            for p in [*self.command.params, *generated]
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
    ("deploy check", "--proc"): "a process the check expects beyond the profile's",
    ("job new", "--proc"): "the process the new job runs in: a property of what it scaffolds",
    ("logs", "--worker"): "with --attachment: whose run in the attachment - a worker or a receiver",
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


#: The verbs that list records, newest or by key: each takes every list flag
#: (#1858) - `deploy history` and `target log` took only --limit.
LISTING_VERBS = {"list", "history", "log"}


def test_every_list_takes_the_same_output_flags():
    lists = [c for p, c in LEAVES.items() if p.split()[-1] in LISTING_VERBS]
    missing = {
        c.path: [f for f in LIST_FLAGS if c.option(f) is None]
        for c in lists
        if any(c.option(f) is None for f in LIST_FLAGS)
    }
    assert set(missing) <= set(LIST_FLAGS_PENDING), missing


#: Lists that do not take every output flag yet, and the issue that adds them.
#: Empty since #1858: keep it so.
LIST_FLAGS_PENDING: dict[str, str] = {}


#: Rule 1's exceptions: the stack's own lifecycle, and the views of it that
#: read a running stack. `uqs list` was the last other top-level verb (#1858).
TOP_LEVEL = {"start", "stop", "restart", "status", "logs", "clean", "exec", "query", "graph",
             "schema"}  # fmt: skip


def test_rule1_only_the_lifecycle_and_its_views_are_top_level_verbs():
    top = {path for path in LEAVES if " " not in path}
    assert top == TOP_LEVEL


def test_a_noun_that_sets_a_setting_can_unset_it():
    """`config set` had no `config unset` beside `target set`/`unset` (#1858)."""
    verbs: dict[str, set[str]] = {}
    for path in LEAVES:
        group, _, verb = path.rpartition(" ")
        verbs.setdefault(group, set()).add(verb)
    assert not [g for g, v in verbs.items() if "set" in v and "unset" not in v]


#: Options named after a verb of the vocabulary, on a command whose own verb
#: is another, with why - `job receive --stop` made a verb its opposite (#1858).
VERB_FLAGS = {
    ("job new", "--check"): "scaffolds a quality check with the job: a thing made, not a verb run",
    ("deploy push", "--restart"): "how the push replaces a deployment already there: it stays a "
    "push",
    ("job attachment add", "--start"): "whether the publisher runs once added - the pair "
    "--start/--no-start (#1857); the command stays an add",
}


def test_no_flag_turns_its_command_into_another_verb():
    verbs = set(CORE_VERBS) | set(DOMAIN_VERBS)
    found = {
        (path, _long(p))
        for path, c in LEAVES.items()
        for p in c.options
        if getattr(p, "is_flag", False)
        and _long(p)[2:] in verbs
        and _long(p)[2:] != path.split()[-1]
    }
    assert found == set(VERB_FLAGS)


#: A time range is given in full wherever one is taken (#1858): `data replay`
#: had no --last.
RANGE = ("--from", "--to", "--date", "--last")


def test_a_command_taking_a_time_range_takes_all_of_it():
    partial = {
        path: [o for o in RANGE if c.option(o) is None]
        for path, c in LEAVES.items()
        if c.option("--from") is not None and any(c.option(o) is None for o in RANGE)
    }
    assert not partial


def test_schema_and_query_reach_a_process_the_same_ways():
    """`query --tunnel` with no `schema --tunnel` (#1858): the two read one
    process, and take the same options to find it."""
    reach = ("--port", "--base-port", "--host", "--tunnel", "--user", "--password-env")
    assert all(LEAVES[p].option(o) is not None for p in ("query", "schema") for o in reach)


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
    "--allow-dirty": Meaning("retired: --override allow-dirty", None),
    "--apply": Meaning("make the changes; without it, a dry run", r"without it, a dry run"),
    "--arch": Meaning("the servers' CPU architecture", r"x86_64"),
    "--attach": Meaning("retired: --attachment - a verb naming a noun (#1855)", None),
    "--attachment": Meaning(
        "a saved attachment, by name", r"^the (saved(, verified)? )?attachment whose\b"
    ),
    "--base-port": Meaning("the stack's base port", r"base port"),
    "--break-lock": Meaning("retired: --override break-lock", None),
    "--bundle": Meaning("a sidecar bundle folder", r"bundle folder|sidecar bundle"),
    "--cast": Meaning("cast a column's type", r"cast"),
    "--columns": Meaning("which columns of a table to show", r"`all`"),
    "--command-timeout": Meaning("seconds per remote step", r"remote step"),
    "--connect-timeout": Meaning("ssh/scp seconds", r"ssh/scp"),
    "--date": Meaning("one day of a time range: --from D --to D+1", r"--from D --to D\+1"),
    "--dataset": Meaning("the dataset a bounded worker fills", r"table it fills"),
    "--debug": Meaning("log at DEBUG", r"^Log at DEBUG\. Same as LOG_LEVEL"),
    "--dest": Meaning(
        "the absolute directory on the server", r"(directory|root) on the server|installs, absolute"
    ),
    "--dry-run": Meaning(
        "show what would change, change nothing",
        r"\b(change|write|start|run|build|install|store|remove|delete|stop)\w* nothing\b",
    ),
    "--every": Meaning("re-read every DURATION", r"^with -i, re-read every DURATION"),
    "--stop-on-exit": Meaning(
        "with --follow: stop what it started when the stream ends", r"stop what this started"
    ),
    "--export": Meaning("also write the rows to FILE", r"write output to FILE"),
    "--follow": Meaning("keep streaming log lines until Ctrl-C", r"streaming (new|every)"),
    "--force": Meaning("retired: each use gets its own name", None),
    "--from": Meaning("start of a time range", r"^start of the time range"),
    "--hdb": Meaning("an HDB directory", r"HDB directory|this database"),
    "--fixture": Meaning(
        "a source with no credential may run on its fixture; --live refuses it",
        r"^let a (worker whose )?source (with|has) no credential",
    ),
    "--via-gateway": Meaning(
        "route through the gateway, or (--no-gateway) not", r"through \.gw\.syncexec"
    ),
    "--home": Meaning("retired: --odbc-home (#1857)", None),
    "--host": Meaning("where a q process listens", r"^(host|where)\b.*\blistens?\b"),
    "--interactive": Meaning("browse the table", r"^browse the table"),
    "--json": Meaning("one JSON document on stdout", r"^Print (one )?JSON"),
    "--keep": Meaning("how many newest releases to keep", r"newest (n )?releases"),
    "--last": Meaning("a time range up to now", r"up to now"),
    "--level": Meaning("the lowest log level shown", r"this level and above"),
    "--limit": Meaning("show at most N records", r"(most recent|at most) N"),
    "--lines": Meaning("how many lines of a log to show", r"^lines (of|per)\b"),
    "--live": Meaning("retired as its own flag: the other side of --fixture/--live", None),
    "--mode": Meaning("backfill's validate/plan/run mode", r"validate"),
    "--odbc-home": Meaning("a private ODBC setup", r"private ODBC setup"),
    "--on-conflict": Meaning(
        "what a write does with a row already there",
        r"what a (write|copy) (may )?do(es)? with a (row|partition)",
    ),
    "--output": Meaning("a FILE to write instead of stdout", r"^write it to this file"),
    "--output-dir": Meaning("a directory to write into", r"^directory to write"),
    "--proc": Meaning("a process, as an option", r"^(a|the) process (the check expects|that runs)"),
    "--overwrite": Meaning(
        "replace what is already there", r"^replace .*(already holds|that differ)"
    ),
    "--override": Meaning("bypass one named check", r"bypass"),
    "--passwd": Meaning("retired: --password-env NAME", None),
    "--password-env": Meaning("the variable holding the password", r"variable holding"),
    "--port": Meaning("one process's port", r"directly"),
    "--print": Meaning("retired: --show-command", None),
    "--profile": Meaning(
        "a start profile", r"named start set|start profile|profile (the release started|to start)"
    ),
    "--q-version": Meaning("the servers' kdb+ version", r"kdb\+ version|q must be this version"),
    "--show-command": Meaning(
        "show the command, run nothing", r"show its command without running it"
    ),
    "--python": Meaning("the servers' Python version", r"major\.minor"),
    "--python-component": Meaning("a bundled CPython", r"CPython"),
    "--q-home": Meaning("a QHOME", r"^(QHOME on the server|the managed QHOME)"),
    "--raw": Meaning("retired: --no-gateway / --unresolved", None),
    "--reach": Meaning(
        "reach the server first; save nothing it cannot reach", r"^reach the server first"
    ),
    "--reason": Meaning("why an --override is acceptable", r"--override"),
    "--release": Meaning("a release on the server, by name - not a path", r"^the release\b"),
    "--remote-user": Meaning("run as this account through sudo", r"sudo"),
    "--rename": Meaning("rename a column", r"rename"),
    "--reverse": Meaning("sort descending", r"descending"),
    "--schema": Meaning("tables' columns and their types", r"^its table's schema"),
    "--schema-file": Meaning("a tickerplant's schema file", r"^(load|pin) (this|the) schema file"),
    "--sort": Meaning("sort by a column", r"sort by"),
    "--start-with-all": Meaning("whether a `start all` starts the process", r"start all"),
    "--ssh": Meaning("the ssh destination of a target", r"ssh destination"),
    "--table": Meaning("one table to act on", r"this table"),
    "--tables": Meaning("a set of tables", r"^comma-separated tables(;|$)|^tables stp1 must carry"),
    "--target": Meaning("run this command on a deployed target", r"instead of this machine"),
    "--timeout": Meaning("seconds for the whole command", r"^seconds\b"),
    "--to": Meaning("end of a time range, exclusive", r"exclusive"),
    "--tunnel": Meaning("a deploy target reached through an ssh tunnel", r"ssh tunnel"),
    "--trace": Meaning(
        "log every query the source is sent", r"every query the (source is sent|fetch sends)"
    ),
    "--user": Meaning("the q user", r"user to log in"),
    "--verify-timeout": Meaning("how long the processes have to become ready", r"become ready"),
    "--version": Meaning("print uqs's version", r"uqs's version"),
    "--yes": Meaning(
        "do not ask",
        r"^(do not ask (before|first)|ask nothing|with --\w+: (do not ask|run without asking))",
    ),
}

#: A connector's start options, shared by `connector start` and `connector
#: restart` - one function's parameters, so one meaning by construction.
OPTIONS |= {
    name: Meaning(f"a connector's own start option ({name})", r"\((databento|kafka|crypto)")
    for name in (
        "--api-key", "--brokers", "--databento-dataset", "--group", "--interval-ms",
        "--oms-socket-path", "--poll-interval-ms", "--symbol", "--symbols", "--top-n-levels",
        "--topic", "--tp-credential", "--tp-credential-env", "--venues",
    )
}  # fmt: skip

#: The generated `--skip-<id>` options (cli/skip_options.py, #1826, #1918): each is
#: one guardrail's declaration on every command that declaration names, so one
#: meaning by construction - the --override it spells, which its help names.
OPTIONS |= {
    skip_options.flag(g): Meaning(f"waive {g.id}", "--override " + re.escape(g.name))
    for g in safety.registry()
    if g.skip_flag
}

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


# ------------------------------------------------- the vocabulary (#1857)


def _is_duration(param) -> bool:
    return getattr(param.type, "func", None) in vocabulary.PARSERS


def _term_type(param) -> str:
    """A parameter's type as cli/vocabulary.py's TERMS spell it."""
    if _is_duration(param):
        return "duration"
    if getattr(param, "is_flag", False):
        return "flag"
    return param.type.name.removesuffix(" range")


def test_the_vocabulary_holds_every_command_to_its_names_and_types():
    """cli/vocabulary.py's TERMS: an option a term names has the term's type
    on every command, and no command takes a spelling a term retired."""
    wrong, retired = set(), set()
    terms = {t.name: t for t in vocabulary.TERMS}
    gone = {old: t.name for t in vocabulary.TERMS for old in t.retired}
    for cmd in TREE:
        for param in cmd.options:
            names = [o for o in param.opts + param.secondary_opts if o.startswith("--")]
            for name in names:
                term = terms.get(name)
                if term is not None and _term_type(param) != term.type:
                    wrong.add((cmd.path, name, _term_type(param), term.type))
                if name in gone:
                    retired.add((cmd.path, name, gone[name]))
        for param in cmd.command.params:
            if param.param_type_name == "argument" and _metavar(param) in gone:
                retired.add((cmd.path, _metavar(param), gone[_metavar(param)]))
    assert not wrong, f"(command, option, its type, the vocabulary's): {sorted(wrong)}"
    assert not retired, f"(command, retired spelling, its name now): {sorted(retired)}"


#: Options that take a span of time in a unit of their own, with why. A
#: connector's start options are the connector's: declared by the feed
#: (external/feeds.py), possibly an installed bundle's, and passed to it as
#: typed - their unit is in their name (`--interval-ms`).
OWN_UNIT = {"--interval-ms", "--poll-interval-ms"}

#: What makes an option a span of time: its name, or its help naming a unit.
_TIME_NAME = re.compile(r"-timeout$|^--(every|soak|deadline|window)$")
_TIME_HELP = re.compile(r"\b(seconds|minutes|milliseconds)\b", re.I)


def test_every_duration_is_a_duration():
    """Minutes on one command, seconds as a float or an int on others (#1857):
    a span of time is a DURATION everywhere - seconds bare, or `500ms`, `5m`."""
    found = {
        (cmd.path, _long(p))
        for cmd in TREE
        for p in cmd.options
        if (_TIME_NAME.search(_long(p)) or _TIME_HELP.search(p.help or ""))
        and _long(p) not in OWN_UNIT
        and not _is_duration(p)
    }
    assert not found, f"spans of time that are no DURATION: {sorted(found)}"


def test_a_negative_flag_has_its_positive():
    """`--no-start` beside `--health/--no-health` (#1857): a flag that turns a
    default off is the off side of a pair, so the default can be said too."""
    lone = {
        (cmd.path, _long(p))
        for cmd in TREE
        for p in cmd.options
        if getattr(p, "is_flag", False) and _long(p).startswith("--no-")
    }
    assert not lone, f"--no-X flags with no --X: {sorted(lone)}"


def test_every_shared_option_name_is_in_the_vocabulary_or_the_registry():
    """A shared name the registry gives one meaning, and the vocabulary one
    type: a term's name needs no row of OPTIONS's to be held."""
    terms = {t.name for t in vocabulary.TERMS if t.name.startswith("--")}
    assert terms <= set(OPTIONS) | set(_option_uses()) | terms


# ------------------------------------------------ one grammar per option (#1856)

#: A pair an option takes, as its help leads with it: `OLD:NEW`, `COL:TYPE`.
_PAIR = re.compile(r"^([A-Z][A-Z0-9]*)([=:])([A-Z][A-Z0-9|]*|[a-z|]+)\b")


def test_a_pair_is_spelt_with_a_colon_everywhere():
    """`data alter --rename OLD=NEW` beside `data migrate --rename OLD:NEW`
    (#1856): an option taking a pair - a column and its new name, type or
    attribute - spells it `A:B` on every command."""
    found = {
        (cmd.path, _long(p), m.group(0))
        for cmd in TREE
        for p in cmd.options
        if (m := _PAIR.match(p.help or "")) and m.group(2) != ":"
    }
    assert not found, f"pairs not spelt A:B: {sorted(found)}"


def test_tables_are_chosen_one_way():
    """`--table` repeatable on one command, one value on another, `--tables`
    comma-separated on a third (#1856): tables are chosen by a repeatable
    `--table` everywhere (NAME,NAME works in one too)."""
    found = {
        (cmd.path, _long(p))
        for cmd in TREE
        for p in cmd.options
        if _long(p) in ("--table", "--tables") and (_long(p) != "--table" or not p.multiple)
    }
    assert not found, f"tables chosen another way: {sorted(found)}"


def test_one_command_fills_a_missing_hdb_table_and_the_other_runs_it():
    """`deploy push --fix-hdb` (`--override fix-hdb` since #1852) and `data
    check --apply` both filled missing HDB partitions (#1856): data check is
    the one that fills, and the push runs the release's own - its override
    says so."""
    (fix,) = [g for g in safety.registry() if g.name == "fix-hdb"]
    assert "`uqs data check --apply`" in fix.description
    assert LEAVES["data check"].option("--apply") is not None


#: Pairs (option, other option) whose leading clauses a registered pattern may
#: both match, each with why: the two are kin, not one meaning spelt twice.
PATTERN_KIN: dict[tuple[str, str], str] = {
    ("--password-env", "--tp-credential-env"): "each names the variable holding a secret - "
    "the q password, a connector's tickerplant credential (#1853)",
}


def _said(text: str) -> str:
    """What an option IS: its help up to the first default, aside or sentence."""
    return re.split(r"[;(]|\. |Default|default:", text, maxsplit=1)[0]


def test_rule3_each_meaning_pattern_tells_its_option_apart():
    """A one-word pattern - `schema`, `release`, `attach` - matched every use
    of its name whatever it meant, so `data replay --schema FILE` passed as
    `job new --schema SPEC`'s meaning (#1855). A pattern must state the
    meaning closely enough that the leading clause of no option with another
    name matches it: what holds two meanings apart is then the test's, not
    the reader's."""
    found = set()
    uses = _option_uses()
    for name, meaning in OPTIONS.items():
        if meaning.pattern is None or name not in uses or "(databento" in meaning.pattern:
            continue
        for other, by in uses.items():
            if other != name and any(
                re.search(meaning.pattern, _said(t), re.I) for t in by.values()
            ):
                found.add((name, other))
    _hold("rule 3 (a pattern tells its option apart)", found, PATTERN_KIN)


def _value_type(param) -> str:
    """What an option takes: `flag`, `duration`, or its Click type (a range is its type)."""
    if getattr(param.type, "func", None) in vocabulary.PARSERS:
        return "duration"
    return "flag" if param.is_flag else param.type.name.removesuffix(" range")


def _option_params() -> dict[str, list]:
    """option name -> its parameter on each command that takes it."""
    params: dict[str, list] = {}
    for cmd in TREE:
        for param in cmd.options:
            params.setdefault(_long(param), []).append(param)
    return params


#: Option names whose uses take a value of more than one type, or one value on
#: one command and many on another -> the issue that gives them one (#1855).
#: Empty since #1856 and #1857: keep it so.
RULE3_TYPE_ALLOWLIST: dict[str, str] = {}

#: Names one command takes once and another many times, by what each does with
#: it - the type is still one. Each says why.
ONE_OR_MANY = {
    "--bundle": "job new scaffolds into one bundle; a release or a runtime carries several",
    "--on-conflict": "contract approve permits several policies; a write uses one",
    "--proc": "deploy check expects several; a new job runs in one",
}


def test_rule3_one_option_name_has_one_value_type():
    """`--release` was a release's name on `deploy rollback` and a path on
    `target adopt`; `--schema` a table's columns on `job new` and a file on
    `data replay` (#1855). A name takes one type of value everywhere, and the
    same number of them."""
    found = set()
    for name, uses in _option_params().items():
        types = {_value_type(p) for p in uses}
        many = {p.multiple for p in uses}
        if len(types) > 1 or (len(many) > 1 and name not in ONE_OR_MANY):
            found.add(name)
    _hold("rule 3 (one value type)", found, RULE3_TYPE_ALLOWLIST)


#: A word in an option's name, or in a positional's metavar, means one thing
#: wherever it appears (#1855): `--q-target` named a kdb+ version where every
#: other "target" is a deploy target, and `--torq-runtime` a TorQ artifact
#: where every other "runtime" is a uqs runtime. A use of the word whose help
#: does not match the pattern is a second meaning; a None pattern retires it.
WORDS = {
    "target": Meaning("a deploy target, from deploy_targets.toml", r"\btargets?\b"),
    "runtime": Meaning("a uqs runtime: uqf, torq, peachq, ...", r"\buqf\b"),
    "attach": Meaning("retired: the noun is attachment (--attachment)", None),
    "stack-id": Meaning("retired: the stack's base port is --base-port", None),
}

#: (command, option or metavar) -> the issue that renames it
RULE3_WORD_ALLOWLIST: dict[tuple[str, str], str] = {}


def _words(name: str) -> set[str]:
    """The words of `--q-target` or `TARGET...`, and each pair of them (`stack-id`)."""
    parts = [w for w in re.split(r"[-_.\s]+", name.lower().strip("-[].")) if w]
    return {*parts, *("-".join(pair) for pair in zip(parts, parts[1:], strict=False))}


def test_rule3_a_word_in_an_option_name_has_one_meaning():
    found = set()
    for cmd in TREE:
        named = [(_long(p), p) for p in cmd.options]
        named += [(_metavar(p), p) for p in cmd.command.params if p.param_type_name == "argument"]
        for name, param in named:
            text = " ".join((getattr(param, "help", "") or "").split())
            for word in _words(name) & set(WORDS):
                pattern = WORDS[word].pattern
                if pattern is None or not re.search(pattern, text, re.I):
                    found.add((cmd.path, name))
    _hold("rule 3 (one meaning per word)", found, RULE3_WORD_ALLOWLIST)


#: A concept -> the one option name it has (#1855): the stack's base port was
#: also `job attachment add --stack-id`, and the servers' kdb+ version
#: `deploy build --q-target`. An option whose help says it IS the concept - in
#: its first clause, before a default or an aside - is called by its name.
CONCEPTS = {
    "--base-port": r"(?<![-<\w])base port\b",
    "--q-version": r"\bkdb\+ version\b",
}

#: (command, option) -> the issue that renames it
RULE3_CONCEPT_ALLOWLIST: dict[tuple[str, str], str] = {}


def test_rule3_one_concept_has_one_option_name():
    found = set()
    for cmd in TREE:
        for param in cmd.options:
            text = " ".join((param.help or "").split())
            said = _said(text)
            for name, pattern in CONCEPTS.items():
                if re.search(pattern, said, re.I) and _long(param) != name:
                    found.add((cmd.path, _long(param)))
    _hold("rule 3 (one name per concept)", found, RULE3_CONCEPT_ALLOWLIST)


def test_rule3_a_list_kind_is_not_another_nouns_name():
    """`uqs list overrides` listed process_overrides.csv beside `uqs override
    list`, the checks a command bypasses (#1855): a kind of the stack's own
    tables must not share its word with a noun group, which owns that word."""
    from uqs.cli import lists
    from uqs.stack import listing

    nouns = {path.split()[-1] for path in GROUPS}
    kinds = set(listing.LISTABLE_KINDS) - set(lists.MOVED)
    clash = {k for k in kinds if {k, k.removesuffix("s"), k.removesuffix("es")} & nouns}
    assert not clash, f"`uqs list` kinds named like a noun group: {sorted(clash)}"


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
    "attach", "cleanup", "deploy_server", "hdb_scope", "install", "install_bundle", "migrate",
    "remove", "target", "target_adopt", "target_edit",
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

#: The option names that read as a bypass (#1852): each lifts a refusal, and so
#: is `--override NAME --reason TEXT` instead - a guardrail `uqs override list`
#: shows, warned about and recorded with its reason.
BYPASS_SHAPED = ("--allow-", "--break-", "--any-", "--even-if-", "--accept-", "--fix-")

#: (command, option) -> why an option shaped like a bypass lifts no refusal.
NOT_A_BYPASS: dict[tuple[str, str], str] = {
    ("job attachment add", "--accept-risks"): "a confirmation, like --yes: it waives "
    "nothing itself - each risk it confirms was waived by --override CODE:COMPONENT --reason",
}


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


#: `--json` commands that print a JSON object a line rather than one document,
#: with why (#1859).
JSON_LINES = {"config sources check": "one object per source, as each source's check ends"}


def test_rule5a_every_json_help_is_the_one_phrasing():
    """Ten phrasings of `--json` across 36 commands (#1859): every one is
    `json_out.JSON_HELP`, then the document's shape, then a note on when it
    applies - one help, built by `json_opt` or led by JSON_HELP."""
    from uqs.cli.json_out import JSON_HELP, JSON_LINES_HELP

    found = {}
    for path, cmd in LEAVES.items():
        param = cmd.option("--json")
        if param is None:
            continue
        lead = JSON_LINES_HELP if path in JSON_LINES else JSON_HELP
        text = " ".join((param.help or "").split())
        if text != lead and not text.startswith((f"{lead}:", f"{lead}.")):
            found[path] = text
    assert not found, f"--json helps in another phrasing: {found}"
    assert set(JSON_LINES) <= set(LEAVES)


def test_rule5b_dry_run_n_exactly_on_what_changes_state():
    """A confirms-on-server command is a dry run without --apply, so it may
    spell that out (`data alter --dry-run`) or not (`data check`): with -n."""
    found = set()
    for path, cmd in LEAVES.items():
        kind = _kind(path)
        dry_by_default = cmd.option("--apply") is not None  # rule 5e: --apply writes
        if (
            kind == CHANGES
            and path not in NO_DRY_RUN
            and not dry_by_default
            and not _flag(cmd, "--dry-run", "-n")
        ):
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
    """Every question is asked through safety.ask, confirm or choose (#1885):
    one default (No), one refusal without a terminal, one exit code for a no.
    Only safety itself calls a prompt."""
    cli_dir = Path(shared.__file__).parent
    texts = {f.stem: f.read_text() for f in cli_dir.glob("*.py")}
    direct = {
        stem
        for stem, text in texts.items()
        if re.search(r"typer\.confirm\(|Confirm\.ask\(|Prompt\.ask\(|\.prompt\(", text)
    }
    assert direct == {"safety"}, "a question asked around safety.ask"
    asking = {
        stem for stem, text in texts.items() if re.search(r"safety\.(ask|confirm|choose)\(", text)
    }
    assert asking == PROMPTING_MODULES


def test_rule5e_one_write_model_per_kind_of_change():
    """#1854: a command that writes with --apply is dry by default, asks before
    --apply writes and takes --yes/-y for that question, and takes no visible
    --dry-run - it is what it does anyway; a confirms-on-server command is one."""
    found = set()
    for path, cmd in LEAVES.items():
        applies = cmd.option("--apply") is not None
        if _kind(path) == CONFIRMS and not applies:
            found.add((path, "--apply missing"))
        if applies and not _flag(cmd, "--yes", "-y"):
            found.add((path, "--yes/-y missing"))
        if applies and cmd.option("--dry-run") is not None:
            found.add((path, "--dry-run beside --apply"))
    assert not found, sorted(found)


def _bypasses(tree: list[Cmd]) -> set[tuple[str, str]]:
    """(command, option) for each bypass that is not `--override NAME --reason`
    nor a `--skip-<id>` the guardrail registry generated."""
    found = set()
    for cmd in tree:
        for param in cmd.options:
            name = _long(param)
            reason_taking = (param.metavar or "").upper() == "REASON" and name != "--reason"
            handwritten_skip = name.startswith("--skip-") and not skip_options.is_generated(param)
            shaped = name.startswith(BYPASS_SHAPED) and (cmd.path, name) not in NOT_A_BYPASS
            if handwritten_skip or name == "--override-reason" or reason_taking or shaped:
                found.add((cmd.path, name))
    return found


def test_rule5d_a_bypass_shaped_flag_is_caught(monkeypatch):
    """#1852: `--allow-dirty` beside `--override` was invisible to the rule."""
    small = typer.Typer()

    @small.command("push", cls=safety.ChangesState)
    def push(
        allow_dirty: bool = typer.Option(False, "--allow-dirty"),
        fix_it: bool = typer.Option(False, "--fix-it"),
        dry_run: bool = typer.Option(False, "--dry-run"),
    ) -> None: ...

    @small.command("other", cls=safety.ChangesState)
    def other() -> None: ...

    tree = list(_walk(typer.main.get_command(small), []))
    assert _bypasses(tree) == {("push", "--allow-dirty"), ("push", "--fix-it")}


def test_rule5d_every_exemption_names_a_live_option():
    gone = {k for k in NOT_A_BYPASS if k[0] not in LEAVES or LEAVES[k[0]].option(k[1]) is None}
    assert not gone, f"NOT_A_BYPASS rows for options that are gone - delete them: {gone}"


def test_rule5d_a_bypass_is_override_name_reason():
    _hold("rule 5d", _bypasses(TREE), RULE5D_ALLOWLIST)


def test_rule5d_a_generated_skip_passes_and_a_handwritten_one_does_not(monkeypatch):
    """The same rule over a small tree: a guardrail declared with skip_flag
    gives `job run` --skip-test-guard, which passes; `job run` also spells a
    --skip-by-hand of its own, which does not."""
    from uqs import guardrails

    declared = guardrails.Guardrail("test-guard", ("job run",), "skips it", skip_flag=True)
    monkeypatch.setattr(safety, "registry", lambda: (declared,))
    small, job = typer.Typer(), typer.Typer()
    small.add_typer(job, name="job")

    @job.command("run", cls=safety.ChangesState)
    def run(
        override: safety.OverrideOpt = None,
        reason: safety.ReasonOpt = None,
        by_hand: bool = typer.Option(False, "--skip-by-hand"),
    ) -> None: ...

    tree = list(_walk(typer.main.get_command(small), []))
    leaf = next(c for c in tree if c.path == "job run")
    assert leaf.option("--skip-test-guard") is not None
    assert _bypasses(tree) == {("job run", "--skip-by-hand")}


def test_rule5d_every_generated_skip_is_on_the_commands_it_names():
    """A guardrail declared with skip_flag names leaves that exist, are
    command classes that generate it, and take --override and --reason
    (skip_options.generated refuses one that does not)."""

    for g in safety.registry():
        if not g.skip_flag:
            continue
        for command in g.commands:
            assert command in LEAVES, f"guardrail {g.id} names no command `uqs {command}`"
            assert LEAVES[command].option(skip_options.flag(g)) is not None, (g.id, command)


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
        RULE3_TYPE_ALLOWLIST,
        RULE3_WORD_ALLOWLIST,
        RULE3_CONCEPT_ALLOWLIST,
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
