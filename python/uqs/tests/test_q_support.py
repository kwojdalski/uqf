"""A job's `q` key: the q interpreters it runs on, refused elsewhere (#2747)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from uqs.deploy import overrides
from uqs.deploy.config import DeployError
from uqs.deploy.selection import Selection, select_jobs
from uqs.interpreter import KDBX, PEACHQ
from uqs.model import q_support
from uqs.model.declarations import read_file_text
from uqs.paths import UqsError
from uqs.stack import start_policy

# --- the form ---------------------------------------------------------------


def test_each_term_form_parses_and_spells_back() -> None:
    text = "kdbx kdbx>=4.1 kdbx=4 peachq"
    terms = q_support.parse(text)
    assert terms == (
        q_support.Term(KDBX),
        q_support.Term(KDBX, ">=", (4, 1)),
        q_support.Term(KDBX, "=", (4,)),
        q_support.Term(PEACHQ),
    )
    assert q_support.text(terms) == text


@pytest.mark.parametrize("bad", ["kdbx>=", "kdbx=4.", "kdbx>=4.x", "peachq>=1", "q5", "KDBX"])
def test_a_malformed_term_is_refused_by_name(bad: str) -> None:
    with pytest.raises(ValueError, match=f"holds {bad}, which is not kdbx"):
        q_support.parse(f"kdbx {bad}")


def test_a_q_naming_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="names no interpreter"):
        q_support.parse("  ")


# --- the meaning ------------------------------------------------------------

#: (q key, then whether each q runs it: kdb+ 4.0 - whose .z.K prints "4" -, 4.1,
#: KDB-X 5.0 and PeachQ).
QS = [(KDBX, "4"), (KDBX, "4.1"), (KDBX, "5.0"), (PEACHQ, "5.0")]


@pytest.mark.parametrize(
    ("key", "runs"),
    [
        ("kdbx", [True, True, True, False]),
        ("peachq", [False, False, False, True]),
        ("kdbx>=4.1", [False, True, True, False]),
        ("kdbx=4", [True, True, False, False]),
        ("kdbx=4.0", [True, False, False, False]),
        ("kdbx>=5 peachq", [False, False, True, True]),
    ],
)
def test_a_q_runs_the_job_when_a_term_names_it(key: str, runs: list[bool]) -> None:
    terms = q_support.parse(key)
    assert [q_support.supports(terms, impl, version) for impl, version in QS] == runs


def test_no_q_key_runs_on_every_q() -> None:
    assert all(q_support.supports((), impl, version) for impl, version in QS)


def test_an_unknown_version_meets_no_version_but_meets_a_bare_interpreter() -> None:
    assert not q_support.supports(q_support.parse("kdbx>=4.1"), KDBX, "")
    assert q_support.supports(q_support.parse("kdbx"), KDBX, "")


# --- read from the declaration ------------------------------------------------

STREAM = """.qetl.job.stream.define[`zz_job;`procname`subscribe_to`outputs`on_batch`q!(
    `zz_job1; enlist `trades; `symbol$(); {[t;x] }; "kdbx>=4.1 peachq")];
"""


def test_the_declaration_carries_its_q() -> None:
    (d,) = read_file_text(STREAM, Path("zz_job.q"))
    assert q_support.text(d.q) == "kdbx>=4.1 peachq"


def test_a_declaration_without_q_runs_everywhere() -> None:
    (d,) = read_file_text(STREAM.replace("`q!(", "!(").replace('; "kdbx>=4.1 peachq"', ""),
                          Path("zz_job.q"))  # fmt: skip
    assert d.q == ()


def test_a_malformed_declared_q_is_refused_where_it_is_read() -> None:
    with pytest.raises(UqsError, match="zz_job.q: zz_job's q holds q5"):
        read_file_text(STREAM.replace("kdbx>=4.1 peachq", "q5"), Path("zz_job.q"))


# --- uqs start ----------------------------------------------------------------


def _paths() -> Any:
    """The one field refuse_unsupported reads of UqsPaths for named processes."""
    return cast(Any, SimpleNamespace(repo_root=Path(".")))


def _cfg(reason: str | None) -> Any:
    """The one field for_job_q reads of a deploy Config."""
    return cast(Any, SimpleNamespace(accept_job_q=reason))


@pytest.fixture
def declared(monkeypatch):
    """The tree declares zz_job (process zz_job1) on KDB-X 5 or later, and
    zz_any (zz_any1) on every q; the q is whatever `q` is set to."""
    jobs = read_file_text(STREAM.replace("kdbx>=4.1 peachq", "kdbx>=5"), Path("zz_job.q"))
    jobs += read_file_text(
        STREAM.replace("zz_job", "zz_any").replace("`q!(", "!(").replace('; "kdbx>=5"', ""),
        Path("zz_any.q"),
    )
    asked = SimpleNamespace(q=(KDBX, "4.1"), count=0)

    def impl(_env):
        asked.count += 1
        return asked.q[0]

    monkeypatch.setattr(start_policy, "read_declarations", lambda _root: jobs)
    monkeypatch.setattr(start_policy, "q_interpreter", lambda _env: Path("/opt/q"))
    monkeypatch.setattr(start_policy, "q_impl", impl)
    monkeypatch.setattr(start_policy, "q_version", lambda _q: asked.q[1])
    return asked


def test_a_start_on_a_q_the_job_does_not_name_is_refused(declared) -> None:
    paths = _paths()
    with pytest.raises(UqsError) as err:
        start_policy.refuse_unsupported(paths, "rdb1 zz_job1", {})
    assert 'KDB-X 4.1, which the job declares no support for: zz_job (q "kdbx>=5")' in str(
        err.value
    )
    assert "--skip-q-version-check" in str(err.value)


def test_a_start_on_a_q_it_names_passes(declared) -> None:
    declared.q = (KDBX, "5.0")
    start_policy.refuse_unsupported(_paths(), "zz_job1", {})


def test_the_q_is_not_asked_when_no_started_job_declares_one(declared) -> None:
    start_policy.refuse_unsupported(_paths(), "rdb1 zz_any1", {})
    assert declared.count == 0


# --- uqs deploy push ------------------------------------------------------------

MANIFEST = {
    "bundles": {
        "pb": {
            "version": "1",
            "jobs": [
                {"name": "zz_job", "kind": "streaming", "procname": "zz_job1", "q": "kdbx>=5"},
                {"name": "zz_any", "kind": "streaming", "procname": "zz_any1"},
            ],
        }
    }
}


def test_the_selection_keeps_each_chosen_jobs_q() -> None:
    assert select_jobs(MANIFEST, ["zz_job", "zz_any"]).q == {"zz_job": "kdbx>=5"}
    assert select_jobs(MANIFEST, ["zz_any"]).q == {}


def test_a_push_onto_a_q_a_job_does_not_name_is_refused() -> None:
    cfg = _cfg(None)
    sel = select_jobs(MANIFEST, ["zz_job"])
    with pytest.raises(DeployError, match=r"KDB-X 4\.1, which the job declares no support"):
        overrides.for_job_q(cfg, sel, {"qversion": "4.1"})


def test_a_bundled_peachq_is_judged_as_peachq() -> None:
    cfg = _cfg(None)
    sel = select_jobs(MANIFEST, ["zz_job"])
    with pytest.raises(DeployError, match="the q is PeachQ"):
        overrides.for_job_q(cfg, sel, {"qversion": "5.0", "qimpl": PEACHQ})


def test_accept_job_q_deploys_and_records_what_it_waived() -> None:
    cfg = _cfg("this site's 4.1 has what it needs")
    sel = select_jobs(MANIFEST, ["zz_job"])
    (entry,) = overrides.for_job_q(cfg, sel, {"qversion": "4.1"})
    assert entry == {
        "override": "accept-job-q",
        "reason": "this site's 4.1 has what it needs",
        "requirement": 'zz_job: q "kdbx>=5"',
        "observed": "KDB-X 4.1",
        "applied": True,
    }


def test_no_declared_q_is_no_check() -> None:
    assert overrides.for_job_q(_cfg(None), Selection(), {}) == []
