"""A backfill extracted on one machine and written by the stack's own receiver (#1650).

End to end on real q processes, with the world test_attach_backfill_run builds: an upstream
the worker reads, a scripted stack, a native-shaped HDB and a fake reader. The receiver runs
as `uqs job receive` starts it; the extractor as `uqs job backfill --attach --writer` does,
under an attachment directory of its own, as it would be on another machine.
"""

from __future__ import annotations

import shutil
import socket
import time

import pytest
from attach_backfill_support import save_attachment
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

from uqs.attach import backfill_owner, backfill_remote, backfill_run, config
from uqs.attach.config import AttachmentError
from uqs.attach.schema import parse_definition
from uqs.interpreter import PEACHQ, q_impl
from uqs.paths import UqsError

pytestmark = pytest.mark.skipif(
    q_impl() == PEACHQ,
    reason="the world is test_attach_backfill_run's: a kdb+ HDB and upstream, which PeachQ "
    "loads neither of",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listening(port: int) -> bool:
    for _ in range(150):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


@pytest.mark.parametrize("bad", ["localhost", "localhost:0", "host:99999", "a b:1", ":5000"])
def test_a_writer_that_is_not_host_and_port_is_refused(bad):
    with pytest.raises(UqsError, match="--writer must be HOST:PORT"):
        backfill_remote.parse_writer(bad)


def test_a_backfill_extracted_here_is_written_by_the_stacks_receiver(world):  # noqa: F811
    port = _free_port()
    started = backfill_remote.receive(world.paths, "pb-uat", WORKER, port=port, ipc=world.stack)
    receiver_log = started["log"]
    try:
        assert _listening(port), open(receiver_log).read()
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
    finally:
        import os
        import signal

        os.kill(started["pid"], signal.SIGTERM)


def test_an_extractor_with_no_receiver_fails_and_covers_nothing(world):  # noqa: F811
    theirs = world.tmp / "extractor-attachments"
    shutil.copytree(world.attach_dir, theirs)
    world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
    run_, _ = backfill_remote.launch_remote(
        world.paths, "pb-uat", WORKER, "v1", FROM, TO,
        writer=f"localhost:{_free_port()}", mode="run", on_conflict="replace",
    )  # fmt: skip
    code, _ = backfill_run.follow(run_, timeout=120)
    assert code == 2, run_.log.read_text()
    assert "cannot reach the receiver" in run_.log.read_text()
    assert not (run_.state_dir / "etl_coverage").exists()


def test_a_receiver_holds_detach_off_until_it_is_stopped(world):  # noqa: F811
    from uqs.attach import backfill_owner
    from uqs.attach.config import AttachmentError

    port = _free_port()
    started = backfill_remote.receive(world.paths, "pb-uat", WORKER, port=port, ipc=world.stack)
    assert _listening(port), open(started["log"]).read()
    with pytest.raises(AttachmentError, match="upstream_trades_backfill.receiver"):
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


def _refused_receive(world, match, exc=UqsError):  # noqa: F811
    with pytest.raises(exc, match=match):
        backfill_remote.receive(world.paths, "pb-uat", WORKER, port=_free_port(), ipc=world.stack)
    assert backfill_owner.active("pb-uat") == [], "nothing was started"


@pytest.mark.parametrize("state", ["applied", "detached"])
def test_a_receiver_is_never_started_for_an_unverified_attachment(world, state):  # noqa: F811
    save_attachment(
        "pb-uat", world.hdb, host="localhost", table=parse_definition(TABLE),
        origin={"folder": str(world.bundle)}, state=state,
    )  # fmt: skip
    _refused_receive(world, f"attachment pb-uat is {state}, not verified")


def test_a_receiver_is_not_started_inside_the_end_of_day_fence(world):  # noqa: F811
    world.stack.state_over["wdb1"] = dict(FUTURE) | {
        "partition": "2090.01.01",
        "now": "2090.01.01D23:30:00.000000000",
    }
    _refused_receive(world, "end of day is at 2090-01-02T00:00:00\\+00:00, 30 minutes away")


def test_a_receiver_is_not_started_for_a_wdb_that_reports_another_hdb(world):  # noqa: F811
    world.stack.named("wdb1")["hdbdir"] = f":{world.tmp / 'elsewhere'}"
    _refused_receive(world, "report another HDB directory than attachment pb-uat")


def test_a_second_receiver_for_the_attachment_is_refused(world):  # noqa: F811
    port = _free_port()
    started = backfill_remote.receive(world.paths, "pb-uat", WORKER, port=port, ipc=world.stack)
    try:
        assert _listening(port), open(started["log"]).read()
        with pytest.raises(AttachmentError, match="has an attached backfill running"):
            backfill_remote.receive(
                world.paths, "pb-uat", WORKER, port=_free_port(), ipc=world.stack
            )
        assert [r["pid"] for r in backfill_owner.active("pb-uat")] == [started["pid"]]
    finally:
        backfill_remote.stop_receivers("pb-uat")


def test_a_killed_receiver_is_replaced_and_the_rerun_fills_the_hdb(world):  # noqa: F811
    """A receiver killed outright (SIGKILL, no clean-up) holds nothing: the extractor
    against it fails and covers nothing, and a receiver started in its place takes
    the rerun to completion."""
    import os
    import signal

    port = _free_port()
    first = backfill_remote.receive(world.paths, "pb-uat", WORKER, port=port, ipc=world.stack)
    assert _listening(port), open(first["log"]).read()
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
    port = _free_port()
    second = backfill_remote.receive(world.paths, "pb-uat", WORKER, port=port, ipc=world.stack)
    try:
        assert _listening(port), open(second["log"]).read()
        world.monkeypatch.setenv(config.ATTACH_DIR_ENV, str(theirs))
        rerun, _ = backfill_remote.launch_remote(
            world.paths, "pb-uat", WORKER, "v1", FROM, TO,
            writer=f"localhost:{port}", mode="run", on_conflict="replace",
        )  # fmt: skip
        code, result = backfill_run.follow(rerun, timeout=180)
        assert code == 0 and result["state"] == "completed", rerun.log.read_text()
        assert read_rows(world.hdb, "2015.01.07") > 0
        assert (rerun.state_dir / "etl_coverage").is_file()
    finally:
        os.kill(second["pid"], signal.SIGTERM)
