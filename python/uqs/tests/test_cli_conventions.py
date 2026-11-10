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

import inspect
import io
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import typer.main
from plain_cli import PlainRunner
from typer.testing import CliRunner

import uqs.cli.entry  # noqa: F401 - registers every command on the app
from uqs import duration, paths
from uqs.cells import cell as cell_text
from uqs.cli import lists, rendering, safety, shared, skip_options, vocabulary
from uqs.cli.shared import app
from uqs.cli.target_confirm import changes_the_server, effect_of, trait_of
from uqs.stack import alive, listing
from uqs.stack import procs as stack_procs

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

# The verbs (CORE_VERBS, DOMAIN_VERBS) are declared in uqs/cli/vocabulary.py (#2170).

#: Rule 1: a group named after one of these is a verb with nouns under it.
GROUP_VERBS = set(vocabulary.CORE_VERBS) | {
    "start",
    "stop",
    "restart",
    "delete",
    "get",
    "create",
    "run",
}


# ------------------------------------------------------------- rule 1: groups


#: group path -> the issue that renames it
RULE1_ALLOWLIST: dict[str, str] = {}


def test_rule1_no_group_is_named_after_a_verb():
    found = {path for path in GROUPS if path.split()[-1] in GROUP_VERBS}
    _hold("rule 1", found, RULE1_ALLOWLIST)


# ------------------------------------------------------------- group help


def _summary(command) -> str:
    """The first paragraph of a command's help: what its group's listing shows."""
    return inspect.cleandoc(command.help or "").split("\n\n")[0].replace("\n", " ")


