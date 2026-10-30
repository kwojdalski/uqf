"""The deploy check's contract with `.qetl.transform.verify_all[]` (#1784).

`uqs deploy check` asks every process that loads the ETL tree whether each
registered transform still reproduces its examples, and reads ONE thing of
verify_all's result: its boolean `passed` column, reduced on the process by
`all` (verify.ETL_EXPR). A name-existence check proves the column is spelled
right; it does not prove what the check makes of a result that is shaped
differently, or of a verify_all that throws. Before this, both read as
success on a process outside the pipelines - the trap answered 0n, "not
loaded" - and any truthy answer read as `ok`.

Two halves. The q half runs ETL_EXPR itself under KDB-X against the loaded
tree: passing, failing, not loaded, throwing, and each malformed result. It
skips without KDB-X, as test_etl_standalone_load.py does. The Python half
feeds check_process the answers kola decodes (measured: a q boolean is a
Python bool, a string or symbol a str, 0n a NaN, 0N the long-null int) and
needs no q. tests/q/test_transform.q pins the result's columns and types on
the q side, where verify_all lives.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from uqs.deploy import verify
from uqs.interpreter import q_interpreter

UQF_ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_SECONDS = 120

#: Each scenario: a label, the q run before ETL_EXPR is evaluated (in order, in
#: one process), and the verdict check_process must reach.
_WRONG_MID = (
    ".qetl.transform.define[`xf_contract_wrong;`inputs`output`fn`examples!("
    "enlist[`quotes]!enlist ([] sym:`symbol$(); bid:`float$(); ask:`float$());"
    "([] sym:`symbol$(); mid:`float$());"
    "{[q] select sym, mid:bid from q};"
    "enlist `inputs`expected!(enlist[`quotes]!enlist ([] sym:enlist`EURUSD; bid:1.1; ask:1.2);"
    "([] sym:enlist`EURUSD; mid:1.15)))]"
)
SCENARIOS = [
    ("not_loaded", "", "not loaded"),
    ("passing", "\\l src/init.q\n\\l src/etl/init.q", "ok"),
    ("failing", _WRONG_MID, "failed"),
    ("threw", '.qetl.transform.verify_all:{[] \'"boom"}', "failed: verify_all threw: boom"),
    (
        "long_passed",
        ".qetl.transform.verify_all:{[] ([] passed:1 1)}",
        "failed: verify_all's passed is type 7, not boolean",
    ),
    (
        "no_passed",
        ".qetl.transform.verify_all:{[] ([] ok:11b)}",
        "failed: verify_all answered no passed column",
    ),
    (
        "atom",
        ".qetl.transform.verify_all:{[] 1b}",
        "failed: verify_all answered type -1, not a table",
    ),
    (
        "empty",
        ".qetl.transform.verify_all:{[] ([] passed:`boolean$())}",
        "failed: verify_all verified no transform",
    ),
    ("well_formed", ".qetl.transform.verify_all:{[] ([] passed:11b)}", "ok"),
]


def _kdbx() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


@pytest.fixture(scope="module")
def q_answers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """{label: ETL_EXPR's answer}, as JSON decodes q's .j.j of it."""
    qbin, env = _kdbx()
    tmp = tmp_path_factory.mktemp("verify_contract")
    expr = tmp / "etl_expr.txt"
    expr.write_text(verify.ETL_EXPR + "\n")
    lines = [f'e:first read0 `$":{expr}";']
    for label, setup, _ in SCENARIOS:
        if setup:
            lines.append(setup + ";" if not setup.startswith("\\") else setup)
        lines.append(f'-1 "{label}\\t",.j.j value e;')
    lines.append("exit 0")
    script = tmp / "verify_contract.q"
    script.write_text("\n".join(lines) + "\n")
    result = subprocess.run(
        [qbin, str(script), "-q"],
        cwd=UQF_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
    )
    out = result.stdout.decode(errors="replace")
    assert result.returncode == 0, f"{out}\n{result.stderr.decode(errors='replace')}"
    answers = dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)
    return {label: json.loads(answer) for label, answer in answers.items()}


@pytest.mark.parametrize(("label", "verdict"), [(s[0], s[2]) for s in SCENARIOS])
def test_qs_answer_reaches_the_verdict_it_means(q_answers, label, verdict):
    assert verify.etl_verdict(q_answers[label]) == verdict


# --- what kola hands Python, without q ------------------------------------------


def _check(etl: object, pipeline: bool) -> verify.ProcessCheck:
    answers = {
        verify.IDENTITY_EXPR: "rdb1",
        verify.LIBRARY_EXPR: verify.LIBRARY_EXPECTED,
        verify.ETL_EXPR: etl,
    }
    return verify.check_process(
        verify.ProcessCheck("rdb1", 5002, pipeline), lambda expr, port: answers[expr]
    )


@pytest.mark.parametrize("pipeline", [True, False])
def test_every_example_passing_is_ok(pipeline):
    p = _check(True, pipeline)
    assert (p.etl, p.ok) == ("ok", True)


@pytest.mark.parametrize("pipeline", [True, False])
def test_a_failing_example_fails(pipeline):
    p = _check(False, pipeline)
    assert (p.etl, p.ok) == ("failed", False)


@pytest.mark.parametrize("pipeline", [True, False])
def test_a_verify_all_that_threw_fails_rather_than_reading_as_not_loaded(pipeline):
    p = _check("verify_all threw: type", pipeline)
    assert p.etl == "failed: verify_all threw: type" and not p.ok


@pytest.mark.parametrize("answer", [1, 1.0, "true", [True], {"passed": True}, b"\x01"])
@pytest.mark.parametrize("pipeline", [True, False])
def test_a_truthy_answer_that_is_not_a_boolean_is_never_ok(answer, pipeline):
    p = _check(answer, pipeline)
    assert p.etl.startswith("failed") and not p.ok, p.etl


@pytest.mark.parametrize("null", [None, float("nan"), *verify.Q_INT_NULLS])
def test_q_null_is_not_loaded_which_only_a_pipeline_fails_on(null):
    assert _check(null, pipeline=True).etl == "not loaded"
    assert not _check(null, pipeline=True).ok
    assert _check(null, pipeline=False).ok
