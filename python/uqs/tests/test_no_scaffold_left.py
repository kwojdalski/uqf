"""Nothing `uqs new-job` wrote as a placeholder is left in the tree.

The scaffold marks every placeholder it writes with SCAFFOLDED: the handler
that throws, the note that says "say why this exists", the catalog row that
says "describe this table". Each of those used to be guarded, if at all, by a
different test failing somewhere else - and the job's `note` by nothing, so a
declaration could reach master still reading "SCAFFOLDED: say why this
exists", and processes.md would publish it.

This is the one list of what is still to write. It is deliberately a single
test with every location in its message, rather than a scattering of
failures a reader has to assemble into a to-do list.
"""

from __future__ import annotations

from pathlib import Path

from uqs.paths import repo_root

#: The word every placeholder carries. The scaffold's templates spell it, so
#: they - and the tests of those templates - are the only files allowed it.
MARKER = "SCAFFOLDED"

#: Where a scaffold writes, relative to the repository root. Not `docs/`: the
#: only files it touches there are generated, and the guides that explain the
#: marker have to be able to name it.
SCANNED = ("src", "tests", "scripts", "python/uqf_frontend")

#: What may mention the marker without being a placeholder: the templates
#: that write it, their tests, runtime output nothing reviews, and the
#: GENERATED code - it copies a marker from a source file already listed, and
#: regenerates clean once that source is edited.
EXEMPT = ("python/uqs/src/uqs/scaffold", "python/uqs/tests", "scripts/output", "src/etl/generated")

SUFFIXES = {".q", ".py", ".md", ".csv"}


def scaffold_left(root: Path) -> list[str]:
    """Every `path:line` under SCANNED still carrying MARKER, in path order."""
    found: list[str] = []
    for top in SCANNED:
        for path in sorted((root / top).rglob("*")):
            rel = path.relative_to(root).as_posix()
            if path.suffix not in SUFFIXES or not path.is_file():
                continue
            if any(rel == e or rel.startswith(e + "/") for e in EXEMPT):
                continue
            for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if MARKER in line:
                    found.append(f"{rel}:{n}")
    return found


def test_no_scaffold_placeholder_is_left_in_the_tree():
    left = scaffold_left(repo_root())
    assert not left, (
        f"{len(left)} scaffolded placeholder(s) still to write - replace each, and "
        "delete the SCAFFOLDED marker with it:\n  " + "\n  ".join(left)
    )


def test_the_guard_finds_a_placeholder_and_ignores_the_templates(tmp_path: Path):
    """A guard that has never been shown to fire protects nothing."""
    (tmp_path / "src" / "etl").mkdir(parents=True)
    (tmp_path / "src" / "etl" / "j.q").write_text('ok\n"SCAFFOLDED: say why"\n')
    (tmp_path / "python" / "uqs" / "src" / "uqs" / "scaffold").mkdir(parents=True)
    (tmp_path / "python" / "uqs" / "src" / "uqs" / "scaffold" / "t.py").write_text("SCAFFOLDED")
    (tmp_path / "scripts" / "output").mkdir(parents=True)
    (tmp_path / "scripts" / "output" / "x.md").write_text("SCAFFOLDED")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("the SCAFFOLDED marker, explained")
    (tmp_path / "src" / "etl" / "img.svg").write_text("SCAFFOLDED")
    assert scaffold_left(tmp_path) == ["src/etl/j.q:2"]
