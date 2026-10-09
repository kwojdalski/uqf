"""A release converted at build time is recognised where it starts (#936).

`uqs deploy build --q-target 4.0` releases passed conversion and smoke on a
managed q 4.0 and were then refused by `uqs start`, whose guard asked only
the runtime's declared q_tree. These build a release directory as a
deployment leaves one - its files and RELEASE_MANIFEST.json - and hold
stack/converted.recognise, and the guard through it, to the evidence.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from uqs import interpreter
from uqs.deploy import artifact, portable
from uqs.paths import UqsError, default_paths
from uqs.stack import converted, qtree

FILES = {
    "src/init.q": "/ flattened\n",
    "src/etl/init.q": "/ flattened too\n",
    "pyproject.toml": "[project]\n",
}


def _release(root: Path, *, q: str | None = "4.0", portable_q: str | None = "4.0") -> Path:
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    manifest = {
        "release": "20261009T000000Z-abc",
        "target": {"os": "linux", "arch": "x86_64", "python": "3.14", **({"q": q} if q else {})},
        "files": {rel: hashlib.sha256(text.encode()).hexdigest() for rel, text in FILES.items()},
    }
    if portable_q:
        manifest["portable"] = {"q": portable_q, "transformed": ["src/init.q"], "excluded": []}
    (root / converted.MANIFEST).write_text(json.dumps(manifest))
    return root


def _override(root: Path, **fields) -> str:
    record = {
        "attempt": "tok123",
        "release": "20261009T000000Z-abc",
        "reason": "smoke passed on the 3.6 server",
        "smoke": "ok",
        **fields,
    }
    (root / converted.OVERRIDE).write_text(json.dumps(record))
    return record["attempt"]


def test_the_names_match_what_deploy_writes():
    assert converted.MANIFEST == artifact.MANIFEST
    assert converted.TARGETS == portable.Q_TARGETS


def test_a_checkout_or_a_release_as_written_records_no_conversion(tmp_path):
    assert converted.recognise(tmp_path, "4.0", {}) is None
    assert converted.recognise(_release(tmp_path, q=None, portable_q=None), "4.0", {}) is None


@pytest.mark.parametrize("server", ["4.0", "4.1"])
def test_a_converted_release_is_recognised_on_a_q_at_least_its_target(tmp_path, server):
    evidence = converted.recognise(_release(tmp_path), server, {})
    assert evidence == converted.Evidence("20261009T000000Z-abc", "4.0", 2)


def test_a_target_and_a_conversion_that_disagree_are_refused(tmp_path):
    with pytest.raises(UqsError, match="target says kdb\\+ 4.0 and its conversion says none"):
        converted.recognise(_release(tmp_path, portable_q=None), "4.0", {})


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda r: (r / "src/init.q").write_text("\\d .a.b\n"), "src/init.q differs"),
        (lambda r: (r / "src/etl/init.q").unlink(), "src/etl/init.q is missing"),
    ],
)
def test_a_release_whose_q_is_not_what_was_built_is_refused(tmp_path, damage, message):
    root = _release(tmp_path)
    damage(root)
    with pytest.raises(UqsError, match=message):
        converted.recognise(root, "4.0", {})


def test_a_q_older_than_the_target_needs_the_deployments_override(tmp_path):
    with pytest.raises(UqsError, match="--accept-converted-release"):
        converted.recognise(_release(tmp_path), "3.6", {})


def test_the_override_starts_it_for_its_own_attempt_only(tmp_path):
    root = _release(tmp_path)
    token = _override(root)
    evidence = converted.recognise(root, "3.6", {converted.ATTEMPT_VAR: token})
    assert evidence is not None and evidence.override is not None
    assert evidence.override["reason"].startswith("smoke passed")
    for env in ({}, {converted.ATTEMPT_VAR: "another-attempt"}):
        with pytest.raises(UqsError, match="--accept-converted-release"):
            converted.recognise(root, "3.6", env)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"smoke": "failed"}, "no passing smoke test"),
        ({"release": "someone-else"}, "is for release someone-else"),
        ({"reason": " "}, "gives no reason"),
    ],
)
def test_an_override_without_its_evidence_is_refused(tmp_path, fields, message):
    root = _release(tmp_path)
    token = _override(root, **fields)
    with pytest.raises(UqsError, match=message):
        converted.recognise(root, "3.6", {converted.ATTEMPT_VAR: token})


def test_the_override_never_excuses_a_damaged_release(tmp_path):
    root = _release(tmp_path)
    token = _override(root)
    (root / "src/init.q").write_text("\\d .a.b\n")
    with pytest.raises(UqsError, match="differs from the release"):
        converted.recognise(root, "3.6", {converted.ATTEMPT_VAR: token})


# --- through the startup guard ---------------------------------------------------


def _q(tmp_path: Path, version: str) -> Path:
    q = tmp_path / "bin" / "q"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(f"#!/bin/sh\necho {version}\n")
    q.chmod(q.stat().st_mode | stat.S_IEXEC)
    return q


def _paths(monkeypatch, root: Path, q_tree: str):
    paths = dataclasses.replace(default_paths(), repo_root=root)
    decl = dataclasses.replace(paths.runtime_declaration, q_tree=q_tree)
    monkeypatch.setattr(type(paths), "runtime_declaration", property(lambda _s: decl))
    return paths


def _env(q: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("UQF_Q_IMPL", "QCMD")}
    return {**env, "QCMD": str(q)}


@pytest.mark.parametrize("q_tree", ["source", "flattened"])
def test_a_converted_release_starts_on_q_4_0_whatever_its_runtime_declares(
    tmp_path, monkeypatch, q_tree
):
    interpreter.q_version.cache_clear()
    root = _release(tmp_path / "release")
    paths = _paths(monkeypatch, root, q_tree)
    qtree.refuse_unloadable(paths, _env(_q(tmp_path, "4.0")))
    # and it loads its packaged code: nothing is converted a second time
    assert not qtree.is_flattened(paths)
    assert qtree.code_root(paths) == root


def test_the_tree_as_written_is_still_refused_on_q_4_0(tmp_path, monkeypatch):
    interpreter.q_version.cache_clear()
    root = _release(tmp_path / "release", q=None, portable_q=None)
    with pytest.raises(UqsError, match="no nested contexts"):
        qtree.refuse_unloadable(_paths(monkeypatch, root, "source"), _env(_q(tmp_path, "4.0")))
