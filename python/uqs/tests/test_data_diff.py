"""`uqs data diff`: one table on two endpoints, compared (#2033).

The rules are q's (src/etl/core/compare.q, held by tests/q/test_compare.q); what
is held here is the orchestration around them - each side named and reached, the
call written without splicing caller text into q unchecked, the report turned
into text, JSON and an exit code - and, end to end, two FIXTURE TARGETS: real q
processes standing in for two gateways, each serving the three reads a
comparison makes (meta through .gw.syncexec, .uqf.browse, .qetl.coverage.claims)
from tables it was given. Every outcome is reached through them.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from plain_cli import PlainRunner

from uqs import cli, timerange
from uqs.cli import data_diff as diff_cli
from uqs.deploy import diff_endpoints
from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsUsageError, default_paths
from uqs.stack import data_diff

runner = PlainRunner()
WINDOW = ("--from", "2026-09-11", "--to", "2026-09-16")


def window():
    return timerange.resolve("2026-09-11", "2026-09-16")


# --------------------------------------------------------------------- the call


def _script(**kw: Any) -> str:
    args: dict[str, Any] = {
        "left": data_diff.Endpoint("local", "localhost:5000"),
        "right": data_diff.Endpoint("prod", "127.0.0.1:61000"),
        "tables": ["demo_deals"],
        "window": window(),
        "filters": [],
        "limit": 100,
        "source_version": None,
        "partition": None,
    } | kw
    return data_diff.script(**args)


def test_the_script_calls_the_one_q_function_with_both_sides_and_the_window():
    text = _script(filters=["sym=EURUSD"], source_version="v1")
    assert "\\l src/etl/init.q" in text
    assert '.qetl.compare.run[`name`address`error!("local";"localhost:5000";"");' in text
    assert "(),`demo_deals;" in text
    assert "2026.09.11D00:00:00.000000000;2026.09.16D00:00:00.000000000;" in text
    assert 'enlist "sym=EURUSD";100j;`$"v1";`;first read0 hsym `$"login")' in text


def test_the_password_is_never_in_the_script():
    assert "admin" not in _script() and "first read0 hsym" in _script()


def test_a_filter_or_reason_q_could_misread_is_quoted():
    text = _script(
        filters=['note="a;b"'], right=data_diff.Endpoint("prod", error='ssh said "no"\\')
    )
    assert 'enlist "note=\\"a;b\\""' in text
    assert '"ssh said \\"no\\"\\\\"' in text


def test_a_one_character_text_is_a_q_string():
    assert '`name`address`error!(enlist "p";"";"")' in _script(right=data_diff.Endpoint("p"))


@pytest.mark.parametrize(
    ("kw", "said"),
    [
        ({"tables": ["1bad"]}, "is not a table name"),
        ({"tables": ["x`y"]}, "is not a table name"),
        ({"tables": []}, "name at least one --table"),
        ({"limit": 0}, "--limit 0: expected 1 to"),
        ({"limit": data_diff.MAX_LIMIT + 1}, "expected 1 to"),
        ({"source_version": 'v1"; exit 1'}, "--source-version"),
        ({"partition": "a b"}, "--slice"),
    ],
)
def test_what_q_could_misread_is_refused_before_it_is_spliced(kw, said):
    with pytest.raises(UqsError, match=said):
        _script(**kw)


def fake_q(monkeypatch, answer: dict) -> dict:
    sent: dict = {}
    monkeypatch.setattr(data_diff, "q_interpreter", lambda: Path("/usr/bin/q"))

    def run(cmd, **kw):
        sent["script"] = Path(cmd[1]).read_text()
        login = Path(cmd[1]).parent / "login"
        sent["login"] = (login.read_text(), login.stat().st_mode & 0o777)
        head = {k: v for k, v in answer.items() if k != "tables"}
        rows = "".join(f"UQS_DIFF_TABLE {json.dumps(t)}\n" for t in answer.get("tables", []))
        out = f"loading\nUQS_DIFF {json.dumps(head)}\n{rows}"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(data_diff.subprocess, "run", run)
    return sent


def test_the_login_reaches_q_in_a_file_only_it_reads(monkeypatch):
    sent = fake_q(monkeypatch, report("equal_in_sample"))
    data_diff.compare(
        default_paths(),
        data_diff.Endpoint("a", "h:1"),
        data_diff.Endpoint("b", "h:2"),
        ["demo_deals"],
        window(),
        login="u:secret",
    )
    assert sent["login"] == ("u:secret", 0o600)
    assert "secret" not in sent["script"]


def test_a_q_refusal_is_the_commands_error(monkeypatch):
    fake_q(monkeypatch, {"error": "filter sym: expected COLUMN OP VALUE"})
    with pytest.raises(UqsError, match="data diff: filter sym: expected COLUMN OP VALUE"):
        data_diff.compare(
            default_paths(),
            data_diff.Endpoint("a", "h:1"),
            data_diff.Endpoint("b", "h:2"),
            ["demo_deals"],
            window(),
        )


def _compared(**kw: Any) -> dict:
    return data_diff.compare(
        default_paths(),
        data_diff.Endpoint("a", "h:1"),
        data_diff.Endpoint("b", "h:2"),
        ["demo_deals"],
        window(),
        **kw,
    )


def test_a_q_refusal_is_not_a_fault_of_the_run(monkeypatch):
    """#2529: the frontend tells a refusal (422) from a run that failed."""
    fake_q(monkeypatch, {"error": "filter sym: expected COLUMN OP VALUE"})
    with pytest.raises(UqsError) as caught:
        _compared()
    assert not isinstance(caught.value, data_diff.DiffNotRun | data_diff.DiffTimedOut)


