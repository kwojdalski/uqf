"""Tests for `scripts/gates/policy/etl_layering.py`'s TorQ-owner rule (#619).

The gate ran in CI with no tests of its own. The rule added here - each TorQ
namespace read by one owner file in src/ - gets a case it must flag and one it
must not for each way a read is spelled, plus the real tree, which must be
clean. Against master before #619 it reported the three process-name reads in
bounded_worker.q and uptime.q, and nothing else.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "etl_layering",
    Path(__file__).resolve().parents[3] / "scripts" / "gates" / "policy" / "etl_layering.py",
)
assert _SPEC and _SPEC.loader
layering = importlib.util.module_from_spec(_SPEC)
sys.modules["etl_layering"] = layering
_SPEC.loader.exec_module(layering)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.mark.parametrize("ns", sorted(layering.TORQ_OWNERS))
def test_the_owner_file_may_read_its_namespace(tmp_path, ns):
    owner = layering.TORQ_OWNERS[ns]
    root = _tree(tmp_path, {owner: f"x:@[value;`{ns}.thing;`]\ny:{ns}.thing\n"})
    assert layering.torq_reach(root) == []


@pytest.mark.parametrize("ns", sorted(layering.TORQ_OWNERS))
def test_any_other_file_reading_it_is_flagged_with_the_owner(tmp_path, ns):
    root = _tree(tmp_path, {"src/etl/core/other.q": f"p:@[value;`{ns}.thing;`]\n"})
    (hit,) = layering.torq_reach(root)
    assert hit.startswith("src/etl/core/other.q:1:")
    assert layering.TORQ_OWNERS[ns] in hit


def test_the_quant_library_is_held_to_it_too(tmp_path):
    root = _tree(tmp_path, {"src/pricing/forwards.q": "n:.proc.procname\n"})
    assert len(layering.torq_reach(root)) == 1


def test_a_namespace_given_as_a_symbol_is_a_read(tmp_path):
    root = _tree(tmp_path, {"src/etl/core/other.q": "t:@[{`l in key x};`.lg;{0b}]\n"})
    assert len(layering.torq_reach(root)) == 1


def test_a_comment_or_a_string_is_not_a_read(tmp_path):
    root = _tree(
        tmp_path,
        {"src/etl/core/other.q": '/ reads .proc.procname\nm:"see .lg.l"\n'},
    )
    assert layering.torq_reach(root) == []


def test_a_name_that_only_contains_one_is_not_a_read(tmp_path):
    root = _tree(
        tmp_path,
        {"src/etl/core/other.q": "a:.qetl.run.proc_name[]\nb:.foo.lg.x\nc:.processes\n"},
    )
    assert layering.torq_reach(root) == []


@pytest.mark.parametrize("call", ["getenv`TORQDATA", "getenv[`TORQAPPHOME]", "getenv `TORQHOME"])
def test_reading_a_torq_environment_variable_is_flagged(tmp_path, call):
    root = _tree(tmp_path, {"src/etl/core/other.q": f"d:{call}\n"})
    (hit,) = layering.torq_env_reach(root)
    assert "src/etl/core/other.q:1" in hit and "UQF_*" in hit


def test_an_allow_listed_torq_variable_is_allowed_only_in_its_file(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/etl/plant_registry.q": "p:getenv`TORQAPPHOME\n",
            "src/etl/core/other.q": "p:getenv`TORQAPPHOME\n",
        },
    )
    (hit,) = layering.torq_env_reach(root)
    assert hit.startswith("src/etl/core/other.q:1")


def test_a_uqf_variable_a_comment_or_a_string_is_not_a_torq_read(tmp_path):
    root = _tree(
        tmp_path,
        {"src/etl/core/other.q": ('a:getenv`UQF_MODE\n/ getenv`TORQDATA\ns:"getenv`TORQDATA"\n')},
    )
    assert layering.torq_env_reach(root) == []


def test_an_env_allowance_nothing_uses_is_reported(tmp_path):
    root = _tree(tmp_path, {"src/etl/core/other.q": "x:1\n"})
    assert len(layering.unused_env_reads(root)) == len(layering.TORQ_ENV_READS)


def test_the_real_tree_reads_only_the_listed_torq_variables():
    assert layering.torq_env_reach(layering.REPO) == []
    assert layering.unused_env_reads(layering.REPO) == []


@pytest.mark.parametrize(
    "line",
    [
        'system "l lib/thing/thing.q"',
        'system"l thing.q"',
        'system "cd ",home',
        'system "cd /var/data"',
    ],
)
def test_a_relative_load_or_a_cd_is_flagged(tmp_path, line):
    root = _tree(tmp_path, {"src/etl/core/other.q": f"f:{{[] {line}}}\n"})
    (hit,) = layering.cwd_reach(root)
    assert hit.startswith("src/etl/core/other.q:1:") and "working directory" in hit


@pytest.mark.parametrize(
    "text",
    [
        'system "l /abs/path/thing.q"\n',
        'system "l ",path\n',
        'p:system "cd"\n',
        '/ system "l lib/thing.q"\n',
        "\\l src/etl/core/thing.q\n",
    ],
)
def test_an_absolute_or_built_load_a_pwd_and_a_comment_are_not_flagged(tmp_path, text):
    root = _tree(tmp_path, {"src/etl/core/other.q": text})
    assert layering.cwd_reach(root) == []


def test_a_cwd_allowance_holds_only_in_its_file_and_a_stale_one_is_reported(tmp_path):
    file, cmd = next(iter(layering.CWD_CALLS))
    root = _tree(
        tmp_path,
        {file: f'system "{cmd}"\n', "src/etl/core/other.q": f'system "{cmd}"\n'},
    )
    (hit,) = layering.cwd_reach(root)
    assert hit.startswith("src/etl/core/other.q:1:")
    stale = layering.unused_cwd_calls(root)
    assert len(stale) == len(layering.CWD_CALLS) - 1


def test_the_real_tree_has_only_the_listed_cwd_calls():
    assert layering.cwd_reach(layering.REPO) == []
    assert layering.unused_cwd_calls(layering.REPO) == []


def test_the_real_tree_is_clean():
    assert layering.torq_reach(layering.REPO) == []
    assert layering.run(layering.REPO) == 0


@pytest.mark.parametrize(
    "call", ['.Q.hp[u;"application/json";b]', ".Q.hg u", ".Q.hmb[u;`GET;()]", "hopen `:http://h:80"]
)
def test_outbound_http_outside_the_transport_is_flagged(tmp_path, call):
    root = _tree(tmp_path, {"src/etl/streaming/sink.q": f"post:{{[u;b] {call}}}\n"})
    (hit,) = layering.http_reach(root)
    assert hit.startswith("src/etl/streaming/sink.q:1:")


def test_the_transport_file_may_speak_http_and_a_comment_may_name_it(tmp_path):
    root = _tree(
        tmp_path,
        {
            layering.HTTP_OWNER: "u:.Q.hap x\n",
            "src/etl/streaming/sink.q": '/ .Q.hp returns the body of a 500\nm:"see .Q.hp"\n',
        },
    )
    assert layering.http_reach(root) == []


@pytest.mark.parametrize("sub", ["workers", "reactions"])
def test_core_reaching_into_a_declaration_is_flagged(tmp_path, sub):
    # #1333: reactions/ was not a declaring directory, so core could reach a
    # reaction's namespace and the gate passed.
    root = _tree(
        tmp_path,
        {
            f"src/etl/{sub}/job.q": ".qpipe.job.job.cfg:1\n",
            "src/etl/core/react.q": "reach:{.qpipe.job.job.cfg}\n",
        },
    )
    (hit,) = layering.core_reach(root)
    assert hit.startswith("src/etl/core/react.q:1: core reaches into .qpipe.job.job")
    assert f"src/etl/{sub}/job.q" in hit


def test_core_reach_scans_core_subdirectories(tmp_path):
    # #2547: core/variants/ was globbed non-recursively, so it was never checked.
    root = _tree(
        tmp_path,
        {
            "src/etl/workers/demo.q": ".qpipe.job.demo.facts:{[] 1}\n",
            "src/etl/core/variants/v.q": "f:{[] .qpipe.job.demo.facts[]}\n",
        },
    )
    (hit,) = layering.core_reach(root)
    assert hit.startswith("src/etl/core/variants/v.q:1: core reaches into .qpipe.job.demo")


def test_every_directory_uqs_scaffolds_into_is_a_declaring_one():
    # The directories `uqs job new` writes a declaration into, from the one
    # place uqs keeps them, read as text so this does not need uqs installed.
    paths = (layering.REPO / "python/uqs/src/uqs/paths.py").read_text()
    scaffolded = set(re.findall(r'^\w+_DIR = ETL_DIR / "(\w+)"', paths, re.MULTILINE))
    assert {"sources", "workers", "streaming", "reactions"} <= scaffolded
    declaring = {d.name for d in layering.declaring_dirs()}
    assert scaffolded <= declaring


# #2156: the TorQ namespaces src/ may not read at all are derived from TorQ's
# own definitions under lib/torq, so a namespace TorQ adds is refused the day
# the vendored tree is updated, instead of the day someone remembers to list it.

#: The ones the compression and housekeeping audit found unguarded.
AUDITED = [".cmp", ".hk", ".dotz", ".wdb"]


def test_the_derived_set_holds_what_the_audit_found():
    assert set(AUDITED) <= layering.torq_namespaces()


def test_the_derived_set_holds_every_owned_namespace():
    assert set(layering.TORQ_OWNERS) <= layering.torq_namespaces()


def test_a_namespace_q_reserves_is_not_counted_as_torq():
    assert ".h" not in layering.torq_namespaces()
    assert ".Q" not in layering.torq_namespaces()


def test_both_ways_torq_defines_a_namespace_are_found(tmp_path):
    torq = _tree(
        tmp_path,
        {
            "code/common/a.q": "\\d .alpha\nx:1\n\\d .\n",
            "code/processes/b.q": ".beta.f:{x}\n  .gamma.g:{x}\n/ .delta.h:1\n",
            "torq.q": "\\d .epsilon\n",
        },
    )
    assert layering.torq_namespaces(torq) == {".alpha", ".beta", ".epsilon"}


@pytest.mark.parametrize("ns", AUDITED)
@pytest.mark.parametrize("spelling", ["{ns}.thing[]", "@[value;`{ns}.thing;`]", "`{ns} in key `"])
def test_src_reading_a_torq_namespace_it_has_no_owner_for_is_refused(tmp_path, ns, spelling):
    code = spelling.format(ns=ns)
    root = _tree(tmp_path, {"src/etl/core/compress.q": f"p:{code}\n"})
    (hit,) = layering.torq_reach(root)
    assert hit.startswith("src/etl/core/compress.q:1:")
    assert ns in hit


@pytest.mark.parametrize("ns", AUDITED)
def test_a_comment_or_a_string_naming_one_is_still_exempt(tmp_path, ns):
    root = _tree(
        tmp_path,
        {"src/etl/core/compress.q": f'/ TorQ calls {ns}.thing\nm:"unlike {ns}.thing"\n'},
    )
    assert layering.torq_reach(root) == []


def test_an_allowed_remote_call_is_allowed_only_in_its_own_file(tmp_path):
    (path, ns), _ = next(iter(layering.TORQ_REMOTE_CALLS.items()))
    call = f"r:h(`{ns}.thing;1)\n"
    assert layering.torq_reach(_tree(tmp_path / "a", {path: call})) == []
    assert len(layering.torq_reach(_tree(tmp_path / "b", {"src/etl/core/other.q": call}))) == 1


def test_every_allowed_remote_call_has_a_reason_and_is_still_made():
    assert all(why.strip() for why in layering.TORQ_REMOTE_CALLS.values())
    assert layering.unused_remote_calls(layering.REPO) == []


def test_an_allowance_nothing_uses_any_more_is_reported(tmp_path):
    (path, ns), _ = next(iter(layering.TORQ_REMOTE_CALLS.items()))
    root = _tree(tmp_path, {path: "x:1\n"})
    assert f"{path}: {ns}" in layering.unused_remote_calls(root)
