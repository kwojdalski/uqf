"""POST /compare: the Compare view's API (#2033).

The API only delegates: reaching each side is `uqs.cli.data_diff.run`'s, and
every rule is q's (src/etl/core/compare.q). So most cases hand the route a
stand-in for `run` and hold what it is asked and what comes back; the last
reaches two FIXTURE TARGETS - real q processes standing in for two gateways -
through uqs's real `run`, so the document the browser reads is the one q wrote.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import sqlite3
import subprocess
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs.cli import data_diff as uqs_diff
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsUsageError, default_paths
from uqs.stack import data_diff as uqs_stack_diff

TOKEN = secrets.token_hex(32)
BODY = {
    "left": "local",
    "right": "box",
    "tables": ["demo_deals"],
    "range_from": "2026-09-11T00:00:00Z",
    "range_to": "2026-09-16T00:00:00Z",
    "filters": [{"column": "sym", "op": "eq", "value": "EURUSD"}],
    "limit": 50,
}


def table(outcome: str = "equal_in_sample", **kw) -> dict:
    return {
        "table": "demo_deals",
        "outcome": outcome,
        "label": "equal in a sample cut at 50 rows per side - NOT proof the tables are equal",
        "truncated": True,
        "key": ["deal_id"],
        "basis": "events",
        "read": [50, 50],
        "schema": [],
        "rows": [],
        "unpaired": 0,
        "coverage": {s: {"checked": False, "covered": [], "gaps": []} for s in ("left", "right")},
        "reasons": {"left": "", "right": ""},
    } | kw


def document(outcome: str = "equal_in_sample", **kw) -> dict:
    return {
        "ok": outcome == "equal_in_sample",
        "outcome": outcome,
        "left": "local",
        "right": "box",
        "range_from": "2026-09-11T00:00:00.000000000+00:00",
        "range_to": "2026-09-16T00:00:00.000000000+00:00",
        "limit": 50,
        "filters": ["sym=EURUSD"],
        "source_version": "",
        # uqs data diff's document, which keeps the ledger's word (#2261)
        "partition": "",
        "tables": [table(outcome, **kw)],
    }


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "deploy_targets.toml"
    path.write_text('[targets.box]\nssh = "ops@box.example"\ndest = "/srv/uqf"\n')
    monkeypatch.setenv("UQS_DEPLOY_TARGETS", str(path))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    monkeypatch.delenv("UQS_RUNTIME", raising=False)
    return path


@pytest.fixture
def asked(monkeypatch):
    """uqs's run replaced: records each call and answers `answer[0]`."""
    calls: list[dict] = []
    answer: list = [document()]

    def run(paths, left, right, tables, window, **kw):
        calls.append({"left": left, "right": right, "tables": tables, "window": window, **kw})
        if isinstance(answer[0], Exception):
            raise answer[0]
        return answer[0]

    monkeypatch.setattr(uqs_diff, "run", run)
    return calls, answer


def _client(*, writes: bool = True, settings: Settings | None = None) -> TestClient:
    settings = settings or (
        Settings(enable_writes=True, write_token=TOKEN) if writes else Settings()
    )
    return TestClient(
        create_app(gateway=FakeGateway(), settings=settings),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {TOKEN}"} if writes else {},
    )


def test_the_request_reaches_uqs_as_given(registry, asked):
    calls, _ = asked
    r = _client().post("/compare", json=BODY)
    assert r.status_code == 200, r.text
    (call,) = calls
    assert (call["left"], call["right"], call["tables"]) == ("local", "box", ["demo_deals"])
    assert (call["filters"], call["limit"], call["source_version"]) == (["sym=EURUSD"], 50, None)
    assert [b.isoformat() for b in call["window"].bounds] == [
        "2026-09-11T00:00:00+00:00",
        "2026-09-16T00:00:00+00:00",
    ]


