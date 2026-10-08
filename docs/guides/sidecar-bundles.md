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

ODBC sources also need unixODBC, the source's driver and KX's `odbc.k` where q
can load it, with `LD_LIBRARY_PATH` set on Linux (see [KX's ODBC
client](https://code.kx.com/q/interfaces/q-client-for-odbc/)). On macOS,
`scripts/dev/odbc_rosetta.sh setup` builds the x86_64 setup KX's library needs.

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
