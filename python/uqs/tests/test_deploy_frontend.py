"""The browser frontend in a release (#1646): opt-in, built on the builder,
installed offline, and no Node on the server.

`uqs deploy build --frontend DIST` adds the app's built assets at web/dist and
uqf-frontend's wheel, with its locked dependencies, beside uqs's. uv, pip and
git are fakes here, as in test_build_release.py; the installer runs for real.
"""

from __future__ import annotations

import os
import subprocess
import tarfile
from pathlib import Path

import pytest
from test_build_release import _CLI, _built, _tools, _tree
from test_deploy_python_runtime import push, runtime

from uqs.deploy import frontend, python_runtime
from uqs.deploy.artifact import ReleaseError


def _dist(root: Path) -> Path:
    """What `npm --prefix web run build` writes: an index and its assets."""
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><script src=/ui/assets/app.js></script>\n")
    (root / "assets" / "app.js").write_text("console.log('uqf')\n")
    return root


def _with_frontend_wheel(tools):
    """The fake uv, building uqf-frontend's wheel when asked for it."""

    def run(argv, **kw):
        if "build" in argv and "uqf-frontend" in argv:
            out = Path(argv[argv.index("--out-dir") + 1])
            (out / "uqf_frontend-0.1.0-py3-none-any.whl").write_bytes(b"frontend")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        return tools(argv, **kw)

    return run


def _build(tmp_path: Path, *argv: str):
    root = tmp_path / "tree"
    tools, calls = _tools(_tree(root))
    p = _CLI.commands["build"].make_context("build", ["--output", str(tmp_path / "out"), *argv])
    art = _built(
        ["--output", str(tmp_path / "out"), *argv],
        root=root,
        runner=_with_frontend_wheel(tools),
        frontend_dist=p.params["frontend_dist"],
    )
    return art, calls


def _members(art) -> list[str]:
    with tarfile.open(art.path) as tar:
        return tar.getnames()


def test_a_default_release_carries_no_frontend(tmp_path):
    art, calls = _build(tmp_path)
    assert not [m for m in _members(art) if m.startswith("web/")]
    assert "frontend" not in art.manifest and "extra_wheels" not in art.manifest["python"]
    export = next(c for c in calls if "export" in c)
    assert [export[i + 1] for i, a in enumerate(export) if a == "--package"] == ["uqs"]


def test_frontend_carries_the_built_app_and_the_backends_wheels(tmp_path):
    art, calls = _build(tmp_path, "--frontend", str(_dist(tmp_path / "web-dist")))
    members = _members(art)
    assert {"web/dist/index.html", "web/dist/assets/app.js"} <= set(members)
    wheel = ".release/wheels/uqf_frontend-0.1.0-py3-none-any.whl"
    assert wheel in members and art.manifest["python"]["extra_wheels"] == [wheel]
    assert art.manifest["frontend"] == {
        "package": "uqf-frontend", "wheel": wheel, "web_dist": "web/dist", "files": 2,
    }  # fmt: skip
    assert art.manifest["files"]["web/dist/index.html"], "every asset is checksummed"
    export = next(c for c in calls if "export" in c)
    packages = [export[i + 1] for i, a in enumerate(export) if a == "--package"]
    assert packages == ["uqs", "uqf-frontend"], "its locked dependencies join uqs's"


def test_a_dist_with_no_index_is_refused_naming_the_build(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ReleaseError, match="no index.html - build the app first"):
        frontend.select(tmp_path / "empty")


@pytest.mark.parametrize("name", [".env", "assets/server.key", "node_modules/x/index.js"])
def test_a_file_no_release_ships_is_refused(tmp_path, name):
    dist = _dist(tmp_path / "dist")
    (dist / name).parent.mkdir(parents=True, exist_ok=True)
    (dist / name).write_text("secret\n")
    with pytest.raises(ReleaseError, match=f"{name} is a file no release ships"):
        frontend.select(dist)


def test_a_symbolic_link_is_refused(tmp_path):
    dist = _dist(tmp_path / "dist")
    os.symlink("/etc/passwd", dist / "assets" / "passwd")
    with pytest.raises(ReleaseError, match="assets/passwd is a symbolic link"):
        frontend.select(dist)


def test_the_assets_go_where_the_backend_serves_them_from():
    import inspect

    from uqf_frontend import stack

    assert 'root / "web" / "dist"' in inspect.getsource(stack)
    assert frontend.WEB_DIST == "web/dist"


# --- installed beside uqs, offline ---------------------------------------------------


def test_a_bundled_python_venv_installs_the_frontend_after_uqs():
    py = {
        "wheel_dir": ".release/wheels",
        "requirements": ".release/requirements.txt",
        "app_wheel": ".release/wheels/uqs.whl",
        "extra_wheels": [".release/wheels/f.whl"],
    }
    lines = python_runtime.venv_lines("/rt/bin/python3.14", py)
    assert lines[-2].endswith("--no-deps .release/wheels/uqs.whl")
    assert lines[-1].endswith("--no-deps .release/wheels/f.whl") and "--no-index" in lines[-1]


def _listing_a_frontend_wheel(monkeypatch) -> None:
    """Every artifact a push reads lists an extra wheel, as --frontend's does."""
    from uqs.deploy import artifact

    real = artifact.read_artifact

    def with_frontend(path, expected=None):
        art = real(path, expected)
        art.manifest["python"]["extra_wheels"] = [".release/wheels/f.whl"]
        return art

    monkeypatch.setattr("uqs.deploy.config.read_artifact", with_frontend)


def test_a_push_installs_the_frontend_wheel_with_uv_offline(tmp_path, monkeypatch):
    from test_deploy import _artifact
    from test_deploy_overrides import _deploy

    _listing_a_frontend_wheel(monkeypatch)

    code, remote, _ = _deploy(_artifact(tmp_path))
    (prepare,) = remote.stage("prepare")
    assert code == 0
    assert "uv pip install --quiet --offline --no-index --python .venv/bin/python --no-deps " \
        ".release/wheels/f.whl" in prepare  # fmt: skip


def test_a_bundled_python_push_installs_it_too(tmp_path, monkeypatch):
    _listing_a_frontend_wheel(monkeypatch)
    code, remote, _ = push(tmp_path, runtime(tmp_path))
    (prepare,) = remote.stage("prepare")
    assert code == 0 and "--no-deps .release/wheels/f.whl" in prepare
