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
        "owned": {},
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
        #: publishers the CLI under test started
        self.started: list[str] = []
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
                logged={},
                updtab={},
                logfiles={},
                replay={},
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
                    "tablelist": ["quote", "trade"],
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
        proc = self.procs[endpoint.port]
        if op == "probe":
            return dict(proc)
        handler = {
            "stp_register": self._register,
            "consumer_attach": self._attach,
        }.get(op)
        if handler is None:
            raise AssertionError(f"FakeStack does not answer {op}")
        return handler(proc, request)

    def _register(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        t, cols = req["table"], req["columns"]
        ledger = p["owned"].get(t)
        if p["schemas"].get(t):
            same = [list(c) for c in p["schemas"][t]] == cols
            mine = ledger is not None and ledger["token"] == req["token"]
            if same and ledger and not mine and req.get("join"):
                users = ledger.setdefault("users", [])
                if req["attachment"] not in users:
                    users.append(req["attachment"])
            return {
                "status": "owned" if mine else "shared" if ledger else "existing",
                "matches": same,
                "published": t in p["published"],
                "owner": ledger["attachment"] if ledger else "",
                "users": ledger.get("users", []) if ledger else [],
            }
        if p["multilog"] not in ("tabperiod", "tabular"):
            return {"status": "refused", "detail": "unsupported tickerplant mode"}
        log = f"{p['dldir'].lstrip(':')}/stp1_{t}20261016090000"
        p["schemas"][t] = cols
        p["tables"].append(t)
        p["published"].append(t)
        p["subscribers"][t] = 0
        p["logged"] = {**p.get("logged", {}), t: True}
        p["updtab"] = {**p.get("updtab", {}), t: True}
        p["logfiles"] = {**p.get("logfiles", {}), t: [{"name": log, "size": 8, "open": True}]}
        p["replay"] = {**p.get("replay", {}), t: 1}
        p["owned"][t] = {"token": req["token"], "attachment": req["attachment"], "created": "now"}
        return {"status": "created", "logs": [{"name": log, "size": 8}], "handle": 18}

    def _attach(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        t, cols = req["table"], req["columns"]
        ledger = p["owned"].get(t)
        had = bool(p["schemas"].get(t))
        if had and [list(c) for c in p["schemas"][t]] != cols:
            return {"status": "refused", "detail": "the table exists with a different schema"}
        mine = ledger is not None and ledger["token"] == req["token"]
        if had and ledger and not mine and req.get("join"):
            users = ledger.setdefault("users", [])
            if req["attachment"] not in users:
                users.append(req["attachment"])
        if had and t in p["subscribed"]:
            return {
                "status": "owned" if mine else "shared" if ledger else "existing",
                "rows": 0,
                "owner": ledger["attachment"] if ledger else "",
            }
        p["schemas"][t] = cols
        p["subscribed"].append(t)
        key = "subtables" if p["proctype"] == "rdb" else "tablelist"
        p[key] = [*p.get(key, []), t]
        self.named("stp1")["subscribers"][t] = self.named("stp1")["subscribers"].get(t, 0) + 1
        if not had:
            p["owned"][t] = {
                "token": req["token"],
                "attachment": req["attachment"],
                "created": "now",
            }
        if mine:
            status = "owned"
        else:
            status = ("shared" if ledger else "existing") if had else "created"
        return {"status": status, "replayed": 0}

    def ops(self) -> set[str]:
        return {op for _, op, _ in self.calls}


class FaultIpc:
    """An Ipc that fails on purpose: before a call is sent, or after the process
    has answered - the two ways a connection can be lost, and the second is the
    dangerous one, because the change happened and the caller was not told."""

    def __init__(self, inner, rules: list[tuple[str, int, str]]) -> None:
        self.inner = inner
        #: (operation, nth call of that operation from 0, "before" | "after")
        self.rules = rules
        self.ops: list[str] = []
        self._seen: dict[str, int] = {}

    def call(self, endpoint: Endpoint, op: str, request: dict[str, Any]) -> dict[str, Any]:
        self.ops.append(op)
        n = self._seen.get(op, 0)
        self._seen[op] = n + 1
        if (op, n, "before") in self.rules:
            raise IpcError(endpoint, op, "injected: connection lost before the call was sent")
        answer = self.inner.call(endpoint, op, request)
        if (op, n, "after") in self.rules:
            raise IpcError(endpoint, op, "injected: connection lost after the process answered")
        return answer
