"""The tool-pins rule, `scripts/gates/policy/tool_pins.py`: every copy of a
tool's version pin agrees with its authority (#1838)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tp = _load("tool_pins", "scripts/gates/policy/tool_pins.py")
check = _load("gates_check", "scripts/gates/check.py")


def _pins(*tools: str):
    return tuple(p for p in tp.PINS if p.tool in tools)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    return tmp_path


CI = """\
jobs:
  hooks:
    steps:
      - uses: astral-sh/setup-uv@abc # v10
        with:
          version: "0.12.14"
          python-version: "3.14"
      - run: uv tool run --from pre-commit==4.6.2 pre-commit run --all-files
"""

PRE_COMMIT_COPIES = {
    ".github/workflows/ci.yml": CI,
    "install.sh": 'PRE_COMMIT="pre-commit==4.6.2"\n',
    "scripts/dev/kdbx_attestation.py": 'PRE_COMMIT = "pre-commit==4.6.2"\n',
    "scripts/dev/hooks_full.py": 'PRE_COMMIT = "pre-commit==4.6.2"\n',
}


def test_the_real_tree_is_clean():
    assert tp.problems(REPO) == []


def test_every_pin_has_more_than_one_copy_in_the_real_tree():
    """A pin with one copy has nothing to disagree with: it belongs in the
    list only while it is repeated."""
    for pin in tp.PINS:
        found, empty = tp.copies(REPO, pin)
        assert len(found) >= 2 and not empty, (pin.tool, found, empty)


def test_a_copy_that_disagrees_fails_naming_it_and_the_authority(tmp_path):
    stale = 'x=1\nPRE_COMMIT="pre-commit==4.5.0"\n'
    root = _tree(tmp_path, {**PRE_COMMIT_COPIES, "install.sh": stale})
    assert tp.problems(root, _pins("pre-commit")) == [
        "install.sh:2: pre-commit '4.5.0', but its authority .github/workflows/ci.yml:8 "
        "pins '4.6.2' - bump every copy together"
    ]


def test_a_bump_of_the_authority_alone_names_every_copy_left_behind(tmp_path):
    files = dict(PRE_COMMIT_COPIES)
    files[".github/workflows/ci.yml"] = CI.replace("4.6.2", "4.7.0")
    out = tp.problems(_tree(tmp_path, files), _pins("pre-commit"))
    assert [line.split(":")[:2] for line in out] == [
        ["install.sh", "1"],
        ["scripts/dev/kdbx_attestation.py", "1"],
        ["scripts/dev/hooks_full.py", "1"],
    ]
    assert all("authority .github/workflows/ci.yml:8 pins '4.7.0'" in line for line in out)


def test_a_required_copy_that_disappears_fails_rather_than_dropping_out(tmp_path):
    root = _tree(tmp_path, {**PRE_COMMIT_COPIES, "install.sh": "# no pin here\n"})
    assert tp.problems(root, _pins("pre-commit")) == [
        "install.sh: holds no pre-commit pin any more - if it was moved or reworded, "
        "point scripts/gates/policy/tool_pins.py at the new copy"
    ]


def test_a_step_s_key_is_never_taken_from_the_next_step(tmp_path):
    """A setup-uv step with no `version:` must not read the quarto step's."""
    wf = """\
steps:
  - uses: astral-sh/setup-uv@abc
    with:
      python-version: "3.14"
  - uses: quarto-dev/quarto-actions/setup@v2
    with:
      version: "1.10.18"
"""
    read = tp._in_block(tp._SETUP_UV, "version")
    assert read(wf) == []
    assert tp._in_block(tp._SETUP_UV, "python-version")(wf) == [(4, "3.14")]


def test_the_workflow_python_must_match_requires_python(tmp_path):
    root = _tree(
        tmp_path,
        {
            "pyproject.toml": 'requires-python = ">=3.14"\ntarget-version = "py314"\n',
            "python/uqs/pyproject.toml": 'requires-python = ">=3.14"\n',
            ".github/workflows/ci.yml": CI.replace('"3.14"', '"3.15"'),
        },
    )
    assert tp.problems(root, _pins("python")) == [
        ".github/workflows/ci.yml:7: python '3.15', but its authority pyproject.toml:1 "
        "pins '3.14' - bump every copy together"
    ]


def test_a_node_range_written_shorter_is_the_same_range(tmp_path):
    files = {
        "web/package.json": (
            '{\n  "engines": {\n    "node": "^22.13.0 || ^24.0.0 || >=26.0.0"\n  }\n}\n'
        ),
        "install.sh": 'NODE_RANGE="^22.13 || ^24 || >=26"\n',
        "README.md": "| **Node** | the app, `^22.13 \\|\\| ^24 \\|\\| >=26` | no |\n",
        "web/README.md": "Requires Node **`^22.13 || ^24 || >=26`** (CI uses Node 24)\n",
    }
    assert tp.problems(_tree(tmp_path, files), _pins("node")) == []
    files["install.sh"] = 'NODE_RANGE="^22.13 || >=24"\n'
    (out,) = tp.problems(_tree(tmp_path, files), _pins("node"))
    expect = "install.sh:1: node '^22.13 || >=24', but its authority web/package.json:3"
    assert out.startswith(expect)


def test_pinned_reads_the_authority():
    assert tp.pinned("pre-commit") == tp.copies(REPO, _pins("pre-commit")[0])[0][0].value
    assert tp.pinned("python") == "3.14"


def test_the_check_fails_with_each_disagreement_on_stderr(capsys, tmp_path):
    files = dict(PRE_COMMIT_COPIES)
    files["scripts/dev/kdbx_attestation.py"] = 'PRE_COMMIT = "pre-commit==4.0.0"\n'
    root = _tree(tmp_path, {**files, "src/.keep": ""})
    rc = check.main(["tool-pins", "--root", str(root)])
    out, err = capsys.readouterr()
    assert rc == 1 and out == ""
    assert "scripts/dev/kdbx_attestation.py:1: pre-commit '4.0.0', but its authority" in err