def test_the_response_is_uqs_document_with_its_truncation_label(registry, asked):
    doc = _client().post("/compare", json=BODY).json()
    assert (doc["outcome"], doc["range_from"], doc["left"], doc["right"]) == (
        "equal_in_sample",
        "2026-09-11T00:00:00.000000000+00:00",
        "local",
        "box",
    )
    t = doc["tables"][0]
    assert t["truncated"] is True and "NOT proof" in t["label"]
    assert t["schema"] == [] and t["read"] == [50, 50]


@pytest.mark.parametrize(
    "outcome",
    [
        "endpoint_unavailable",
        "missing_table",
        "differs",
        "not_comparable",
        "incomplete_coverage",
        "equal_in_sample",
    ],
)
def test_every_outcome_is_returned_not_refused(registry, asked, outcome):
    _, answer = asked
    answer[0] = document(outcome)
    r = _client().post("/compare", json=BODY)
    assert r.status_code == 200 and r.json()["outcome"] == outcome
    assert r.json()["ok"] is (outcome == "equal_in_sample")


def test_two_local_sides_need_no_remote_operations(registry, asked):
    r = _client(writes=False).post("/compare", json=BODY | {"right": "local", "left": "local"})
    assert r.status_code == 200, r.text


def test_a_target_side_needs_remote_operations(registry, asked):
    r = _client(writes=False).post("/compare", json=BODY)
    assert r.status_code == 403 and "reaches the server over ssh" in r.json()["detail"]
    assert not asked[0]


def test_an_unregistered_target_is_not_reached(registry, asked):
    r = _client().post("/compare", json=BODY | {"right": "nosuch"})
    assert r.status_code == 404 and "no deployment target 'nosuch'" in r.json()["detail"]
    assert not asked[0]


@pytest.mark.parametrize(
    "change",
    [
        {"range_from": "2026-09-11T00:00:00"},  # no offset: whose clock?
        {"limit": 0},
        {"limit": 100_001},
        {"tables": []},
        {"by": "x"},  # a key the request does not have
    ],
)
def test_a_request_that_is_not_explicit_is_refused(registry, asked, change):
    assert _client().post("/compare", json=BODY | change).status_code == 422
    assert not asked[0]


def test_a_usage_refusal_and_a_q_refusal_are_both_422(registry, asked):
    _, answer = asked
    answer[0] = UqsUsageError("--left and --right both name box - compare two endpoints")
    r = _client().post("/compare", json=BODY)
    assert r.status_code == 422 and "both name box" in r.json()["detail"]
    answer[0] = UqsError("data diff: filter sym: expected COLUMN OP VALUE")
    r = _client().post("/compare", json=BODY)
    assert r.status_code == 422 and r.json()["error"] == "CompareRefused"
    assert "expected COLUMN OP VALUE" in r.json()["detail"]


# --------------------------------------------------- the filters' shape (#2223)


def test_a_filter_is_the_object_query_takes_not_text(registry, asked):
    """`filters` was COLUMN OP VALUE text here and {column, op, value} on
    /query: the Desk's filters could not be handed to Compare."""
    r = _client().post("/compare", json=BODY | {"filters": ["sym=EURUSD"]})
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "RequestInvalid"
    assert (
        'filters are {column, op, value} objects, as on /query - `sym=EURUSD` is {"column": '
        '"sym", "op": "eq", "value": "EURUSD"}'
    ) in r.json()["detail"]
    assert not asked[0]


def test_the_response_echoes_the_filters_as_they_were_sent(registry, asked):
    doc = _client().post("/compare", json=BODY).json()
    assert doc["filters"] == BODY["filters"]


@pytest.mark.parametrize(
    ("sent", "text"),
    [
        ({"column": "sym", "op": "eq", "value": "EURUSD"}, "sym=EURUSD"),
        ({"column": "sym", "op": "ne", "value": "EURUSD"}, "sym!=EURUSD"),
        ({"column": "px", "op": "lt", "value": 1.5}, "px<1.5"),
        ({"column": "px", "op": "le", "value": 2}, "px<=2"),
        ({"column": "px", "op": "gt", "value": -1}, "px>-1"),
        ({"column": "px", "op": "ge", "value": "1.1"}, "px>=1.1"),
        ({"column": "live", "op": "eq", "value": True}, "live=1"),
        ({"column": "sym", "op": "in", "value": ["EURUSD", "GBPUSD"]}, "sym=EURUSD,GBPUSD"),
    ],
)
def test_each_operator_reaches_uqs_as_its_text(registry, asked, sent, text):
    calls, _ = asked
    r = _client().post("/compare", json=BODY | {"filters": [sent]})
    assert r.status_code == 200, r.text
    assert calls[0]["filters"] == [text]


