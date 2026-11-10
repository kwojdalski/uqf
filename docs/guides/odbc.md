# ODBC sources on a server

A source read over ODBC needs three things on the server: - KX's q client for
ODBC (the native `odbc.so`; the q side, `src/etl/core/odbc.q`, is in this
repository and needs no `odbc.k`); - a driver manager (unixODBC); - the
database's own driver.

They live in one directory the deployment owns, the **ODBC home**. Nothing is
installed system-wide, nothing needs sudo, and the managed `QHOME` and `/etc`
are never written to. A process loads the setup by sourcing the home's
`current/env.sh`.

Fixture tests never need any of this. Only a live run and a live check do.

## The package

KX's client and most drivers may not be redistributed, so this repository ships
none of them. The operator assembles a package, a `.tar.gz`, from approved
binaries:

  | Path           | What it holds                                                                                                          |
  | ---            | ---                                                                                                                    |
  | `package.json` | `name`, `version`, `platform` (`linux-x86_64`), every file's sha256 under `files`, and `drivers`: name → library path  |
  | `q/`           | laid over the managed QHOME: `l64/odbc.so` for the platform                                                            |
  | `lib/`         | native libraries: unixODBC's `libodbc.so.2` and each driver                                                            |
  | `certs/`       | optional CA certificates a driver's TLS settings name                                                                  |

Packages are built for Linux. On a development machine, `./install.sh --odbc`
sets ODBC up as far as it can without root:

- **macOS** builds unixODBC, KX's client and the DuckDB driver under Rosetta,
  into `output/odbc-x86_64`, through `scripts/dev/odbc_rosetta.sh setup`. Run q
  with them through `scripts/dev/odbc_rosetta.sh q`. KX's only macOS client is
  x86_64, so an arm64 unixODBC from Homebrew is no use to q.
- **Linux** checks for unixODBC and prints the package command if it is missing,
  for example `sudo apt-get install unixodbc`. It never runs sudo. It also
  checks whether KX's client is in `QHOME`, which it never modifies.

unixODBC is a C library that q's client links, so it can't come from uv or any
Python package. A database's own driver is never installed by `install.sh`.

## Installing

List each package the deployment approves, by the archive's sha256, in
`<home>/approved_packages.csv`:

```csv
name,version,platform,sha256
singlestore,1.1.7,linux-x86_64,4f0c...e2
```

Then, as the service account:

```bash
uqs setup odbc install singlestore-1.1.7.tar.gz --odbc-home /srv/uqf/odbc --q-home /opt/kx
. /srv/uqf/odbc/current/env.sh
```

`install` refuses: - an archive whose sha256 isn't approved, before opening it; -
a manifest that disagrees with the approved row or this machine's platform; -
any file that's missing, unlisted or fails its checksum; - any member outside
the package.

`env.sh` sets the following, so every process sourcing it loads that package's
client, driver manager and drivers:

  | Variable                     | Set to                                                       |
  | ---                          | ---                                                          |
  | `QHOME`                      | an overlay of the managed one, with the package's client in  |
  | `ODBCSYSINI` / `ODBCINSTINI` | the version's own driver registry                            |
  | `LD_LIBRARY_PATH`            | the package's `lib/` first                                   |

A source's credential then names the driver as registered:
`DRIVER=SingleStore ODBC Driver;SERVER=...;PWD={secret}`.

## Verifying

```bash
uqs config sources check deals_db quotes_db --odbc-home /srv/uqf/odbc --timeout 120
```

Each named source is checked in its own q, under one overall timeout. The q
loads that source's declaration and what it uses, and no job, so a demo job
whose table this site lacks can't stop the check. The stages, in order:

1. **credential:** missing is a failure, never the fixture.
2. **tls:** a setting that turns certificate verification off is refused.
3. **connect:** the driver loads and the login is accepted.
4. **schema:** the physical tables and columns the adapter reads are present,
   with their types. That's the source's `raw` contract when it declares one,
   and its own `columns` and `types` otherwise.
5. **read:** the adapter's bounded read of the time range runs: the last hour
   when no range is given, or `--last`, `--date`, `--from`/`--to` - a past
   window, for a source with nothing in the last hour.
