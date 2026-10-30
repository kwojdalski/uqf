"""`uqs deploy check` (formerly verify): is a deployed profile actually up? (#773, #835)

`uqs deploy push` runs this ON THE SERVER, from the release's own
environment, after `uqs start --profile ...` has returned. A start command
that returned says only that torq.sh launched something; a deployment
succeeds when every process the profile promises answers. So for each one,
until a deadline:

  identity  - `.proc.procname` over q IPC is that process's own name, so the
              port is held by the process meant and not a leftover;
  library   - where the quant library is loaded (the pipeline processes,
              through .qtorq.load_uqf), a known forward comes back right;
  ETL       - where the ETL tree is loaded, every registered transform's own
              examples pass (.qetl.transform.verify_all) - a synthetic check
              that reads and writes nothing.

Every pipeline process must pass BOTH: it loads the library and the ETL tree
(.qtorq.load_uqf), so one that answers "not loaded" for either is broken,
however healthy the rest of the profile is. Other processes are checked where
they load either. The result is one JSON object on stdout, then
DEPLOY_VERIFY_OK or DEPLOY_VERIFY_FAILED on the last line, and the exit code
agrees with it. Nothing secret is printed: the IPC credentials are the
stack's defaults and never appear in the output.

A deployment that selected sidecar jobs (#800) adds `--procs`, the jobs'
processes and their dependency closure, checked exactly like the profile's;
`--tables`, the bundles' plant tables, which the tickerplant (stp1) must
carry; and with `--live`, every pipeline process must report that it refuses
a source's fixture (.qetl.source.live_required) - proof the deployment's
setting reached the processes, rather than a hope that it did.

`--ports-free` asks only whether something already listens on the profile's
ports - push runs it just before it starts the profile, so a port held by
anything else fails the deployment before a process can wedge on it.
"""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from uqs.model import infra, profiles
from uqs.model.registry import PIPELINES
from uqs.paths import default_paths
from uqs.stack import listing, runtime

OK_MARKER = "DEPLOY_VERIFY_OK"
FAILED_MARKER = "DEPLOY_VERIFY_FAILED"

IDENTITY_EXPR = ".proc.procname"
#: The same forward deploy_smoke.q checks offline; 0n where no library is loaded.
LIBRARY_EXPR = "@[{.qf.px.fwd_simple[1.1;0.05;0.02;1f]};::;0n]"
LIBRARY_EXPECTED = 1.1323529411764708
#: What `.qetl.transform.verify_all[]` said, checked on the process (#1784):
#: 1b or 0b - `all` of its boolean `passed` column - where the ETL tree is
#: loaded, 0n where it is not, and a string saying why when it threw or
#: answered something else. The trap used to answer 0n for all three, so a
#: verify_all that threw read as "not loaded", which a process outside the
#: pipelines passes with. Only `passed` is read, so only its shape is checked;
#: test_deploy_verify_contract.py runs this against the tree under KDB-X.
ETL_EXPR = (
    "$[@[{100h=type get x};`.qetl.transform.verify_all;0b];"
    "@[{r:.qetl.transform.verify_all[];"
    '$[0=count r;"verify_all verified no transform";'
    'not 98h=type r;"verify_all answered type ",string[type r],", not a table";'
    'not `passed in cols r;"verify_all answered no passed column";'
    'not 1h=type r`passed;"verify_all\'s passed is type ",string[type r`passed],", not boolean";'
    'all r`passed]};::;{"verify_all threw: ",x}];'
    "0n]"
)
#: 1b where a missing credential is refused rather than read as the fixture.
LIVE_EXPR = "@[{.qetl.source.live_required[]};::;0n]"
#: The process that must carry every plant table.
PLANT = "stp1"

Query = Callable[[str, int], object]


@dataclass
class ProcessCheck:
    """What one process answered."""

    process: str
    port: int
    pipeline: bool
    identity: str = ""
    library: str = "not checked"
    etl: str = "not checked"
    ok: bool = False
    error: str = ""


def missing_tables_expr(tables: list[str]) -> str:
    """q for the `tables` the process does not define, shown with -3!."""
    return "-3!((),`" + "`".join(tables) + ") except tables[]"


def missing_tables(answer: object) -> str:
    """'' when the answer to missing_tables_expr names nothing, else the names."""
    text = str(answer)
    return "" if text in ("`symbol$()", "0#`") else text


def check_process(proc: ProcessCheck, query: Query, live: bool = False) -> ProcessCheck:
    """Ask one process who it is and, where it has them, the library and ETL
    checks. Raises when it does not answer, so the caller retries it."""
    name = str(query(IDENTITY_EXPR, proc.port))
    proc.identity = name
    if name != proc.process:
        proc.error = f"port {proc.port} answered as {name!r}, not {proc.process}"
        proc.ok = False
        return proc
    value = query(LIBRARY_EXPR, proc.port)
    if _is_null(value):
        proc.library = "not loaded"
    elif isinstance(value, (int, float)) and abs(value - LIBRARY_EXPECTED) < 1e-6:
        proc.library = "ok"
    else:
        proc.library = f"wrong: {value!r}, expected {LIBRARY_EXPECTED!r}"
    proc.etl = etl_verdict(query(ETL_EXPR, proc.port))
    if proc.pipeline:
        # a pipeline process loads both; "not loaded" is a broken process
        missing = [c for c, v in (("library", proc.library), ("ETL", proc.etl)) if v != "ok"]
        if live:
            required = query(LIVE_EXPR, proc.port)
            if _is_null(required) or not bool(required):
                missing.append("live-sources")
        proc.ok = not missing
        proc.error = "" if proc.ok else f"pipeline process: {' and '.join(missing)} check not ok"
        return proc
    proc.ok = not proc.library.startswith("wrong") and not proc.etl.startswith("failed")
    proc.error = "" if proc.ok else "a library or ETL check failed"
    return proc


