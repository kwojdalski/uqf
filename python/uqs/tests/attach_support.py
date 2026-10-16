"""Test doubles for the attach workflow: a scripted stack and a bundle fixture.

`FakeStack` answers `probe` the way a real TorQ 5.2.12 process does (the shapes
here were copied from probe.q's answers on an isolated stack, see
test_attach_stack.py), and records every call, so a test can assert that a plan
asked only read-only questions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from uqs.attach.remote import Endpoint, IpcError

LEDGER_COLUMNS = [
    ["time", "p"],
    ["sym", "s"],
    ["account", "s"],
    ["amount", "f"],
    ["note", " "],
]
LEDGER_Q = (
    "piggybank_general_ledger:([] time:`timestamp$(); sym:`symbol$(); account:`symbol$(); "
    "amount:`float$(); note:())"
)
JOB_Q = (
    ".qetl.job.stream.define[`pb_general_ledger;"
    "`procname`subscribe_to`publishes`note!(`pb_ledger1;`symbol$();enlist `{table};"
    '"publishes the general ledger")];\n'
)
SORT_DEFAULT = [
    {"tabname": "default", "att": "p", "col": "sym", "sort": True},
    {"tabname": "default", "att": "", "col": "time", "sort": True},
]


def make_bundle(
    where: Path, ledger_q: str = LEDGER_Q, table: str = "piggybank_general_ledger"
) -> Path:
    """A sidecar bundle with a `piggybank_general_ledger` table and the job that
    publishes it - a fixture; the real bundle is not in this repository."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "bundle.json").write_text(json.dumps({"name": "piggybank", "version": "1.0.0"}))
    (where / "pb_general_ledger.q").write_text(JOB_Q.format(table=table))
    (where / "tables.q").write_text(ledger_q + "\n")
    (where / "catalog.q").write_text(f'.qcat.describe[`{table}]:"the ledger";\n')
    return where


def probe(procname: str, proctype: str, port: int, stack: str, **extra: Any) -> dict[str, Any]:
    base = {
        "procname": procname,
        "proctype": proctype,
        "stackid": stack,
        "torq": "5.2.12",
        "kdb": "5",
        "port": port,
        "host": "testhost",
        "schemafile": "",
        "tplogdir": "",
        "procfile": "",
        "owned": [],
    }
    return {**base, **extra}


class FakeStack:
    """A stack of fake processes on one host, reachable by port."""

    def __init__(
        self, stack_id: int = 6000, *, torq: str = "5.2.12", schema_file: str = ""
    ) -> None:
        self.stack_id = stack_id
        self.calls: list[tuple[int, str, dict]] = []
        self.procs: dict[int, dict[str, Any]] = {}
        self.down: set[int] = set()
        #: what the CLI's `_die` was handed, when a test wires it in
        self.errors: list[str] = []
        sid = str(stack_id)
        self.add(
            probe("discovery1", "discovery", stack_id + 1, sid, torq=torq, services=[]),
        )
        self.add(
            probe(
                "stp1",
                "segmentedtickerplant",
                stack_id,
                sid,
                torq=torq,
                schemafile=schema_file,
                tplogdir="/data/tplogs",
                chained=False,
                multilog="tabperiod",
                batchmode="defaultbatch",
                createlogs=True,
                errmode=True,
                replayperiod="day",
                kdbtplog="/data/tplogs",
                dldir=":/data/tplogs/stp1_2026.10.16",
                custom=[],
                tables=["currlog", "logmsg", "quote", "trade"],
                published=["quote", "trade"],
                schemas={},
                subscribers={},
                filtered={},
                logs={},
                msgcount={},
                rowcount={},
                buffered={},
            )
        )
        for name, offset in (("rdb1", 2), ("wdb1", 5)):
            kind = name[:3]
            ext: dict[str, Any] = {
                "ignorelist": ["heartbeat", "logmsg"],
                "tables": ["quote", "trade"],
                "schemas": {},
                "rows": {},
                "tickerplanttypes": ["segmentedtickerplant"],
                "subscribed": ["quote", "trade"],
            }
            if kind == "rdb":
                ext |= {
                    "subscribeto": [""],
                    "subfiltered": False,
                    "subtables": ["quote", "trade"],
                    "hdbdir": ":/data/hdb",
                    "reloadenabled": True,
                }
            else:
                ext |= {
                    "subtabs": [""],
                    "subsyms": [""],
                    "mode": "save",
                    "writedownmode": "default",
                    "savedir": ":/data/wdbhdb",
                    "hdbdir": ":/data/hdb",
                    "sortrows": list(SORT_DEFAULT),
                }
            self.add(probe(name, kind, stack_id + offset, sid, torq=torq, **ext))
        self.add(probe("hdb1", "hdb", stack_id + 3, sid, torq=torq), listed_only=True)
        self.refresh_discovery()

    def add(self, p: dict[str, Any], listed_only: bool = False) -> None:
        if not listed_only:
            self.procs[p["port"]] = p
        else:
            self.procs.setdefault(-p["port"], p)

    def refresh_discovery(self) -> None:
        listed = sorted(
            (p for p in self.procs.values() if p["proctype"] != "discovery"),
            key=lambda p: p["port"],
        )
        services = [
            {
                "procname": p["procname"],
                "proctype": p["proctype"],
                "hpup": f":testhost:{abs(p['port'])}",
            }
            for p in listed
        ]
        for p in self.procs.values():
            if p["proctype"] == "discovery":
                p["services"] = services

    def named(self, procname: str) -> dict[str, Any]:
        return next(p for p in self.procs.values() if p["procname"] == procname)

    def call(self, endpoint: Endpoint, op: str, request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((endpoint.port, op, request))
        if endpoint.port in self.down or endpoint.port not in self.procs:
            raise IpcError(endpoint, op, "cannot connect: Connection refused")
        if op != "probe":
            raise AssertionError(f"FakeStack answers probe only, not {op}")
        answer = dict(self.procs[endpoint.port])
        return answer

    def ops(self) -> set[str]:
        return {op for _, op, _ in self.calls}
