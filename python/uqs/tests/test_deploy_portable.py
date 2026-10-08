"""`uqs deploy build --q-target 4.0` (#861): the release's q converted for a
kdb+ 4.0 server, in a staged copy - the checkout is never written.

uv, pip and git are fakes, as in test_build_release.py; the converter is the
real scripts/portable/flatten_contexts.py, copied into each fake tree as a
checkout carries its own.
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
from pathlib import Path

import pytest

from uqs.deploy import build as release_build
from uqs.deploy import portable
from uqs.deploy.artifact import ReleaseError

REPO = Path(__file__).resolve().parents[3]
REV = "0123456789abcdef0123456789abcdef01234567"

NESTED = "\\d .m.n\nk:1\nf:{x+k}\n\\d .\n"
FLAT = "\\d .\n.m.n.k:1\n.m.n.f:{x+.m.n.k}\n\\d .\n"


def _done(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def _tree(root: Path, extra: dict[str, str] | None = None) -> list[str]:
    files = {
        "pyproject.toml": "x\n",
        "python/uqs/pyproject.toml": 'requires-python = ">=3.14"\n',
        "src/m.q": NESTED,
        **(extra or {}),
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    converter = root / portable.CONVERTER
    converter.parent.mkdir(parents=True, exist_ok=True)
    converter.write_text((REPO / portable.CONVERTER).read_text())
    return sorted([*files, portable.CONVERTER.as_posix()])


def _tools(files: list[str]):
    def runner(argv, **_):
        if "ls-files" in argv:
            return _done("\0".join(files) + "\0")
        if "rev-parse" in argv:
            return _done(REV + "\n")
        if "status" in argv:
            return _done("")
        if "export" in argv:
            Path(argv[argv.index("-o") + 1]).write_text("rich==15.0.0 --hash=sha256:00\n")
            return _done()
        if "build" in argv:
            (Path(argv[argv.index("--out-dir") + 1]) / "uqs-0.1.0-py3-none-any.whl").write_bytes(
                b"u"
            )
            return _done()
        if "download" in argv:
            return _done()
        raise AssertionError(argv)

    return runner


def _digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _build(tmp_path: Path, extra: dict[str, str] | None = None, **kwargs):
    root = tmp_path / "tree"
    files = _tree(root, extra)
    before = _digest(root)
    art = release_build.build(tmp_path / "dist", root=root, runner=_tools(files), **kwargs)
    assert _digest(root) == before, "the checkout was written"
    return art


def _member(art, name: str) -> str:
    with tarfile.open(art.path) as tar:
        f = tar.extractfile(name)
        assert f is not None
        return f.read().decode()


def test_the_artifact_carries_converted_q_and_the_checkout_is_untouched(tmp_path):
    art = _build(tmp_path, q_target="4.0")
    assert _member(art, "src/m.q") == FLAT
    assert art.manifest["target"]["q"] == "4.0"
    assert art.manifest["portable"] == {
        "q": "4.0",
        "transformed": ["src/m.q"],
        "excluded": [],
        "warnings": [],
    }


def test_without_a_q_target_the_q_ships_as_written(tmp_path):
    art = _build(tmp_path)
    assert _member(art, "src/m.q") == NESTED
    assert "q" not in art.manifest["target"] and "portable" not in art.manifest


def test_an_excluded_file_ships_as_written(tmp_path):
    art = _build(tmp_path, {"src/keep.q": NESTED}, q_target="4.0", q_exclude=["src/keep.q"])
    assert _member(art, "src/keep.q") == NESTED and _member(art, "src/m.q") == FLAT
    assert art.manifest["portable"]["excluded"] == ["src/keep.q"]


def test_a_refusal_fails_the_build_naming_the_line(tmp_path):
    with pytest.raises(ReleaseError) as err:
        _build(tmp_path, {"src/bad.q": "\\d .a.b\n`v set 1\n"}, q_target="4.0")
    assert err.value.stage == "portable"
    assert "src/bad.q:2:4 load-time-lookup" in str(err.value)
    assert "--q-exclude PATTERN" in str(err.value)
    assert not list(tmp_path.rglob("*.tar.gz"))


def test_a_refused_file_can_be_left_out(tmp_path):
    art = _build(
        tmp_path, {"src/bad.q": "\\d .a.b\n`v set 1\n"}, q_target="4.0", q_exclude=["src/bad.q"]
    )
    assert _member(art, "src/bad.q") == "\\d .a.b\n`v set 1\n"


def test_an_unknown_q_target_is_refused(tmp_path):
    with pytest.raises(ReleaseError, match="--q-target '3.6' must be one of 4.0"):
        _build(tmp_path, q_target="3.6")