def test_no_interpreter_and_a_crashed_run_are_diff_not_run(monkeypatch):
    monkeypatch.setattr(data_diff, "q_interpreter", lambda: None)
    with pytest.raises(data_diff.DiffNotRun, match="no q interpreter"):
        _compared()
    monkeypatch.setattr(data_diff, "q_interpreter", lambda: Path("q"))
    monkeypatch.setattr(
        data_diff.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="'boom"),
    )
    with pytest.raises(data_diff.DiffNotRun, match="did not run"):
        _compared()


def test_a_run_past_its_timeout_is_diff_timed_out(monkeypatch):
    monkeypatch.setattr(data_diff, "q_interpreter", lambda: Path("q"))
    seen = {}

    def run(cmd, **kw):
        seen["timeout"] = kw["timeout"]
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(data_diff.subprocess, "run", run)
    with pytest.raises(data_diff.DiffTimedOut, match="no result after 40s"):
        _compared(timeout=40)
    assert seen["timeout"] == 40


# ------------------------------------------------------------- the command


def verdict(outcome: str, **kw) -> dict:
    return {
        "table": "demo_deals",
        "outcome": outcome,
        "label": f"{outcome} - the label",
        "truncated": False,
        "key": ["deal_id"],
        "basis": "events",
        "read": [5, 5],
        "schema": [],
        "rows": [],
        "unpaired": 0,
        "coverage": {
            side: {"checked": False, "covered": [], "gaps": []} for side in ("left", "right")
        },
        "reasons": {"left": "", "right": ""},
    } | kw


def report(outcome: str, **kw) -> dict:
    return {
        "left": "local",
        "right": "prod",
        "range_from": "2026-09-11T00:00:00.000000000",
        "range_to": "2026-09-16T00:00:00.000000000",
        "limit": 1000,
        "filters": [],
        "source_version": "",
        "partition": "",
        "tables": [verdict(outcome, **kw)],
        "outcome": outcome,
    }


@contextmanager
def _reached(name: str, user: str, passwd: str, paths=None) -> Iterator[data_diff.Endpoint]:
    yield data_diff.Endpoint(name, "localhost:1")


def invoke(monkeypatch, answer: dict, *extra: str):
    sent = fake_q(monkeypatch, answer)
    monkeypatch.setattr(diff_endpoints, "reach", _reached)
    result = runner.invoke(
        cli.app,
        ["data", "diff", "--left", "local", "--right", "prod", "--table", "demo_deals", *WINDOW,
         *extra],
    )  # fmt: skip
    return result, sent