def etl_verdict(answer: object) -> str:
    """ETL_EXPR's answer as a check: "ok", "failed" (an example failed), "not
    loaded", or "failed: why" - for q's own reason, and for any answer that is
    not one ETL_EXPR gives, which must never read as `ok` the way any truthy
    value used to."""
    if isinstance(answer, bool):
        return "ok" if answer else "failed"
    if answer is None or (isinstance(answer, (int, float)) and _is_null(answer)):
        return "not loaded"
    if isinstance(answer, str):
        return f"failed: {answer}"
    return f"failed: malformed answer {type(answer).__name__} {answer!r:.80}"


#: q's int and long nulls as kola decodes them: an ordinary int, and truthy (#1654).
Q_INT_NULLS = (-(2**31), -(2**63))


def _is_null(value: object) -> bool:
    """q's null as kola hands it over: None, NaN for a float null - which is why
    every trap above answers `0n` - or the int/long null sentinel, which kola
    leaves an ordinary (and truthy) int, so a trap answering 0N read as `ok`."""
    if value is None:
        return True
    if isinstance(value, int) and not isinstance(value, bool) and value in Q_INT_NULLS:
        return True
    try:
        return value != value  # NaN, q's float null
    except Exception:  # noqa: BLE001 - an exotic object is not a null
        return False


def verify(
    expected: dict[str, int],
    pipelines: set[str],
    query: Query,
    deadline_s: float,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 2.0,
    tables: list[str] | None = None,
    live: bool = False,
) -> tuple[bool, list[ProcessCheck], str]:
    """(passed, one check per process, why it failed or "")."""
    checks = {
        name: ProcessCheck(process=name, port=port, pipeline=name in pipelines)
        for name, port in expected.items()
    }
    pending = set(checks)
    end = clock() + deadline_s
    while pending:
        for name in sorted(pending):
            try:
                check_process(checks[name], query, live)
            except Exception as exc:  # noqa: BLE001 - not answering yet; retried until the deadline
                checks[name].error = f"no answer: {type(exc).__name__}"
                continue
            pending.discard(name)
        if not pending or clock() >= end:
            break
        sleep(poll_s)
    results = [checks[name] for name in expected]
    unanswered = sorted(pending)
    if unanswered:
        return False, results, f"no answer within {deadline_s:g}s from {', '.join(unanswered)}"
    bad = [c for c in results if not c.ok]
    if bad:
        detail = "; ".join(f"{c.process}: {c.error}" for c in bad)
        return False, results, f"checks failed on {detail}"
    if tables:
        if PLANT not in checks:
            return False, results, f"tables {', '.join(tables)} need {PLANT}, which is not started"
        try:
            gone = missing_tables(query(missing_tables_expr(tables), checks[PLANT].port))
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            return False, results, f"{PLANT} did not list its tables: {type(exc).__name__}"
        if gone:
            return False, results, f"{PLANT} does not carry the bundle table(s) {gone}"
    return True, results, ""


def busy_ports(expected: dict[str, int], connect: Callable[[int], bool]) -> dict[str, int]:
    """The profile's processes whose port something already listens on."""
    return {name: port for name, port in expected.items() if connect(port)}


def _listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _expected(
    profile: str, base_port: int | None, procs: list[str] | None = None
) -> tuple[dict[str, int], set[str]]:
    resolved = profiles.resolve([profile])
    names = resolved + tuple(p for p in dict.fromkeys(procs or ()) if p not in resolved)
    # A one-shot process exits once started; waiting for it to answer would
    # fail every deployment of a profile that starts one.
    names = tuple(n for n in names if n not in infra.ONE_SHOT)
    ports = listing.configured_ports(default_paths(), base_port=base_port)
    missing = [n for n in names if n not in ports]
    if missing:
        raise SystemExit(f"{FAILED_MARKER}: no port for {', '.join(missing)}")
    return {n: int(ports[n]) for n in names}, {p.procname for p in PIPELINES}


def run(
    profile: str,
    *,
    deadline: float = 180.0,
    query_timeout: int = 5,
    port: int | None = None,
    procs: list[str] | None = None,
    tables: list[str] | None = None,
    live: bool = False,
    ports_free: bool = False,
    marker: bool = True,
) -> int:
    """Check the profile, print the result and - unless not `marker`, for
    `--json` - its marker line; the exit code."""
    expected, pipelines = _expected(profile, port, procs)
    if ports_free:
        busy = busy_ports(expected, _listening)
        print(json.dumps({"profile": profile, "busy": busy}))
        if marker:
            print(FAILED_MARKER if busy else OK_MARKER)
        return 1 if busy else 0

    def query(expr: str, port: int) -> object:
        return runtime.query(expr, port, timeout=query_timeout)

    passed, results, why = verify(
        expected, pipelines, query, deadline, tables=tables or [], live=live
    )
    report = {
        "profile": profile,
        "passed": passed,
        "reason": why,
        "processes": [asdict(r) for r in results],
    }
    print(json.dumps(report))
    if marker:
        print(OK_MARKER if passed else FAILED_MARKER)
    return 0 if passed else 1
