"""What still names a job after `uqs job remove` has planned its own edits (#717).

The removal works from naming conventions, so it knows only the files a
scaffold writes. Anything added later that names the job - an example script,
a docs page, a diagram, a test in another suite - is left behind, and goes
stale silently: an example script that names a removed worker then fails in
the q-scripts lane, long after the removal looked clean.

So after the plan is made, the tree is searched for every name the removal
takes away - the job, its process, its source when that goes too, and the
tables it drops - and each surviving line is reported. Rewritten files are
searched in their NEW text; deleted files are not searched at all. Generated
files are skipped, because the command regenerates them, as is the port lock,
which keeps a removed process's offset on purpose. A name the removal KEEPS
(a table or a source something else uses) is not searched: its other
references are why it was kept.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

#: Directories never searched wherever they appear: version control,
#: environments, caches, and other sessions' worktrees.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "worktrees",
    ".pytest_cache",
    ".ruff_cache",
}

#: Files the removal regenerates or deliberately keeps naming the job.
_SKIP_FILES = {
    Path("src/etl/generated/pipeline_dag.q"),
    Path("docs/reference/processes.md"),
    Path("scripts/processes/process_ports.csv"),
}

#: Trees skipped at their own path - not any directory of that name, since
#: tests/lib holds the suite's helpers: the vendored TorQ trees, build and run
#: output, and the contract surface, which is re-exported after a removal.
_SKIP_PREFIXES = (
    Path("lib"),
    Path("build"),
    Path("scripts/output"),
    Path("docs/reference/surfaces"),
)

#: What is searched: source, tests, docs, config. A rendered .svg is not - its
#: .d2 source is, and that is the file to edit.
_SUFFIXES = {".q", ".py", ".md", ".qmd", ".d2", ".yml", ".yaml", ".toml", ".sh", ".csv", ".txt"}


@dataclass(frozen=True)
class Reference:
    """One line that still names something the removal takes away."""

    path: Path
    line: int
    text: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.text.strip()[:120]}"


def _searched(repo_root: Path) -> list[Path]:
    """Every searchable file, relative to `repo_root`, in a stable order.

    os.walk, pruning as it goes: the environment and the vendored trees are
    most of the files on disk, and descending into them only to discard them
    made this the slow part of a removal.
    """
    found = []
    for here, dirs, files in os.walk(repo_root):
        base = Path(here).relative_to(repo_root)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIP_DIRS and not any((base / d) == p for p in _SKIP_PREFIXES)
        )
        for f in sorted(files):
            rel = base / f
            if rel.suffix in _SUFFIXES and rel not in _SKIP_FILES:
                found.append(rel)
    return sorted(found)


def stale_references(
    repo_root: Path,
    names: set[str],
    deletes: list[Path],
    rewrites: dict[Path, str],
) -> list[Reference]:
    """Every line outside the removal's own edits that still names one of `names`.

    A name matches on word boundaries, so a backfill's NAME does not match
    inside NAME_backfill - which is why the caller passes both.
    """
    if not names:
        return []
    pattern = re.compile(
        r"(?<![\w])(?:"
        + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
        + r")(?![\w])"
    )
    gone = set(deletes)
    refs = []
    for rel in _searched(repo_root):
        if rel in gone:
            continue
        text = rewrites.get(rel)
        if text is None:
            text = (repo_root / rel).read_text(errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                refs.append(Reference(rel, lineno, line))
    return refs