@pytest.mark.parametrize("outcome", data_diff.OUTCOMES)
def test_only_equal_in_sample_exits_0(monkeypatch, outcome):
    r, _ = invoke(monkeypatch, report(outcome))
    assert r.exit_code == (0 if outcome == data_diff.EQUAL else 1), r.output
    assert f"{outcome}  demo_deals" in r.output and f"{outcome} - the label" in r.output


def test_json_carries_every_table_and_its_times_in_utc(monkeypatch):
    gap = {
        "range_from": "2026-09-13T00:00:00.000000000",
        "range_to": "2026-09-16T00:00:00.000000000",
    }
    cov = {"left": {"checked": True, "covered": [], "gaps": []},
           "right": {"checked": True, "covered": [], "gaps": [gap]}}  # fmt: skip
    r, _ = invoke(monkeypatch, report("differs", coverage=cov), "--json")
    assert r.exit_code == 1
    doc = json.loads(r.stdout)
    assert doc["ok"] is False and doc["outcome"] == "differs"
    assert doc["range_from"] == "2026-09-11T00:00:00.000000000+00:00"
    assert doc["tables"][0]["coverage"]["right"]["gaps"] == [
        {
            "range_from": "2026-09-13T00:00:00.000000000+00:00",
            "range_to": "2026-09-16T00:00:00.000000000+00:00",
        }
    ]


def test_json_says_ok_on_equal_in_sample(monkeypatch):
    r, _ = invoke(monkeypatch, report("equal_in_sample"), "--json")
    assert r.exit_code == 0 and json.loads(r.stdout)["ok"] is True


def test_the_text_names_each_difference(monkeypatch):
    rows = [{"rowkey": "deal_id=3", "kind": "changed", "detail": "rate: 1.0847 -> 1.2"}]
    schema = [{"column": "venue", "left": "absent", "right": "symbol (s)"}]
    r, _ = invoke(monkeypatch, report("differs", rows=rows, schema=schema))
    assert "row      deal_id=3: changed - rate: 1.0847 -> 1.2" in r.output
    assert "schema   venue: absent -> symbol (s)" in r.output
    assert "paired on deal_id (events)" in r.output


def test_filters_limit_and_coverage_reach_q(monkeypatch):
    _, sent = invoke(
        monkeypatch,
        report("equal_in_sample"),
        "--filter", "sym=EURUSD", "--filter", "notional>=1000000", "--limit", "50",
        "--source-version", "v1", "--slice", "EURUSD",
    )  # fmt: skip
    assert '("sym=EURUSD";"notional>=1000000");50j;`$"v1";`$"EURUSD";' in sent["script"]


@pytest.mark.parametrize(
    ("extra", "said"),
    [
        (["--left", "prod", "--right", "prod"], "both name prod"),
        (["--left", "a", "--right", "b", "--slice", "x"], "needs --source-version"),
    ],
)
def test_a_command_that_names_nothing_to_compare_is_refused(monkeypatch, extra, said):
    fake_q(monkeypatch, report("equal_in_sample"))
    monkeypatch.setattr(diff_endpoints, "reach", _reached)
    died: list[UqsError] = []

    def record(exc: UqsError) -> None:
        died.append(exc)
        raise SystemExit(1)

    monkeypatch.setattr(diff_cli, "_die", record)
    runner.invoke(cli.app, ["data", "diff", *extra, "--table", "demo_deals", *WINDOW])
    assert len(died) == 1 and isinstance(died[0], UqsUsageError) and said in str(died[0])


# ------------------------------------------------------------- reaching a side


def test_a_local_stack_that_is_down_is_an_unavailable_side(monkeypatch):
    monkeypatch.setattr(
        diff_endpoints.listing, "configured_ports", lambda paths: {"gateway1": "17007"}
    )
    monkeypatch.setattr(diff_endpoints.alive, "running", lambda paths: set())
    with diff_endpoints.reach("local", "u", "p") as ep:
        assert ep == data_diff.Endpoint(
            "local", error="gateway1 on this machine: gateway1 is not running - start it with "
            "`uqs start gateway1`"
        )  # fmt: skip