@pytest.mark.parametrize(
    ("sent", "said"),
    [
        ({"column": "sym", "op": "eq", "value": "EURUSD,GBPUSD"}, "holds no comma"),
        ({"column": "sym", "op": "eq", "value": ""}, "is not empty"),
        ({"column": "sym", "op": "eq", "value": ["EURUSD"]}, "only `in` takes a list"),
        ({"column": "sym", "op": "in", "value": []}, "`in` takes a non-empty list"),
        ({"column": "sym", "op": "in", "value": "EURUSD"}, "`in` takes a non-empty list"),
        ({"column": "sym", "op": "eq", "value": {"a": 1}}, "a string, a number or a boolean"),
        ({"column": "sym=x", "op": "eq", "value": "y"}, "is not a column name"),
        # #2479: sent as px<=1.5 / px>=1.5, q would read le / ge
        ({"column": "px", "op": "lt", "value": "=1.5"}, "does not start with `=`"),
        ({"column": "px", "op": "gt", "value": "=1.5"}, "does not start with `=`"),
        ({"column": "sym", "op": "in", "value": ["=A", "B"]}, "does not start with `=`"),
    ],
)
def test_a_filter_that_cannot_be_rendered_is_refused_before_uqs(registry, asked, sent, said):
    r = _client().post("/compare", json=BODY | {"filters": [sent]})
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "ValidationFailed" and said in r.json()["detail"]
    assert not asked[0]


def test_a_value_q_would_read_as_part_of_the_operator_is_refused():
    """#2479: `lt` with `=1.5` must not reach uqs as `px<=1.5`."""
    from uqf_frontend.compare import filter_texts
    from uqf_frontend.errors import ValidationFailed
    from uqf_frontend.models.query import Filter

    with pytest.raises(ValidationFailed, match="does not start with `=`"):
        filter_texts([Filter(column="px", op="lt", value="=1.5")])


def test_every_query_operator_has_its_text():
    """compare.TEXT_OPS is the one place /query's words become uqs's text:
    an operator added to Filter.op without a text would reach uqs as a KeyError."""
    import typing

    from uqf_frontend import compare
    from uqf_frontend.models.query import Filter

    ops = typing.get_args(Filter.model_fields["op"].annotation)
    assert set(compare.TEXT_OPS) == set(ops)


@pytest.mark.skipif(q_interpreter() is None, reason="no q interpreter")
def test_q_reads_each_rendered_filter_back_as_the_operator_sent(tmp_path):
    """The text is only right if q's own parser (.qetl.compare.parse_filter)
    reads it back as the operator the API was sent."""
    from uqf_frontend import compare
    from uqf_frontend.models.query import Filter

    sent = {
        op: Filter.model_validate(
            {"column": "sym", "op": op, "value": ["A", "B"] if op == "in" else "A"}
        )
        for op in compare.TEXT_OPS
    }
    texts = dict(zip(sent, compare.filter_texts(list(sent.values())), strict=True))
    script = tmp_path / "parse.q"
    script.write_text(
        "\\l src/init.q\n\\l src/etl/init.q\n"
        + "".join(
            f'-1 "{op} ",string (.qetl.compare.parse_filter "{text}")`op;\n'
            for op, text in texts.items()
        )
        + "exit 0\n"
    )
    out = subprocess.run(
        [str(q_interpreter()), str(script), "-q"],
        cwd=default_paths().repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    ).stdout
    read = dict(line.split() for line in out.splitlines() if line.strip())
    assert read == {op: op for op in compare.TEXT_OPS}, out


