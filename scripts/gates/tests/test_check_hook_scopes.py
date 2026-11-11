"""Tests for `check.py hook-scopes` (scripts/gates/policy/hook_scopes.py), its coverage half.

The gate exists to fail on a hook that is valid but too narrow. It read only
each gate hook's `files:`, so an `exclude:` that took nearly all Python out
of the lint, test and type gates still reported full coverage (#1335). Each
case writes a small config and a fixed file list, so what is judged is the
scope logic alone, not this repository's current config.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "hook_scopes", REPO / "scripts" / "gates" / "policy" / "hook_scopes.py"
)
assert _SPEC and _SPEC.loader
chs = importlib.util.module_from_spec(_SPEC)
sys.modules["hook_scopes"] = chs
_SPEC.loader.exec_module(chs)

FILES = [
    "lib/vendored/a.py",
    "python/uqs/src/uqs/cli.py",
    "python/uqs/tests/test_cli.py",
    "scripts/gates/check_x.py",
]


def _config(exclude: str) -> str:
    hooks = "".join(
        f"      - id: {hook}\n        files: '\\.py$'\n        exclude: '{exclude}'\n"
        for hook in ("ruff", "ruff-format", "python-tests", "ty")
    )
    return f"repos:\n  - repo: local\n    hooks:\n{hooks}"


@pytest.fixture
def run(tmp_path, monkeypatch, capsys):
    def go(config_text: str) -> tuple[int, str]:
        (tmp_path / chs.CONFIG).write_text(config_text)
        monkeypatch.setattr(chs, "tracked_files", lambda root: FILES)
        code = chs.run(tmp_path)
        out = capsys.readouterr()
        return code, out.out + out.err

    return go


def test_an_exclude_that_drops_python_from_every_gate_is_reported(run):
    code, out = run(_config("^(lib|python)/"))
    assert code == 1
    assert "1/3 tracked .py file(s) covered by the lint gate" in out
    assert "UNGATED (lint)  python/uqs" in out
    assert "UNGATED (test)  python/uqs" in out
    assert "UNGATED (type)  python/uqs" in out


def test_the_vendored_exclude_alone_leaves_everything_gated(run):
    code, out = run(_config("^lib/"))
    assert code == 0, out
    assert "3/3 tracked .py file(s) covered by the lint gate" in out
    assert "UNGATED" not in out


def test_an_exclude_before_files_or_at_top_level_is_applied(run):
    """Both spellings dropped python/ from the gates while coverage read 3/3
    (#2539): a hook's keys count in any order, and a column-0 key is global."""
    before = (
        "repos:\n  - repo: local\n    hooks:\n"
        "      - id: ruff\n        exclude: '^python/'\n        files: '\\.py$'\n"
    )
    code, out = run(before)
    assert code == 1
    assert "UNGATED (lint)  python/uqs" in out
    top = "exclude: '^python/'\n" + _config("^lib/") + "      - id: other\n        files: '.'\n"
    code, out = run(top)
    assert code == 1
    assert "1/3 tracked .py file(s) covered by the lint gate" in out
    assert "UNGATED (type)  python/uqs" in out


def test_a_top_level_files_narrows_every_hook():
    hooks = chs.scoped_hooks(
        "files: '^python/'\nrepos:\n  - hooks:\n      - id: a\n        files: 'src/'\n"
    )
    [(hook, files, exclude)] = hooks
    assert (hook, exclude) == ("a", None)
    assert [f for f in FILES if re.search(files, f)] == ["python/uqs/src/uqs/cli.py"]
    assert not re.search(files, "scripts/src/x.py")


def test_the_real_config_gates_all_tracked_python():
    """The repository's own config passes, so the hook is committable."""
    assert chs.run(REPO) == 0


# --- the q-docs scopes name exactly the documents with q-example blocks ----

DOCS = {"docs/a.md", "docs/b.md", "docs/plain.md", "README.md"}
MARKED = {"docs/a.md", "docs/b.md"}


def _doc_hooks(pattern: str) -> list[tuple[str, str, str | None]]:
    return [(hook, pattern, None) for hook in chs.DOC_HOOKS] + [("q-tests", r"\.q$", None)]


def test_a_doc_scope_naming_exactly_the_marked_documents_passes():
    assert chs.doc_scope_drift(_doc_hooks(r"\.q$|^docs/(a|b)\.md$"), DOCS, MARKED) == []


def test_a_marked_document_outside_the_doc_scope_is_reported():
    """Its block would go unrun on a docs edit, and unattested in CI."""
    problems = chs.doc_scope_drift(_doc_hooks(r"\.q$|^docs/a\.md$"), DOCS, MARKED)
    assert problems == [
        f"{hook}: docs/b.md has a q-example block its scope does not take" for hook in chs.DOC_HOOKS
    ]


def test_a_doc_scope_taking_an_unmarked_document_is_reported():
    """An edit to it would ask for a KDB-X attestation that judges nothing."""
    problems = chs.doc_scope_drift(_doc_hooks(r"\.(q|md)$"), DOCS, MARKED)
    assert "q-docs: its scope takes docs/plain.md, which has no q-example block" in problems
    assert "q-docs: its scope takes README.md, which has no q-example block" in problems


def test_the_real_doc_scopes_match_the_marked_documents():
    hooks = chs.scoped_hooks((REPO / chs.CONFIG).read_text())
    assert {h for h, _, _ in hooks} >= set(chs.DOC_HOOKS)
    assert chs.doc_scope_drift(hooks, *chs.doc_files_and_marked(REPO)) == []


# --- every hook runs the project's interpreter, not PATH's (#2449) ----------

# `python3`/`python` as a command word: at the start of a line, or after a
# shell separator or `$(` - not inside `uv run python` or a path.
_BARE_PYTHON = re.compile(r"(?:^|[\s;&|(])(python3?)(?=\s)", re.MULTILINE)


def _bare_python_hooks(config_text: str) -> list[str]:
    bare = []
    for repo in yaml.safe_load(config_text)["repos"]:
        for hook in repo.get("hooks", []):
            command = " ".join([hook.get("entry", ""), *hook.get("args", [])])
            if _BARE_PYTHON.search(command.replace("uv run python", "uv-run-python")):
                bare.append(hook["id"])
    return bare


def test_a_hook_calling_python3_from_path_is_caught():
    config = (
        "repos:\n  - repo: local\n    hooks:\n"
        "      - id: a\n        entry: python3 scripts/x.py\n"
        "      - id: b\n        entry: bash\n        args: ['-c', 'x=$(python3 scripts/y.py)']\n"
        "      - id: c\n        entry: uv run python scripts/z.py\n"
    )
    assert _bare_python_hooks(config) == ["a", "b"]


def test_no_real_hook_runs_whichever_python_is_first_on_path():
    """test_impact.py and friends are 3.14 code (ruff's py314 target writes
    `except A, B:`); a python3 from PATH may be 3.12 and fail to parse them."""
    assert _bare_python_hooks((REPO / chs.CONFIG).read_text()) == []


def test_kdbx_only_prints_the_tagged_hooks_and_checks_nothing(tmp_path, monkeypatch, capsys):
    """ci.yml reads stdout as its SKIP list, so it is the ids alone."""
    (tmp_path / chs.CONFIG).write_text(
        "repos:\n  - repo: local\n    hooks:\n"
        "      - id: a\n        # kdbx-only\n        files: 'nothing-matches'\n"
        "      - id: b\n        files: '.'\n"
        "      - id: c\n        # kdbx-only\n"
    )
    monkeypatch.setattr(chs, "tracked_files", lambda root: pytest.fail("read the tree"))
    assert chs.run(tmp_path, kdbx_only=True) == 0
    assert capsys.readouterr().out == "a,c\n"