def test_a_running_local_stack_is_its_gateways_port(monkeypatch):
    monkeypatch.setattr(
        diff_endpoints.listing, "configured_ports", lambda paths: {"gateway1": "17007"}
    )
    monkeypatch.setattr(diff_endpoints.alive, "running", lambda paths: {"gateway1"})
    with diff_endpoints.reach("local", "u", "p") as ep:
        assert ep == data_diff.Endpoint("local", "localhost:17007")


def test_an_undeclared_target_is_refused_not_reported(monkeypatch):
    def nobody(name, root):
        raise UqsError(f"no target or deployed server named {name}")

    monkeypatch.setattr(diff_endpoints.control, "remote_for", nobody)
    with pytest.raises(UqsError, match="no target"), diff_endpoints.reach("nosuch", "u", "p"):
        pass


def test_a_target_that_cannot_be_reached_is_an_unavailable_side_without_the_password(
    monkeypatch,
):
    monkeypatch.setattr(diff_endpoints.control, "remote_for", lambda name, root: None)

    @contextmanager
    def refused(*args, **_):
        raise UqsError("ssh: connect to host prod: password sekret rejected")
        yield

    monkeypatch.setattr(diff_endpoints.via_target, "connect", refused)
    with diff_endpoints.reach("prod", "u", "sekret") as ep:
        assert ep.address == "" and "gateway1 on target prod: ssh: connect" in ep.error
        assert "sekret" not in ep.error


def test_a_reached_target_is_its_tunnels_local_end(monkeypatch):
    monkeypatch.setattr(diff_endpoints.control, "remote_for", lambda name, root: None)

    @contextmanager
    def tunnelled(name, proc, port, base_port, user, passwd, *, change):
        assert (name, proc, change) == ("prod", "gateway1", None)  # a read (#2179)
        yield diff_endpoints.via_target.Via(name, "prod.example", proc, 17007, 61234, passwd)

    monkeypatch.setattr(diff_endpoints.via_target, "connect", tunnelled)
    with diff_endpoints.reach("prod", "u", "p") as ep:
        assert ep == data_diff.Endpoint("prod", "127.0.0.1:61234")


# --------------------------------------------- end to end, on fixture targets

