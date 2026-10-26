"""Test doubles for the attach workflow: a scripted stack and a bundle fixture.

`FakeStack` answers `probe` the way a real TorQ 5.2.12 process does (the shapes
here were copied from probe.q's answers on an isolated stack, see
test_attach_stack.py), and records every call, so a test can assert that a plan
asked only read-only questions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from uqs.attach.remote import Endpoint, IpcError

#: Loads a schema file the way a restarting tickerplant does (`\l`), and says what it defined.
_LOAD_Q = """r:@[{system "l ",x;1b};getenv`UQS_SCHEMA_FILE;{x}];
owned:@[value;`.uqfattach.owned;()!()];
-1 .j.j `loaded`error`tables`owned!(1b~r;$[1b~r;"";r];string tables[];
  $[count owned;(key owned)!.j.k each value owned;()!()]);
exit 0
"""


def q_load(q_binary: tuple[str, dict[str, str]], schema: Path, scratch: Path) -> dict[str, Any]:
    """What a q that loads `schema` - as a tickerplant restart does - ends up
    with: whether it loaded, its tables and its `.uqfattach.owned` records."""
    q, env = q_binary
    script = scratch / "load_schema.q"
    script.write_text(_LOAD_Q)
    run = subprocess.run(
        [q, str(script), "-q"],
        env={**env, "UQS_SCHEMA_FILE": str(schema)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = [line for line in run.stdout.splitlines() if line.startswith("{")]
    assert lines, f"q printed no answer: {run.stdout}{run.stderr}"
    return json.loads(lines[-1])


LEDGER_COLUMNS = [
    ["time", "p"],
    ["sym", "s"],
    ["account", "s"],
    ["amount", "f"],
    ["note", "s"],
]
LEDGER_Q = (
    "piggybank_general_ledger:([] time:`timestamp$(); sym:`symbol$(); account:`symbol$(); "
    "amount:`float$(); note:`symbol$())"
)
JOB_Q = (
    ".qetl.job.stream.define[`pb_general_ledger;"
    "`procname`subscribe_to`publishes`note!(`pb_ledger1;`symbol$();enlist `{table};"
    '"publishes the general ledger")];\n'
)
#: Root globals of a starter-pack process (`system"f"` / `system"v"`), as the probe reports them.
TP_GLOBALS = ["upd", "tablelist", "init", "setup", "loghandles"]
CONSUMER_GLOBALS = {
    "rdb": ["upd", "endofday", "endofperiod", "reload", "countbysym", "hloc"],
    "wdb": ["upd", "endofday", "endofperiod", "sym"],
}
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
        "names": [],
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
        #: "host:port" -> q's error, for an address discovery's `reach` cannot open
        self.unreachable: dict[str, str] = {}
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
                names=[*TP_GLOBALS, "currlog", "logmsg", "quote", "trade"],
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
                "names": [*CONSUMER_GLOBALS[kind], "quote", "trade"],
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
                    "sortcsv": ":config/sort.csv",
                    "cwd": "/data",
                    "sortreload": True,
                    "sorttypes": ["sort"],
                    "sortworkertypes": [],
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
            "consumer_detach": self._detach,
            "consumer_unsubscribe": self._unsubscribe,
            "stp_unregister": self._unregister,
            "ledger_leave": self._leave,
            "sort_reload": self._sort_reload,
            "reach": self._reach,
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
        p["names"].append(t)
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
        key = "subtables" if p["proctype"] == "rdb" else "tablelist"
        added = p["proctype"] == "rdb" and t not in p.get(key, [])
        p["schemas"][t] = cols
        if not had:
            p["names"].append(t)
            p["tables"].append(t)
        p["subscribed"].append(t)
        if t not in p.get(key, []):
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
            status = ("shared" if ledger else "subscribed") if had else "created"
        return {"status": status, "replayed": 0, "subtables": added}

    # ------------------------------------------------------------ sort.csv (#1517)

    def use_sort_file(self, path: Path, *, sort_process: bool = False) -> None:
        """Point wdb1 (and, with `sort_process`, a sort1 the wdb names) at the real
        file `path`, holding its rows live as a process that started on it does."""
        procs = [self.named("wdb1")]
        if sort_process:
            sid = str(self.stack_id)
            self.add(probe("sort1", "sort", self.stack_id + 6, sid, sortreload=True))
            self.refresh_discovery()
            procs.append(self.named("sort1"))
        for p in procs:
            p["sortcsv"], p["cwd"] = ":" + str(path), "/elsewhere"
            self._load_sort(p)

    def _load_sort(self, p: dict[str, Any]) -> None:
        from uqs.attach import sort_file

        rules = sort_file.read(Path(p["sortcsv"].lstrip(":"))).rules
        p["sortrows"] = [
            {"tabname": r.table, "att": r.att, "col": r.column, "sort": r.sort} for r in rules
        ]

    def _reach(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        where = f"{req['host']}:{req['port']}"
        error = self.unreachable.get(where, "")
        return {"reached": not error, "address": where, "error": error}

    def _sort_reload(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        """sort_reload.q: `.sort.getsortcsv` on the process's own file."""
        if p.get("reload_fails"):
            return {"status": "failed", "detail": p["reload_fails"], "path": p["sortcsv"]}
        self._load_sort(p)
        rows = [
            {"att": r["att"], "col": r["col"], "sort": r["sort"]}
            for r in p["sortrows"]
            if r["tabname"] == req["table"]
        ]
        return {"status": "reloaded", "detail": "", "path": p["sortcsv"], "rows": rows}

    def ops(self) -> set[str]:
        return {op for _, op, _ in self.calls}

    # ------------------------------------------------ the operations that undo

    def _owned_problems(self, p: dict[str, Any], req: dict[str, Any]) -> list[str]:
        t, ledger = req["table"], p["owned"].get(req["table"])
        out: list[str] = []
        if ledger is None and not req.get("follows_plant"):
            out.append("no ownership record for the table")
        elif ledger is not None and ledger["token"] != req["token"]:
            out.append("the ownership record is another attachment's")
        elif ledger is not None and ledger.get("users"):
            out.append("used by attachment(s) " + ", ".join(ledger["users"]))
        if p.get("rows", {}).get(t, 0) > 0:
            out.append(f"the table holds {p['rows'][t]} row(s)")
        return out

    def _detach(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        t = req["table"]
        if not p["schemas"].get(t) and t not in p["owned"] and t not in p["subscribed"]:
            return {"status": "absent", "problems": []}
        problems = self._owned_problems(p, req)
        # subscriptions from a tickerplant other than the one this attachment registered on
        elsewhere = p.get("foreign_subs", {}).get(t, [])
        if elsewhere:
            problems.append("also subscribed from another tickerplant: " + ", ".join(elsewhere))
        if problems:
            return {"status": "refused", "problems": problems}
        if req["dry_run"]:
            return {"status": "dry_run", "problems": []}
        p["schemas"].pop(t, None)
        p["owned"].pop(t, None)
        for key in ("subscribed", "subtables", "tablelist", "names", "tables"):
            if t in p.get(key, []):
                p[key].remove(t)
        return {"status": "removed", "problems": []}

    def _unsubscribe(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        """consumer_unsubscribe.q: only the subscription, never the table or its rows."""
        t = req["table"]
        if not req.get("tickerplant"):
            return {"status": "refused", "problems": ["the request names no tickerplant"]}
        if t not in p["subscribed"]:
            return {"status": "absent", "problems": []}
        if req["dry_run"]:
            return {"status": "dry_run", "problems": []}
        p["subscribed"].remove(t)
        if req.get("subtables") and t in p.get("subtables", []):
            p["subtables"].remove(t)
        return {"status": "removed", "problems": []}

    def _unregister(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        t = req["table"]
        if not p["schemas"].get(t) and t not in p["owned"]:
            return {"status": "absent", "problems": []}
        problems = self._owned_problems(p, req)
        if p["msgcount"].get(t, 0) > 0:
            problems.append("messages were published to it")
        problems += [
            f"log {f['name']} is not the empty header"
            for f in p["logfiles"].get(t, [])
            if f["size"] != req["empty_size"]
        ]
        if p["subscribers"].get(t, 0) > req["expect_subscribers"]:
            problems.append("subscribers other than the attached consumers")
        if p["filtered"].get(t, 0):
            problems.append("a filtered subscription exists")
        if problems:
            return {"status": "refused", "problems": problems}
        if req["dry_run"]:
            return {"status": "dry_run", "problems": []}
        for key in ("schemas", "owned", "subscribers", "logged", "updtab", "logfiles", "replay"):
            p[key].pop(t, None)
        for key in ("tables", "published", "names"):
            if t in p[key]:
                p[key].remove(t)
        return {"status": "removed", "problems": []}

    def _leave(self, p: dict[str, Any], req: dict[str, Any]) -> dict[str, Any]:
        ledger = p["owned"].get(req["table"])
        if ledger is None:
            return {"status": "absent", "users": []}
        users = ledger.get("users", [])
        if req["attachment"] in users:
            users.remove(req["attachment"])
        return {"status": "left", "users": users}

    # ------------------------------------------------------ a publication

    def publish(self, table: str, rows: int = 3) -> None:
        """What a publisher's rows do to the processes' counters and logs."""
        tp = self.named("stp1")
        tp["msgcount"][table] = tp["msgcount"].get(table, 0) + 1
        tp["rowcount"][table] = tp["rowcount"].get(table, 0) + rows
        for f in tp["logfiles"][table]:
            f["size"] += 40
        for name in ("rdb1", "wdb1"):
            self.named(name).setdefault("rows", {})
            self.named(name)["rows"][table] = self.named(name)["rows"].get(table, 0) + rows


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
