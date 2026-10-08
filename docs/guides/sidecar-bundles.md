# Sidecar bundles

A **bundle** is a versioned folder of jobs kept outside this repository, plus
the plant tables, catalog entries and process overrides they need. The same
installer puts it into a checkout (`uqs job install`) or a release
(`uqs deploy build --bundle`). A deployment starts only the jobs you name.
Bundles are installed as copies, so a release runs on a clean server with no
workstation or Git checkout behind it.

## Layout

```
piggybank/
  bundle.json              {"name": "piggybank", "version": "1.4.0"}
  *.q                      sources, workers and streaming jobs
  tables.q                 optional: plant tables and nested[...] contracts
  catalog.q                optional: .qcat.describe entries for those tables
  process_overrides.csv    optional: procname,field,value for its own processes
```

Each `.q` file is placed by what it declares, as `uqs job install` does for any
folder. `test_*.q` and every non-`.q` file (`.env`, logs, data) stay out of the
tree. `tables.q` takes one `name:([]...)` or `nested[...]` line per entry, and
`catalog.q` must describe every table it defines.

## Install into a checkout

```bash
uqs job install ../piggybank --dry-run   # the plan
uqs job install ../piggybank --yes
```

The additions go between `/ BEGIN bundle <name>` and `/ END bundle <name>` in
`plant_tables.q`, `uqs_catalog.q` and `process_overrides.csv`.
`src/etl/installed_bundles.json` records what each bundle installed.
Reinstalling is idempotent, and an upgrade replaces only the bundle's own files.
A conflict with the tree or another bundle is refused before anything is
written.

## Bundles a runtime declares

A runtime decides which stack exists, so it also decides which bundles are in
it. Declare them in `runtime_bundles.json` at the repository root (gitignored),
or in a file `UQS_RUNTIME_BUNDLES` names, with folders relative to the file:

```json
{"uqf": ["../piggybank"], "crypto": ["../piggybank", "../marketwarehouse"]}
```

```bash
uqs --runtime crypto runtime prepare --dry-run   # the composition, nothing written
uqs --runtime crypto runtime prepare             # install it
```

A runtime has its declared bundles' jobs, the processes they depend on and their
tables, and no other bundle's. So a profile cannot start a bundle job its
runtime does not declare. A bundle a runtime stops declaring leaves that
runtime's stack, but stays installed. `uqs job install <bundle>` adds a bundle
to the runtime it runs under. `prepare` starts nothing.

`uqs --runtime crypto deploy build` resolves the same composition. `--bundle`
adds folders to the declared ones, never replaces them. The manifest records the
`runtime`, and each bundle's `source` (`runtime` or `explicit`); `push` runs the
release under that runtime. `uqs deploy build --dry-run` prints the composition
without building.

## Build and deploy

```bash
uqs deploy build --bundle ../piggybank
uqs deploy push dist/uqf-<release>.tar.gz --host uqf-server --dest /opt/uqf \
  --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \
  --profile essential --jobs pb_quotes --live --dry-run
```

- **`--jobs`** names streaming jobs from the artifact's bundles. Each starts
  beside the profile with the processes it depends on, and `--dry-run` prints
  the resolved list. Without `--jobs`, no sidecar job starts.

- **Workers are installed, never run.** Naming one in `--jobs` is refused. A
  backfill is a separate, deliberate step:

  ```bash
  ssh uqf-server 'cd /opt/uqf/current && source ./deploy.env && \
    .venv/bin/uqs backfill pb_backfill --version v1 --from 2026-09-01 --to 2026-09-02'
  ```

- **Verification** checks that the added processes answer as themselves and that
  `stp1` carries the bundle tables. Failure blocks activation, and rollback
  restores the previous release's processes.

## Credentials and ODBC

Credentials never enter an artifact. On the server, put them in
`<dest>/shared/config/`:

- `scripts/torqconfig/sources.csv`: what each source connects to, naming the
  variable that holds any secret (`uqs config sources`);
- `secrets.env`: the `NAME=VALUE` lines for those variables. `deploy.env`
  sources it, and a deployment refuses it if others can read it.

ODBC sources also need a driver set up: see [ODBC](odbc.md).

## Fixtures and real connections

- **Fixture runs.** The q suites, and any source run without a credential, read
  the source's declared fixture. They prove the code, not the connection.
