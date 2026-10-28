"""No test fakes `shutil.which` with a function that takes one argument (#1759).

`shutil` is one module: patching `x.shutil.which` replaces it for every caller
in the process, and a one-argument fake raises TypeError inside any code that
calls `which(cmd, path=...)`. which_support.fake_which has the real signature;
use it instead of writing a lambda.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from which_support import fake_which

#: python/, holding every package's tests/ directory.
PYTHON_DIR = Path(__file__).resolve().parents[2]

#: `"which", lambda NAME:` - a fake that accepts the command and nothing else.
ONE_ARG_FAKE = re.compile(r"""["']which["']\s*,\s*lambda\s+\*?\w+\s*:""")


def one_arg_fakes(root: Path) -> list[str]:
    return [
        f"{path.relative_to(root)}:{text.count(chr(10), 0, m.start()) + 1}"
        for path in sorted(root.glob("*/tests/**/*.py"))
        if path.resolve() != Path(__file__).resolve()  # its own examples, below
        for text in [path.read_text()]
        for m in ONE_ARG_FAKE.finditer(text)
    ]


def test_no_test_fakes_which_with_a_one_argument_lambda():
    found = one_arg_fakes(PYTHON_DIR)
    assert not found, (
        "these replace shutil.which with a fake that rejects which()'s keyword "
        f"arguments - use which_support.fake_which instead: {found}"
    )


@pytest.mark.parametrize(
    "fake",
    [
        'monkeypatch.setattr(shutil, "which", lambda _tool: "/usr/bin/true")',
        "monkeypatch.setattr(\n    mod.shutil,\n    'which',\n    lambda name: None,\n)",
    ],
)
def test_the_guard_sees_a_one_argument_fake(tmp_path: Path, fake: str):
    tests = tmp_path / "pkg" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text(fake + "\n")
    assert [f.split(":")[0] for f in one_arg_fakes(tmp_path)] == ["pkg/tests/test_x.py"]


def test_the_helper_takes_whichs_real_signature(monkeypatch: pytest.MonkeyPatch):
    import os
    import shutil

    fake_which(monkeypatch, {"qcon": "/x/qcon", "q": None}, other="/usr/bin/true")
    assert shutil.which("qcon", os.X_OK, path="/nowhere") == "/x/qcon"
    assert shutil.which("q", path="/nowhere") is None
    assert shutil.which("rlwrap", mode=os.F_OK) == "/usr/bin/true"
    fake_which(monkeypatch, {"qcon": "/x/qcon"})
    assert shutil.which("rlwrap") is None
