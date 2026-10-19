"""Shared doubles for the attached-backfill tests (#1454): a scripted stack that also answers
`hdb_reader` and `writer_state`, an attachment on disk, and the world a test runs in."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from attach_support import FakeStack

from uqs.attach import backfill as attached
from uqs.attach import backfill_coordination as coordination
from uqs.attach import backfill_plan as planning
from uqs.attach import config, service
from uqs.attach.config import Attachment, Publisher, Stack, Storage
from uqs.attach.facts import Host
from uqs.attach.manifest import Manifest
from uqs.attach.remote import Endpoint, IpcError
from uqs.cli import shared
from uqs.interpreter import q_interpreter
from uqs.paths import paths_for_root

UQF_ROOT = Path(__file__).resolve().parents[3]
WORKER = "crypto_market_data_backfill"
DATASET = "crypto_market_data"
FROM = datetime(2026, 9, 11, tzinfo=UTC)
TO = datetime(2026, 9, 13, tzinfo=UTC)


def tree_table() -> Any:
    found = attached._table_in(UQF_ROOT / "src/etl/plant_tables.q", DATASET)
    if found is None:
        raise AssertionError("plant table missing")
    return found


class _ErrorLog:
    """What the CLI's `_die` logged: refusals go to the log, not stdout."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


class HdbStack(FakeStack):
    """FakeStack whose HDB reader answers `hdb_reader` like a TorQ hdb process."""

    def __init__(self, hdb: Path, other: Path | None = None) -> None:
        super().__init__(6000)
        #: per-process overrides of writer_state's answer
        self.state_over: dict[str, dict[str, Any]] = {}
        for name in ("rdb1", "wdb1"):
            self.named(name)["hdbdir"] = f":{hdb}"
        self.readers = {
            "hdb1": {"stackid": "6000", "hdbdir": str(hdb), "partitions": 2},
        }
        if other is not None:
            self.add(
                {**self.named("hdb1"), "procname": "hdb2", "port": self.stack_id + 4},
                listed_only=True,
            )
            self.readers["hdb2"] = {"stackid": "6000", "hdbdir": str(other), "partitions": 1}
        self.refresh_discovery()

    def writer_state(self, name: str) -> dict[str, Any]:
        """A healthy writer on 2026-10-16 midday, end of day at midnight; tests override."""
        base = {
            "procname": name,
            "now": "2026.10.16D12:00:00.000000000",
            "eod_date": "2026.10.16",
            "next_roll": "2026.10.17D00:00:00.000000000",
        }
        if name.startswith("wdb"):
            return base | {
                "proctype": "wdb",
                "partition": "2026.10.16",
                "reloadcomplete": True,
                "hdbdir": self.named(name)["hdbdir"],
            }
        return base | {
            "proctype": "rdb",
            "savetables": False,
            "reloadenabled": True,
            "hdbdir": self.named(name)["hdbdir"],
        }

    def call(self, endpoint: Endpoint, op: str, request: dict[str, Any]) -> dict[str, Any]:
        if op in ("hdb_reader", "writer_state"):
            self.calls.append((endpoint.port, op, request))
            if endpoint.port in self.down:
                raise IpcError(endpoint, op, "cannot connect: Connection refused")
            if op == "writer_state":
                return {
                    **self.writer_state(endpoint.procname),
                    **self.state_over.get(endpoint.procname, {}),
                }
            return {"procname": endpoint.procname, **self.readers[endpoint.procname]}
        return super().call(endpoint, op, request)


def snapshot(*roots: Path) -> dict[str, tuple[int, int]]:
    """Every path under `roots` (and the roots themselves) with its size and mtime."""
    seen: dict[str, tuple[int, int]] = {}
    for root in roots:
        seen[str(root)] = (-1, -1) if not root.exists() else (0, 0)
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                st = p.stat()
                seen[str(p)] = (st.st_size, st.st_mtime_ns)
    return seen


def make_world(monkeypatch, tmp_path):
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    attach_dir, status = tmp_path / "attachments", tmp_path / "status"
    hdb = tmp_path / "hdb"
    (hdb / "2026.09.11" / DATASET).mkdir(parents=True)
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(attach_dir))
    monkeypatch.setenv("UQF_STATUS_DIR", str(status))
    # a developer shell may hold the credential: these tests mean the unconfigured source
    monkeypatch.setenv("UQF_SOURCE_CRED_CRYPTO_MARKET_DATA", "")
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    stack = HdbStack(hdb)
    # the scripted stack's host is 'testhost': it is this machine as far as these tests go
    monkeypatch.setattr(coordination, "HOST", Host(local_names=("testhost",)))
    monkeypatch.setattr(service, "ipc_for", lambda att: stack)
    save_attachment("pb-uat", hdb)
    paths = paths_for_root(UQF_ROOT)
    return type(
        "World",
        (),
        {
            "stack": stack,
            "hdb": hdb,
            "status": status,
            "attach_dir": attach_dir,
            "paths": paths,
            "tmp": tmp_path,
            "errors": errors.messages,
            "monkeypatch": monkeypatch,
        },
    )


def save_attachment(
    name: str,
    hdb: Path,
    *,
    state: str | None = "verified",
    stack_id: int = 6000,
    pin: bool = True,
    table=None,
    origin: dict | None = None,
) -> Attachment:
    att = Attachment(
        name=name,
        stack=Stack(stack_id, "testhost", "testhost", stack_id + 1, "stp1", ("rdb1", "wdb1")),
        publisher=Publisher("pb_job", "pb_ledger1"),
        tables=(table or tree_table(),),
        storage=Storage(hdb_dir=str(hdb) if pin else ""),
        origin=origin or {},
    )
    config.save(att)
    Manifest.path_for(name).unlink(missing_ok=True)
    if state is not None:
        m = Manifest.new(att, Manifest.path_for(name))
        m.set_state(state, "test")
    return att


def plan(world, *, ipc=None, port=None, fixture=False):
    return planning.plan_attached(
        world.paths,
        "pb-uat",
        WORKER,
        "v1",
        FROM,
        TO,
        ipc=ipc or world.stack,
        port=port,
        fixture=fixture,
        on_conflict="replace",
    )


def facts_of(**over):
    base = {
        "worker": WORKER,
        "dataset": DATASET,
        "source": DATASET,
        "source_version": "v1",
        "live": True,
        "custom_io": False,
        "on_conflict": "upsert",
        "width": "1D",
        "partition": "",
        "time_column": "source_time",
        "planned": 2,
        "cursor": "",
        "windows": [
            {
                "range_from": "2026.09.11D00:00:00.000000000",
                "range_to": "2026.09.12D00:00:00.000000000",
                "decision": "covered",
            },
            {
                "range_from": "2026.09.12D00:00:00.000000000",
                "range_to": "2026.09.13D00:00:00.000000000",
                "decision": "fetch",
            },
        ],
        "dates": ["2026.09.12"],
    }
    return base | over


def plan_with(world, facts, *, ipc=None):
    world.monkeypatch.setattr(planning, "evaluate", lambda *a, **k: facts)
    return plan(world, ipc=ipc)
