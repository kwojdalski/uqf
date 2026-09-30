"""What `uqs job new` writes must LOAD in q, and register what it declares.

test_scaffold.py and test_scaffold_options.py read the generated text back
through the registry's own reader, which proves the tree can PARSE it. Neither
can prove q accepts it: that needs an interpreter, and both templates have
failed exactly that way before (a fixture that threw stopped src/etl/init.q
loading; an empty one was refused by `.qetl.transform.define`). A core change
that a template does not follow - a new required key, a renamed function -
would otherwise go unseen until someone scaffolded a job.

So every kind is scaffolded into a copy of the tree, the whole ETL tree is
loaded the way a process loads it, and each declaration is looked up in the
registry it should have landed in. The handlers are then called, and must
throw "not implemented": a scaffold that loads but does nothing is the
failure the templates exist to prevent.

Needs q, so it skips without one - like test_example_scripts.py. Run it with
the q lanes, where the interpreter is.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from uqs.interpreter import q_interpreter
from uqs.paths import RUN_TESTS_FILE, STACK_TABLES_TEST
from uqs.scaffold import jobs, write
from uqs.scaffold import worker as backfill
from uqs.scaffold.normalizer import definition_columns, normalizer
from uqs.scaffold.templates import table_definition

UQF_ROOT = Path(__file__).resolve().parents[3]
TIMEOUT_SECONDS = 120

#: One of each kind, plus the ODBC variant of a source. The etl and the
#: normalizer read the feed's table, so they are scaffolded after it.
_FEED_COLUMNS = "sym:symbol, px:float"

#: Each line is `label: ok` or `label: FAILED`. Checked by label, so a check
#: that never ran is missing rather than silently passing.
_CHECKS = """
\\l src/init.q
\\l src/etl/init.q
registered:{[def;name] @[{x y; 1b}[def];name;{[e] 0b}]}
unwritten:{[f] @[{x[]; 0b};f;{[e] e like "*not implemented*"}]}
check:{[label;ok] -1 label,": ",$[ok;"ok";"FAILED"];}
check["feed registered"; registered[.qetl.job.stream.def;`smokefeed]]
check["etl registered"; registered[.qetl.job.stream.def;`smokeetl]]
check["normalizer registered"; registered[.qetl.job.stream.def;`smokenorm]]
check["ipc source registered"; registered[.qetl.source.def;`smokebf]]
check["odbc source registered"; registered[.qetl.source.def;`smokedb]]
check["odbc source declares its transport"; `odbc~.qetl.source.def[`smokedb]`transport]
check["ipc worker registered"; registered[.qetl.job.bounded.def;`smokebf_backfill]]
check["odbc worker registered"; registered[.qetl.job.bounded.def;`smokedb_backfill]]
check["feed on_timer throws not implemented"; unwritten {.qpipe.job.smokefeed.on_timer[]}]
check["etl on_batch throws not implemented"; unwritten {.qpipe.job.smokeetl.on_batch[`t;()]}]
exit 0
"""

LABELS = [line.split('"')[1] for line in _CHECKS.splitlines() if line.startswith('check["')]


def _kdbx() -> tuple[str, dict[str, str]]:
    env = os.environ.copy()
    q = q_interpreter(env)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    env.setdefault("QHOME", str(Path.home() / ".kx"))
    return str(q), env


def _copy_of_the_tree(root: Path) -> None:
    """What loading and scaffolding touch: src/, the plant's q files, and the
    two test lists a scaffold appends to. Not the whole repository."""
    shutil.copytree(UQF_ROOT / "src", root / "src")
    shutil.copytree(UQF_ROOT / "scripts" / "processes", root / "scripts" / "processes")
    for rel in (RUN_TESTS_FILE, STACK_TABLES_TEST):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(UQF_ROOT / rel, root / rel)


def _scaffold_every_kind(root: Path) -> None:
    feed_table = table_definition("smoke_ticks", jobs.parse_columns(_FEED_COLUMNS))
    plans = [
        jobs.streaming_job("smokefeed", [], "smoke_ticks", _FEED_COLUMNS),
        jobs.streaming_job(
            "smokeetl",
            ["smoke_ticks"],
            "smoke_stats",
            "sym:symbol, n:long",
            known_tables={"smoke_ticks"},
        ),
        normalizer(
            "smokenorm",
            ["smoke_ticks"],
            jobs.parse_columns("sym:symbol, mid:float"),
            {"smoke_ticks": definition_columns(feed_table)},
            known_tables={"smoke_ticks"},
        ),
        backfill.bounded_worker("smokebf", "smoke_hist", "sym:symbol, px:float"),
        backfill.bounded_worker("smokedb", "smoke_db", "sym:symbol, amt:float", transport="odbc"),
    ]
    for plan in plans:
        write.apply_plan(plan, root)


def test_the_checks_are_all_named():
    """A check this list does not name would be printed and never asserted."""
    assert len(LABELS) == _CHECKS.count('check["')
    assert len(set(LABELS)) == len(LABELS)


def test_every_scaffolded_kind_loads_and_registers(tmp_path):
    qbin, env = _kdbx()
    _copy_of_the_tree(tmp_path)
    _scaffold_every_kind(tmp_path)
    (tmp_path / "smoke.q").write_text(_CHECKS)
    result = subprocess.run(
        [qbin, "smoke.q", "-q"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        text=True,
    )
    reported = dict(
        line.rsplit(": ", 1)
        for line in result.stdout.splitlines()
        if line.endswith((": ok", ": FAILED"))
    )
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-25:])
    assert result.returncode == 0, f"the scaffolded tree did not load:\n{tail}"
    missing = [label for label in LABELS if label not in reported]
    failed = [label for label in LABELS if reported.get(label) == "FAILED"]
    assert not missing, f"checks that never ran: {missing}\n{tail}"
    assert not failed, f"checks that failed: {failed}\n{tail}"