@pytest.mark.skipif(q_interpreter() is None, reason="no q interpreter")
def test_q_types_a_compare_filter_as_query_coerces_it(tmp_path):
    """#2472: the same Filter is the same predicate on /compare as on /query -
    a timespan of 1234 is 1234 ns, and a timestamp's offset is applied."""
    from uqf_frontend import compare, queries
    from uqf_frontend.catalog import QType
    from uqf_frontend.models.query import Filter

    sent = [
        Filter(column="skew", op="eq", value=1234),
        Filter(column="time", op="ge", value="2026-09-13T06:00:00+02:00"),
        Filter(column="time", op="lt", value="2026-09-13T06:00:00.000000001Z"),
    ]
    span = queries.coerce(1234, QType.TIMESPAN, "skew", as_list=False)
    stamps = [queries.coerce(f.value, QType.TIMESTAMP, "time", as_list=False) for f in sent[1:]]
    texts = compare.filter_texts(sent)
    script = tmp_path / "typed.q"
    script.write_text(
        "\\l src/init.q\n\\l src/etl/init.q\n"
        'm:([] c:`skew`time; t:"np");\n'
        "fs:.qetl.compare.parse_filter each (" + ";".join(f'"{t}"' for t in texts) + ");\n"
        "v:last .qetl.compare.browse_filters[m;fs];\n"
        '-1 string "j"$v 0;\n'
        '-1 string "j"$v 1;\n'
        '-1 string "j"$v 2;\n'
        "exit 0\n"
    )
    out = subprocess.run(
        [str(q_interpreter()), str(script), "-q"],
        cwd=default_paths().repo_root,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    ).stdout.split()
    # q counts a timestamp's nanoseconds from 2000; polars from 1970
    epoch_2000 = 946_684_800 * 10**9
    assert int(out[0]) == span.dt.total_nanoseconds().item(), out
    assert [int(n) + epoch_2000 for n in out[1:]] == [s.cast(int).item() for s in stamps], out


# --------------------------------------------- the coverage slice (#2221)


def test_a_coverage_check_without_a_slice_is_refused_naming_the_sentinel(registry, asked):
    """/coverage's rule (#185): the slice is said, never defaulted. A blank
    slice used to read the no-slice-dimension slice, which a sliced dataset
    records nothing under, and report every window a gap."""
    r = _client().post("/compare", json=BODY | {"source_version": "v1"})
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "RequestInvalid"
    assert 'slice is required with source_version - give ""' in r.json()["detail"]
    r = _client().post("/compare", json=BODY | {"source_version": "v1", "slice": None})
    assert r.status_code == 422, r.text
    assert not asked[0]


def test_a_slice_without_a_source_version_is_refused(registry, asked):
    r = _client().post("/compare", json=BODY | {"slice": "EURUSD"})
    assert r.status_code == 422, r.text
    assert "slice reads coverage, which needs source_version" in r.json()["detail"]
    r = _client().post("/compare", json=BODY | {"slice": ""})
    assert r.status_code == 422, r.text
    assert not asked[0]


def test_an_empty_source_version_is_refused_not_read_as_none(registry, asked):
    r = _client().post("/compare", json=BODY | {"source_version": "", "slice": ""})
    assert r.status_code == 422, r.text
    assert not asked[0]


def test_the_no_slice_dimension_slice_is_asked_for_explicitly(registry, asked):
    calls, answer = asked
    answer[0] = document() | {"source_version": "v1", "partition": ""}
    r = _client().post("/compare", json=BODY | {"source_version": "v1", "slice": ""})
    assert r.status_code == 200, r.text
    assert (calls[0]["source_version"], calls[0]["partition"]) == ("v1", "")
    assert (r.json()["source_version"], r.json()["slice"]) == ("v1", "")
    assert "partition" not in r.json()


def test_a_named_slice_reaches_uqs_and_comes_back_as_slice(registry, asked):
    """The API's `slice` is the ledger's `partition` at the uqs/q boundary,
    both ways (#2261)."""
    calls, answer = asked
    answer[0] = document() | {"source_version": "v1", "partition": "EURUSD"}
    r = _client().post("/compare", json=BODY | {"source_version": "v1", "slice": "EURUSD"})
    assert r.status_code == 200, r.text
    assert calls[0]["partition"] == "EURUSD"
    assert r.json()["slice"] == "EURUSD"


