"""Applying a scaffold plan: writing its files, and placing each appended line.

Split from scaffold/jobs.py when it crossed the 400-line threshold this package
holds its modules to - the seam `scaffold/plan.py` already names: jobs.py BUILDS
a plan, this APPLIES one. The two change for different reasons - a new kind of
job there, a new file with its own idea of where an appended line goes here.
"""

from __future__ import annotations

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
    spec = importlib.util.spec_from_file_location("flatten_contexts_scaffold", path)
    if spec is None or spec.loader is None:  # pragma: no cover - a broken checkout
        return text
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    module = sys.modules[spec.name]
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

    Every file's resulting content is computed BEFORE anything is written: a
    scaffold that created three files and then refused the fourth would leave
    a tree that neither loads nor reverts cleanly, and the half of it that did
    land registers itself on load. An append can refuse on what its file holds
    (a profile line past the limit, a list it cannot find), so existence alone
    is not enough to check (#1568). A write that fails part way is undone.
    """
    contents: dict[Path, str] = {}
    created: set[Path] = set()
    for action in plan.actions:
        target = repo_root / action.path
        match action.mode:
            case WriteMode.CREATE:
                if target in contents or target.exists():
                    raise UqsError(
                        f"{action.path} already exists - pick another name, or remove it first"
                    )
                is_src = action.path.parts[:1] == ("src",) and action.path.suffix == ".q"
                contents[target] = flat_source(action.body, repo_root) if is_src else action.body
                created.add(target)
            case WriteMode.APPEND:
                if target not in contents and not target.is_file():
                    raise UqsError(
                        f"{action.path} does not exist, so there is nothing to append to"
                    )
                existing = contents[target] if target in contents else target.read_text()
                contents[target] = _appended(existing, action)
            case _:  # pragma: no cover - unreachable while WriteMode has two members
                # Named rather than folded into the append branch, which is
                # what the old `else` did: a third mode added later would have
                # been silently appended instead of refused.
                raise UqsError(f"unknown write mode {action.mode!r} for {action.path}")

    _write_all(contents, created, repo_root)
    return [action.path for action in plan.actions]


def _write_all(contents: dict[Path, str], created: set[Path], repo_root: Path) -> None:
    """Write every file in `contents`, or, if one write fails, put back the
    ones already written: files this plan created are removed (with any
    folders made for them), appended files get their old text back."""
    originals = {t: t.read_text() for t in contents if t not in created}
    made_dirs: list[Path] = []
    done: list[Path] = []
    try:
        for target, text in contents.items():
            missing = [p for p in [target.parent, *target.parent.parents] if not p.exists()]
            target.parent.mkdir(parents=True, exist_ok=True)
            made_dirs.extend(reversed(missing))
            target.write_text(text)
            done.append(target)
    except BaseException:
        for target in done:
            if target in created:
                target.unlink(missing_ok=True)
            else:
                target.write_text(originals[target])
        for folder in reversed(made_dirs):
            if folder.is_dir() and folder != repo_root and not any(folder.iterdir()):
                folder.rmdir()
        raise


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
