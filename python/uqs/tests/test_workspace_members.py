"""The workspace's Python packages are named once (#1796 item 13).

The authority is pyproject.toml's `[tool.uv.workspace] members`. Three lists
repeat it, each for a reason of its own: pytest's `testpaths` (so `uv run
pytest` from the root runs every member's suite), ruff's `src` (so isort
classifies every member as first-party from the root) and the release's
`payload.ALLOWLIST` (uv's workspace lock needs every member's pyproject). A
member added to one and not the others either never runs its tests, gets its
imports reformatted, or breaks `uv sync` on the server - so they must agree.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from uqs.deploy import payload

ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
MEMBERS = set(PYPROJECT["tool"]["uv"]["workspace"]["members"])


def test_members_are_the_python_packages():
    assert MEMBERS == {
        f"python/{p.parent.name}" for p in (ROOT / "python").glob("*/pyproject.toml")
    }


def test_pytest_runs_every_members_tests():
    testpaths = PYPROJECT["tool"]["pytest"]["ini_options"]["testpaths"]
    members = {p.removesuffix("/tests") for p in testpaths if p.startswith("python/")}
    assert members == MEMBERS


def test_ruff_knows_every_member_as_first_party():
    src = PYPROJECT["tool"]["ruff"]["src"]
    assert {p.removesuffix("/src") for p in src} == MEMBERS


def test_a_release_carries_every_member():
    assert {p for p in payload.ALLOWLIST if p.startswith("python/")} == MEMBERS
