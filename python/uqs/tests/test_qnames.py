"""Python's copies of q's naming rules, held to q's own output (#1368).

The prefixes and tags are generated from the q tree; what Python adds is the
`upper` and the order tags are put on, and a wrong guess there is silent: the
browser tells an operator to unset a variable that does nothing, `uqs config
sources` blames the wrong source, `uqs runs` suggests the wrong `--version`.
So this loads the tree, asks q for each name on the same inputs, and compares.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from uqs.generated.q_facts import FIXTURE_TAG, REVISION_TAG
from uqs.interpreter import q_interpreter
from uqs.model.qnames import config_env_var, credential_var
from uqs.stack import runs

UQF_ROOT = Path(__file__).resolve().parents[3]

KEYS = ["dry_run", "backfill_from", "Mixed_Case"]
SOURCES = ["demo_deals", "duckdb_deals", "FX"]

QUERY = r"""
\l src/init.q
\l src/etl/init.q
run_version:.qetl.job.bounded.runtime.run_version;
cfgkeys:%s;
srcnames:%s;
-1 .j.j `env_name`credential_var`fixture`revision`revision_fixture!(
    {.qetl.cfg.env_name `$x} each cfgkeys;
    {.qetl.source.credential_var `$x} each srcnames;
    {string .qetl.source.fixture_version `$x} each ("v1";"v2~fixture");
    {string run_version[`source`revision!(`demo_deals;2);`$x]} each enlist "v1";
    {string run_version[`source`revision!(`demo_deals;0N);`$x]} each enlist "v1");
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
