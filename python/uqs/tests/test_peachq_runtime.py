"""A runtime that declares its interpreter: the peachq runtime (#764)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from uqs import paths as stack_paths
from uqs.model import profiles
from uqs.paths import UqsError
from uqs.runtimes import RUNTIMES, Runtime
from uqs.stack import env as stack_env
from uqs.stack import runtime

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """peachq_binary as if the resolver had answered, without fetching or building."""
    binary = tmp_path / "peachq" / "q"
    monkeypatch.setattr(stack_env, "peachq_binary", lambda scripts_dir: binary)
    return binary


def test_a_peachq_runtime_with_pipelines_is_refused_naming_the_blocker():
    with pytest.raises(
        ValueError, match=r"cannot load the nested ETL namespaces \(peachq-org/peachq#80\)"
    ):
        Runtime("p", "d", "uqs-p", pipelines=True, overlays=True, base_port=1, interpreter="peachq")


def test_an_unknown_interpreter_is_refused():
    with pytest.raises(ValueError, match="interpreter is kdbx or peachq, not 'q9'"):
        Runtime("p", "d", "uqs-p", pipelines=False, overlays=False, base_port=1, interpreter="q9")


def test_the_peachq_runtime_is_the_starter_pack_alone_on_peachq():
    p = RUNTIMES["peachq"]
    assert (p.interpreter, p.pipelines, p.overlays) == ("peachq", False, False)
    assert all(r.interpreter == "kdbx" for r in RUNTIMES.values() if r.name != "peachq")


def test_only_the_peachq_runtime_gets_an_interpreter_of_its_own(pinned):
    assert stack_env.interpreter_env(stack_paths.paths_for_root(ROOT, "torq")) == {}
    assert stack_env.interpreter_env(stack_paths.paths_for_root(ROOT, "peachq")) == {
        "UQF_Q_IMPL": "peachq",
        "QCMD": str(pinned),
    }


def test_build_env_stays_pure_and_never_resolves_a_binary(monkeypatch, tmp_path):
    """Listings call build_env for every runtime: it must not fetch or build."""

    def resolve(scripts_dir):
        raise AssertionError("build_env resolved PeachQ")

    monkeypatch.setattr(stack_env, "peachq_binary", resolve)
    stack_env.build_env(stack_paths.paths_for_root(tmp_path, "peachq"))


def test_bootstrap_hands_torq_sh_and_setenv_the_peachq_binary(pinned, monkeypatch, tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(
        ROOT / "lib" / "torq-finance-starter-pack", root / "lib" / "torq-finance-starter-pack"
    )
    (root / "lib" / "torq").mkdir(parents=True)
    (root / "lib" / "torq" / "torq.q").touch()
    (root / "src" / "etl").mkdir(parents=True)
    monkeypatch.setattr(shutil, "which", lambda t, path=None: None if t == "q" else "/usr/bin/true")
    monkeypatch.setattr(runtime, "fill_hdb_partitions", lambda paths: False)
    p = stack_paths.paths_for_root(root, "peachq")
    env = runtime.bootstrap(p)
    assert (env["QCMD"], env["UQF_Q_IMPL"]) == (str(pinned), "peachq")
    assert f'export QCMD="{pinned}"' in p.generated_setenv.read_text()


def test_the_peachq_runtime_is_held_to_no_licence_budget(monkeypatch):
    monkeypatch.delenv("UQS_LICENCE_CONNECTIONS", raising=False)
    monkeypatch.delenv("UQF_Q_IMPL", raising=False)
    monkeypatch.setenv("UQS_RUNTIME", "peachq")
    assert profiles.licence_limit() is None
    monkeypatch.setenv("UQS_RUNTIME", "torq")
    assert profiles.licence_limit() is not None


def test_a_resolver_refusal_is_a_uqs_error_naming_the_runtime(monkeypatch, tmp_path):
    """The real resolver, told to use a binary that is not there."""
    stack_env.peachq_binary.cache_clear()
    monkeypatch.setenv("UQF_PEACHQ", str(tmp_path / "nope"))
    try:
        with pytest.raises(UqsError, match="the peachq runtime needs a PeachQ binary: UQF_PEACHQ="):
            stack_env.peachq_binary(ROOT / "scripts")
    finally:
        stack_env.peachq_binary.cache_clear()
