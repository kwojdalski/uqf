"""Fleet health (FE-01), and the properties that make it honest."""

from __future__ import annotations

from fastapi.testclient import TestClient

from uqf_frontend import health, ops
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.fleet import FakeFleet
from uqf_frontend.gateway import FakeGateway
from uqf_frontend.procfile import DeclaredProcess

HEADER = "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd\n"


def ident(pid: int, port: int, procname: str, proctype: str = "rdb"):
    return [{"pid": pid, "port": port, "procname": procname, "proctype": proctype}]


def declared(name: str, port: int | None, *, swa: bool = True, proctype: str = "rdb"):
    return DeclaredProcess(name, proctype, "localhost", port, swa)


def test_a_process_that_answers_is_up_with_its_pid():
    fleet = FakeFleet()
    fleet.probes["localhost:6052"] = ident(4242, 6052, "rdb1")
    got = health.check(fleet, [declared("rdb1", 6052)])
    assert got[0].up is True
    assert got[0].pid == 4242
    assert got[0].error is None


def test_a_process_that_does_not_answer_is_down_with_a_reason():
    got = health.check(FakeFleet(), [declared("rdb1", 6052)])
    assert got[0].up is False
    assert "refused" in (got[0].error or "")


def test_one_down_process_does_not_affect_the_others():
    fleet = FakeFleet()
    fleet.probes["localhost:6052"] = ident(1, 6052, "rdb1")
    got = health.check(fleet, [declared("rdb1", 6052), declared("hdb1", 6053)])
    assert [h.up for h in got] == [True, False]


def test_an_unresolvable_port_is_undetermined_not_down():
    """Reporting "down" for a process whose port could not even be computed
    would be a guess. It is a different state and gets a different flag.
    """
    got = health.check(FakeFleet(), [declared("mystery1", None)])
    assert got[0].up is False
    assert got[0].port_unresolved is True
    assert health.summarise(got)["undetermined"] == 1
    assert health.summarise(got)["down"] == 0


def test_a_stale_process_squatting_a_port_is_flagged():
    """The failure any is-something-listening check misses: something answers,
    but it is not the process declared on that port.
    """
    fleet = FakeFleet()
    fleet.probes["localhost:6052"] = ident(9, 6052, "hdb1")
    got = health.check(fleet, [declared("rdb1", 6052)])
    assert got[0].up is True
    assert got[0].identity_mismatch is not None
    assert "rdb1" in got[0].identity_mismatch and "hdb1" in got[0].identity_mismatch
    assert health.summarise(got)["identity_mismatches"] == 1


def test_a_process_without_dot_proc_is_not_flagged_as_a_mismatch():
    """A plain q process reports `unknown, which is absence of information
    rather than evidence of the wrong process.
    """
    fleet = FakeFleet()
    fleet.probes["localhost:6052"] = ident(9, 6052, "unknown")
    got = health.check(fleet, [declared("rdb1", 6052)])
    assert got[0].identity_mismatch is None


def test_down_unexpected_excludes_startwithall_zero():
    """tap1 is configured not to start with the stack, so it being down is
    the configured behaviour. Counting it as a fault would make the headline
    number permanently wrong.
    """
    got = health.check(FakeFleet(), [declared("tap1", 6078, swa=False), declared("rdb1", 6052)])
    s = health.summarise(got)
    assert s["down"] == 2
    assert s["down_unexpected"] == 1


def test_the_identity_program_is_used_for_probing():
    fleet = FakeFleet()
    fleet.probes["localhost:6052"] = ident(1, 6052, "rdb1")
    health.check(fleet, [declared("rdb1", 6052)])
    assert fleet.calls[-1][1] == ops.IDENTITY


def test_endpoint_reports_groups_and_summary(tmp_path):
    path = tmp_path / "process.csv"
    path.write_text(
        HEADER
        + "localhost,{KDBBASEPORT}+2,rdb,rdb1,,1,0,,,x.q,1,,q\n"
        + "localhost,{KDBBASEPORT}+3,hdb,hdb1,,1,0,,,y.q,1,,q\n"
        + "localhost,{KDBBASEPORT}+4,hdb,hdb2,,1,0,,,y.q,1,,q\n"
    )
    fleet = FakeFleet()
    fleet.probes["localhost:6052"] = ident(11, 6052, "rdb1")
    c = TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(process_csv=path, base_port=6050),
            fleet=fleet,
        )
    )
    body = c.get("/ops/processes").json()
    assert body["summary"] == {
        "declared": 3,
        "up": 1,
        "down": 2,
        "down_unexpected": 2,
        "undetermined": 0,
        "identity_mismatches": 0,
    }
    assert body["groups"] == {"hdb": 2, "rdb": 1}
    assert body["poll_seconds"] > 0


def test_endpoint_says_what_is_missing_when_unconfigured():
    c = TestClient(create_app(gateway=FakeGateway(), settings=Settings(), fleet=FakeFleet()))
    resp = c.get("/ops/processes")
    assert resp.status_code == 422
    assert "UQF_FRONTEND_PROCESS_CSV" in resp.json()["detail"]


def test_endpoint_reports_a_missing_file_rather_than_crashing(tmp_path):
    c = TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(process_csv=tmp_path / "absent.csv"),
            fleet=FakeFleet(),
        )
    )
    assert c.get("/ops/processes").status_code == 422
