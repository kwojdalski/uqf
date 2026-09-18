"""Tests for `scripts/render_diagrams.py` - and mostly for the two ways it
can pass while checking nothing.

The gate's whole job is to fail when a committed `.svg` no longer matches
its `.d2`, and it has two deliberate escape hatches: no `d2` on PATH, and a
`d2` whose version is not the one the SVGs were rendered with. Both are
right - the bytes are only comparable for one renderer - and both turn the
gate off. An escape hatch nobody checks is how a gate ends up green over
code it has stopped reading, which is the failure this repository keeps
finding (`docs/man.q` called itself generated while covering 78 of 348
functions).

So the cases below are: it FIRES on a stale render, it PASSES on a matching
one, each skip path is taken only for its own reason and says so, and an
empty source directory is an error rather than a quiet success.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

# scripts/ is not a package, so the script is loaded by path - the same
# idiom test_check_doc_references.py uses, and for the same reason.
_SPEC = importlib.util.spec_from_file_location(
    "render_diagrams", REPO / "scripts" / "render_diagrams.py"
)
assert _SPEC and _SPEC.loader
render_diagrams = importlib.util.module_from_spec(_SPEC)
sys.modules["render_diagrams"] = render_diagrams
_SPEC.loader.exec_module(render_diagrams)


def _renderable() -> bool:
    """Whether the d2 on this machine is the one the SVGs were rendered with."""
    return render_diagrams.installed_version() == render_diagrams.D2_VERSION


requires_d2 = pytest.mark.skipif(
    not _renderable(),
    reason=f"needs d2 {render_diagrams.D2_VERSION} on PATH",
)


@pytest.fixture
def diagram_dir(tmp_path, monkeypatch):
    """A throwaway diagrams directory, so a test never rewrites the real SVG."""
    monkeypatch.setattr(render_diagrams, "DIAGRAMS", tmp_path)
    monkeypatch.setattr(render_diagrams, "REPO", tmp_path)
    return tmp_path


def _check() -> int:
    """Run the gate in check mode.

    argv is set here rather than in each test because main() parses it, and
    a test that left a stale argv behind would hand the next one a different
    mode - which reads as the gate being wrong rather than the test.
    """
    return _run("--check")


def _run(*args: str) -> int:
    import unittest.mock

    with unittest.mock.patch.object(sys, "argv", ["render_diagrams", *args]):
        return render_diagrams.main()


def _write_source(directory: Path, label: str = "one") -> Path:
    source = directory / "sample.d2"
    source.write_text(f'a: "{label}"\nb: "two"\na -> b\n')
    return source


# --------------------------------------------------------------- it fires


@requires_d2
def test_a_stale_svg_is_reported(diagram_dir, capsys):
    source = _write_source(diagram_dir)
    render_diagrams.render(source, source.with_suffix(".svg"))
    # The edit a contributor forgets to re-render.
    _write_source(diagram_dir, label="one, renamed")

    assert _check() == 1
    assert "is stale" in capsys.readouterr().err


@requires_d2
def test_a_missing_svg_is_reported(diagram_dir, capsys):
    _write_source(diagram_dir)
    assert _check() == 1
    assert "is missing" in capsys.readouterr().err


@requires_d2
def test_a_matching_svg_passes(diagram_dir):
    source = _write_source(diagram_dir)
    render_diagrams.render(source, source.with_suffix(".svg"))
    assert _check() == 0


@requires_d2
def test_rendering_then_checking_is_clean(diagram_dir):
    """Render mode must produce something check mode accepts, or the fix the
    failure message tells you to run does not fix it."""
    _write_source(diagram_dir)
    assert _run() == 0
    assert _check() == 0


# ------------------------------------------------- and the ways it goes quiet


def test_an_absent_d2_skips_and_says_so(diagram_dir, monkeypatch, capsys):
    _write_source(diagram_dir)
    monkeypatch.setattr(render_diagrams, "installed_version", lambda: None)
    assert _check() == 0
    assert "no d2 on PATH" in capsys.readouterr().out


def test_a_different_d2_version_skips_and_names_both(diagram_dir, monkeypatch, capsys):
    """Not a failure: a different renderer emits different, equally correct
    bytes, and reporting that as a stale diagram would start a re-render war
    between two contributors.
    """
    _write_source(diagram_dir)
    monkeypatch.setattr(render_diagrams, "installed_version", lambda: "v9.9.9")
    assert _check() == 0
    out = capsys.readouterr().out
    assert "v9.9.9" in out and render_diagrams.D2_VERSION in out


def test_an_empty_diagram_directory_is_an_error(diagram_dir, capsys):
    """A moved directory must fail loudly rather than gate nothing - the same
    refusal check_etl_layering.py makes over an empty namespace set."""
    assert _check() == 1
    assert "no .d2 sources" in capsys.readouterr().err


# --------------------------------------------------- the committed diagram


@requires_d2
def test_the_repositorys_own_svg_is_current():
    """The gate, run for real. This is the assertion CI makes."""
    assert _check() == 0