@pytest.mark.parametrize("body", [{"partition": ""}, {"source_version": "v1", "partition": ""}])
def test_a_request_still_sending_partition_is_refused_naming_slice(registry, asked, body):
    """No alias (#2261): `partition` is not read as the slice, and not
    dropped as an unknown key either - the 422 says what it is now."""
    r = _client().post("/compare", json=BODY | body)
    assert r.status_code == 422, r.text
    assert "`partition` is now `slice`" in r.json()["detail"]
    assert not asked[0]


def test_no_coverage_check_answers_null_not_the_empty_slice(registry, asked):
    """q answers an unchecked slice as "" (its null symbol); the API says null,
    so "" in a response means only the no-slice-dimension slice."""
    doc = _client().post("/compare", json=BODY).json()
    assert (doc["source_version"], doc["slice"]) == (None, None)


def test_a_policy_without_compare_refuses_it(registry, asked, tmp_path):
    policy = tmp_path / "policy.csv"
    policy.write_text("p, *, desk, read\n")
    settings = Settings(enable_writes=True, write_token=TOKEN, policy_file=policy)
    r = _client(settings=settings).post("/compare", json=BODY)
    assert r.status_code == 403 and "compare:read" in r.json()["detail"]
    assert not asked[0]


def _policy(tmp_path, *lines: str) -> Settings:
    policy = tmp_path / "policy.csv"
    policy.write_text("".join(f"{line}\n" for line in lines))
    return Settings(
        enable_writes=True,
        write_token=TOKEN,
        policy_file=policy,
        activity_db=tmp_path / "activity.sqlite3",
    )


@pytest.mark.parametrize("sides", [{"right": "box"}, {"left": "box", "right": "local"}])
def test_a_target_side_needs_services_read(registry, asked, tmp_path, sides):
    """A side names a deploy target, which the workspace header reaches only
    with services:read (#2066): a caller refused the registry may not read a
    target's rows through /compare either - and uqs is never called."""
    settings = _policy(tmp_path, "p, *, compare, read", "p, *, desk, read")
    c = _client(settings=settings)
    workspace = c.post(
        "/query", json={"table": "trades", "limit": 5}, headers={"x-uqf-target": "box"}
    )
    assert workspace.status_code == 403 and "services:read" in workspace.json()["detail"]
    r = c.post("/compare", json=BODY | sides)
    assert r.status_code == 403, r.text
    assert r.json()["error"] == "Forbidden"
    assert r.json()["detail"] == (
        "target box is a deployment target, and services:read is not granted to anonymous"
    )
    assert not asked[0]


def test_a_target_side_with_services_read_is_compared(registry, asked, tmp_path):
    settings = _policy(tmp_path, "p, *, compare, read", "p, *, services, read")
    r = _client(settings=settings).post("/compare", json=BODY)
    assert r.status_code == 200, r.text
    assert [(c["left"], c["right"]) for c in asked[0]] == [("local", "box")]


def test_a_local_comparison_needs_no_services_read(registry, asked, tmp_path):
    settings = _policy(tmp_path, "p, *, compare, read")
    r = _client(settings=settings).post("/compare", json=BODY | {"right": "local"})
    assert r.status_code == 200, r.text
    assert [(c["left"], c["right"]) for c in asked[0]] == [("local", "local")]