def _named(word: str, text: str) -> bool:
    """`word` in `text` as itself: `check` is not named by `hdb-check`."""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def test_every_group_help_names_every_command_in_it():
    """A group's one line of help, the one `uqs --help` shows beside it, names
    each command the group holds (#1737): `data`'s said "replay, seed,
    hdb-check" while it also held `migrate`. Hidden aliases are not held."""
    unnamed = {
        path: [n for n, sub in group.command.commands.items()
               if not getattr(sub, "hidden", False) and not _named(n, _summary(group.command))]
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
    verbs = set(vocabulary.VERBS)
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
    verbs = set(vocabulary.VERBS)
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
#: had no --last. Any one of the four brings the other three (#2249): `config
#: sources check` took `--last` alone, with a default, and no --from it was
#: held to.
RANGE = ("--from", "--to", "--date", "--last")


def test_a_command_taking_a_time_range_takes_all_of_it():
    partial = {
        path: [o for o in RANGE if c.option(o) is None]
        for path, c in LEAVES.items()
        if any(c.option(o) is not None for o in RANGE) and any(c.option(o) is None for o in RANGE)
    }
    assert not partial


#: The commands that act on whole HDB partitions (#2245): each takes the range
#: in whole days and refuses, never rounds, a bound off a midnight - data
#: seed and data compress used to drop a partial first day and take a partial
#: last one whole, past --to.
WHOLE_DAY_COMMANDS = {"data replay", "data seed", "data compress", "data migrate"}


def test_a_whole_partition_command_takes_whole_days_only():
    whole = {
        path
        for path, c in LEAVES.items()
        if c.option("--from") is not None and "whole days only" in (c.option("--from").help or "")
    }
    assert whole == WHOLE_DAY_COMMANDS
    for path in WHOLE_DAY_COMMANDS:
        days = LEAVES[path].option("--last").help or ""
        assert "whole days" in days and "never in it" in days, path
    # and each resolves it through the one refusal, never a rounding of its own
    for module in ("compress_plan", "seed", "replay", "migrate"):
        text = inspect.getsource(__import__(f"uqs.cli.{module}", fromlist=["_"]))
        assert ".partition_dates(" in text or ".day(" in text, module


#: The `data` commands that do not act on one HDB (#2237): `diff` compares two
#: endpoints' tables through their gateways; `snapshot`'s list and show read
#: the markout configuration snapshots in the status directory.
NOT_ONE_HDB = {"data diff", "data snapshot list", "data snapshot show"}


def test_every_data_command_names_its_hdb_the_same_ways():
    """#2237: check and alter took --hdb and --attachment, compress and replay
    --hdb only (replay's with a help of its own), migrate and seed neither. A
    `data` command that reads or writes one HDB takes both, with
    cli/hdb_scope.py's one help each; one that cannot act on an attachment's
    HDB refuses it saying why (hdb_scope.local_hdb), rather than not knowing it."""
    from uqs.cli import hdb_scope

    def helps(opt) -> str:
        return opt.__metadata__[0].help if hasattr(opt, "__metadata__") else opt.help

    want = {"--hdb": helps(hdb_scope.HdbOpt), "--attachment": helps(hdb_scope.AttachOpt)}
    data = {path: c for path, c in LEAVES.items() if path.startswith("data ")}
    assert NOT_ONE_HDB <= set(data)
    wrong = {
        (path, name): c.option(name) and c.option(name).help
        for path, c in data.items()
        if path not in NOT_ONE_HDB
        for name, help_text in want.items()
        if c.option(name) is None or c.option(name).help != help_text
    }
    assert not wrong, f"data commands naming their HDB another way: {wrong}"


@pytest.mark.parametrize(
    ("module", "argv", "said"),
    [
        ("replay", ["data", "replay"], "data replay cannot take --attachment pb-uat: a replay"),
        (
            "migrate",
            ["data", "migrate", "trade", "--date", "2026-09-13", "--cast", "px"],
            "data migrate cannot take --attachment pb-uat: a migration",
        ),
        (
            "seed",
            ["data", "seed", "--from-runtime", "uqf"],
            "data seed cannot take --attachment pb-uat: a seed",
        ),
        ("compress", ["data", "compress"], "--attachment is for --report"),
    ],
)
def test_a_data_command_that_cannot_reach_an_attachment_says_why(monkeypatch, module, argv, said):
    """#2237: `No such option` was all an operator got for --attachment on
    compress, migrate, replay and seed; each now knows it, and refuses it with
    its reason before reaching anything."""
    mod = __import__(f"uqs.cli.{module}", fromlist=["_"])
    died: list[Exception] = []
    monkeypatch.setattr(mod, "_die", died.append)
    CliRunner().invoke(app, [*argv, "--attachment", "pb-uat"])
    assert len(died) == 1 and isinstance(died[0], paths.UqsUsageError), died
    assert said in str(died[0])


@pytest.mark.parametrize("module", ["replay", "migrate", "seed"])
def test_hdb_and_attachment_together_are_refused_as_check_refuses_them(monkeypatch, module):
    mod = __import__(f"uqs.cli.{module}", fromlist=["_"])
    died: list[Exception] = []
    monkeypatch.setattr(mod, "_die", died.append)
    argv = {
        "replay": ["data", "replay"],
        "migrate": ["data", "migrate", "trade", "--date", "2026-09-13", "--cast", "px"],
        "seed": ["data", "seed", "--from-runtime", "uqf"],
    }[module]
    CliRunner().invoke(app, [*argv, "--attachment", "pb-uat", "--hdb", "/tmp"])
    assert [str(e) for e in died] == [
        "give one of --target, --attachment and --hdb: each names an HDB"
    ]


def test_schema_and_query_reach_a_process_the_same_ways():
    """`query --tunnel` with no `schema --tunnel` (#1858): the two read one
    process, and take the same options to find it."""
    reach = ("--port", "--base-port", "--host", "--tunnel", "--user", "--password-env")
    assert all(LEAVES[p].option(o) is not None for p in ("query", "schema") for o in reach)


def _all_help(cmd: Cmd) -> list[str]:
    """`cmd`'s own help and each of its parameters', flattened."""
    texts = [cmd.command.help or ""] + [getattr(p, "help", "") or "" for p in cmd.command.params]
    return [" ".join(t.split()) for t in texts]


def test_the_running_set_is_called_the_stack():
    """ "stack" 39 times in help, "plant" 6 and "fleet" 3 (#1890): the running
    set of processes is the stack. ("plant" stays where it names the
    tickerplant's tables, plant_tables.q; "runtime" is a configuration.)"""
    found = {c.path for c in TREE for t in _all_help(c) if re.search(r"\bfleet\b", t, re.I)}
    assert not found, f"help calling the running stack a fleet: {sorted(found)}"


def test_a_connector_is_never_called_a_feed():
    """A feed is a q streaming job that only publishes; the Python process
    outside the stack is a connector (#1890), under `uqs connector`."""
    found = {
        c.path
        for c in TREE
        if c.path == "connector" or c.path.startswith("connector ")
        for t in _all_help(c)
        if re.search(r"\bfeeds?\b", t, re.I)
    }
    assert not found, f"connector help that says feed: {sorted(found)}"


#: Plural groups, with why each stays: renamed only if renamed anyway (#1890).
PLURAL_GROUPS = {
    "config sources": "sources.csv's rows; renaming it is churn with nothing else to gain",
    "job runs": "the run ledger; renaming it is churn with nothing else to gain",
}


def test_group_names_are_singular():
    singular_s = {"process"}  # a noun that ends in s and is singular
    plural = {p for p in GROUPS if p.split()[-1].endswith("s") and p.split()[-1] not in singular_s}
    assert plural == set(PLURAL_GROUPS)


def test_one_option_name_has_one_flag_shape():
    """`--start-with-all` was a --x/--no-x pair on `job attachment add` and a
    lone flag on `job new` (#1890): a flag name is a pair everywhere, or
    nowhere."""
    shapes: dict[str, set[bool]] = {}
    for cmd in TREE:
        for p in cmd.options:
            if getattr(p, "is_flag", False):
                shapes.setdefault(_long(p), set()).add(bool(p.secondary_opts))
    mixed = sorted(name for name, seen in shapes.items() if len(seen) > 1)
    assert not mixed, f"flag names with two shapes: {mixed}"


def test_the_guide_lists_the_same_vocabulary():
    """The guide's verb table and the lists above are one vocabulary."""
    text = GUIDE.read_text()
    section = text.split("\n## CLI conventions\n", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    rows = re.findall(r"^\s*\|\s*(`[^|]*`)\s*\|", section, flags=re.M)
    documented = {w for row in rows for w in re.findall(r"`([a-z-]+)`", row)}
    assert documented == set(vocabulary.VERBS)


# ----------------------------------------------------- rule 3: option meaning


@dataclass(frozen=True)
class Meaning:
    says: str  # the one meaning, for the reader of a failure
    pattern: str | None  # matches (ignoring case) the help text that says it; None: retired


#: Every option name more than one command uses, with its one meaning. A help
#: text that does not match the pattern is a second meaning.
OPTIONS = {
    "--allow-dirty": Meaning("retired: --override allow-dirty", None),
    # the frontend's activity history (#2000): one filter each, on list and export alike
    "--action": Meaning("an activity action, or its dotted prefix", r"dotted prefix"),
    "--db": Meaning("the activity history's SQLite file", r"activity history's SQLite file"),
    "--outcome": Meaning("how an activity event ended", r"^only events that ended so"),
    "--recorded-by": Meaning("the browser or the server's audit", r"^only events recorded by"),
    "--request": Meaning("one operation's request id", r"correlated by this request id"),
    "--session": Meaning("one browser session's events", r"browser session id"),
    "--subject": Meaning("what an activity event is about", r"^only events about this name"),
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
    "--dataset": Meaning(
        "the markout dataset a snapshot configures (a job's tables are --outputs, #2102)",
        r"snapshots that configure this markout dataset",
    ),
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
    "--grace": Meaning(
        "how long a start waits before it fails on a process that died",
        r"fail if a process this started has died",
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
    # a log record's id column IS its subsystem channel (#2274): `--channel` is the filter, the
    # old `--id` is refused naming it, and the old `--channel` (subscribe) is `--subscribe`
    "--channel": Meaning("a log channel to show, repeatable", r"^Only show lines on this channel"),
    "--id": Meaning("retired: --channel, a log record's id is its channel (#2274)", None),
    "--subscribe": Meaning(
        "read the published log lines, not the files", r"^Subscribe to the lines"
    ),
    "--level": Meaning("the lowest log level shown", r"this level and above"),
    "--limit": Meaning("show at most N records", r"(most recent|at most) N"),
    "--lines": Meaning("how many lines of a log to show", r"^lines (of|per)\b"),
    "--local": Meaning("one process's local port in a tunnel plan", r"this local port"),
    "--local-offset": Meaning("added to each remote port in a tunnel plan", r"each remote port"),
    "--live": Meaning("retired as its own flag: the other side of --fixture/--live", None),
    "--mode": Meaning("backfill's validate/plan/run mode", r"validate"),
    "--odbc-home": Meaning("a private ODBC setup", r"private ODBC setup"),
    "--on-conflict": Meaning(
        "what a write does with a row already there",
        r"what a (write|copy) (may )?do(es)? with a (row|partition)",
    ),
    "--outputs": Meaning(
        "the tables a job produces: published, filled or written", r"tables it produces"
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
    "--reset-empty": Meaning("rebuild an empty table from its declaration", r"^Rebuild a table"),
    "--reverse": Meaning("sort descending", r"descending"),
    "--schema": Meaning("tables' columns and their types", r"^its table's schema"),
    "--schema-file": Meaning("a tickerplant's schema file", r"^(load|pin) (this|the) schema file"),
    "--sort": Meaning("sort by a column", r"sort by"),
    "--source-version": Meaning(
        "a source release coverage is recorded under", r"^the source_version\b"
    ),
    "--slice": Meaning("the coverage slice of a dataset", r"^the slice of\b"),
    "--start-with-all": Meaning("whether a `start all` starts the process", r"start all"),
    "--ssh": Meaning("the ssh destination of a target", r"ssh destination"),
    "--table": Meaning("one table to act on", r"this table"),
    "--tables": Meaning("a set of tables", r"^comma-separated tables(;|$)|^tables stp1 must carry"),
    "--target": Meaning("run this command on a deployed target", r"instead of this machine"),
    "--timeout": Meaning("seconds for the whole command", r"^seconds\b"),
    "--to": Meaning("end of a time range, exclusive", r"exclusive"),
    "--tplogdir": Meaning("a tickerplant's log directory", r"log directory"),
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
        "--topics", "--tp-credential", "--tp-credential-env", "--venues",
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
#: `--last` is how far back, and `job new`'s `--period`, `--horizon` and
#: `--width` a span written into q (#2121).
_TIME_NAME = re.compile(r"-timeout$|^--(every|soak|deadline|window|last|period|horizon|width)$")
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


#: A grammar spelt out in a help: two or more examples of a duration in a row.
_GRAMMAR_LIST = re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|m|h|d)\b(?:,\s*\d+(?:\.\d+)?(?:ms|s|m|h|d)\b)+")


def test_an_option_documented_as_a_duration_reads_it_with_the_shared_parser():
    """#2121: `--last`'s help said DURATION and its parser refused `30` and
    `1.5m`, which every other DURATION takes. An option whose help or metavar
    says DURATION is parsed by uqs/duration.py, through one of
    vocabulary.PARSERS; one parsed so says DURATION, by its metavar and its
    help; and no help spells out a grammar of its own beside the shared one."""
    unparsed, unsaid, own_grammar = set(), set(), set()
    for cmd in TREE:
        for p in cmd.options:
            said = "DURATION" in (p.help or "") or p.metavar == "DURATION"
            if said and not _is_duration(p):
                unparsed.add((cmd.path, _long(p)))
            if _is_duration(p) and not (p.metavar == "DURATION" and "DURATION" in (p.help or "")):
                unsaid.add((cmd.path, _long(p)))
            if _GRAMMAR_LIST.search(p.help or ""):
                own_grammar.add((cmd.path, _long(p)))
    assert not unparsed, f"documented as a DURATION, parsed otherwise: {sorted(unparsed)}"
    assert not unsaid, f"parsed as a DURATION, not documented as one: {sorted(unsaid)}"
    assert not own_grammar, f"a grammar of its own in the help: {sorted(own_grammar)}"


def test_the_shared_parsers_read_the_shared_grammar():
    """The parsers the gate above looks for are the grammar's, not a copy."""
    assert vocabulary.seconds("1.5m") == duration.seconds("1.5m") == 90.0
    assert vocabulary.whole_seconds("500ms") == duration.whole_seconds("500ms") == 1
    assert vocabulary.timespan("5s") == "0D00:00:05"
    assert vocabulary.timespan("1D") == "1D"


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
    # #2239: an HDB date partition, as kdb+ says it (`.Q.par`); the coverage
    # slice a worker fills was `--partition` too, and is `--slice` now
    "partition": Meaning("an HDB date partition", r"\b(date|day)\b"),
    "slice": Meaning(
        "a coverage slice of a dataset, never a date", r"\bslice\b.*\bnot a date\b|--slice\b"
    ),
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
    kinds = set(listing.LISTABLE_KINDS) - set(lists.MOVED) - set(lists.REFUSED)
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
#: something else, so --json and --dry-run do not apply - declared where the
#: command is, `safety.marked(..., own_output=WHY)` (#1893).
def _marked(trait: str) -> dict[str, str]:
    """{leaf: what it says} for every leaf `safety.marked` gave `trait`."""
    return {p: trait_of(c.command, trait) for p, c in LEAVES.items() if trait_of(c.command, trait)}


OWN_OUTPUT = _marked("own_output")

#: Changes-state commands that report and take no --dry-run, each marked
#: `safety.marked(..., no_dry_run=WHY)`: rule 5a holds them, 5b does not.
NO_DRY_RUN = _marked("no_dry_run")


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
    "remove", "replay", "seed", "target", "target_adopt", "target_config", "target_edit",
    "compress_plan",
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
    """Every leaf has a kind - its Effect, or the third kind - and every
    no_dry_run mark is on a command that changes state."""
    unmarked = sorted(p for p in LEAVES if _kind(p) is None)
    assert not unmarked, f"no Effect on: {unmarked} - give each its cls= from cli/safety.py"
    stale = sorted(p for p in NO_DRY_RUN if BY_EFFECT.get(effect_of(LEAVES[p].command)) != CHANGES)
    assert not stale, f"no_dry_run marks on commands that do not change state: {stale}"


def test_rule5_the_marks_beyond_an_effect_are_declared_where_their_command_is():
    """#1893: which commands own their output, report without a dry run, start
    a process even in a dry run, or act under an option are marks on the
    commands (`safety.marked`), which `uqs --target` reads too - so a new
    command with one of these properties cannot skip being confirmed."""
    assert set(OWN_OUTPUT) == {"activity export", "exec", "job attachment contract export",
                               "setup odbc env", "target shell"}  # fmt: skip
    assert set(NO_DRY_RUN) == {"query"}
    starts = set(_marked("dry_run_starts"))
    assert starts == {"job backfill"}
    for path in starts:
        assert changes_the_server([*path.split(), "w", "--dry-run"]) is True, path
    acting = _marked("acts_when")
    assert acting == {"status": "--interactive"}
    for path, option in acting.items():
        assert LEAVES[path].option(option) is not None, (path, option)
        assert changes_the_server([*path.split(), option]) is True, path
        assert changes_the_server(path.split()) is False, path


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


def test_rule5a_every_json_help_is_the_one_phrasing():
    """Ten phrasings of `--json` across 36 commands (#1859): every one is
    `json_out.JSON_HELP`, then the document's shape, then a note on when it
    applies - one help, built by `json_opt` or led by JSON_HELP."""
    from uqs.cli.json_out import JSON_HELP

    found = {}
    for path, cmd in LEAVES.items():
        param = cmd.option("--json")
        if param is None:
            continue
        text = " ".join((param.help or "").split())
        if text != JSON_HELP and not text.startswith((f"{JSON_HELP}:", f"{JSON_HELP}.")):
            found[path] = text
    assert not found, f"--json helps in another phrasing: {found}"


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


#: Every command whose deletion cannot be undone (docs/guides/uqs.md's rule):
#: each asks first and takes --yes/-y. `clean`, which removes the HDB with the
#: rest of the data directory, asked nothing (#2233).
IRREVERSIBLE = {
    "clean", "deploy prune", "job checkpoint remove", "target remove", "job remove",
    "job attachment remove",
}  # fmt: skip


def test_rule5c_every_irreversible_deletion_asks():
    missing = {p for p in IRREVERSIBLE if p not in ASKS or not _flag(LEAVES[p], "--yes", "-y")}
    assert not missing, sorted(missing)
    guide = (Path(__file__).resolve().parents[3] / "docs/guides/uqs.md").read_text()
    rule = " ".join(guide[guide.index("Every deletion that cannot be undone") :].split()[:60])
    unnamed = {p for p in IRREVERSIBLE if f"`{p}`" not in rule.replace("`uqs ", "`")}
    assert not unnamed, f"the guide's rule does not name {sorted(unnamed)}"


def test_rule5c_every_irreversible_deletion_asks_on_mcp_too():
    """uqs-mcp has no terminal and no --yes, and its `uqs_clean` deleted the
    data directory, the HDB with it, in one call (#2257). A tool mirroring an
    IRREVERSIBLE command is a dry run unless confirm=true - required, never a
    default - and takes no dry_run or yes beside it; no other tool takes it."""
    from mcp_support import tools

    from uqs import mcp, mcp_server

    schemas = {t.name: t.input_schema for t in tools()}
    mirrored = {path: name for name, path in mcp.MIRRORS.items()}
    assert "clean" in mirrored, "uqs_clean is the tool #2257 is about"
    wrong = {}
    for path in IRREVERSIBLE & set(mirrored):
        schema = schemas[mirrored[path]]
        props = schema["properties"]
        default = props.get(mcp_server.CONFIRM, {}).get("default")
        if default is not False or {"dry_run", "yes"} & set(props):
            wrong[path] = sorted(props)
    assert not wrong, f"irreversible tools not a dry run unless {mcp_server.CONFIRM}=true: {wrong}"
    confirming = {
        mcp.MIRRORS[n] for n, s in schemas.items() if mcp_server.CONFIRM in s["properties"]
    }
    assert confirming <= IRREVERSIBLE, sorted(confirming - IRREVERSIBLE)


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


#: Every leaf that takes --json and --yes -> the test that runs it with --json
#: and no --yes, a terminal ready to answer y, and holds it to the one refusal
#: (#2229): exit 1, `{"ok": false}` naming --yes, nothing asked or changed.
#: Three behaviours were one mistake's: exit 2 (data check/alter/compress),
#: exit 1 (job attachment add/remove) and a prompt mid-run (deploy prune).
JSON_WITHOUT_YES = {
    "data alter": "test_hdb_alter_cli.py::test_json_without_yes_is_refused_and_writes_nothing",
    "data check": "test_hdb_alter_cli.py::test_json_without_yes_is_refused_and_writes_nothing",
    "data compress": "test_data_compress.py::test_json_cannot_ask",
    "data migrate": "test_hdb_migrate.py::test_json_without_yes_apply_is_refused",
    "data replay": "test_replay.py::test_json_is_the_plan_and_without_yes_apply_is_refused",
    "data seed": "test_hdb_seed.py::test_json_without_yes_apply_is_refused",
    "deploy prune": (
        "test_deploy_prune.py::test_json_without_yes_is_refused_before_the_server_is_reached"
    ),
    "job attachment add": (
        "test_attach_cli.py::test_json_execution_without_yes_refuses_with_a_json_error"
    ),
    "job attachment remove": (
        "test_attach_cli.py::test_json_execution_without_yes_refuses_with_a_json_error"
    ),
}


def test_rule5f_json_without_yes_is_one_refusal():
    both = {p for p, c in LEAVES.items() if _flag(c, "--json") and _flag(c, "--yes")}
    assert both == set(JSON_WITHOUT_YES), "name the test that holds each to safety.json_needs_yes"
    here = Path(__file__).parent
    for path, test in JSON_WITHOUT_YES.items():
        file, name = test.split("::")
        assert re.search(rf"^def {name}\(", (here / file).read_text(), re.M), (path, test)
    cli_dir = Path(shared.__file__).parent
    worded = {
        f.stem for f in cli_dir.glob("*.py") if "cannot ask for confirmation" in f.read_text()
    }
    assert worded == {"safety"}, "the refusal is safety.json_needs_yes's alone"


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


# ------------------------------------- help derived from its authority (#1893)


def _choices_of(param) -> tuple[str, ...] | None:
    """The fixed values `param` takes: its Choice's, or those of the
    `completion.choices(...)` completer it carries; None for a free value."""
    if hasattr(param.type, "choices"):
        return tuple(str(c) for c in param.type.choices)
    completer = getattr(param, "_custom_shell_complete", None)
    for cell in getattr(completer, "__closure__", None) or ():  # typer wraps the completer
        values = getattr(cell.cell_contents, "choice_values", None)
        if values is not None:
            return tuple(values)
    return None


def _names(value: str, text: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", text) is not None


def _params(cmd: Cmd) -> list:
    return [p for p in cmd.command.params if not getattr(p, "hidden", False)]


def _param(path: str, name: str):
    """The option `name` (`--kind`), or the argument whose name is `name`, of `path`."""
    cmd = LEAVES[path]
    return next(p for p in _params(cmd) if name in (*p.opts, p.name))


def _authorities() -> dict[tuple[str, str], tuple[str, ...]]:
    """(command, option) -> the tuple that declares the values it takes: the
    option's choices and its help are both held to it."""
    from uqs.attach import config, receiver_contract
    from uqs.cli.external import FEEDS
    from uqs.deploy import components, portable
    from uqs.generated import q_facts
    from uqs.model.kinds import SCAFFOLD_KINDS
    from uqs.scaffold import transform
    from uqs.stack import multitail, render, run_modes

    out = {
        ("job backfill", "--on-conflict"): q_facts.IO_STRATEGIES,
        ("job attachment contract approve", "--on-conflict"): receiver_contract.STRATEGIES,
        ("job backfill", "--mode"): run_modes.MODE_OPTION_MODES,
        ("job new", "--kind"): SCAFFOLD_KINDS,
        ("job new", "--transform"): transform.MODES,
        ("query", "--render"): render.RENDERERS,
        ("logs", "--stream"): tuple(multitail.MULTITAIL_STREAMS),
        ("deploy build", "--q-version"): portable.Q_TARGETS,
        ("job attachment add", "--schema-writer"): tuple(config.SCHEMA_WRITERS),
        ("start", "--level"): q_facts.LOG_LEVELS,
        ("restart", "--level"): q_facts.LOG_LEVELS,
        ("logs", "--level"): q_facts.LOG_LEVELS,
    }
    for component in components.COMPONENTS:
        out[("deploy push", f"--{component}-mode")] = components.MODES
    for verb in ("start", "stop", "restart", "status", "logs"):
        out[(f"connector {verb}", "name")] = tuple(FEEDS)
    return out


def test_an_option_with_fixed_values_takes_its_authoritys():
    """#1893: each option's values are its declaring source's - q's strategies
    and modes, the scaffold kinds, the renderers - not a copy beside them."""
    for (path, name), authority in _authorities().items():
        assert _choices_of(_param(path, name)) == tuple(authority), (path, name)


def test_help_naming_fixed_values_names_every_one():
    """An option that takes a fixed set of values names every one in its help,
    so a value its source adds is shown the day it is accepted. `--sort` takes
    a column name, which the listing's own header shows."""
    found = set()
    for cmd in TREE:
        for param in _params(cmd):
            values = _choices_of(param)
            if values is None or "--sort" in param.opts:
                continue
            if missing := [v for v in values if not _names(v, param.help or "")]:
                found.add((cmd.path, param.opts[0] if param.opts else param.name, *missing))
    assert not found, sorted(found)


#: A digit-led default in an option's help - "default 120", "default: 1D".
_STATED_DEFAULT = re.compile(r"\bdefault:?\s+(\d[\w.]*)", re.IGNORECASE)


def _defaults() -> dict[tuple[str, str], object]:
    """(command, option) -> the authority for a default its help states where
    the option's own default is not it: one a feed, a target or the scaffold
    applies when the option is not given."""
    from uqs.cli import source_check
    from uqs.cli.external import FEEDS
    from uqs.deploy import config
    from uqs.scaffold import worker

    out: dict[tuple[str, str], object] = {
        ("job new", "--width"): worker.DEFAULT_WIDTH,
        ("config sources check", "--last"): source_check.DEFAULT_LAST,
    }
    for command in ("deploy push", "deploy rollback", "deploy prune"):
        out[(command, "--command-timeout")] = config.DEFAULT_COMMAND_TIMEOUT
    for command in ("deploy push", "deploy rollback"):
        out[(command, "--verify-timeout")] = config.DEFAULT_VERIFY_TIMEOUT
    for verb in ("start", "restart"):
        for feed in FEEDS.values():
            for option in feed.options:
                if option.default is not None and not option.secret:
                    out[(f"connector {verb}", option.flag)] = option.default
    return out


def _rendered(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


def test_a_default_stated_in_help_is_the_one_applied():
    """#1893: a default a help states is the option's own, or the value its
    authority applies when it is not given - never a number typed beside it."""
    authorities, found = _defaults(), set()
    for cmd in TREE:
        for param in _params(cmd):
            name = param.opts[0] if param.opts else param.name
            for stated in _STATED_DEFAULT.findall(param.help or ""):
                applied = authorities.get((cmd.path, name), param.default)
                # a DURATION states its seconds with their unit: "default 180s"
                if stated not in (_rendered(applied), f"{_rendered(applied)}s"):
                    found.add((cmd.path, name, stated, _rendered(applied)))
    assert not found, f"(command, option, stated, applied): {sorted(found)}"


def test_a_default_authority_names_a_live_option():
    gone = {k for k in _defaults() if k[0] not in LEAVES or LEAVES[k[0]].option(k[1]) is None}
    assert not gone, gone


def test_job_new_kind_tags_are_shapes():
    """#1893: the "(backfill)" ending a `job new` option's help is rendered from
    create_kinds.SHAPES, the table that refuses the option on any other kind;
    every SHAPES row is an option `job new` has, and so checks."""
    from uqs.cli.create_kinds import SHAPES, for_kinds
    from uqs.model.kinds import SCAFFOLD_KINDS

    cmd = LEAVES["job new"]
    for option, kinds in SHAPES.items():
        param = cmd.option(option)
        assert param is not None, f"SHAPES names {option}, which `job new` does not take"
        assert param.help.endswith(for_kinds(option)), (option, param.help)
        assert set(kinds) <= set(SCAFFOLD_KINDS), (option, kinds)
    tag = re.compile(rf"\((?:(?:{'|'.join(SCAFFOLD_KINDS)})(?:, )?)+\)$")
    untagged = [p for p in cmd.options if _long(p) not in SHAPES and tag.search(p.help or "")]
    assert not untagged, [_long(p) for p in untagged]


def test_job_new_refuses_a_misplaced_option_it_was_never_told_about(monkeypatch):
    """`used` is read from Click, so every SHAPES row is checked: a backfill
    given --outputs was scaffolded with it dropped (#1893). A normalizer and a
    horizon job publish their own NAME, so --outputs shapes neither (#2102)."""
    from typer.testing import CliRunner

    from uqs.cli import create

    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    argv = ["job", "new", "x", "--kind", "normalizer", "--outputs", "t", "--dry-run"]
    CliRunner().invoke(app, argv)
    assert refused == ["--outputs does not apply to --kind normalizer"]


def test_status_presets_are_one_table():
    """#1893: `status --columns`'s presets - resolved, completed, refused and
    described - are summary_columns.SUMMARY_PRESETS, so the help says what each shows."""
    from uqs.cli import completion
    from uqs.cli.summary_columns import SUMMARY_PRESETS, resolve_columns
    from uqs.stack.listing import SUMMARY_ALL_COLUMNS

    help_ = LEAVES["status"].option("--columns").help
    for name, columns in SUMMARY_PRESETS.items():
        assert resolve_columns(name) == list(columns)
        assert f"`{name}`" in help_ and name in completion.summary_columns("")
        if columns != SUMMARY_ALL_COLUMNS:
            assert ", ".join(columns) in help_, name


def test_connector_help_is_rendered_from_each_option():
    """#1893: a connector option's default and variable are its FeedOption's,
    rendered by it - never retyped into its help."""
    from uqs.cli.external import FEEDS

    for feed in FEEDS.values():
        for option in feed.options:
            if option.env:
                assert f"${option.env}" not in option.help, option.flag
            if option.default is not None and not option.secret:
                assert f"default {option.default}" not in option.help, option.flag
            param = LEAVES["connector start"].option(
                option.flag + ("-env" if option.secret else "")
            )
            assert option.described in param.help, option.flag


def test_mode_notes_describe_every_mode():
    from uqs.stack import run_modes

    assert set(run_modes.NOTES) == set(run_modes.MODES)
    assert run_modes.DEFAULT_MODE in run_modes.MODES


def test_the_receivers_row_limit_is_the_contracts():
    """scripts/dev/remote_receiver.q's `-max_rows` default is the contract's
    DEFAULT_MAX_ROWS (#1893): the q receiver cannot import it, so it is held."""
    from uqs.attach.receiver_contract import DEFAULT_MAX_ROWS

    script = (GUIDE.parents[2] / "scripts" / "dev" / "remote_receiver.q").read_text()
    assert re.search(rf'"J"\$first o`max_rows; {DEFAULT_MAX_ROWS}\]', script)
    assert f"(default {DEFAULT_MAX_ROWS})" in script


# ------------------------------------- option values, one mechanism (#1884)


def _completer(param):
    """The function `param` completes with, unwrapped from Typer's adapter."""
    completer = getattr(param, "_custom_shell_complete", None)
    for cell in getattr(completer, "__closure__", None) or ():
        if callable(cell.cell_contents):
            return cell.cell_contents
    return None


#: (command, option) -> why its fixed values are a completer, not a Choice.
NOT_A_CHOICE = {
    ("job backfill", "--mode"): "takes the retired value dry-run for one release "
    "(rename_expiry: `uqs job backfill --mode dry-run`), which its help no longer shows",
    ("job new", "--kind"): "takes the retired kind `external` for one release (#1890), which a "
    "Choice would refuse before its callback names `connector`",
    ("deploy build", "--q-version"): "one name, one type (cli/vocabulary.py): on `deploy push` "
    "--q-version is a free REQUIREMENT, >=4.1",
}


def test_an_option_with_fixed_values_is_a_choice():
    """#1884: an option taking one of a fixed set is a Choice (cli/choices.py
    ChoiceOpt) - its type, completion and help from one tuple - not a free
    str with a completer and validation by hand. `--sort` takes a column the
    listing's header names; an argument may refuse with its own message."""
    found = {
        (cmd.path, _long(p))
        for cmd in TREE
        for p in cmd.options
        if "--sort" not in p.opts
        and (cmd.path, _long(p)) not in NOT_A_CHOICE
        and not hasattr(p.type, "choices")
        and getattr(_completer(p), "choice_values", None) is not None
    }
    assert not found, sorted(found)


def _concept(param) -> str | None:
    """Which concept a parameter names, by its flag or its metavar."""
    opts, metavar = set(param.opts), (param.metavar or "").rstrip(".").rstrip(",")
    if "--attachment" in opts:
        return "attachment"
    if opts & {"--target", "--tunnel"} or metavar == "TARGET":
        return "target"
    if "--profile" in opts:
        return "profile"
    if metavar == "JOB":
        return "job"
    if metavar == "TABLE" or opts & {"--table", "--tables"}:
        return "table"
    if metavar == "SOURCE":
        return "source"
    return None


def _name(fn) -> str | None:
    """A completer's name: Typer hands each command a copy, so not its identity."""
    return f"{fn.__module__}.{fn.__qualname__}" if fn is not None else None


def _concept_completers() -> dict[str, set[str | None]]:
    from uqs.cli import completion as c

    named = {
        "attachment": (c.attachments,),
        "target": (c.targets,),
        "profile": (c.profiles,),
        "job": (c.jobs, c.streaming_jobs, c.backfill_workers),
        "table": (c.plant_table, c.plant_tables),
        "source": (c.sources, c.source_list),
    }
    return {concept: {_name(f) for f in fns} for concept, fns in named.items()}


#: (command, parameter) -> why it names a concept's NEW value, so offers none.
NAMES_A_NEW_ONE = {
    ("job new", "name"): "the job it scaffolds does not exist yet",
    ("job new", "source"): "the source a new backfill reads is usually new too",
    ("target add", "name"): "the target it declares does not exist yet",
    ("target adopt", "name"): "the target it declares does not exist yet",
}


def test_each_concept_completes_with_its_completer():
    """#1884: an attachment, a target, a profile, a job, a table or a source
    completes the same way on every command - shared aliases in
    cli/concepts.py carry the completer."""
    completers, found = _concept_completers(), set()
    for cmd in TREE:
        for param in cmd.command.params:
            if getattr(param, "hidden", False) or (cmd.path, param.name) in NAMES_A_NEW_ONE:
                continue
            concept = _concept(param)
            if concept and _name(_completer(param)) not in completers[concept]:
                found.add((cmd.path, param.name, concept))
    assert not found, sorted(found)


def test_the_target_argument_is_one_definition():
    """#1884: a TARGET is TARGET everywhere and completes targets; a command
    that only reads a server falls back to the latest deployment. #2243: one
    that changes a server never does - `deploy rollback` and `prune` take
    --ssh/--dest in its place, so their TARGET is optional, but their help
    claims no default and a bare call is refused."""
    for path in ("target check", "target shell", "deploy status"):
        param = next(p for p in LEAVES[path].command.params if p.metavar == "TARGET")
        assert not param.required, path
        assert LATEST in _help(param), path
    for path in ("target set", "target unset", "target remove"):
        param = next(p for p in LEAVES[path].command.params if p.metavar == "TARGET")
        assert param.required, path
    for path in ("deploy rollback", "deploy prune"):
        param = next(p for p in LEAVES[path].command.params if p.metavar == "TARGET")
        assert LATEST not in _help(param), path


#: What a TARGET's help says when the command falls back to the latest deployment.
LATEST = "default: the most recent deployment"


def _help(param) -> str:
    return getattr(param, "help", None) or ""


#: Commands that change state and still fall back to the latest deployment, each with why.
LATEST_THOUGH_IT_CHANGES = {
    "target shell": "it does nothing by itself: it says which server it connected to, then a "
    "person types - the shell is marked as changing state because what they type can",
}


def test_only_a_command_that_reads_a_server_falls_back_to_the_latest_deployment():
    """#2243: `deploy rollback`'s help promised it would act on whichever server was
    deployed last - a command that changes a server must be told which. Every TARGET
    that claims the fallback is on a command that only reads."""
    found = {
        c.path
        for c in TREE
        if c.path in LEAVES
        for p in c.command.params
        if p.metavar == "TARGET" and LATEST in _help(p)
        and effect_of(c.command) is not safety.Effect.READ_ONLY
    }  # fmt: skip
    _hold("a TARGET that changes a server falls back to nothing", found, LATEST_THOUGH_IT_CHANGES)


def test_a_comma_separated_option_says_so_in_its_metavar():
    """#1884: one style, `NAME,...`, wherever an option takes a comma-separated list."""
    found = {
        (cmd.path, _long(p), p.metavar)
        for cmd in TREE
        for p in cmd.options
        if re.search(r"comma[- ]separated", p.help or "", re.IGNORECASE)
        and not (p.metavar or "").endswith(",...")
    }
    assert not found, sorted(found)


def test_a_default_said_in_help_is_not_shown_twice():
    """#1884: a help that states its default does not also get `[default: X]`
    - `status --timeout` read "(default 120) ... [default: 120.0]"."""
    found = {
        (cmd.path, _long(p))
        for cmd in TREE
        for p in cmd.options
        if re.search(r"\bdefault", p.help or "", re.IGNORECASE)
        and p.show_default is not False
        and not p.is_flag
        and p.default not in (None, (), [])
    }
    assert not found, sorted(found)


#: "default $NAME", "Default from $NAME", "default: $NAME" - a variable as a fallback.
_ENV_FALLBACK = re.compile(r"\bdefault(?: from|:)?\s+\$([A-Z][A-Z0-9_]*)", re.IGNORECASE)


def test_a_variable_named_as_a_fallback_is_the_one_read():
    """#1884: a `$NAME` a help names as its default is the option's own envvar
    - or, for a connector option, the variable its FeedOption reads (it
    resolves given > $env > default itself, external/feeds.py)."""
    from uqs.cli.external import FEEDS

    feed_env = {
        flag: o.env
        for f in FEEDS.values()
        for o in f.options
        for flag in (o.flag, f"{o.flag}-env")
        if o.env
    }
    found = set()
    for cmd in TREE:
        for p in cmd.options:
            for name in _ENV_FALLBACK.findall(p.help or ""):
                connector = cmd.path.startswith("connector ") and feed_env.get(_long(p)) == name
                if p.envvar != name and not connector:
                    found.add((cmd.path, _long(p), name, p.envvar))
    assert not found, sorted(found)


#: The plant's variables a publisher reads beside q (external/connection.py).
_PUBLISHERS = __import__("uqs.external.connection").external.connection.TP_CREDENTIAL_ENV


def test_a_uqf_variable_in_help_is_one_q_reads():
    """#1884: one prefix per side. uqs's own variables are UQS_; a UQF_ one
    (q_facts.CFG_ENV_PREFIX) is the plant's - q reads it, or a publisher that
    feeds the plant beside q does (UQF_TP_CREDENTIAL, held in
    external/connection.py) - and is named in help only then."""
    from uqs.generated import q_facts

    q_text = "\n".join(p.read_text() for p in (GUIDE.parents[2] / "src").rglob("*.q"))
    named = {
        (cmd.path, name)
        for cmd in TREE
        for p in cmd.options
        for name in re.findall(r"\$((?:UQS|UQF)_[A-Z0-9_]+)", p.help or "")
    }
    stray = {
        (path, name)
        for path, name in named
        if name.startswith(q_facts.CFG_ENV_PREFIX) and name not in q_text + _PUBLISHERS
    }
    assert not stray, sorted(stray)


#: A day written out in a help: 2026-09-13, 2026.09.13, YYYY-MM-DD.
_DAY_SHAPED = re.compile(r"\d{4}[-.]\d{2}[-.]\d{2}|YYYY")


def test_every_date_shaped_parameter_uses_the_shared_forms():
    """#1884: a parameter taking a day names timerange's forms - both spellings,
    which timerange.parse_day accepts - rather than one of its own."""
    from uqs.timerange import BOUND_FORMS, DAY_FORMS

    found = {
        (cmd.path, p.name)
        for cmd in TREE
        for p in cmd.command.params
        if not getattr(p, "hidden", False)
        and _DAY_SHAPED.search(p.help or "")
        and DAY_FORMS not in (p.help or "")
        and BOUND_FORMS not in (p.help or "")
    }
    assert not found, sorted(found)


@pytest.mark.parametrize("partition", ["2026-01-02", "2026.01.02"])
def test_data_migrate_takes_both_day_forms(monkeypatch, partition):
    """`data migrate TABLE PARTITION` refused 2026.01.02, the form on disk (#1884);
    the day is `--date` now (#2239)."""
    from datetime import date

    from typer.testing import CliRunner

    from uqs.stack import hdb_migrate

    seen = []

    def migrate(_paths, _table, day, *_a, **_k):
        seen.append(day)
        raise SystemExit(0)

    monkeypatch.setattr(hdb_migrate, "migrate", migrate)
    CliRunner().invoke(app, ["data", "migrate", "trade", "--date", partition])
    assert seen == [date(2026, 1, 2)]


def test_data_migrate_hdb_names_the_root_it_migrates(monkeypatch, tmp_path):
    """#2237: `data migrate` could only reach this runtime's HDB."""
    from uqs.stack import hdb_migrate

    seen = []

    def migrate(*_a, hdb=None, **_k):
        seen.append(hdb)
        raise SystemExit(0)

    monkeypatch.setattr(hdb_migrate, "migrate", migrate)
    argv = ["data", "migrate", "trade", "--date", "2026-01-02", "--hdb", str(tmp_path)]
    CliRunner().invoke(app, argv)
    assert seen == [tmp_path]


def test_data_migrate_refuses_a_positional_date_naming_date(monkeypatch):
    """#2239: a positional PARTITION read as a slice key to anyone who knows
    `--partition` from `job new`; the day is the shared `--date`."""
    from uqs.stack import hdb_migrate

    monkeypatch.setattr(hdb_migrate, "migrate", lambda *a, **k: pytest.fail("migrated"))
    argv = ["data", "migrate", "trade", "2026-01-02", "--cast", "px"]
    result = PlainRunner().invoke(app, argv)  # CI renders usage errors in colour
    assert result.exit_code == 2
    said = " ".join(re.sub("[│╭╮╰╯─]", " ", result.output).split())
    assert "is now `data migrate TABLE --date D`" in said


def test_every_override_name_is_one_spelling():
    """#1884: `uqs override list` showed `q-version-check`, `accept-digest` and
    `TORQ_VERSION:COMPONENT` side by side; every name is lower case, words
    joined by `-`, with `:COMPONENT` where it is waived per process."""
    pattern = re.compile(r"^[a-z][a-z0-9-]*(:COMPONENT)?$")
    bad = [g.name for g in safety.catalog() if not pattern.fullmatch(g.name)]
    assert not bad, bad


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


# ------------------------------------------------------ help as written (#1883)

#: The placeholder a positional is shown as: one word per thing a command acts
#: on, upper case, the same word wherever that thing is meant (#1883).
VOCABULARY = {
    "PROC", "JOB", "TARGET", "TABLE", "DATASET", "SOURCE", "KIND", "EXPR", "FIELD",
    "VALUE", "PARTITION", "RUNTIME", "RUN", "ATTACHMENT", "FILE", "CONNECTOR",
    "PACKAGE", "ARTIFACT", "COMPONENT", "BUNDLE", "KEY", "CHANGE", "NEW", "PUSH", "SNAPSHOT_ID",
}  # fmt: skip
#: Placeholders the vocabulary replaced: in help they may only name a command's
#: own option (`--override NAME`), never stand for one of the things above.
RETIRED_PLACEHOLDERS = {"WORKER", "PROCNAME", "PROCS", "NAME"}
#: The longest first help paragraph: the one line a group's listing shows.
SUMMARY_MAX = 110
_PLACEHOLDER = re.compile(r"(?<![\w$-])([A-Z][A-Z_]{2,})(?![\w-])")


def _visible_params(command) -> list:
    return [p for p in command.params if not getattr(p, "hidden", False)]


def _help_texts(command) -> list[str]:
    """Everything a reader of `--help` reads: the command's help, its params'."""
    return [command.help or ""] + [
        p.help for p in _visible_params(command) if getattr(p, "help", None)
    ]


def _own_metavars(command) -> set[str]:
    return {w for p in command.params if p.metavar for w in re.findall(r"[A-Z][A-Z_]+", p.metavar)}


def test_every_visible_argument_is_an_upper_case_word_of_the_vocabulary():
    """`query [PROC] [expr]`, `job gaps {job}` beside `job backfill {JOB}`,
    `target ... {name}` beside `target log [NAME]`: one style, one word per
    thing (#1883)."""
    found = {
        (path, p.name, p.metavar)
        for path, c in LEAVES.items()
        for p in _visible_params(c.command)
        if p.param_type_name == "argument"
        and not (p.metavar and set(re.split(r"=", p.metavar.removesuffix("..."))) <= VOCABULARY)
    }
    assert not found, f"positionals outside the vocabulary: {sorted(found)}"


def test_a_placeholder_in_help_is_one_of_the_commands_own():
    """A vocabulary word the prose uses is a positional or an option of the
    same command, and a retired one (WORKER, NAME, ...) is nowhere unless the
    command takes it: `job receive` showed `uqs job backfill WORKER --attach
    NAME`, `job new --source` said it defaults to NAME (#1883). Code spans may
    show another command's line, so only the retired words are held there;
    `OLD=NEW` and `OLD:NEW` describe a value's shape, not a positional."""
    found = set()
    for path, c in LEAVES.items():
        own = _own_metavars(c.command)
        for text in _help_texts(c.command):
            text = re.sub(r"\b[A-Z]+[=:][A-Z]+\b", " ", text)
            code = " ".join(re.findall(r"`([^`]*)`", text))
            prose = re.sub(r"`[^`]*`", " ", text)
            for word in set(_PLACEHOLDER.findall(prose)):
                if word in VOCABULARY | RETIRED_PLACEHOLDERS and word not in own:
                    found.add((path, word))
            for word in set(_PLACEHOLDER.findall(code)):
                if word in RETIRED_PLACEHOLDERS and word not in own:
                    found.add((path, word))
    assert not found, sorted(found)


def test_every_first_help_paragraph_is_one_short_line():
    """The line a group's listing shows: `list`'s ran to 421 characters (#1883)."""
    long = {
        c.path: len(_summary(c.command)) for c in TREE if len(_summary(c.command)) > SUMMARY_MAX
    }
    assert not long, long


def test_no_help_names_an_issue_number():
    """An issue number tells an operator nothing: it is the tree's history (#1883)."""
    found = {c.path for c in TREE for t in _help_texts(c.command) if re.search(r"#\d+", t)}
    assert not found, sorted(found)


def test_every_option_help_starts_with_a_capital_or_a_backtick():
    found = {
        (c.path, _long(p))
        for c in TREE
        for p in c.options
        if p.help and not re.match(r"[A-Z`'\"$]", p.help.strip())
    }
    assert not found, sorted(found)


def _brackets(text: str) -> list[str]:
    return [" ".join(m.split()) for m in re.findall(r"\[[^\[\]\n]*\]", text)]


@pytest.mark.parametrize("cmd", [c for c in TREE if c.path != ROOT], ids=lambda c: c.path)
def test_rendered_help_keeps_every_bracket_in_its_source(cmd):
    """Typer read help as Rich markup, so `"silent": [process]` rendered as
    `"silent": ,` and `[defaults]` vanished from `target unset`: the JSON shapes
    `--help` documents were wrong (#1883)."""
    result = CliRunner().invoke(app, [*cmd.path.split(), "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    # the text a reader sees: CI turns colour on (GITHUB_ACTIONS), and Rich's
    # highlighter then puts escape codes inside `[{table, rows}]`
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
    shown = " ".join(re.sub(r"[│╭╮╰╯─]", " ", plain).split())
    missing = [
        token
        for text in _help_texts(cmd.command)
        for token in _brackets(" ".join(text.split()))
        if token not in shown
    ]
    assert not missing, missing


def test_h_is_help_everywhere():
    for argv in (["-h"], ["job", "-h"], ["status", "-h"]):
        result = CliRunner().invoke(app, argv)
        assert result.exit_code == 0 and "Usage" in result.output, argv


# ------------------------------------------------- output into a pipe (#1883)

#: Every list command, as argv: each of `uqs list`'s kinds and every noun's own.
LIST_COMMANDS = [
    *(["list", kind] for kind in sorted(listing.LISTABLE_KINDS) if kind not in lists.REFUSED),
    ["target", "list"],
    ["runtime", "list"],
    ["job", "list"],
    ["job", "attachment", "list"],
    ["override", "list"],
]


def _uqs_piped(*argv: str) -> subprocess.CompletedProcess[str]:
    """`uqs argv` with stdout a pipe, as `uqs ... | grep` has it."""
    drop = ("UQF_PEACHQ", "UQF_Q_IMPL", "UQF_PEACHQ_REASON", "COLUMNS", "LOG_REGEX")
    env = {k: v for k, v in os.environ.items() if k not in drop} | {"NO_COLOR": "1"}
    return subprocess.run(
        [sys.executable, "-m", "uqs", *argv],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=180,
        check=False,
    )


def test_a_piped_console_neither_cuts_nor_wraps(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    made = rendering.console()
    assert made.width == rendering.UNBOUNDED_WIDTH and made.soft_wrap


@pytest.fixture
def piped(monkeypatch):
    """The CLI's console as a pipe gets it, whatever pytest's own stdout is."""
    with monkeypatch.context() as m:
        m.setattr(sys, "stdout", io.StringIO())
        made = rendering.console()
    monkeypatch.setattr(shared.console, "width", made.width)
    monkeypatch.setattr(shared.console, "soft_wrap", made.soft_wrap)


#: A noun's own list -> the kind of `uqs list` it lists.
_NOUN_KINDS = {
    command.removeprefix("uqs "): kind for kind, command in {**lists.MOVED, **lists.REFUSED}.items()
}


def _cells(argv: list[str]) -> list[str]:
    """The text of every cell the command's table holds, from its rows."""
    if argv[0] == "override":
        rows = lists._bypasses()
    else:
        kind = argv[1] if argv[0] == "list" else _NOUN_KINDS[" ".join(argv)]
        rows = listing.list_items(paths.default_paths(), kind)
    texts = (cell_text(v) for row in rows for v in row.values())
    return [text for text in texts if text]


@pytest.mark.parametrize("argv", LIST_COMMANDS, ids=" ".join)
def test_every_cell_of_a_list_reaches_a_pipe_whole(piped, argv, monkeypatch):
    """`uqs status | grep -c discovery1` was 0: the column showed `dis…`, as
    `list processes` cut `segmentedtickerplant` - an 80-column guess for a
    terminal there is none of (#1883). What runs is held still: the rows are
    read twice, and another test starting a process between the reads changed
    `runtime list`'s running count."""
    monkeypatch.setattr(alive, "running", lambda *_a, **_k: set())
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    missing = [text for text in _cells(argv) if text not in result.stdout]
    assert not missing, missing


def test_status_names_every_process_whole_into_a_pipe():
    run = _uqs_piped("status", "--probe-timeout", "0")
    names = listing.configured_ports(paths.default_paths())
    missing = [name for name in names if not re.search(rf"\b{re.escape(name)}\b", run.stdout)]
    assert not missing, (missing, run.stderr[-400:])


def test_a_scalar_is_one_line_a_shell_can_take():
    """`ls "$(uqs config show rdb1 U)"` failed: the path came back over three lines."""
    run = _uqs_piped("config", "show", "rdb1", "U")
    assert run.returncode == 0, run.stderr
    value = stack_procs.get_process_config(paths.default_paths(), "rdb1")["U"]
    assert len(value) > 80, "the case this covers is a value wider than a terminal"
    assert run.stdout == value + "\n"


def test_a_start_line_is_not_broken_into_a_pipe():
    run = _uqs_piped("start", "--dry-run", "rdb1")
    assert run.returncode == 0, run.stderr
    (line,) = [ln for ln in run.stdout.splitlines() if "-procname rdb1" in ln]
    assert line.split()[0].endswith("nohup") or "torq.q" in line
    assert "torq.q" in line and "-proctype rdb" in line


@pytest.mark.parametrize("old", ["--publishes", "--dataset", "--writes"])
def test_job_new_refuses_the_retired_output_options(old):
    """`--publishes`, `--dataset` and `--writes` are all `--outputs` now (#2102);
    none survives as an alias, so each is an unknown option."""
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["job", "new", "x", old, "t", "--dry-run"])
    assert result.exit_code == 2
    assert "No such option" in result.output


# ------------------------------------------- #2274: a log's id is its channel


class _Said:
    """What `_die` hands the log, kept: loguru does not write into CliRunner's output."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


def _refusal(monkeypatch, *argv: str) -> str:
    """What `uqs logs ARGV` says when it refuses (exit 2): Click's usage box or the
    command's own error, whitespace and box lines folded."""
    said = _Said()
    monkeypatch.setattr(shared, "log", said)
    result = PlainRunner().invoke(app, ["logs", *argv])  # CI renders usage errors in colour
    assert result.exit_code == 2, result.output
    box = re.sub("[│╭╰╮╯─]", " ", result.output)
    return " ".join(" ".join([box, *said.messages]).split())


def test_logs_id_is_refused_naming_channel_with_no_alias(monkeypatch):
    """`--id` filtered TorQ's id column, which is the channel now: refused, not accepted
    beside `--channel` (#2274, and the repo's no-aliases rule)."""
    for argv in (["--id", "fx_positions"], ["--id=fx_positions"]):
        assert "`--id` was renamed `--channel`" in _refusal(monkeypatch, *argv)
    assert "--id" not in {o for p in _params(LEAVES["logs"]) for o in p.opts}


def test_the_old_channel_meaning_is_refused_naming_subscribe(monkeypatch):
    """`--channel` took no value and subscribed; it takes a channel now. The old use is
    answered with where it went, whether it is last or followed by another option."""
    for argv in (["--channel"], ["--channel", "--level", "ERROR"], ["rdb1", "--channel", "-f"]):
        assert "renamed `--subscribe`" in _refusal(monkeypatch, *argv), argv


def test_logs_channel_takes_only_the_declared_channels(monkeypatch):
    from uqs.generated import q_facts

    said = _refusal(monkeypatch, "--channel", "fx_positions")
    assert "is not a channel" in said
    assert all(channel in said for channel in q_facts.LOG_CHANNELS)


def test_logs_subscribe_is_the_subscription_and_channel_the_filter():
    options = {o: p for p in _params(LEAVES["logs"]) for o in p.opts}
    assert options["--subscribe"].is_flag and not options["--channel"].is_flag
    assert options["--channel"].multiple, "several channels match any of them"