6. **output:** the rows it returned have the declared `columns` and `types`.

Declare `raw` when the adapter's output isn't its input. For example, it reads
`CREATED_AT` and a JSON payload and returns `source_time`, `sym` and typed
prices:

```q
raw:enlist[`EVENTS]!enlist ([] CREATED_AT:`timestamp$(); PAYLOAD:())
```

A general column (`()`) accepts any type. Without `raw`, the check would look
for `source_time` in the physical table and refuse a valid adapter at `schema`.

The connection is always closed. Nothing is published, no cursor moves and no
coverage is recorded. Each source reports its status (`ok`, `empty` for a valid
read of no rows, or `failed`), the stage it stopped at, the time taken and a
diagnostic with secrets masked. The command exits nonzero when any source fails.

`UQS_ODBC_HOME` can stand in for `--odbc-home`.

## Deploying with a check

```bash
uqs deploy push dist/uqf-<release>.tar.gz ... \
  --odbc-home /srv/uqf/odbc --live-check deals_db,quotes_db
```

`--odbc-home` makes the release's `deploy.env` source the setup, so every
process the release starts loads it. Preflight refuses a home with no current
version.

`--live-check` runs the check from the new release after every process has
answered and before `current` moves. A failing source fails the deployment, and
the usual rollback follows.

## Upgrading and rolling back

```bash
uqs setup odbc install singlestore-1.1.8.tar.gz --odbc-home /srv/uqf/odbc   # approved first
uqs setup odbc status --odbc-home /srv/uqf/odbc
uqs setup odbc rollback --odbc-home /srv/uqf/odbc
```

An upgrade installs the new version beside the old and moves `current`. The old
one becomes `previous`, and `rollback` swaps the two back. Nothing is deleted.
Processes load the setup when they start, so restart them after either.

## A live test run

```bash
UQF_LIVE_SOURCE_TEST=deals_db,quotes_db UQS_ODBC_HOME=/srv/uqf/odbc \
  uv run pytest python/uqs/tests/test_live_sources_integration.py
```

Skipped unless `UQF_LIVE_SOURCE_TEST` names sources, so ordinary runs and CI
never need a driver, a credential or the network.

## The q client: `odbc.q`, not `odbc.k`

KX's `odbc.k` is k source, which PeachQ cannot run. This repository ships its
own wrapper, `src/etl/core/odbc.q`, over the same native `odbc.so`, and
`.qetl.io.odbc.available[]` loads it. It provides `.odbc.open`, `.odbc.eval`,
`.odbc.close` and `.odbc.tables` and nothing else:

- `open` takes a connection string, a DSN symbol or a `(string;timeout)` pair,
  and registers the handle. A dotted DSN symbol (odbc.k's `.mdb`/`.dsn` file
  conveniences) is refused.
- `eval` takes a handle or `(handle;timeout)` and a SQL string, refuses an
  unknown handle with `invalid`, and returns whole-day datetime columns as
  dates.
- `close` refuses an unknown handle with `invalid` and unregisters it.

Nothing is bound at load time; `odbc.so` is found in `QHOME` on first use.

### What is verified

  | Where                                         | What                                                                                                                                                     |
  | ---                                           | ---                                                                                                                                                      |
  | every lane, with no ODBC                      | `tests/q/test_odbc.q`: the registry, the refusals, timeout routing and the whole-day conversion, against a double of `odbc.so`                           |
  | KDB-X, x86_64 under Rosetta, DuckDB driver    | `tests/qrun/test_odbc_parity.py`: `odbc.k` and `odbc.q` give identical output for the same queries, including the refusals and the whole-day case        |
  | PeachQ, with `odbc.so` doubled                | the same default-lane suite (it needs no `odbc.so`)                                                                                                      |
  | **not verified**                              | `odbc.so` returning tables to PeachQ: KX's macOS `odbc.so` is x86_64-only, so it needs Linux x86_64. Timeout *expiry*: DuckDB's driver never expires one |

The parity test needs `scripts/dev/odbc_rosetta.sh setup` and
`scripts/dev/odbc_rosetta.sh fetch-odbc-k` (KX's `odbc.k`, the reference; no
other part of the tree loads it) and skips, saying which is missing, without
them.
