"""Scaffold an external publisher: `uqs job new NAME --kind connector` (#715).

Real data mostly enters the stack from something a q process cannot hold a
subscription to - a Kafka topic, a vendor's websocket, a broker's API. The
ones that exist (Kafka, and the sidecar bundles' connectors) are each a pair:
a Python process outside q that holds the subscription and publishes the raw
records onto the tickerplant, and an ordinary q streaming job that subscribes
to that raw table and reshapes it.
This writes both halves:

  python/uqs/src/uqs/external/NAME_streamer.py   the process: one function to
                                                 write, `batches()`; connecting
                                                 and publishing are done
  python/uqs/src/uqs/external/NAME_feed.py       its lifecycle (the shared
                                                 DetachedProcess) and a FEED that
                                                 `uqs connector start|stop|restart|status|logs`
                                                 finds without a CLI edit - and
                                                 whose `produces` tells the stack
                                                 the raw table is fed from outside
                                                 it (#888, #1776), so nothing
                                                 else is edited for that
  python/uqs/tests/test_NAME_streamer.py         a failing test, as every scaffold
  the raw table, and the q job NAME              via scaffold/jobs.streaming_job:
                                                 subscribes to the raw table,
                                                 publishes --outputs

The raw table and the published one start with the same --schema: the q job
is where they come to differ, as a fold of forty per-level columns into four
vectors would. Both are defined, so neither half can publish into a table the
plant discards in silence.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from uqs.paths import CATALOG_FILE, TABLES_FILE, UqsError
from uqs.scaffold.columns import Columns, as_columns, plant_declarations, type_char
from uqs.scaffold.docs import EXTERNAL
from uqs.scaffold.example import GENERATED_BY
from uqs.scaffold.jobs import _check_name, _expected_table_action, streaming_job
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode

EXTERNAL_DIR = "python/uqs/src/uqs/external"
PY_TEST_DIR = "python/uqs/tests"


def external_files(name: str) -> list[str]:
    """The Python files an external scaffold writes - what `uqs job remove` deletes."""
    return [
        f"{EXTERNAL_DIR}/{name}_streamer.py",
        f"{EXTERNAL_DIR}/{name}_feed.py",
        f"{PY_TEST_DIR}/test_{name}_streamer.py",
    ]


def connector_mark(name: str) -> str:
    """The line in NAME_feed.py that says a connector scaffold of `name` wrote
    it (#2831). A job that merely shares a shipped connector's name - a
    subscriber called `kafka` - finds no such line in kafka_feed.py, so
    `uqs job remove` leaves the hand-written modules alone. Unlike the
    streamer's SCAFFOLDED, it stays when the feed is edited: it records who
    wrote the file, not that nobody has touched it since."""
    return f"{GENERATED_BY} {name} --kind connector`"


def external_feed(
    name: str,
    raw_table: str,
    publishes: str,
    columns: Columns,
    *,
    known_tables: set[str],
    procname: str | None = None,
    start_with_all: bool = False,
    profile: str | None = None,
    unprofiled: str | None = None,
    known_profiles: Iterable[str] | None = None,
) -> ScaffoldPlan:
    """Plan the Python publisher, the raw table and the q job that reshapes it."""
    _check_name(name, "job name")
    _check_name(raw_table, "raw table")
    if raw_table in known_tables:
        raise UqsError(
            f"--raw-table {raw_table} is already a plant table - an external feed's raw "
            "table is its own, or two publishers would write one table"
        )
    if raw_table == publishes:
        raise UqsError(
            "--raw-table and --outputs must differ: the q job reads one, writes the other"
        )
    cols = as_columns(columns)
    wire = {c: type_char(lit) for c, lit in cols if c != "time"}

    # The q half is an ordinary etl job; it is told the raw table exists so it
    # does not refuse a subscription to a table this same plan defines.
    # --schema always shapes the NEW raw table; they shape the output too
    # only when the output is new - onto an existing table the q job is given
    # none, or it refuses them as shaping nothing (#1581).
    job = streaming_job(
        name,
        [raw_table],
        publishes,
        None if publishes in known_tables else columns,
        procname,
        known_tables=known_tables | {raw_table},
        start_with_all=start_with_all,
        profile=profile,
        unprofiled=unprofiled,
        known_profiles=known_profiles,
        showcase=EXTERNAL,
    )
    raw = [
        FileAction(
            TABLES_FILE,
            f"\n/ {name}'s raw records, as {name}_streamer.py publishes them from outside q.\n"
            f"{plant_declarations(raw_table, columns)}",
            mode=WriteMode.APPEND,
        ),
        _expected_table_action(raw_table),
        # The raw table is the job's input, not something a desk browses:
        # hidden from the catalog, with the reason - which is true by
        # construction, so there is nothing to fill in.
        FileAction(
            CATALOG_FILE,
            f".qcat.hidden[`{raw_table}]:\n"
            f'    "the RAW {name} records, published by {name}_streamer.py outside q and '
            f'read only by {name}, which reshapes them into {publishes}";\n',
            mode=WriteMode.APPEND,
        ),
    ]
    python = [
        FileAction(Path(f"{EXTERNAL_DIR}/{name}_streamer.py"), _streamer(name, raw_table, wire)),
        FileAction(Path(f"{EXTERNAL_DIR}/{name}_feed.py"), feed_module(name, raw_table, publishes)),
        FileAction(Path(f"{PY_TEST_DIR}/test_{name}_streamer.py"), _py_test(name)),
    ]
    notes = [
        f"write {EXTERNAL_DIR}/{name}_streamer.py's batches(): yield lists of records from "
        "the outside source - and acknowledge the source only AFTER publish() returns, "
        "which it does only for a batch the plant stored",
        f"to run it: start the q job {name} with the stack, then `uqs connector start {name}`",
        *job.notes,
    ]
    return ScaffoldPlan(name=name, actions=raw + python + job.actions, notes=notes)


def _streamer(name: str, raw_table: str, wire: dict[str, str]) -> str:
    """The publisher. `wire` is {field: its q type character}, in plant order:
    what to_columns types each column as, so a column reaches the plant as the
    type it declares rather than as kola infers it from Python values (#2873)."""
    return f'''"""{name}_streamer.py - hold the {name} source's subscription and push its
records at the tickerplant. Started by `uqs connector start {name}` ({name}_feed.py).

SCAFFOLDED: write `batches()`. Connecting, converting and publishing are done.

## Delivery: acknowledge AFTER the plant has stored it

If the source can replay - a Kafka offset, a cursor, a sequence number - mark a
record consumed only after `publish` has returned for it (see kafka_streamer.py).
`publish` sends through ACCEPT_Q, not a bare `.u.upd`: the segmented
tickerplant hands an update it refuses to its error log and RETURNS, so a bare
`.u.upd` answers success for a batch that was never stored (#2192). `publish`
raises instead, and the batch is not acknowledged. A crash in between then
re-sends it, which the q job `{name}` can detect and drop if every record
carries a key unique at the source; the other order loses it without a trace.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from typing import Any

from uqs.external.kafka_streamer import ACCEPT_Q
from uqs.paths import UqsError

#: The raw table `{raw_table}`'s columns in plant order, each with its q type
#: character. `time` is not sent: .u.upd stamps it.
WIRE = {json.dumps(wire)}
FIELDS = list(WIRE)


def batches() -> Iterator[list[dict[str, Any]]]:
    """SCAFFOLDED. Yield lists of records, each a dict keyed by FIELDS, from the
    outside source - forever, for a live feed. A string reaches q as a symbol."""
    raise NotImplementedError("{name}_streamer.batches: not implemented")


def to_columns(records: list[dict[str, Any]]) -> list[Any]:
    """Records as .u.upd wants them: a LIST of columns, one per field, in FIELDS
    order - .u.upd places columns by position, not by name. Not a dict: the
    segmented tickerplant prepends its time column with `(enlist ...),x`, which
    a dictionary answers with `type` (#1580).

    Each column TYPED as the raw table declares it. A Python list goes as kola
    infers it - a float column of whole numbers as longs, an int beside a float
    as a general list, a None as `::` - and the plant refuses each with `type`.
    A date or time column goes as a list, so it must hold no None."""
    import polars as pl  # kola's own dependency

    typed = {{
        "p": pl.Datetime("ns"), "s": pl.Categorical, "f": pl.Float64, "j": pl.Int64,
        "i": pl.Int32, "h": pl.Int16, "b": pl.Boolean, "n": pl.Duration("ns"),
    }}  # fmt: skip
    out: list[Any] = []
    for field, char in WIRE.items():
        values = [record[field] for record in records]
        if char in typed:
            out.append(pl.Series(field, values, dtype=typed[char]))
        elif char == "c":
            out.append("".join(values).encode())  # bytes reach q as a char vector
        else:
            out.append(values)
    return out


def publish(q: Any, table: str, records: list[dict[str, Any]]) -> None:
    """Send `records` through ACCEPT_Q, raising unless the plant STORED them."""
    answer = q.sync(ACCEPT_Q, table, to_columns(records))
    if isinstance(answer, str):
        raise UqsError(
            f"the tickerplant refused the {{table}} batch - it is in the tickerplant's error "
            f"log and was NOT stored, so do not acknowledge it: {{answer}}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--table", default="{raw_table}")
    args = parser.parse_args(argv)
    try:
        import kola
    except ImportError as exc:  # pragma: no cover - depends on the extra
        print(f"missing dependency: {{exc}}. uv pip install kola", file=sys.stderr)
        return 1
    # stp1's user:password from the environment {name}_feed.py gave it, never an
    # argument, where ps would show it (#1853).
    from uqs.external.connection import credential_from_env

    user, password = credential_from_env()
    q = kola.Q(args.host, args.port, user=user, passwd=password)
    q.connect()
    for batch in batches():
        if batch:
            publish(q, args.table, batch)
            # acknowledge the batch to the source HERE: publish returned, so it is stored
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
'''


def feed_module(name: str, raw_table: str, publishes: str) -> str:
    """NAME_feed.py as the scaffold writes it. It holds no placeholder to fill,
    so `uqs job remove` compares against this text to tell it is unedited."""
    return f'''"""{name}_feed.py - start, stop and report the {name} publisher.

{connector_mark(name)}: `uqs job remove {name}` removes it with the job.

A process outside TorQ: not a process.csv row, so `uqs connector start|stop|restart|status|logs
{name}` drives it, through FEED below - the CLI finds it without an edit.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from uqs.external import connection
from uqs.external.feeds import ExternalFeed
from uqs.external.lifecycle import DetachedProcess
from uqs.paths import UqsPaths

RAW_TABLE = "{raw_table}"
RESHAPED = "{name} -> {publishes}"


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the {name} feed",
        paths.orchestrator_dir / "{name}_feed.pid",
        paths.log_dir / "{name}_feed.log",
    )


def start(paths: UqsPaths, options: Mapping[str, Any]) -> int:
    """`options` holds every option FEED declares, defaults applied: add a
    FeedOption to FEED's options= and it is a `connector start` option.

    stp1's host, port and credential are every connector's, so they are not
    written here (uqs.external.connection): --base-port N, else the runtime's;
    --tp-credential-env NAME, else $UQF_TP_CREDENTIAL, else the starter pack's
    demo login. The credential reaches the streamer in its environment, never
    its argv (#1853)."""
    port = connection.tickerplant_port(paths, options["base_port"])
    credential = connection.tp_credential(options["tp_credential"])
    runner = Path(__file__).resolve().parent / "{name}_streamer.py"
    cmd = [
        "uv",
        "run",
        "--project",
        str(paths.repo_root / "python" / "uqs"),
        "python",
        str(runner),
        "--host",
        connection.TP_HOST,
        "--port",
        str(port),
        "--table",
        RAW_TABLE,
    ]
    env = connection.credential_env(credential)
    return _process(paths).start(cmd, cwd=paths.repo_root, env=env)


def stop(paths: UqsPaths, *, kill: bool = False) -> int | None:
    return _process(paths).stop(kill=kill)


def status(paths: UqsPaths) -> dict[str, str]:
    return _process(paths).status(publishes=RAW_TABLE, **{{"reshaped by": RESHAPED}})


FEED = ExternalFeed(
    "{name}",
    start,
    stop,
    status,
    options=(connection.BASE_PORT, connection.TP_CREDENTIAL),
    produces={{RAW_TABLE: "the external {name} feed (`uqs connector start {name}`)"}},
)
'''


def _py_test(name: str) -> str:
    return f'''"""The {name} publisher (python/uqs/src/uqs/external/{name}_streamer.py).

SCAFFOLDED, AND FAILING ON PURPOSE: replace this with what batches() and
to_columns() must do with the source's records.
"""

from uqs.external import {name}_streamer


def test_{name}_streamer_is_implemented():
    assert {name}_streamer.FIELDS, "the raw table has columns"
    raise AssertionError(
        "{name}_streamer has not been implemented yet - write this test, then batches()"
    )
'''
