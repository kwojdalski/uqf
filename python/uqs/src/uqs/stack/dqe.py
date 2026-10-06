"""DQE's query list: the vendored rows, plus the metatables this tree builds.

TorQ's DQE process (dqe1) reads `dqengineconfig.csv` at start and at every end
of day, arms one timer per row, and at that time sends the row's `.dqe`
function to the row's process, storing what comes back in DQEDB. That is the
scheduling, transport and persistence `.qmeta` deliberately does not have
(docs/guides/metatables.md), so a metatable is wired in as one more row here.

EXTEND, NEVER EDIT - the same approach as process.csv in stack/procs.py. The
vendored file is read fresh on every bootstrap, its rows are kept as they are,
and this tree's rows are appended into a generated copy in TORQDATA. DQE is
pointed at the copy by scripts/processes/uqs_dqe_config.q, which dqe1 loads
before its own script (VENDORED_LOAD_OVERLAY in stack/procs.py): `.dqe.configcsv`
is read once, when dqe.q loads, and keeps a value that is already set.

dqe1 and dqedb1 keep the vendored startwithall=0, so none of this runs until
`uqs start dqe1 dqedb1`. dqe.q adds `tickerplant` to its own connections, and
stp1's inbound budget (VENDORED_PLANT_CLIENTS in model/pipeline_edges.py) has
one slot to spare - starting DQE with the stack is a separate decision.
"""

from __future__ import annotations

import csv

from uqs.paths import UqsPaths

#: The vendored file's columns, in its order.
DQE_CONFIG_FIELDS = ("query", "params", "proc", "querytype", "starttime")

#: The metatables DQE builds from hdb1, each for the previous day's partition.
#:
#: `params` is q, `value`d by DQE when it arms the timers - at start and at
#: every end of day - so `.z.d-1` is the day just finished each time. DQE runs
#: UTC (`.dqe.utctime`), as the HDB's date partitions are. It must hold no
#: comma: DQE reads the file as plain comma-separated text, with no quoting.
#: The arguments are `.dqe.uqf_metatable`'s (scripts/processes/torq_metatables.q):
#: name, table, partition column, partitions, group columns, aggregates.
#:
#: 04:30 UTC, as the vendored rows: after the end-of-day write and the HDB's
#: reload. `quotes` holds its prices as vectors, so counts and time bounds are
#: the measurements that mean something per row.
UQF_DQE_ROWS: tuple[dict[str, str], ...] = (
    {
        "query": "uqf_metatable",
        "params": (
            "(`meta_quotes_by_sym;`quotes;`date;enlist .z.d-1;enlist`sym;"
            "`rows`first_time`last_time!((count;`i);(min;`time);(max;`time)))"
        ),
        "proc": "`hdb1",
        "querytype": "table",
        "starttime": "04:30:00.000000000",
    },
)


def dqe_config_rows(paths: UqsPaths) -> list[dict[str, str]]:
    """The vendored rows, then this tree's. A vendored file that is absent
    contributes no rows rather than failing: DQE then runs only these."""
    vendored = paths.torqapphome / "appconfig" / "dqengineconfig.csv"
    rows: list[dict[str, str]] = []
    if vendored.is_file():
        with vendored.open(newline="") as f:
            rows = list(csv.DictReader(f))
    return [*rows, *(dict(row) for row in UQF_DQE_ROWS)]


def write_dqe_config(paths: UqsPaths) -> None:
    """Write the generated copy DQE reads (see the module docstring)."""
    with paths.generated_dqe_config.open("w", newline="") as f:
        # Plain \n, as process.csv: q's 0: reads the file, and a \r would
        # end up glued to each row's starttime.
        writer = csv.DictWriter(f, fieldnames=DQE_CONFIG_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(dqe_config_rows(paths))
