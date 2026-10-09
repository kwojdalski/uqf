# Operating uqf - the uqs CLI, runtimes, profiles, deployment

Read this when starting, stopping, deploying or diagnosing a stack. The long
form is `docs/guides/uqs.md`. `uqs <command> --help` is authoritative for flags.

## Setup

```bash
./install.sh           # checks prerequisites, puts uqs on PATH; never runs sudo
./install.sh --dev     # also the Python workspace and the pre-commit hooks
./install.sh --odbc    # ODBC for q: built in user space on macOS, checked on Linux
./install.sh --check   # check only
```

Prerequisites: - KDB-X (q 5.0 or later; `QCMD`/`QHOME` when q isn't on PATH); -
`uv`, `rlwrap` and `envsubst`; - `qlinter`
(`cargo install --git https://github.com/kwojdalski/q-lint`, at the tag pinned
in `.github/workflows/ci.yml`).

## Command map

  | Need                                                   | Command                                                                        |
  | ---                                                    | ---                                                                            |
  | start a set of processes, with what they depend on     | `uqs start --profile NAME [extra procs]`                                       |
  | start one process, or several                          | `uqs start NAME...`                                                            |
  | start and follow its log in the foreground             | `uqs up NAME` / `uqs up --profile NAME`                                        |
  | stop                                                   | `uqs stop [NAME...]` (`--force` for a process that won't stop)                 |
  | status, wiring, heartbeats, batch health               | `uqs summary`                                                                  |
  | who feeds whom                                         | `uqs graph`                                                                    |
  | a q expression on a process                            | `uqs query "expr" --port P` / `uqs query --proc NAME` (interactive)            |
  | a table's columns in a running process                 | `uqs schema`                                                                   |
  | logs                                                   | `uqs logs NAME --level ERROR`, `-f`, `--multitail`                             |
  | list processes, profiles, runtimes, overrides, env     | `uqs list KIND` (no argument lists the kinds)                                  |
  | a bounded worker over a range                          | `uqs backfill WORKER --from T --to T --version V [--wait] [--debug|--trace]`   |
  | runs: unfinished, one run, one window                  | `uqs run status`, `uqs run show RUN_ID`, `uqs run list`                        |
  | holes in a streaming job's output, and refill commands | `uqs gaps JOB --from T --to T`                                                 |
  | scaffold, remove or install a job                      | `uqs job new`, `uqs job remove`, `uqs job install`                             |
  | a source checked live                                  | `uqs config sources check SOURCE... [--odbc-home DIR]`                         |
  | a source's `sources.csv` row                           | `uqs config sources`, `uqs config sources stub SOURCE`                         |
  | the HDB's shape against the schema                     | `uqs data hdb-check [--fix]`                                                   |
  | replay a tickerplant log into the HDB                  | `uqs data replay`                                                              |
  | compare runtimes; install a runtime's bundles          | `uqs runtime diff A B`, `uqs runtime prepare`                                  |
  | external publishers                                    | `uqs feed start|stop|status databento|kafka|crypto|crypto-fills`               |
  | deploy                                                 | `uqs deploy build`, `uqs deploy push`, `uqs deploy verify`, rollback and prune |

Anything else is passed through to TorQ's launcher with
`uqs raw -- <torq.sh verb>`.

## Runtimes and profiles

A **runtime** is which stack exists: its processes, tables, layers and bundles.
A **profile** is which part of it to start. Choose a runtime with
`uqs --runtime NAME` or `UQS_RUNTIME`, and a profile with
`uqs start --profile NAME`. Naming a leaf in a profile pulls in everything it
reads.

  | Runtime         | Base port | What exists                                                             |
  | ---             | ---       | ---                                                                     |
  | `uqf` (default) | 6050      | the starter pack plus all of uqf                                        |
  | `torq`          | 6150      | the starter pack exactly as shipped, to tell TorQ's problems from uqf's |
  | `crypto`        | 6250      | uqf's layers, crypto pipelines only, with its own HDB                   |
  | `fx`            | 6350      | uqf's layers, FX pipelines only, with its own HDB                       |
  | `peachq`        | 6450      | the stack's q on PeachQ                                                 |
  | `peachq-etl`    | 6550      | pipelines on PeachQ, from a flattened tree (experimental)               |

Each runtime has its own data directory and ports, so two can run at once. A
process's port is base + its offset in `scripts/processes/process_ports.csv`;
for example rdb1 is base + 2, so 6052. `uqs list runtimes` is authoritative.

**Connection budget:** the licence allows 16 concurrent connections, and 2 are
held back, so 14 slots. Each started process uses one. `uqs list profiles` shows
each profile's slots and whether it fits; `all` doesn't fit.

## Data and state

  | What                                           | Where                                                                     |
  | ---                                            | ---                                                                       |
  | a runtime's data (HDB, logs, generated config) | `output/uqs/<runtime>/` (`uqs list env`)                                  |
  | run ledger, coverage, status files             | `$UQF_STATUS_DIR`                                                         |
  | logs                                           | `<data>/logs/out_<proc>.log`, `err_<proc>.log`; read them with `uqs logs` |
  | a flattened tree for a `peachq-etl` runtime    | `<data>/qtree`, rebuilt only when its source changes                      |

`uqs remove output` deletes runtime state. `uqs remove checkpoint WORKER`
restarts a worker's range from its beginning on purpose; you rarely need it,
because re-running a backfill resumes it.

## Recovery, in brief

`docs/guides/when-it-breaks.md` has the full runbook.

- **A backfill failed:**
  - find it with `uqs run status` and `uqs run show RUN_ID`;
  - fix the cause;
  - re-run the same command, which resumes, because covered windows are skipped.
- **A streaming job died:**
  - find it with `uqs summary` and `uqs logs NAME --level ERROR`;
  - start it again with `uqs start NAME`;
  - then `uqs gaps NAME --from --to`. Rows it missed while down are not
    republished; a job with a backfill twin can refill them.
- **A job is `up` but its table stays empty:** its producer isn't running. Start
  it through a profile.

## Deployment

Deployment targets Linux. Push needs TorQ and q already on the server
(`docs/guides/deploy.md`).

```bash
uqs deploy build [--q-target 4.0] [--bundle DIR]   # an artifact, from a commit
uqs deploy push dist/uqf-<release>.tar.gz --host H --dest /opt/uqf \
    --torq-home ... --torq-app-home ... --qcmd ... --qhome ... --profile essential
```

Push runs: 1. preflight; 2. transfer; 3. prepare (an offline Python environment
from the artifact's wheels); 4. the HDB check; 5. the smoke test on the server's
q; 6. stop the previous release; 7. port check; 8. start; 9. verify, then an
optional live source check and soak; 10. activate `current`.

Any failure rolls back, and a report is written into the release.

**kdb+ 4.0 servers:** build with `--q-target 4.0`. The release's q is flattened,
and the manifest records what was converted. `uqs start` recognises a converted
release from that manifest, and checks each q file's sha256. For a server q
older than the target, use `--accept-converted-release "REASON"`. It works only
after the smoke test passes, applies only to that deployment, and is recorded in
the report.

## ODBC sources

A source read over ODBC needs KX's q client, a driver manager (unixODBC) and the
database's driver (`docs/guides/odbc.md`). - **On a server:** `uqs odbc install`
installs an approved package. - **On a Mac:** run `./install.sh --odbc` (or
`scripts/dev/odbc_rosetta.sh setup`). Run q with it through
`scripts/dev/odbc_rosetta.sh q`: KX's macOS client is x86_64 only.

Check a source with `uqs config sources check SOURCE`.

## Two interpreters

KDB-X is what everything is verified against. PeachQ, an open q implementation
used in CI, can't load nested `\d` contexts. CI runs the q suite on a flattened
copy (`scripts/portable/full_suite.py --bundles`), against
`tests/q/peachq_known_gaps.txt`. Python tests that need a q run on PeachQ,
against `python/peachq_known_gaps.txt`.
