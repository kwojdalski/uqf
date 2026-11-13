# Installed bundles as an overlay

**Status: a design note, not built (#2733).** Today installing a sidecar bundle
writes into tracked source. This page is the design for installing it into a
directory outside the tracked tree instead, and loading it from there when a
process starts. It was written before the work, as
[`zero-downtime.md`](zero-downtime.md) was. Where it and the code disagree once
the work lands, the code is right and this page is corrected.

The repository keeps no backward compatibility. The plan cuts over: no reader
consults both the old layout and the new one, and no shim translates one to the
other.

## The problem

`uqs job install <bundle>` (`cli/install_bundle.py:86`) and
`uqs runtime prepare` (`stack/runtime_bundles.py:138`) both end in
`bundles.install` (`stack/bundles.py:340`). It writes:

  | What                                                                       | Where it writes                                             | Code                                                                     |
  | ---                                                                        | ---                                                         | ---                                                                      |
  | job files, placed by kind                                                  | `src/etl/{sources,workers,streaming,reactions}/`            | `bundles.py:352`, `stack/install.py:60`                                  |
  | files a previous version owned and this one does not, deleted              | the same directories                                        | `bundles.py:354`                                                         |
  | the table and catalog additions, one `/ BEGIN bundle <name>` block in each | `src/etl/plant_tables.q`, `scripts/processes/uqs_catalog.q` | `bundles.py:356`, `stack/bundle_blocks.py:56`                            |
  | the ledger                                                                 | `src/etl/installed_bundles.json`                            | `bundles.py:377`, `paths.py:54`                                          |
  | process.csv rows for the bundle's own processes                            | `python/uqs/bundle_overrides.csv`                           | `bundles.py:351`, `stack/overrides.py:72`                                |
  | connectors (`*_feed.py`)                                                   | `src/etl/connectors/<bundle>/`                              | `bundles.py:370`, `stack/bundle_connectors.py:70`                        |
  | the evaluated plant schema                                                 | `src/etl/generated/plant_schema.json`                       | `bundles.py:378`, `stack/bundle_shadowing.py:36`                         |

The CLI then runs `_DERIVED` (`cli/regenerate.py:39`) over the checkout
(`install_bundle.py:95`, `runtime_prepare.py:93`). `operational-docs` rewrites
`docs/reference/processes.md`, `src/etl/generated/pipeline_dag.q`,
`src/etl/generated/load_plan.q` and `scripts/processes/process_ports.csv`. So
one install modifies six tracked files and adds untracked ones beside tracked
ones. Only the blocks carry a marker.

Every guard in this area exists because of that:

- the `bundle-blocks` pre-commit hook (#881, #926,
  `scripts/gates/policy/bundle_blocks.py`) refuses staged blocks, the ledger and
  the files it lists;
- `deploy/foreign_blocks.py` (called at `deploy/build.py:319`) refuses a release
  built from a tree carrying another runtime's blocks;
- `q_api_docs.snapshot_files` (`scripts/generate/q_api_docs.py:115`) refuses to
  publish installed files as the tree's API;
- the getdata and stack-table tests exempt ledger tables, so that the committed
  `tableproperties.csv` and the tables list describe the base tree
  (`tests/q/test_catalog.q:123`, `tests/q/test_stack_tables.q:41`);
- shadowing refusal (#1545, `stack/bundle_shadowing.py:48`) reads the load plan
  with the bundle's files substituted for what is on disk, because they would
  land on disk in the same directories;
- the bundle half of #1540 (`.gitignore:116`, `overrides.py:13`) keeps bundle
  rows out of a file in the package directory.

## Every reader of the installed state

What would have to change, found by what reads the files an install writes.

**q, at process start**

- `src/etl/init.q:78` and `:80` load the generated DAG and load plan, and `:157`
  loads `plant_tables.q`, blocks included. `:200` loads the declarations with
  `.qetl.load.declarations`. For a full load it globs each directory with
  `.qetl.load.dir_files` (`src/etl/core/declaration_load.q:90`). For a selective
  one, in a process that names what it runs, it filters `.qetl.load.order` from
  `load_plan.q`. Installed jobs are found only because they sit in those
  directories.
- gateway1 loads `uqs_catalog.q` because `stack/procs.py:103`
  (`VENDORED_LOAD_OVERLAY`) appends it to the vendored row's `load` column. The
  bundle's catalog entries arrive inside that file.
- `scripts/generate/export_plant_schema.q:21` evaluates `plant_tables.q`. A
  bundle's tables are evaluated only once spliced in (its header, `:14`).
- stp1's schema is already composed at start: `stack/runtime.py:74` writes
  `<data dir>/database.q` from `declared_schema` (`stack/hdb_types.py:28`).
- In tests, `test_stack_tables.q:45` and `test_catalog.q:131` parse the ledger
  by its tracked path, and `test_every_worker_runs.q:40` and `:312` enumerate
  `src/etl/workers` and siblings.

**Python**

- The registry. `model/declarations.py:332` globs `src/etl/streaming` and
  `workers`. `model/registry.py:164` adds port offsets from the committed lock
  (`:63`). New processes are allocated past it (`:80`) and written back into the
  lock by `operational-docs`. `PIPELINES` is built once, at import, from
  `repo_root()` (`registry.py:178`).
- The load plan: `model/load_plan.py:121` globs the five declaration
  directories.
- The plant: `model/schemas.py:36` reads `plant_tables.q` as text.
  `model/plant_metadata.py:65` reads `plant_schema.json`, keyed on the sha of
  `plant_tables.q` (`:204`).
- Runtime membership (#852): `model/runtime_members.py:25` reads the ledger.
  `:36` splits bundles into in and out of a runtime by the entry's `runtimes`,
  with an entry naming none in every all-pipelines runtime (`:48`).
  `stack/runtime_bundles.py:140` rewrites `runtimes` on every prepare.
- Overrides: `stack/overrides.py:26` layers `bundle_overrides.csv` under the
  host file, and `stack/procs.py` composes process.csv from that.
- Connectors: `model/connectors.py:26` and `external/feeds.py:305` read the
  ledger's `connectors` and import from `src/etl/connectors/`.
- The installer itself: `bundle_shadowing.known_tables` (`:17`) and
  `bundles.plan`'s catalog scan (`bundles.py:316`) read the tree to tell the
  tree's own from this bundle's block.
- The gateway's table list already copes: `stack/gateway_access.py:150` derives
  a row at bootstrap for every held plant table that `tableproperties.csv`
  omits.
- PeachQ's flattened tree: `stack/qtree.py:163` copies `src/` and `scripts/`
  into `<data dir>/qtree` and converts them. Installed jobs are converted only
  because they are under `src/` (its header, `:7`).

## The overlay

### Where it lives

One directory per runtime, named by `UQS_BUNDLE_HOME`, by default
`<data dir>/bundles`. The data directory is already per runtime and already
outside the tracked tree: `UQS_DATA_ROOT`, or the ignored `output/`
(`paths.py:320`). A new `UqsPaths.bundle_home` is the one place it is spelled.
`stack/env.py` exports it to every process, as it does `UQF_ROOT`. The variable
joins `docs/reference/environment.md`, which the env-reference gate requires.

Per runtime, because a runtime's data directory already is, and because it
replaces membership bookkeeping with presence. A bundle is in a runtime when it
is in that runtime's overlay. The ledger's `runtimes` field, the "named no
runtimes" rule (`runtime_members.py:48`, `bundles.py:374`) and the rewrite in
`runtime_bundles.py:140` all go. `_bundle_split` reduces to "everything in the
overlay", plus the deployment-set filter it already takes.

### Layout

```text
<bundle home>/
  installed.json          the ledger: version, revision, file hashes, jobs, tables, connectors
  overrides.csv           the bundles' process.csv rows (was python/uqs/bundle_overrides.csv)
  ports.csv               offsets of the bundles' processes, append-only, this runtime only
  <bundle>/
    sources/ workers/ streaming/ reactions/    copies, placed by kind as today
    tables.q              the bundle's own, qualified as table_lines writes it
    catalog.q             the bundle's own
    connectors/*.py
  generated/
    load_plan.q  pipeline_dag.q  plant_schema.json    over the tree AND the overlay
```

Nothing is spliced, so there are no blocks. Idempotence comes from rebuilding.
`install` and `runtime prepare` write the new overlay beside the old one and
swap it in by rename, as `qtree.prepare` already publishes its tree. A failed
install leaves the previous overlay exactly as it was. An uninstall is the same
rebuild with one bundle fewer.

### How q picks it up

With `UQS_BUNDLE_HOME` unset, a process loads the base tree, exactly as tests
and the docs tooling do today. With it set:

- `init.q` loads `<home>/generated/load_plan.q` in place of the tree's. It is
  the complete plan over the tree and the overlay, from the same generator, so a
  process reads one plan and never merges two.
- After `plant_tables.q`, it loads each bundle's `tables.q`, in the order the
  generated plan lists the bundles.
- The full load passes `.qetl.load.declarations` each kind's directories, the
  tree's first and then every bundle's, so the directory-major order init.q
  documents still holds. No two files in one directory may depend on each other
  at load time today, so concatenating a kind's directories is safe.
- gateway1's `load` column gains each bundle's `catalog.q` after
  `uqs_catalog.q`. This is composed in `stack/procs.py` exactly as
  `VENDORED_LOAD_OVERLAY` extends the vendored rows, which this tree may never
  edit.
- `export_plant_schema.q` takes the bundles' `tables.q` paths as arguments and
  loads them after `plant_tables.q`.

### How Python picks it up

One seam: a tree view, `(repo_root, bundle_home | None)`, that answers which
declaration files exist, which q files define plant tables and catalog entries,
the ledger, and the overrides file. Every reader above takes it instead of
globbing `repo_root` itself. `read_declarations`, `declaration_files`,
`schemas`, `plant_metadata`, `runtime_members`, `connectors`,
`overrides.effective`, `known_tables` and the catalog scan become calls on the
view. `PIPELINES` and `schemas.TABLES_Q` stay module-level, built from the view
of the runtime that `UQS_RUNTIME` names at import (see the risks).

### Derived files

The tracked derived files describe the base tree and only the base tree, so
their `--check` gates are unchanged and an install can never make them stale.
The overlay's `generated/` holds the same generators' output over the view.
`derive.py plant-schema` and `operational-docs` gain `--bundle-home DIR`, which
writes there and touches nothing tracked. `processes.md` is not produced for the
overlay: `uqs status` and `uqs profile list` already read the live registry.

Ports are the one fact with memory. A bundle process's offset goes in
`<home>/ports.csv`, allocated downward from the top of the runtime's span of 100
ports (`runtimes.py:47`). The tree's lock currently ends at 74. The registry
refuses a start when the two ranges meet, rather than renumbering anything.

## What changes elsewhere

**`uqs deploy build --bundle`.** `stage_bundles` (`deploy/build.py:135`) stops
copying the tree, installing into it and regenerating there. It installs into a
staged `bundles/` overlay, packaged beside the tree. `bundle_build needs` runs
with `UQS_BUNDLE_HOME` pointing at that overlay instead of with the staged tree
on `PYTHONPATH` (`tree_python`, `:109`). `STAGED_EXTRA` (`:90`), which copies
`docs/` only so the generator has something to rewrite, goes. The release's tree
is then exactly its tracked files, and `foreign_blocks.refuse` has nothing to
find. On the server, `UQS_BUNDLE_HOME` names the release's own `bundles/`, so
rolling back the code rolls back its bundles. The manifest's `bundles` record
keeps its shape, with paths relative to the overlay.

**CI's PeachQ lane.** `scripts/peachq/full_suite.py --bundles` (`:149`) still
copies the tree, because it flattens it. It installs every bundle with
`bundle_suite.py --install-into <work>/bundles`, not into `<work>/tree`, and
runs q with `UQS_BUNDLE_HOME` set. `qtree.sources` (`:163`) copies the overlay
into the flattened tree as `bundles/`, converts it with the rest, and folds it
into `FINGERPRINT`. A local PeachQ runtime gets its bundles converted the same
way.

**`scripts/dev/bundle_suite.py`.** `install` (`:92`) installs into a temporary
overlay. A throwaway copy of the checkout remains for one reason only: the
bundle's `test_*.q` must sit in `tests/q/` for the runner to find them, and its
`test_*.py` beside uqs's tests for the fixtures. Letting `tests/run_tests.q` and
pytest take an extra directory would remove the copy, but it is not needed for
the cut-over.

**`scaffold/bundle.py`.** `tree_with` (`:69`) copies `src/etl`, the catalog and
both override files into a scratch root and installs the bundle there, so that
the planners see it. It becomes a scratch overlay over the real checkout, and
the planners read through the tree view.

**Tests.** `test_stack_tables.q:45` and `test_catalog.q:131` read the overlay's
ledger through `getenv`, not a tracked path. They are still needed: the bundle
lane loads bundle tables into the same process as the tree's tests.
`test_every_worker_runs.q:40` and `:312` enumerate declaration directories
through one `tests/lib/support.q` helper that includes the overlay's. Without
it, the bundle lane would stop exercising bundle workers, which today it does
only by accident of location.

**Deletable once the cut-over lands**

  | Guard                                                                                                       | Why it can go                                                                                                     |
  | ---                                                                                                         | ---                                                                                                               |
  | `bundle-blocks` hook (`.pre-commit-config.yaml:197`), `scripts/gates/policy/bundle_blocks.py` and its tests | nothing an install writes is in the tracked tree                                                                  |
  | `stack/bundle_blocks.py` (`with_block`, `without_block`, `block_names`)                                     | no splicing; `BundleError` moves to `bundles.py`                                                                  |
  | `deploy/foreign_blocks.py` and `build.py:319`                                                               | a checkout cannot carry an installed bundle                                                                       |
  | `q_api_docs.py:115` ledger check, and its `LEDGER` (`:60`)                                                  | installed q is never under `src/`                                                                                 |
  | `bundles.plan`'s file-ownership checks (`bundles.py:303`) and `Plan.stale`                                  | each bundle owns its own directory, and a rebuild has no stale files                                              |
  | `runtimes` in the ledger, `runtime_bundles.prepare`'s rewrite                                               | membership is presence                                                                                            |
  | `.gitignore:116`, `overrides.BUNDLE_FILE`, `paths.INSTALLED_CONNECTORS`, both `BUNDLE_LEDGER`s              | the files move into the overlay                                                                                   |
  | the plain-sidecar copy into `src/etl` (`cli/install.py:359`)                                                | folded in: a folder without `bundle.json` is refused, or installs as a bundle without additions (open question 5) |

They stay: shadowing refusal (#1545), because q still upserts on a second define
whichever directory it came from, though it no longer needs files substituted;
the bundle's text validation (`table_lines`, `catalog_lines`,
`_refuse_block_comment`), because a bundle file is still never run before it is
installed; and the two test exemptions above, rewritten.

## Migration, in order

Each step is one PR that ends with the hooks and the targeted tests green.

1. **The tree view, empty.** Add `UqsPaths.bundle_home`, `UQS_BUNDLE_HOME` (with
   its environment.md row) and the view. Route every Python reader listed above
   through it. With no overlay present the behaviour is identical: a pure
   refactor, proven by the existing tests.
2. **Overlay ports.** The registry reads `<home>/ports.csv`, allocates downward
   and refuses a meeting. Unit-tested on a fixture overlay.
3. **q loads an overlay.** The init.q, `declaration_load.q`, export and gateway
   `load`-column changes, and `--bundle-home` on the two generators. Proven
   under KDB-X with `sidecars/mockups` laid out by hand in a temporary overlay.
   The installer still writes the tree, and its files look like the tree's own
   to every reader, so nothing reads two layouts.
4. **Cut over the installer and every caller.** `bundles.install` writes the
   overlay. The CLI, `runtime prepare`, `bundle_build`, `deploy/build.py`,
   `bundle_suite.py`, `full_suite.py`, `qtree.py` and `scaffold/bundle.py` move
   in the same PR, because each calls the installer against a root and none can
   stay green across a split. The tests' exemptions move to the overlay. A tree
   still holding `src/etl/installed_bundles.json` is refused with the command
   that clears it. That is a one-way migration aid, not a reader of the old
   layout. This is the large PR; steps 1 to 3 exist to keep it mechanical.
5. **Delete the guards** in the table above, and their tests.
6. **Docs.** `docs/guides/sidecar-bundles.md`, `docs/guides/deploy.md` where it
   describes `--bundle`, and this page's status.

Rollback is per PR until step 4. After it, reverting step 4 and reinstalling
restores the old behaviour, because the overlay is never committed.

## Risks and open questions

1. **`PIPELINES` is built at import** (`registry.py:178`), and `--runtime` sets
   `UQS_RUNTIME` in a CLI callback (`cli/shared.py:164`). A per-runtime overlay
   is only right if no command imports the registry before that callback runs.
   If one does, `PIPELINES` must become lazy. The fallback is one overlay per
   data root (`$UQS_DATA_ROOT/bundles`), which brings `runtimes` membership
   back.
2. **Port room.** Spans are 100 wide and the tree's lock reaches 74, leaving 25
   offsets for every bundle in a runtime. Widening spans moves every base port.
3. **q facts.** `uqs.generated.q_facts` records sources' transports and workers'
   datasets for readers without q (`gateway_access.py:39`, the Airflow
   provider). It is tracked and base-tree only, which `runtime prepare` never
   regenerated anyway. Open: whether the overlay needs its own facts file or the
   ledger's `jobs` and `tables` suffice.
4. **Processes started outside uqs** see no overlay unless the variable is set.
   That is intended, because it is how tests stay on the base tree, but a
   hand-run q against a prepared runtime will look as if its bundles were
   missing.
5. **Plain sidecars** (`stack/install.py`, folders without `bundle.json`) write
   into `src/etl` too. Either they install as additions-free bundles in the
   overlay, or the mode is deleted. Deleting is simpler and matches "a bundle is
   what a release carries".
6. **Considered and rejected: a composed tree.** Copy the checkout plus the
   bundles into `<data dir>/tree` and point `UQF_ROOT` there, as `qtree.py` does
   for PeachQ and `stage_bundles` does for a release. q would need no change.
   But every Python reader would still have to be told which root, blocks would
   still be spliced (into copies), every edit to the checkout would need
   re-composing, and a PeachQ runtime would stack two compositions. The overlay
   changes more code once in exchange for those costs never arising.
