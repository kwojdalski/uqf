"""ruff is pinned in two files that nothing else compares (#1370).

pyproject.toml pins the environment's ruff (`uv run ruff format`) and
.pre-commit-config.yaml pins the hook's. pyproject.toml's header promises "the
hook and the environment can no longer disagree"; after a
`pre-commit autoupdate` they could, and the two would reformat each other's
output. The authority is pyproject.toml (it also feeds uv.lock); the hook rev
is the copy this test holds there.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _environment_pin() -> str:
    dev = tomllib.loads((ROOT / "pyproject.toml").read_text())["dependency-groups"]["dev"]
    pins = [m.group(1) for d in dev if (m := re.fullmatch(r"ruff==([\w.]+)", d))]
    assert len(pins) == 1, f"pyproject.toml must pin ruff exactly once, found {dev}"
    return pins[0]


def _hook_pin() -> str:
    text = (ROOT / ".pre-commit-config.yaml").read_text()
    m = re.search(
        r"repo:\s*https://github\.com/astral-sh/ruff-pre-commit\s*\n\s*rev:\s*v([\w.]+)", text
    )
    assert m, ".pre-commit-config.yaml has no astral-sh/ruff-pre-commit rev"
    return m.group(1)


def test_ruff_hook_and_environment_pins_agree():
    assert _hook_pin() == _environment_pin(), (
        "ruff differs between .pre-commit-config.yaml (rev) and pyproject.toml (dev group); "
        "bump both together"
    )
