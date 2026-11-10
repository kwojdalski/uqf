"""The start lines a start would run, worked out without writing anything (#1880).

`uqs start --dry-run` used to ask `torq.sh print` for them. torq.sh reads the
generated process.csv and setenv.sh, so the dry run had to `bootstrap()`
first - and on a fresh tree that copied the sample HDB and DQE data, wrote
process.csv, database.q, setenv.sh and the access lists, and filled the HDB:
30 MB, from a command that promised to change nothing. Under `uqs --target`,
which treats a dry run as harmless, it did the same on a server unasked.

So the lines are composed here, from what bootstrap would write: the process
rows (stack/procs.py's effective_process_rows) and the environment
(stack/env.py's build_env), both pure. `startline` and `print` below are
lib/torq/torq.sh's functions of the same names, field for field - the
parameters it reads, the empty and `localtime 0` it drops, the envsubst it
runs on each field, the row's qcmd or $QCMD - and test_start_lines holds the
two to the same output on a bootstrapped tree.

A site's own launcher (UQS_TORQ_LAUNCHER) is checked, as a start checks it,
but its lines are TorQ's: a launcher is run, never read.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass

from uqs.interpreter import q_command
from uqs.paths import UqsPaths
from uqs.stack import attach_state, attached_publisher, deployment
from uqs.stack.env import build_env, interpreter_env, with_interpreter
from uqs.stack.launcher import torq_launcher
from uqs.stack.prerequisites import check_prerequisites
from uqs.stack.procs import effective_process_rows

#: torq.sh startline's `params`: the process.csv columns each line carries as `-name value`.
_PARAMS = ("U", "localtime", "g", "T", "w", "load")
#: envsubst's variable reference: `$NAME` or `${NAME}`.
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


@dataclass(frozen=True)
class StartLine:
    procname: str
    #: the command line torq.sh would `eval`
    line: str
    #: where its output would go
    log: str

    def printed(self) -> str:
        """As `torq.sh print` shows it."""
        return f"Start line for {self.procname}:\nnohup {self.line} </dev/null >{self.log} 2>&1 &"


def _envsubst(value: str, env: dict[str, str]) -> str:
    """`echo value | envsubst`: each variable its value, an unset one empty."""
    return _VARIABLE.sub(lambda m: env.get(m.group(1) or m.group(2), ""), value)


def _names(procs: str, rows: list[dict[str, str]], env: dict[str, str]) -> list[str]:
    """torq.sh getprocs: `all` is every startwithall=1 row, in file order;
    anything else is the names given that are processes, in the order given."""
    words = procs.split()
    if words == ["all"]:
        return [r["procname"] for r in rows if _envsubst(r.get("startwithall", ""), env) == "1"]
    known = {r["procname"] for r in rows}
    return [w for w in words if w in known]


def _line(row: dict[str, str], env: dict[str, str], extras: Sequence[str]) -> str:
    """torq.sh startline for one row."""
    name = row["procname"]

    def field(column: str) -> str:
        return _envsubst(row.get(column, ""), env)

    line = (
        f"{env.get('TORQHOME', '')}/torq.q -stackid {env.get('KDBBASEPORT', '')} "
        f"-proctype {field('proctype')} -procname {name}"
    )
    for param in _PARAMS:
        value = field(param)
        if value and not (param == "localtime" and value == "0"):
            line += f" -{param} {value}"
    qcmd = field("qcmd") or env.get("QCMD", "")
    more = "".join(f"{word} " for word in extras)  # torq.sh's EXTRAS, a space after each
    return f"{qcmd} {line} {field('extras')} -procfile {env.get('TORQPROCESSES', '')} {more}"


def plan(
    paths: UqsPaths,
    procs: str = "all",
    base_port: int | None = None,
    selected: Sequence[str] | None = None,
    extras: Sequence[str] = (),
) -> list[StartLine]:
    """The lines `torq.sh start procs` would run, as bootstrap would set it up -
    with nothing written. `selected` is a profile start's set, which decides
    monitor1's connection budget (stack/deployment.py). `extras` is what the
    start passes torq.sh's `-extras` (runtime.TRACE_EXTRAS), ahead of any an
    attachment's publisher adds, as start_policy.with_publisher_extras orders them."""
    check_prerequisites(paths)
    torq_launcher(paths)  # refused here as a start refuses it
    args = ["start", procs]
    rows = effective_process_rows(
        paths, q_command(with_interpreter(paths)), deployment.planned(paths, args, selected)
    )
    env = {**os.environ, **build_env(paths, base_port=base_port), **interpreter_env(paths)}
    if attached := attached_publisher.publishers(args):
        how = attached_publisher.auth(paths, attached)
        rows, env = attached_publisher.with_auth(rows, attached, how), {**env, **how.env()}
    extras = [*extras, *attach_state.publisher_extras(paths, procs)]
    by_name = {r["procname"]: r for r in rows}
    return [
        StartLine(name, _line(by_name[name], env, extras), f"{env['KDBLOG']}/torq{name}.txt")
        for name in _names(procs, rows, env)
    ]