def test_a_target_side_refused_services_read_is_audited(registry, asked, tmp_path):
    settings = _policy(tmp_path, "p, *, compare, read")
    r = _client(settings=settings).post("/compare", json=BODY, headers={"x-uqf-user": "bob"})
    assert r.status_code == 403, r.text
    with sqlite3.connect(tmp_path / "activity.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        (row,) = list(conn.execute("SELECT * FROM activity ORDER BY seq"))
    assert (row["action"], row["kind"], row["outcome"]) == ("authz.refused", "attempt", "refused")
    assert (row["subject"], row["status_code"], row["claimed_user"]) == ("/compare", 403, "bob")
    assert json.loads(row["detail"])["permission"] == "services:read"


def test_a_target_side_needs_the_write_token(registry, asked):
    """A target side is reached over ssh with the operator's key, so it
    carries the token as /services/{target}/... does (#1950, #2067)."""
    settings = Settings(enable_writes=True, write_token=TOKEN)
    c = TestClient(
        create_app(gateway=FakeGateway(), settings=settings), base_url="http://localhost"
    )
    r = c.post("/compare", json=BODY)
    assert r.status_code == 401, r.text
    assert r.json()["error"] == "WriteTokenRequired"
    assert "need the write token" in r.json()["detail"]
    assert r.headers["www-authenticate"] == "Bearer"
    wrong = c.post("/compare", json=BODY, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401 and "does not match" in wrong.json()["detail"]
    assert not asked[0]
    local = c.post("/compare", json=BODY | {"right": "local"})
    assert local.status_code == 200, local.text


def test_a_target_side_needs_an_allowed_host(registry, asked):
    r = TestClient(
        create_app(gateway=FakeGateway(), settings=Settings(enable_writes=True, write_token=TOKEN)),
        base_url="http://evil.example",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ).post("/compare", json=BODY)
    assert r.status_code == 403 and r.json()["error"] == "HostNotAllowed"
    assert not asked[0]


# --------------------------------------------- end to end, on fixture targets

#: A gateway in miniature, as uqs's own test of `data diff` builds one: the
#: plant's demo_deals and the three reads a comparison makes.
FIXTURE = r"""
\l src/init.q
\l src/etl/init.q
deals:.qpipe.source.demo_deals.fixture[];
demo_deals:(cols .qetl.plant.schema `demo_deals)#update time:deal_time+0D00:00:01 from deals;
if[`changed~`$getenv`FIXTURE_SHAPE; update rate:1.2 from `demo_deals where deal_id=3];
.gw.syncexec:{[qry;procs] value qry};
.uqf.browse:{[tn;fc;fo;fv;lim;procs]
    r:select from (`time xasc value tn) where time>=fv 0, time<fv 1;
    `rows`cap!(lim sublist r;lim)};
.qetl.coverage.claims:{[ds;part;release;at] ([] range_from:`timestamp$(); range_to:`timestamp$())};
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(q_interpreter() is None, reason="no q interpreter")
def test_the_view_reads_q_s_own_document_from_fixture_targets(registry, tmp_path, monkeypatch):
    script = tmp_path / "gateway.q"
    script.write_text(FIXTURE)
    procs, sides = [], {}
    for name, shape in (("local", "same"), ("box", "changed")):
        port = _free_port()
        procs.append(
            subprocess.Popen(
                [str(q_interpreter()), str(script), "-p", str(port), "-q"],
                cwd=default_paths().repo_root,
                env={**os.environ, "FIXTURE_SHAPE": shape},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        sides[name] = uqs_stack_diff.Endpoint(name, f"localhost:{port}")
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @contextmanager
    def reached(name, user, passwd, paths=None):
        yield sides[name]

    monkeypatch.setattr(uqs_diff, "reach", reached)
    try:
        r = _client().post("/compare", json=BODY | {"filters": []})
    finally:
        for p in procs:
            p.kill()
            p.wait(timeout=10)
    assert r.status_code == 200, r.text
    t = r.json()["tables"][0]
    assert (t["outcome"], t["truncated"], t["read"]) == ("differs", False, [5, 5])
    assert t["rows"] == [
        {"rowkey": "deal_id=3", "kind": "changed", "detail": "rate: 1.0847 -> 1.2"}
    ]


def test_the_tabs_workspace_does_not_move_a_comparison(registry, asked):
    """Both sides are in the body, so the workspace header is ignored (#2029):
    a tab working in `box` still compares exactly what it names."""
    calls, _ = asked
    r = _client().post(
        "/compare", json=BODY | {"right": "local", "left": "local"}, headers={"X-UQF-Target": "box"}
    )
    assert r.status_code == 200, r.text
    assert (calls[0]["left"], calls[0]["right"]) == ("local", "local")
