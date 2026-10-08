"""Scaffold an external publisher: `uqs job new NAME --kind external` (#715).

Real data mostly enters the stack from something a q process cannot hold a
subscription to - a Kafka topic, a vendor's websocket, a broker's API. The two
that exist (Databento, Kafka) are each a pair: a Python process outside q that
holds the subscription and publishes the raw records onto the tickerplant, and
an ordinary q streaming job that subscribes to that raw table and reshapes it.
This writes both halves:

  python/uqs/src/uqs/external/NAME_streamer.py   the process: one function to
                                                 write, `batches()`; connecting
                                                 and publishing are done
  python/uqs/src/uqs/external/NAME_feed.py       its lifecycle (the shared
                                                 DetachedProcess) and a FEED that
                                                 `uqs feed start|stop|status`
                                                 finds without a CLI edit
  python/uqs/tests/test_NAME_streamer.py         a failing test, as every scaffold
  the raw table, and the q job NAME              via scaffold/jobs.streaming_job:
                                                 subscribes to the raw table,
                                                 publishes --publishes

The raw table and the published one start with the same --columns: the q job
is where they come to differ, as databento1 folds 40 per-level columns into
four vectors. Both are defined, so neither half can publish into a table the
plant discards in silence.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from uqs.paths import TABLES_FILE, UqsError
from uqs.scaffold.columns import Columns, as_columns, nested_declaration, table_definition
from uqs.scaffold.docs import EXTERNAL
from uqs.scaffold.jobs import _check_name, _expected_table_action, streaming_job
from uqs.scaffold.plan import FileAction, ScaffoldPlan, WriteMode

EXTERNAL_DIR = "python/uqs/src/uqs/external"
PY_TEST_DIR = "python/uqs/tests"


def external_files(name: str) -> list[str]:
    """The Python files an external scaffold owns - what `uqs job remove` deletes."""
    return [
        f"{EXTERNAL_DIR}/{name}_streamer.py",
        f"{EXTERNAL_DIR}/{name}_feed.py",
        f"{PY_TEST_DIR}/test_{name}_streamer.py",
    ]


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
            "--raw-table and --publishes must differ: the q job reads one, writes the other"
        )
    cols = as_columns(columns)
    fields = [c for c, _ in cols if c != "time"]

    # The q half is an ordinary etl job; it is told the raw table exists so it
    # does not refuse a subscription to a table this same plan defines.
    job = streaming_job(
        name,
        [raw_table],
        publishes,
        columns,
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
            f"{table_definition(raw_table, cols)}\n{nested_declaration(raw_table, cols)}",
            mode=WriteMode.APPEND,
        ),
        _expected_table_action(raw_table),
    ]
    python = [
        FileAction(Path(f"{EXTERNAL_DIR}/{name}_streamer.py"), _streamer(name, raw_table, fields)),
        FileAction(Path(f"{EXTERNAL_DIR}/{name}_feed.py"), _feed(name, raw_table, publishes)),
        FileAction(Path(f"{PY_TEST_DIR}/test_{name}_streamer.py"), _py_test(name)),
    ]
    notes = [
        f"write {EXTERNAL_DIR}/{name}_streamer.py's batches(): yield lists of records from "
        "the outside source - and acknowledge the source only AFTER .u.upd returns",
        f"to run it: start the q job {name} with the stack, then `uqs feed start {name}`",
        *job.notes,
    ]
    return ScaffoldPlan(name=name, actions=raw + python + job.actions, notes=notes)


def _streamer(name: str, raw_table: str, fields: list[str]) -> str:
    return f'''"""{name}_streamer.py - hold the {name} source's subscription and push its
records at the tickerplant. Started by `uqs feed start {name}` ({name}_feed.py).

SCAFFOLDED: write `batches()`. Connecting, converting and publishing are done.

## Delivery: acknowledge AFTER the publish

If the source can replay - a Kafka offset, a cursor, a sequence number - mark a
record consumed only after `.u.upd` has returned for it (see kafka_streamer.py).
A crash in between then re-sends it, which the q job `{name}` can detect and
drop if every record carries a key unique at the source; the other order loses
it without a trace.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from typing import Any

#: The raw table `{raw_table}`'s columns in plant order. `time` is not sent:
#: .u.upd stamps it.
FIELDS = {fields!r}


def batches() -> Iterator[list[dict[str, Any]]]:
    """SCAFFOLDED. Yield lists of records, each a dict keyed by FIELDS, from the
    outside source - forever, for a live feed. A string reaches q as a symbol."""
    raise NotImplementedError("{name}_streamer.batches: not implemented")


def to_columns(records: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """Records as .u.upd wants them: one list per column, in FIELDS order -
    .u.upd places columns by position, not by name."""
    return {{field: [record[field] for record in records] for field in FIELDS}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--credential", required=True)
    parser.add_argument("--table", default="{raw_table}")
    args = parser.parse_args(argv)
    try:
        import kola
    except ImportError as exc:  # pragma: no cover - depends on the extra
        print(f"missing dependency: {{exc}}. uv pip install kola", file=sys.stderr)
        return 1
    user, _, password = args.credential.partition(":")
    q = kola.Q(args.host, args.port, user=user, passwd=password)
    q.connect()
    for batch in batches():
        if batch:
            q.sync(".u.upd", args.table, to_columns(batch))
            # acknowledge the batch to the source HERE, after the publish
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
'''


def _feed(name: str, raw_table: str, publishes: str) -> str:
    return f'''"""{name}_feed.py - start, stop and report the {name} publisher.

A process outside TorQ: not a process.csv row, so `uqs feed start|stop|status
{name}` drives it, through FEED below - the CLI finds it without an edit.
"""

from __future__ import annotations

from pathlib import Path

from uqs.external.feeds import ExternalFeed
from uqs.external.lifecycle import DetachedProcess
from uqs.paths import UqsPaths
from uqs.stack.procs import get_process_config

RAW_TABLE = "{raw_table}"

#: The tickerplant credential every external publisher uses; stp1 checks it.
CREDENTIAL = "feed:pass"


def _process(paths: UqsPaths) -> DetachedProcess:
    return DetachedProcess(
        "the {name} feed",
        paths.orchestrator_dir / "{name}_feed.pid",
        paths.log_dir / "{name}_feed.log",
    )


def start(paths: UqsPaths) -> int:
    port = get_process_config(paths, "stp1")["port"]
    runner = Path(__file__).resolve().parent / "{name}_streamer.py"
    cmd = [
        "uv", "run", "--project", str(paths.repo_root / "python" / "uqs"),
        "python", str(runner),
        "--host", "localhost", "--port", str(port),
        "--credential", CREDENTIAL, "--table", RAW_TABLE,
    ]
    return _process(paths).start(cmd, cwd=paths.repo_root)


def stop(paths: UqsPaths) -> int | None:
    return _process(paths).stop()


def status(paths: UqsPaths) -> dict[str, str]:
    return _process(paths).status(
        publishes=RAW_TABLE, **{{"reshaped by": "{name} -> {publishes}"}}
    )


FEED = ExternalFeed("{name}", start, stop, status)
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
