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
from test_attach_backfill_run import FROM, TO, WORKER, read_rows, upstream, world  # noqa: F401

from uqs.attach import backfill_remote, backfill_run, config
from uqs.paths import UqsError


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