- **`--live`.** A deployment with `--live` refuses a source with no credential
  instead of falling back to its fixture, so fixture rows are never published as
  real ones.
- **Real connection check.**
  `python3 scripts/test.py smoke --targets HOST:PORT --tables TABLE:COL,COL`
  checks that each live source answers and its tables still have those columns.
  It is never part of ordinary CI.

Not done by a deployment: data migration, automatic backfills, or probing
sources before a job connects.

## PeachQ

`peachq-etl` is an **experimental** runtime: PeachQ's capture stack with this
tree's layers, plus the bundles declared for it. It has no uqf pipelines of its
own. The `peachq` runtime is unchanged and still runs the capture stack only.

```bash
export UQF_PEACHQ=/path/to/peachq/q
uqs --runtime peachq-etl runtime prepare --bundle ../synthpq --dry-run
uqs --runtime peachq-etl runtime prepare --bundle ../synthpq
uqs --runtime peachq-etl start --profile capture
uqs --runtime peachq-etl start synthfeed1
```

Bundles are declared and installed as for any other runtime, through
`runtime_bundles.json` or `--bundle`, with the same installer. The ledger
records `peachq-etl` as each one's runtime, so the jobs join no other runtime.

### The converted q tree

PeachQ cannot load nested `\d` contexts, and the ETL tree uses them. So the
processes do not load the checkout. They load a copy in
`output/uqs-peachq-etl/qtree`:

- **What it holds.** `src/` and `scripts/` from the checkout, installed bundle
  jobs included. Each q file is rewritten by
  `scripts/portable/flatten_contexts.py`, the converter that
  `uqs deploy build --q-target 4.0` uses. `UQF_ROOT`, `UQF_SCRIPTS` and both
  service layers point at the copy.
- **When it is built.** Every `start`, before anything starts. A dry run of
  `runtime prepare` converts the tree and the bundles' files in memory and
  writes nothing.
- **When it is refused.** If any place cannot be converted with certainty, the
  error names its file and line. The copy must also load `src/init.q` and
  `src/etl/init.q` on PeachQ and print a sentinel. q exits 0 whatever happens
  while it loads, so only the sentinel counts.
- **Publishing.** The copy is built beside the published tree and swapped in by
  rename. A failed preparation leaves the previous tree as it was, records the
  reason in `qtree-failed.json`, and starts nothing. `qtree.json` records what
  was converted.
- **Rebuilding.** An unchanged checkout reuses the tree. `stop` and `summary`
  never rebuild it.
- **What is never written.** The checkout and the vendored TorQ trees.

### What runs, and what is refused

Verified by `test_peachq_pipelines.py`:

- the capture stack: discovery, the tickerplants, rdb1, hdb1 and gateway1;
- a bundle's streaming job publishing into rdb1;
- a bundle's bounded worker writing into the HDB, with its run status and
  coverage recorded.

PeachQ cannot upsert onto a partition on disk, set an attribute there, or run
`.Q.chk`. So on PeachQ, `src/etl/core/io_hdb.q` works differently:

- **Appending.** It rewrites a partition whole, rather than upserting onto it.
- **Finishing.** It sorts each partition by sym, then time, but sets no `p#sym`
  and runs no `.Q.chk`. uqs's bootstrap fills in missing tables from the schema
  instead.
- **Telling a partition is finished.** With no attribute to read, a partition
  counts as finished when it is in that order.

KDB-X does exactly what it did before.

Before a backfill starts on `peachq-etl`, uqs asks the PeachQ binary what it
supports. Today that is one thing: whether it can load a shared library (`2:`).
A worker whose source uses ODBC needs a driver, which is a shared library, so it
is refused on a build that cannot load one, naming the reason. The pinned build
(`scripts/peachq.py`) can; a static download cannot. `--mode validate` and
`plan` open no source, so they need nothing.

Bounded workers still never start on their own. Installing, preparing and
starting the stack run no backfill.

Not yet proven on PeachQ:

- the rest of the ETL tree's sources and jobs;
- end-of-day, keyed rewrites (`--on-conflict replace`) and the other HDB
  operations still listed in `tests/q/peachq_known_gaps.txt`;
- `uqs deploy` of this runtime.

`UQF_PEACHQ_STACK_TEST=1` runs the whole-stack test. It needs the 6550 ports.
