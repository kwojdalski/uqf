"""Python's copies of q's naming rules, held to q's own output (#1368).

The prefixes and tags are generated from the q tree; what Python adds is the
`upper` and the order tags are put on, and a wrong guess there is silent: the
browser tells an operator to unset a variable that does nothing, `uqs config
sources` blames the wrong source, `uqs runs` suggests the wrong `--version`.
So this loads the tree, asks q for each name on the same inputs, and compares.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from uqs.generated.q_facts import FIXTURE_TAG, REVISION_TAG
from uqs.interpreter import KDBX, q_impl, q_interpreter
from uqs.model.qnames import Q_WORDS, config_env_var, credential_var
from uqs.stack import runs

UQF_ROOT = Path(__file__).resolve().parents[3]

#: The one word PeachQ keeps that KDB-X does not (the PeachQ lane loads the
#: same tree, so Q_WORDS and the traps gate carry it; the flattener does not).
PEACHQ_ONLY = frozenset({"rlike"})


def _script(name: str, relpath: str):
    """A module under scripts/, loaded by path: scripts/ is not a package, and
    its gates cannot import uqs - so their copies are compared here."""
    spec = importlib.util.spec_from_file_location(name, UQF_ROOT / relpath)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


RISKY_PARAM_NAMES = _script("check_q_traps", "scripts/gates/check_q_traps.py").RISKY_PARAM_NAMES
BUILTINS = _script("flatten_contexts", "scripts/portable/flatten_contexts.py").BUILTINS

KEYS = ["dry_run", "backfill_from", "Mixed_Case"]
SOURCES = ["demo_deals", "duckdb_deals", "FX"]

QUERY = r"""
\l src/init.q
\l src/etl/init.q
run_version:.qetl.job.bounded.runtime.run_version;
cfgkeys:%s;
srcnames:%s;
-1 .j.j `env_name`credential_var`fixture`revision`revision_fixture`q_words!(
    {.qetl.cfg.env_name `$x} each cfgkeys;
    {.qetl.source.credential_var `$x} each srcnames;
    {string .qetl.source.fixture_version `$x} each ("v1";"v2~fixture");
    {string run_version[`source`revision!(`demo_deals;2);`$x]} each enlist "v1";
    {string run_version[`source`revision!(`demo_deals;0N);`$x]} each enlist "v1";
    string distinct .Q.res,k where (k:key .q) like "[a-z]*");
exit 0
"""


def _q_string_list(items: list[str]) -> str:
    return "(" + ";".join(json.dumps(i) for i in items) + ")"


@pytest.fixture(scope="module")
def from_q(tmp_path_factory) -> dict[str, list[str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    script = QUERY % (_q_string_list(KEYS), _q_string_list(SOURCES))
    path = tmp_path_factory.mktemp("qnames") / "names.q"
    path.write_text(script)
    result = subprocess.run(
        [str(q), str(path), "-q"],
        stdin=subprocess.DEVNULL,
        cwd=UQF_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_config_env_name_is_what_q_computes(from_q):
    assert [config_env_var(k) for k in KEYS] == from_q["env_name"]


def test_the_credential_variable_is_what_q_computes(from_q):
    assert [credential_var(s) for s in SOURCES] == from_q["credential_var"]


def test_q_appends_the_tags_in_the_order_runs_strips_them(from_q):
    """`v1` run revised on a fixture is recorded `v1@r2~fixture`; untagged
    undoes exactly that, and a revision-less release only gains the fixture."""
    assert from_q["revision"] == [f"v1{REVISION_TAG}2{FIXTURE_TAG}"]
    assert from_q["revision_fixture"] == [f"v1{FIXTURE_TAG}"]
    assert from_q["fixture"] == [f"v1{FIXTURE_TAG}", f"v2{FIXTURE_TAG}"]
    assert [runs.untagged(v) for v in from_q["revision"] + from_q["fixture"]] == ["v1", "v1", "v2"]


def test_the_q_words_python_refuses_are_the_ones_q_keeps(from_q):
    """A column or table named for one of these cannot load (#1588); a q
    release that adds a keyword must add it here too. A subset, not equal:
    the list covers KDB-X and PeachQ, and PeachQ lacks some KDB-X words."""
    assert set(from_q["q_words"]) <= Q_WORDS


def test_the_q_words_are_exactly_what_kdbx_keeps_and_peachqs_own(from_q):
    """The other direction (#1795): a word in Q_WORDS that no q keeps refuses a
    correct name. Only KDB-X can say so - PeachQ lacks some of its words."""
    if q_impl() != KDBX:
        pytest.skip("the exact set is KDB-X's; on PeachQ only the subset above holds")
    assert Q_WORDS - PEACHQ_ONLY == set(from_q["q_words"])


def test_the_traps_gate_refuses_every_q_word_and_nothing_else(from_q):
    """check_q_traps.py's reserved-name-in-embedded-q rule reads
    RISKY_PARAM_NAMES: a q word missing from it lets `([] word: ...)` reach a
    live process as `'assign`, and a name that is no q word is reported for
    nothing (#1795). It held `fill` and `find`, which are neither."""
    assert set(from_q["q_words"]) <= RISKY_PARAM_NAMES, "q words the gate misses"
    if q_impl() == KDBX:
        assert RISKY_PARAM_NAMES - PEACHQ_ONLY <= set(from_q["q_words"]), "not q words"
    assert RISKY_PARAM_NAMES == Q_WORDS


def test_the_flatteners_builtins_are_the_q_words_less_peachqs_own():
    """flatten_contexts.py never qualifies these: one missing is a call inside
    a nested context rewritten into a global that does not exist - `use` was
    (#1795); one too many is a context's own global left unqualified."""
    assert BUILTINS == Q_WORDS - PEACHQ_ONLY
