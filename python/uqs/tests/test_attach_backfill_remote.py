"""A backfill extracted on one machine and written by the stack's own receiver (#1650).

End to end on real q processes, with the world test_attach_backfill_run builds: an upstream
the worker reads, a scripted stack, a native-shaped HDB and a fake reader. The receiver runs
as `uqs job receive` starts it, from a receiver contract exported from the worker and
approved for the attachment (#1717); the extractor as `uqs job backfill --attach --writer`
does, under an attachment directory of its own, as it would be on another machine.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from attach_backfill_support import (
    UQF_ROOT,
    Receivers,
    free_port,
    receivers_under,
    save_attachment,
)
from test_attach_backfill_run import (  # noqa: F401
    FROM,
    FUTURE,
    TABLE,
    TO,
    WORKER,
    read_rows,
    upstream,
    world,
)

from uqs.attach import (
    backfill_coordination,
    backfill_owner,
    backfill_remote,
    backfill_run,
    config,
    receiver_contract,
)
from uqs.attach.config import AttachmentError
from uqs.attach.schema import parse_definition
from uqs.interpreter import PEACHQ, q_impl
from uqs.paths import UqsError, paths_for_root

pytestmark = pytest.mark.skipif(
    q_impl() == PEACHQ,
    reason="the world is test_attach_backfill_run's: a kdb+ HDB and upstream, which PeachQ "
    "loads neither of",
)


@pytest.fixture(scope="module")
def exported() -> receiver_contract.Contract:
    """The worker's contract, exported from this checkout - the extraction machine's."""
    return receiver_contract.export(paths_for_root(UQF_ROOT), WORKER)


@pytest.fixture
def approved(world, exported) -> str:  # noqa: F811
    """The contract approved for pb-uat on the stack's machine: its identity."""
    return receiver_contract.approve(world.paths, exported, "pb-uat")["contract"]["identity"]


#: How long after it starts a test's receiver stops itself, should its teardown be
#: missed (a killed pytest worker): its -deadline is the stack's end-of-day fence.
RECEIVER_LIFETIME = timedelta(minutes=15)


@pytest.fixture
def receivers(world) -> Iterator[Receivers]:  # noqa: F811
    """Every receiver the test starts (`receivers.start`), stopped in teardown on
    every outcome - and any other under the test's tmp_path (#1760).

    The world's clock is 2090 (FUTURE), so the fence it reports would let a receiver
    run until then; here the stack's clock is the real one, with end of day
    RECEIVER_LIFETIME past the fence, so the receiver's -deadline is minutes away."""
    now = datetime.now(UTC)
    roll = now + backfill_coordination.EOD_GUARD + RECEIVER_LIFETIME
    clock = {
        "now": now.strftime("%Y.%m.%dD%H:%M:%S.%f000"),
        "eod_date": now.strftime("%Y.%m.%d"),
        "next_roll": roll.strftime("%Y.%m.%dD%H:%M:%S.%f000"),
        "partition": now.strftime("%Y.%m.%d"),
    }
    world.stack.state_over = {n: dict(clock) for n in ("rdb1", "wdb1")}
    owned = Receivers(world.tmp)
    try:
        yield owned
    finally:
        owned.stop_all()


def _serving(started: dict) -> str:
    """Wait for the receiver `receive` started to say it is serving; its log so far.

    An open port is not that signal: q binds `-p` before it loads remote_receiver.q,
    so the port accepts while the script is still loading src/ - and before it has
    printed a line - which under load is long enough to read an empty log (#1746).
    The receiver's own readiness signal is the line it prints once it serves."""
    log = Path(started["log"])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        text = log.read_text()
        if "remote_receiver: serving" in text:
            assert _accepting(started["port"]), text
            return text
        time.sleep(0.1)
    pytest.fail(f"the receiver never said it was serving:\n{log.read_text()}")


@pytest.mark.parametrize("bad", ["localhost", "localhost:0", "host:99999", "a b:1", ":5000"])
def test_a_writer_that_is_not_host_and_port_is_refused(bad):
    with pytest.raises(UqsError, match="--writer must be HOST:PORT"):
        backfill_remote.parse_writer(bad)


