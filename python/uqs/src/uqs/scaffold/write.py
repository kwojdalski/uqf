"""Applying a scaffold plan: writing its files, and placing each appended line.

Split from scaffold/jobs.py when it crossed the 400-line threshold this package
holds its modules to - the seam `scaffold/plan.py` already names: jobs.py BUILDS
a plan, this APPLIES one. The two change for different reasons - a new kind of
job there, a new file with its own idea of where an appended line goes here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

from uqs import paths
from uqs.paths import STACK_TABLES_LIST, STACK_TABLES_TEST, UqsError
from uqs.scaffold import external, profile
from uqs.scaffold.docs import SHOWCASE_PAGE, with_card
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode

CONVERTER = Path("scripts") / "portable" / "flatten_contexts.py"


def flat_source(text: str, repo_root: Path) -> str:
    """A scaffolded src/ file with no `\\d` more than a root switch (#1411).

    The templates write a job as `\\d .qpipe.job.<name>` and bare names, which is
    how it reads; this tree keeps no q with a context two levels deep, so the
    file is written the way every other declaration in src/ is: each name fully
    qualified from the root. The repository's own converter does it - the same
    lexer that wrote the rest of the tree - so a template cannot disagree with
    it. The tree being written to may be a partial copy without the converter, so
    the one uqs itself was installed from is the fallback.
    """
    path = next(
        (p for p in (repo_root / CONVERTER, paths.repo_root() / CONVERTER) if p.is_file()), None
    )
    if "\\d ." not in text or path is None:
        return text
    # Named by the converter's own path and registered fresh each call: its
    # dataclasses resolve through sys.modules while it executes, and a name
    # shared across trees kept the first tree's converter for every later one
    # in the same process (#1601).
    resolved = path.resolve()
    name = "flatten_contexts_scaffold_" + hashlib.sha1(str(resolved).encode()).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, resolved)
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout
        return text
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    converter = module.Converter("4.0", False)
    converter.collect("scaffold.q", text)
    converter.propagate()
    result = converter.convert("scaffold.q", text)
    if result.refusals:
        raise UqsError(
            "the scaffolded file cannot be written with fully qualified names: "
            + "; ".join(f"line {r.line}: {r.reason}" for r in result.refusals)
        )
    return result.output


def apply_plan(plan: ScaffoldPlan, repo_root: Path) -> list[Path]:
    """Write a plan, or refuse the whole thing.

    Every target is checked BEFORE anything is written: a scaffold that
    created three files and then refused the fourth would leave a tree that
    neither loads nor reverts cleanly, and the half of it that did land
    registers itself on load.
    """
    for action in plan.actions:
        target = repo_root / action.path
        if action.mode is WriteMode.CREATE and target.exists():
            raise UqsError(f"{action.path} already exists - pick another name, or remove it first")
        if action.mode is WriteMode.APPEND and not target.is_file():
            raise UqsError(f"{action.path} does not exist, so there is nothing to append to")

    written: list[Path] = []
    for action in plan.actions:
        target = repo_root / action.path
        match action.mode:
            case WriteMode.CREATE:
                target.parent.mkdir(parents=True, exist_ok=True)
                is_src = action.path.parts[:1] == ("src",) and action.path.suffix == ".q"
                target.write_text(flat_source(action.body, repo_root) if is_src else action.body)
            case WriteMode.APPEND:
                target.write_text(_appended(target.read_text(), action))
            case _:  # pragma: no cover - unreachable while WriteMode has two members
                # Named rather than folded into the append branch, which is
                # what the old `else` did: a third mode added later would have
                # been silently appended instead of refused.
                raise UqsError(f"unknown write mode {action.mode!r} for {action.path}")
        written.append(action.path)
    return written


def _appended(existing: str, action: FileAction) -> str:
    """`existing` with the action's body added where that file wants it.

    Some files take their content somewhere other than the end, and refuse
    rather than guess when they do not look the way this expects - appending to
    the end would be syntactically valid and register nothing.

    test_stack_tables.q: the table belongs at the end of the `expected` list.

    The Showcase page: a card belongs at the end of its section - see
    scaffold/docs.py's with_card.
    """
    if action.path == STACK_TABLES_TEST:
        return _with_expected_table(existing, action.body)
    if action.path == profile.PROFILES_FILE:
        return profile.apply(existing, action)
    if action.path == SHOWCASE_PAGE:
        return with_card(existing, action.body, action.anchor)
    if action.path == external.DEPENDENCIES_FILE:
        return external.with_producer(existing, action.body)
    return existing.rstrip("\n") + "\n" + action.body


def _with_expected_table(existing: str, entry: str) -> str:
    """`test_stack_tables.q` with `entry` added to its `expected:` list.

    One line of backtick symbols with no terminator, so the entry goes at its
    end. Refusals rather than guesses: exactly one `expected:` line, and
    the table not already on it - a name the q file already defines means the
    scaffold would be redefining someone else's table.
    """
    lines = existing.splitlines(keepends=True)
    found = [i for i, line in enumerate(lines) if line.startswith(f"{STACK_TABLES_LIST}:")]
    if len(found) != 1:
        raise UqsError(
            f"{STACK_TABLES_TEST} has {len(found)} lines starting "
            f"`{STACK_TABLES_LIST}:`, expected 1 - "
            "this scaffold cannot tell where a table goes, so add it by hand"
        )
    index = found[0]
    line = lines[index].rstrip("\n").rstrip()
    listed = line.removeprefix(f"{STACK_TABLES_LIST}:").split("`")
    if entry.lstrip("`") in listed:
        raise UqsError(
            f"{entry} is already in {STACK_TABLES_TEST}'s expected list - "
            "that table exists, so pick another name"
        )
    lines[index] = line + entry + "\n"
    return "".join(lines)
