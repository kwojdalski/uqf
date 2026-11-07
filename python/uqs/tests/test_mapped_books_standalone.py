"""The as-of reads run on hdb1, which loads nothing of uqf but two files (#2288).

A markout's books are read where the partitions are mapped, over IPC, by
`.qetl.mapped.tob_at` and `.qetl.mapped.depth_at`; hdb1 loads src/etl/core/mapped_books.q
and mapped_depth.q after its database (procs.VENDORED_LOAD_OVERLAY). Those two files must
therefore stand on their own: this loads them into a bare q - no library, no etl tree - over a
small partitioned database, and asks for the books and their depth.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from uqs.paths import repo_root
from uqs.stack import procs

ROOT = repo_root()

#: argv: the database root, then the two files.
SCRIPT = r"""
root:hsym `$getenv`HDB;
syms:`EURUSD`GBPUSD;
(` sv root,`sym) set syms;
`sym set syms;
day:{[root;d]
    n:12;
    t:2026.01.02D00:00:00+1000000000*asc n?3600;
    r:([] time:t; sym:n#syms; source:n#`a`b; market:n#`fx; source_time:t; clock:n#`venue;
        bid:1+0.001*til n; ask:2+0.001*til n);
    r:update bid_prices:{x,x-0.001}each bid, ask_prices:{x,x+0.001}each ask from r;
    r:`sym`source`source_time xasc r;
    r:update rid:til count i from r;
    market_data::r;
    .Q.dpfts[root;d;`sym;`market_data;`sym]};
day[root;2026.01.02];
system "l ",getenv`HDB;
system "l ",getenv`MAPPED_BOOKS;
system "l ",getenv`MAPPED_DEPTH;
p:([] sym:`EURUSD`GBPUSD; time:2#2026.01.02D00:30:00);
b:.qetl.mapped.tob_at`probes`read_from`skew`venues!(p;2026.01.01D00:00:00;1;`all);
-1 "BOOKS|",string count b;
-1 "ARRANGED|",$[0=count .qetl.mapped.arranged b;"yes";"no"];
d:.qetl.mapped.depth_at`books`cols!(b;`bid_prices`ask_prices);
-1 "DEPTH|",string[count d],"|",string[(first each d`bid_prices)~d`bid];
-1 "NEWEST|",string[.qetl.mapped.newest enlist[`market]!enlist `];
exit 0
"""


def test_the_overlay_names_exactly_the_files_this_runs(q_binary, tmp_path: Path):
    _, after = procs.VENDORED_LOAD_OVERLAY["hdb1"]
    assert "${UQF_ROOT}/src/etl/core/mapped_books.q" in after
    assert "${UQF_ROOT}/src/etl/core/mapped_depth.q" in after


def test_the_two_files_stand_alone_on_a_partitioned_database(q_binary, tmp_path: Path):
    q, env = q_binary
    script = tmp_path / "standalone.q"
    script.write_text(SCRIPT)
    db = tmp_path / "hdb"
    db.mkdir()
    r = subprocess.run(
        [q, str(script), "-q"],
        env={
            **env,
            "HDB": str(db),
            "MAPPED_BOOKS": str(ROOT / "src/etl/core/mapped_books.q"),
            "MAPPED_DEPTH": str(ROOT / "src/etl/core/mapped_depth.q"),
        },
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )
    out = r.stdout
    assert "BOOKS|2" in out, out + r.stderr
    assert "ARRANGED|yes" in out, out
    assert "DEPTH|2|1" in out, "the ladders read through the rids start at the flat top"
    assert "NEWEST|2026.01.02D" in out, out
