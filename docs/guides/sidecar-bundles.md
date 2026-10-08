# Sidecar bundles

Jobs kept outside this repository, such as a desk's own feeds and backfills,
reach a server as a **bundle**: a versioned folder holding the jobs and the tree
additions they need. One installer puts a bundle into a checkout
(`uqs job install`) or into a release (`uqs deploy build --bundle`). A
deployment then starts only the jobs you name (`uqs deploy push --jobs`).

A bundle does not need the workstation it came from, a Git checkout or any
links. It is installed as copies, and a release built with it runs on a clean
server.

## What a bundle holds

```
piggybank/
  bundle.json              {"name": "piggybank", "version": "1.4.0"}
  piggybank_source.q       a source (.qetl.source.define)
  piggybank_backfill.q     a bounded worker (.qetl.job.bounded.define)
  piggybank_quotes.q       a streaming job (.qetl.job.stream.define)
  tables.q                 optional: plant tables and nested-column contracts
  catalog.q                optional: catalog descriptions of those tables
  process_overrides.csv    optional: process.csv fields for the bundle's processes
  test_piggybank.q         reported, never installed
```

Each `.q` file is placed by what it declares, in `src/etl/sources`,
`src/etl/workers` or `src/etl/streaming`, as for a plain sidecar folder. Other
files are never read, so a `.env`, a log or a data file beside the jobs stays
out of the tree.

`bundle.json` takes `name` (lower_snake_case), `version` and an optional
`description`. An unknown key is refused.

`tables.q` holds one definition per line, in the form `src/etl/plant_tables.q`
uses. A `nested[...]` line must follow the table it describes:

```q
/ piggybank's quotes
pb_quote:([]time:`timestamp$();sym:`symbol$();bid_px:();ask_px:())
nested[`pb_quote;`bid_px`ask_px!"FF"];
```

`catalog.q` must describe every table `tables.q` defines, fully qualified:

```q
.qcat.describe[`pb_quote]:
    "one piggybank quote: five levels a side, as vectors";
```

`process_overrides.csv` may set fields only for the bundle's own streaming
processes, and never `procname` or `port`:

```
procname,field,value
pb_quotes1,startwithall,0
```

## Installing into a checkout

```bash
uqs job install ../piggybank --dry-run    # the plan, nothing written
uqs job install ../piggybank --yes
```

The jobs are copied into place. The table, catalog and override additions go
into `src/etl/plant_tables.q`, `scripts/processes/uqs_catalog.q` and
`python/uqs/process_overrides.csv`, each bundle's lines between
`/ BEGIN bundle piggybank` and `/ END bundle piggybank`.
`src/etl/installed_bundles.json` records the version, the revision (when the
bundle is a Git checkout), every installed file with its sha256, the job
identities, the tables and the overrides. The derived files are regenerated
afterwards.

Installing again is idempotent. The bundle's own block is replaced, not
appended, and an unchanged bundle changes nothing. An upgrade replaces the files
the bundle installed before, and removes those it no longer ships.

Every refusal comes before anything is written:

- a job file the bundle did not install, or one another bundle installed;
- a job name the tree already declares;
- a table the tree already defines, or a table described twice;
- a bundle table with no catalog description;
- an override for a process that is not the bundle's own, or one an operator
  already set to a different value;
- a `tables.q` line that is not a definition, `nested[...]` or a `/ ` comment. A
  line of only `/` would open a q block comment in the tree file.

`--mode symlink` is refused for a bundle.

## Building a release with bundles

```bash
uqs deploy build --output dist/ \
  --bundle ../piggybank --bundle ../marketwarehouse
```

The tracked files are copied into a staging directory, and each bundle is
installed there by the same installer. The derived files, such as the port lock
and the pipeline DAG, are regenerated there. The release is packaged from that
staged tree, so the checkout is never changed. Every hash in the manifest is of
an installed file.

The manifest gains `bundles`. For each bundle it holds the version, the
revision, the installed files, the tables, the overrides and the jobs. Each
streaming job also has `needs`, its dependency closure: every uqf process it
needs, itself included. Without `--bundle`, nothing is staged and the artifact
is built as before.

## Deploying selected jobs

A deployment starts no sidecar job unless asked, and never assumes a profile
includes one:

```bash
uqs deploy push dist/uqf-<release>.tar.gz \
  --host uqf-server --dest /opt/uqf \
  --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \
  --profile essential --jobs pb_quotes,mw_quotes --live --dry-run
```

`--jobs` names streaming jobs from the artifact's bundles. Each job's `needs`
starts beside the profile: `uqs start --profile essential pb_quotes1 ...`.
`--dry-run` prints the bundles with their revisions, the selected jobs, the
resolved process list, and the workers that are installed but not run.

Naming a bounded worker is refused. A deployment installs workers and never runs
them, so installing one cannot start a backfill.

Verification checks the added processes like the profile's own: each must answer
with its own name. It also checks that `stp1` carries every bundle table. It
reads and writes no source data. A failure stops activation, and an upgrade
rolls back to the previous release's profile and the sidecar processes it ran
(`extra_processes` in its `deploy-report.json`), then checks those again. The
report records `jobs`, `extra_processes`, `bundles` with their revisions, and
`live`, never a secret.

A backfill is its own deliberate step afterwards, from the deployed release:

```bash
ssh uqf-server 'cd /opt/uqf/current && source ./deploy.env && \
  .venv/bin/uqs backfill pb_backfill --version v1 --from 2026-09-01 --to 2026-09-02'
```

## Credentials on the server

Credentials never enter an artifact. `uqs deploy build` excludes `.env`,
`*.env`, `.envrc`, keys and licences. A bundle installs only its job files and
its three addition files.

On the server, two files in `<dest>/shared/config/` are linked or loaded into
every release:

- `scripts/torqconfig/sources.csv` says which source connects to what. A row
  holds a setting and, when that setting needs a secret, the *name* of the
  variable holding it (see `uqs config sources`).
- `secrets.env` holds the `NAME=VALUE` lines those variables need. `deploy.env`
  sources it, so every process `uqs` starts inherits them and nothing prints
  them. The deployment refuses a `secrets.env` that anyone but its owner may
  read.

With `--live`, `deploy.env` exports `UQS_REQUIRE_LIVE_SOURCES=1`. A source with
no credential is then refused, not read as its fixture. A bounded worker fails
at init, before taking its lock or reading a row, and a polling feed publishes
nothing. So a missing credential cannot publish fixture rows or record fixture
windows as covered. Verification checks that every pipeline process reports
`.qetl.source.live_required[]` as `1b`.

Without `--live`, a source with no credential runs on its fixture, as it does in
a demo stack. That is the explicit test mode, and it shares the deployment's
data directory, so keep it to a test destination of its own.

## What is not done

- Existing data is not migrated, and no external database is connected to at
  build or deploy time. Connections are made only when a job runs.
- Network access and driver checks against a live source are each job's own,
  when it connects. A deployment does not probe sources.
