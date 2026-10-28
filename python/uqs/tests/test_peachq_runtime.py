"""A runtime that declares its interpreter: the peachq runtime (#764)."""

from __future__ import annotations

import csv
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
    monkeypatch.setattr(stack_env, "peachq_binary", lambda **_kw: binary)
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
    on_peachq = {r.name for r in RUNTIMES.values() if r.interpreter == "peachq"}
    assert on_peachq == {"peachq", "peachq-etl"}, "only these two, and peachq-etl is experimental"
    assert RUNTIMES["peachq-etl"].experimental


def test_the_peachq_runtime_starts_only_what_runs_on_peachq():
    """capture leaves out each process that dies on PeachQ today."""
    from uqs.model.infra import CAPTURE_INFRA

    assert RUNTIMES["peachq"].profiles == ("capture",)
    assert not {"wdb1", "sort1", "reporter1", "monitor1"} & set(CAPTURE_INFRA)
    assert "feed1" in CAPTURE_INFRA, "so trade and quote come in"


def test_only_the_peachq_runtime_gets_an_interpreter_of_its_own(pinned):
    assert stack_env.interpreter_env(stack_paths.paths_for_root(ROOT, "torq")) == {}
    assert stack_env.interpreter_env(stack_paths.paths_for_root(ROOT, "peachq")) == {
        "QCMD": str(pinned),
        "QINIT": str(ROOT / "scripts" / "peachq_init.q"),
    }


def test_the_peachq_init_file_exists_and_only_fills_gaps():
    """QINIT names a file that is there, and that defines .Q.chk only when the
    interpreter lacks it (#1650: TorQ's wdb calls it as it starts)."""
    init = ROOT / "scripts" / stack_env.PEACHQ_INIT
    assert init.is_file()
    assert "if[10h=type @[value;`.Q.chk;{x}];" in init.read_text()


def test_build_env_stays_pure_and_never_resolves_a_binary(monkeypatch, tmp_path):
    """Listings call build_env for every runtime: it must not fetch or build."""

    def resolve(**_kw):
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
    assert env["QCMD"] == str(pinned)
    assert f'export QCMD="{pinned}"' in p.generated_setenv.read_text()
    with p.generated_procs.open(newline="") as f:
        assert {r["qcmd"] for r in csv.DictReader(f)} == {str(pinned)}, "every row runs PeachQ"


def test_the_peachq_runtime_is_held_to_no_licence_budget(monkeypatch):
    monkeypatch.delenv("UQS_LICENCE_CONNECTIONS", raising=False)
    monkeypatch.setattr(profiles, "q_impl", lambda env=None: "kdbx")
    monkeypatch.setenv("UQS_RUNTIME", "peachq")
    assert profiles.licence_limit() is None
    monkeypatch.setenv("UQS_RUNTIME", "torq")
    assert profiles.licence_limit() is not None


def test_a_resolver_refusal_is_a_uqs_error_naming_the_runtime(monkeypatch):
    def unavailable(**_kw):
        raise UqsError("no PeachQ: offline, and the cache is cold")

    monkeypatch.setattr(stack_env, "peachq_binary", unavailable)
    with pytest.raises(
        UqsError, match="the peachq runtime needs a PeachQ binary: no PeachQ: offline"
    ):
        stack_env.interpreter_env(stack_paths.paths_for_root(ROOT, "peachq"))


def test_the_runtime_takes_qcmd_when_it_is_peachq(monkeypatch, tmp_path):
    """One PeachQ lookup (#1753): QCMD on PeachQ is the runtime's binary too,
    so a deployed runtime's deploy.env needs nothing beside QCMD."""
    q = tmp_path / "q"
    q.write_text("#!/bin/sh\necho peachq\n")
    q.chmod(0o755)
    monkeypatch.setenv("QCMD", str(q))
    env = stack_env.interpreter_env(stack_paths.paths_for_root(ROOT, "peachq"))
    assert env["QCMD"] == str(q)