#: A gateway in miniature: the plant's demo_deals, and the three reads a
#: comparison makes, each as the stack serves it. .gw.syncexec applies the
#: program it is sent, as a TorQ tier's `value` does; .uqf.browse reads the
#: window and equality filters, sorted by time, at most `lim` rows.
FIXTURE = r"""
\l src/init.q
\l src/etl/init.q
deals:.qpipe.source.demo_deals.fixture[];
demo_deals:(cols .qetl.plant.schema `demo_deals)#update time:deal_time+0D00:00:01 from deals;
if[`changed~`$getenv`FIXTURE_SHAPE; update rate:1.2 from `demo_deals where deal_id=3];
if[`bare~`$getenv`FIXTURE_SHAPE; delete demo_deals from `.];
upto:$[`part~`$getenv`FIXTURE_SHAPE; 2026.09.13D; 2026.09.16D];
claims:([] range_from:enlist 2026.09.11D; range_to:enlist upto);
.gw.syncexec:{[qry;procs] value qry};
.uqf.browse:{[tn;fc;fo;fv;lim;procs]
    r:`time xasc value tn;
    r:select from r where time>=fv 0, time<fv 1;
    `rows`cap!(lim sublist r;lim)};
.qetl.coverage.claims:{[ds;part;release;at] claims};
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def fixture_targets(tmp_path):
    """Start fixture targets by shape; yields a function shape -> Endpoint."""
    q = q_interpreter()
    started: list[subprocess.Popen] = []
    script = tmp_path / "gateway.q"
    script.write_text(FIXTURE)

    def start(name: str, shape: str = "same") -> data_diff.Endpoint:
        port = _free_port()
        proc = subprocess.Popen(
            [str(q), str(script), "-p", str(port), "-q"],
            cwd=default_paths().repo_root,
            env={**__import__("os").environ, "FIXTURE_SHAPE": shape},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started.append(proc)
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail(f"fixture target {name} did not listen on {port}")
        return data_diff.Endpoint(name, f"localhost:{port}")

    yield start
    for proc in started:
        proc.kill()
        proc.wait(timeout=10)


def _compare(left, right, **kw) -> dict:
    return data_diff.compare(
        default_paths(), left, right, ["demo_deals"], window(), login="u:p", **kw
    )


needs_q = pytest.mark.skipif(q_interpreter() is None, reason="no q interpreter")


@needs_q
def test_two_equal_fixture_targets_are_equal_in_the_sample(fixture_targets):
    r = _compare(fixture_targets("local"), fixture_targets("prod"))
    t = r["tables"][0]
    assert (r["outcome"], t["truncated"], t["read"]) == ("equal_in_sample", False, [5, 5])
    assert "every row in the window was read" in t["label"]


@needs_q
def test_a_truncated_fixture_sample_says_it_is_not_proof(fixture_targets):
    t = _compare(fixture_targets("local"), fixture_targets("prod"), limit=2)["tables"][0]
    assert (t["outcome"], t["truncated"]) == ("equal_in_sample", True)
    assert "NOT proof the tables are equal" in t["label"]


@needs_q
def test_a_changed_row_on_a_fixture_target_differs(fixture_targets):
    t = _compare(fixture_targets("local"), fixture_targets("prod", "changed"))["tables"][0]
    assert t["outcome"] == "differs"
    assert t["rows"] == [
        {"rowkey": "deal_id=3", "kind": "changed", "detail": "rate: 1.0847 -> 1.2"}
    ]


@needs_q
def test_a_fixture_target_without_the_table_is_a_missing_table(fixture_targets):
    t = _compare(fixture_targets("local"), fixture_targets("prod", "bare"))["tables"][0]
    assert t["outcome"] == "missing_table" and "not on prod" in t["label"]


@needs_q
def test_published_coverage_on_fixture_targets(fixture_targets):
    part = fixture_targets("prod", "part")
    t = _compare(fixture_targets("local"), part, source_version="v1")["tables"][0]
    assert t["outcome"] == "differs" and "published coverage" in t["label"]
    t = _compare(fixture_targets("local", "part"), part, source_version="v1")["tables"][0]
    assert t["outcome"] == "incomplete_coverage"


@needs_q
def test_an_unreachable_side_is_endpoint_unavailable(fixture_targets):
    closed = data_diff.Endpoint("prod", f"localhost:{_free_port()}")
    t = _compare(fixture_targets("local"), closed)["tables"][0]
    assert t["outcome"] == "endpoint_unavailable"
    assert t["reasons"]["right"].startswith("could not connect to localhost:")
    unreached = data_diff.Endpoint("prod", error="gateway1 on target prod: ssh refused")
    t = _compare(fixture_targets("local"), unreached)["tables"][0]
    assert t["label"] == "endpoint unavailable - prod: gateway1 on target prod: ssh refused"


@needs_q
def test_the_command_end_to_end_on_fixture_targets(fixture_targets, monkeypatch):
    sides = {"local": fixture_targets("local"), "prod": fixture_targets("prod", "changed")}

    @contextmanager
    def reached(name, user, passwd, paths=None):
        yield sides[name]

    monkeypatch.setattr(diff_endpoints, "reach", reached)
    r = runner.invoke(
        cli.app,
        ["data", "diff", "--left", "local", "--right", "prod", "--table", "demo_deals",
         *WINDOW, "--json"],
    )  # fmt: skip
    assert r.exit_code == 1, r.output
    doc = json.loads(r.stdout)
    assert (doc["outcome"], doc["left"], doc["right"]) == ("differs", "local", "prod")
    assert doc["range_to"] == "2026-09-16T00:00:00.000000000+00:00"


def test_partition_is_refused_naming_slice(monkeypatch):
    """#2239: "partition" is an HDB date partition; the coverage slice is
    `--slice`, and the old spelling is refused - never read as a date."""
    fake_q(monkeypatch, report("equal_in_sample"))
    monkeypatch.setattr(diff_endpoints, "reach", _reached)
    argv = ["data", "diff", "--table", "demo_deals", *WINDOW, "--partition", "2026.09.13"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 2
    said = " ".join(re.sub("[│╭╮╰╯─]", " ", result.output).split())
    assert (
        "`--partition` is now `--slice` (the coverage slice); a date partition is `--date`" in said
    )