def test_a_backfill_extracted_here_is_written_by_the_stacks_receiver(world, approved, receivers):  # noqa: F811
    port = free_port()
    started = receivers.start(world.paths, "pb-uat", approved, port=port, ipc=world.stack)
    _serving(started)
    stops_in = datetime.fromisoformat(started["stops_at"]) - datetime.now(UTC)
    assert stops_in <= RECEIVER_LIFETIME, "a test's receiver stops itself within minutes"
    receiver_log = started["log"]
    assert started["readers"] == [f"hdb1@localhost:{world.reader.port}"]
    # the extractor's own attachment directory: on another machine it would be its own
    theirs = world.tmp / "extractor-attachments"
    shutil.copytree(world.attach_dir, theirs, ignore=shutil.ignore_patterns("backfills"))
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    run_, plan = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{port}", mode="run", on_conflict="replace",
    )  # fmt: skip
    assert plan["writer"] == f"localhost:{port}"
    code, result = backfill_run.follow(run_, timeout=180)
    log = run_.log.read_text()
    assert code == 0 and result["state"] == "completed", log + open(receiver_log).read()
    assert result["outcome"] == "queryable", "the receiver's readers reloaded: released here"
    assert read_rows(world.hdb, "2015.01.07") > 0, "the receiver wrote the stack's HDB"
    assert world.reader.reloads >= 1, "and asked its reader to reload"
    assert (run_.state_dir / "etl_coverage").is_file(), "coverage is filed by the extractor"
    rows = read_rows(world.hdb, "2015.01.07")
    again, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{port}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, result = backfill_run.follow(again, timeout=180)
    assert code == 0 and result["state"] == "idle", again.log.read_text()
    assert read_rows(world.hdb, "2015.01.07") == rows, "a covered range sends nothing"


def test_an_extractor_with_no_receiver_fails_and_covers_nothing(world):  # noqa: F811
    theirs = world.tmp / "extractor-attachments"
    shutil.copytree(world.attach_dir, theirs)
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    run_, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{free_port()}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, _ = backfill_run.follow(run_, timeout=120)
    assert code == 2, run_.log.read_text()
    assert "cannot reach the receiver" in run_.log.read_text()
    assert not (run_.state_dir / "etl_coverage").exists()


def test_a_receiver_holds_detach_off_until_it_is_stopped(world, approved, receivers):  # noqa: F811
    port = free_port()
    started = receivers.start(world.paths, "pb-uat", approved, port=port, ipc=world.stack)
    _serving(started)
    with pytest.raises(AttachmentError, match="imported_trades.receiver"):
        backfill_owner.refuse_while_active("pb-uat")
    stopped = backfill_remote.stop_receivers("pb-uat")
    assert [r["pid"] for r in stopped] == [started["pid"]]
    for _ in range(50):
        if not _accepting(port):
            break
        time.sleep(0.2)
    assert not _accepting(port), "the receiver is gone"
    backfill_owner.refuse_while_active("pb-uat")  # nothing holds detach off any more


def _accepting(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close()
        return True
    except OSError:
        return False


# ------------------------------------------------- the receiver's admission (#1650)
#
# `uqs job receive` admits the destination as a direct write does: a verified
# attachment, the coordination rules, one owner at a time. Each refusal starts no
# q and leaves no owner record.


def _refused_receive(world, receivers, approved, match, exc=UqsError):  # noqa: F811
    with pytest.raises(exc, match=match):
        receivers.start(world.paths, "pb-uat", approved, port=free_port(), ipc=world.stack)
    assert backfill_owner.active("pb-uat") == [], "nothing was started"
    assert receivers_under(world.tmp) == [], "no receiver process was left behind"


@pytest.mark.parametrize("state", ["applied", "detached"])
def test_a_receiver_is_never_started_for_an_unverified_attachment(
    world,  # noqa: F811
    approved,
    receivers,
    state,
):
    save_attachment(
        "pb-uat", world.hdb, host="localhost", table=parse_definition(TABLE),
        origin={"folder": str(world.bundle)}, state=state,
    )  # fmt: skip
    _refused_receive(world, receivers, approved, f"attachment pb-uat is {state}, not verified")


def test_a_receiver_is_not_started_inside_the_end_of_day_fence(world, approved, receivers):  # noqa: F811
    world.stack.state_over["wdb1"] = dict(FUTURE) | {
        "partition": "2090.01.01",
        "now": "2090.01.01D23:30:00.000000000",
    }
    _refused_receive(
        world, receivers, approved, "end of day is at 2090-01-02T00:00:00\\+00:00, 30 minutes away"
    )


def test_a_receiver_is_not_started_for_a_wdb_that_reports_another_hdb(world, approved, receivers):  # noqa: F811
    world.stack.named("wdb1")["hdbdir"] = f":{world.tmp / 'elsewhere'}"
    _refused_receive(
        world, receivers, approved, "report another HDB directory than attachment pb-uat"
    )


def test_a_second_receiver_for_the_attachment_is_refused(world, approved, receivers):  # noqa: F811
    port = free_port()
    started = receivers.start(world.paths, "pb-uat", approved, port=port, ipc=world.stack)
    _serving(started)
    with pytest.raises(AttachmentError, match="has an attached backfill running"):
        receivers.start(world.paths, "pb-uat", approved, port=free_port(), ipc=world.stack)
    assert [r["pid"] for r in backfill_owner.active("pb-uat")] == [started["pid"]]
    assert receivers_under(world.tmp) == [started["pid"]], "the refused one started no process"


def test_a_killed_receiver_is_replaced_and_the_rerun_fills_the_hdb(world, approved, receivers):  # noqa: F811
    """A receiver killed outright (SIGKILL, no clean-up) holds nothing: the extractor
    against it fails and covers nothing, and a receiver started in its place takes
    the rerun to completion."""
    port = free_port()
    first = receivers.start(world.paths, "pb-uat", approved, port=port, ipc=world.stack)
    _serving(first)
    os.kill(first["pid"], signal.SIGKILL)
    for _ in range(50):
        if not _accepting(port):
            break
        time.sleep(0.2)
    theirs = world.tmp / "extractor-attachments"
    shutil.copytree(world.attach_dir, theirs, ignore=shutil.ignore_patterns("backfills"))
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    failed, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{port}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, _ = backfill_run.follow(failed, timeout=120)
    assert code != 0, failed.log.read_text()
    assert not (failed.state_dir / "etl_coverage").exists(), "a dead receiver covers nothing"
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(world.attach_dir))
    port = free_port()
    second = receivers.start(world.paths, "pb-uat", approved, port=port, ipc=world.stack)
    _serving(second)
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    rerun, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{port}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, result = backfill_run.follow(rerun, timeout=180)
    assert code == 0 and result["state"] == "completed", rerun.log.read_text()
    assert read_rows(world.hdb, "2015.01.07") > 0
    assert (rerun.state_dir / "etl_coverage").is_file()


# ------------------------------------------------- the receiver needs no job (#1717)


def test_a_receiver_is_never_started_for_a_contract_not_approved_here(world, receivers):  # noqa: F811
    _refused_receive(
        world, receivers, "0123456789abcdef", "no contract 0123456789abcdef is approved"
    )


def _checkout_without_jobs(world):  # noqa: F811
    """A receiving checkout: this tree's src/ and scripts/dev/ with every source,
    transform, worker, streaming job and reaction removed - nothing that could
    define the worker whose rows it is about to write."""
    root = world.tmp / "receiving-checkout"
    shutil.copytree(UQF_ROOT / "src", root / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "dev", root / "scripts" / "dev")
    for kind in ("sources", "transforms", "workers", "streaming", "reactions"):
        shutil.rmtree(root / "src" / "etl" / kind, ignore_errors=True)
    assert not list(root.rglob("upstream_trades*")), "no trace of the job is left"
    return paths_for_root(root)


def test_a_checkout_with_no_job_definitions_receives_the_backfill(world, approved, receivers):  # noqa: F811
    receiving = _checkout_without_jobs(world)
    port = free_port()
    started = receivers.start(receiving, "pb-uat", approved, port=port, ipc=world.stack)
    receiver_log = started["log"]
    assert f"under contract {approved}" in _serving(started)
    theirs = world.tmp / "extractor-attachments"
    shutil.copytree(world.attach_dir, theirs, ignore=shutil.ignore_patterns("backfills"))
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    run_, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{port}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, result = backfill_run.follow(run_, timeout=180)
    assert code == 0 and result["state"] == "completed", (
        run_.log.read_text() + open(receiver_log).read()
    )
    assert result["outcome"] == "queryable"
    assert read_rows(world.hdb, "2015.01.07") > 0, "written by a receiver with no job code"


def test_a_policy_the_approval_does_not_permit_writes_and_covers_nothing(
    world,  # noqa: F811
    exported,
    receivers,
):
    """The stack's operator approved upsert only: an extractor running replace is
    refused batch by batch, so nothing reaches the HDB and nothing is covered."""
    ident = receiver_contract.approve(
        world.paths, exported, "pb-uat", conflict_policies=["upsert"]
    )["contract"]["identity"]
    port = free_port()
    started = receivers.start(world.paths, "pb-uat", ident, port=port, ipc=world.stack)
    _serving(started)
    assert started["conflict_policies"] == ["upsert"]
    theirs = world.tmp / "extractor-attachments"
    shutil.copytree(world.attach_dir, theirs, ignore=shutil.ignore_patterns("backfills"))
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    run_, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{port}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, _ = backfill_run.follow(run_, timeout=180)
    log = run_.log.read_text()
    assert code != 0, log
    assert "is not one the contract permits" in log
    assert not list(world.hdb.glob("*/imported_trades")), "nothing was written"
    assert not (run_.state_dir / "etl_coverage").exists(), "nothing was covered"
