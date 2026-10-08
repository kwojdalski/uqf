# Deploying to a server

Deploying is two commands, run separately (#773, #778):

- `uqs deploy build` builds a versioned **release artifact** once, with no SSH
  access or credentials, so CI can build it.
- `uqs deploy push` puts that artifact on a Linux server that already has TorQ
  and the finance starter pack installed. It starts a `uqs` profile there and
  checks that every process answers before it calls the deployment done. The
  check is `uqs deploy verify`, which `push` runs on the server from the release
  itself.

One artifact can go to any number of servers. The server installs it without a
network.

> **Not yet run end to end.** The tools are unit tested with ssh, uv and pip
> replaced (`python/uqs/tests/test_deploy.py`, `test_build_release.py`). The run
> against a real server is `python/uqs/tests/test_deploy_integration.py`, which
> needs a disposable host and is skipped without one. Until it has passed
> somewhere, treat the commands below as the design rather than a recording.

## What the server needs

`push` installs none of these, and checks every one before it changes anything:

- ssh access from where you run `push`, already working with your own
  `~/.ssh/config` and known hosts. It runs `ssh` and `scp` in batch mode, so a
  missing key or an unknown host key fails instead of prompting. It never turns
  host-key checking off.

- `uv`, with the Python the artifact was built for (3.14 by default) where
  `uv python find` sees it, and `python3`, `bash`, `tar` and `timeout`. The
  server's OS, architecture and Python must match the artifact's manifest.

- A licensed q. `--qhome` and `--qcmd` are optional. Without them, the values
  are resolved on the server, in the environment of the account that deploys:
  the `--remote-user`'s login environment, or else the ssh login's. Your own
  machine's values are never used (#782):

  | Setting | First                 | Otherwise                                               |
  | ---     | ---                   | ---                                                     |
  | QHOME   | `--qhome`             | that account's `$QHOME`; refused if unset               |
  | QCMD    | `--qcmd`              | that account's `$QCMD`, else `q` on that account's PATH |

  QCMD must be an executable name or an absolute path, not a command with
  arguments, so `rlwrap q` is refused. Preflight runs a script with the resolved
  pair. It then writes the values into the release's `deploy.env`, so the smoke
  test, start, verification and rollback all use what was checked. The dry run
  shows each value and where it came from.

- An existing TorQ (`--torq-home`, holding `torq.q` and `torq.sh`, or only
  `torq.q` with `--torq-launcher`, below) and starter pack (`--torq-app-home`,
  holding `database.q` and `appconfig/process.csv`). Without these flags the
  release would need its own `lib/`, which a release does not ship.

- `envsubst` and `rlwrap`, which `torq.sh` needs.

- For live sources, their credentials and, for ODBC, a driver: see [credentials
  and ODBC](sidecar-bundles.md#credentials-and-odbc).

The server needs no access to PyPI. Every Python dependency arrives as a wheel
inside the artifact.

## Building a release

```bash
uqs deploy build
```

This writes three files into `dist/` at the repository root (`--output DIR` puts
them elsewhere), named after the release, which is the UTC build time and the
first 12 characters of the revision:

  | File                               | What it is                                                                       |
  | ---                                | ---                                                                              |
  | `dist/uqf-<release>.tar.gz`        | the artifact                                                                     |
  | `dist/uqf-<release>.tar.gz.sha256` | its checksum, in `sha256sum -c` format                                           |
  | `dist/uqf-<release>.manifest.json` | the manifest, readable without opening the archive                               |

Inside the artifact:

- **The tree.** The tracked files under an allowlist (`src`, `scripts`, the
  three Python packages, `pyproject.toml`, `uv.lock`), at their repository
  paths, because the q loaders and `uqs` find each other by layout. Never
  shipped: TorQ (`lib/`), `.env`, `.envrc`, keys and licences, virtual
  environments, runtime data, logs, tests, build output, and
  `scripts/torqconfig/permissions/gateway_users.csv`, which holds the demo
  gateway passwords.
- **`.release/`.** `requirements.txt`, the dependencies of `uqs` pinned with
  hashes from the committed `uv.lock`. `wheels/` holds the `uqs` wheel and a
  binary wheel of every pinned dependency for the target.
- **`RELEASE_MANIFEST.json`.** The revision, whether the tree had uncommitted
  changes, the target OS, architecture and Python, and every member's sha256.

`--bundle <folder>`, repeatable, adds a [sidecar bundle](sidecar-bundles.md).

The target defaults to Linux on x86_64 with the Python `requires-python` names.
Pass `--arch aarch64` or `--python 3.14` for others. Uncommitted changes are
refused unless you pass `--allow-dirty`, which the manifest records. Building
needs `uv`, and network access to fetch the wheels.

## A first deployment

Show the plan first. `--dry-run` checks the artifact and runs only the read-only
preflight on the server. It then prints the artifact, its target beside what the
server reports, every step, and any restart it would make. It changes nothing:

```bash
uqs deploy push dist/uqf-<release>.tar.gz \
  --host uqf-server --dest /opt/uqf \
  --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \
  --qcmd /opt/kx/bin/q --qhome /opt/kx \
  --profile essential --init-data --dry-run
```

Then the same command without `--dry-run`. `--init-data` lets `push` create the
runtime data directory the first time. A destination takes each release once: to
redeploy, build a new artifact. Without it, a missing data directory is refused,
so a mistyped `--dest` cannot quietly start an empty HDB.

## What it does, in order

Each stage stops the deployment if it fails:

  | Stage     | What happens                                                                                                                                                                                                                                                                                                                                                                                   |
  | ---       | ---                                                                                                                                                                                                                                                                                                                                                                                            |
  | artifact  | the archive matches its `.sha256`, and every member matches the checksum its manifest lists, with nothing missing or extra. This runs before anything reaches the server                                                                                                                                                                                                                       |
  | preflight | ssh works and the destination is writable; uv, the release's Python, q (it must run a script), the TorQ trees and the launcher's tools are there; the server's OS, architecture and Python match the artifact's target; the release is not already there; the data directory exists or `--init-data` was given; another deployment there needs `--restart`; no other deployment holds the lock |
  | transfer  | `scp` into `staging/<release>/`; the archive's sha256 is checked **on the server** before it is extracted into `releases/<release>/`                                                                                                                                                                                                                                                           |
  | prepare   | `releases/<release>/deploy.env` (below), and a release-local `.venv` installed **offline** from the artifact's wheels: the dependencies with their locked hashes required, then `uqs` itself. `UV_OFFLINE=1` stays set, so nothing later fetches either                                                                                                                                        |
  | smoke     | `scripts/deploy_smoke.q` loads the quant library and checks known numbers. It must print `DEPLOY_SMOKE_OK` and exit 0 within `--smoke-timeout`                                                                                                                                                                                                                                                 |
  | restart   | with `--restart`, the previous release's processes stop. Their replacements take the same ports, so an upgrade has downtime                                                                                                                                                                                                                                                                    |
  | ports     | nothing else listens on the profile's ports                                                                                                                                                                                                                                                                                                                                                    |
  | start     | `uqs start --profile <profile>` from the new release                                                                                                                                                                                                                                                                                                                                           |
  | verify    | `uqs deploy verify`: within `--verify-timeout`, every process the profile resolves to answers `.proc.procname` with its own name over q IPC. **Every pipeline process** must pass both the library check (a forward prices correctly) and the ETL check (every transform's examples pass); other processes are checked for whichever they load                                                 |
  | report    | `deploy-report.json` is written into the release **before** activation, and a failed write fails the deployment: the next upgrade reads it to know which processes to stop                                                                                                                                                                                                                     |
  | activate  | `current` moves to the new release in a single rename. The previous release stays where it was                                                                                                                                                                                                                                                                                                 |

The tool prints a report as JSON when it finishes, and leaves it in the release
as `deploy-report.json`. The report names the release, the source revision, each
process and what it answered, the stage that failed if one did, and, separately,
how the rollback went. Any unsuccessful deployment exits 1.

## When something fails

Before `start`, nothing on the server has changed apart from a new directory
under `releases/`, which is kept for inspection.

From `start` on, the new release's processes are stopped. If `--restart` had
stopped the previous ones, they are started again from the previous release, and
that release's own verifier is then run with its own `deploy.env` and profile. A
start command that returned is not taken as a recovery. `current` only moves in
the activate stage, so it still names the previous release. The report's
`rollback` field says what was done, and whether the restored processes
verified.

What `current` names, and so which release `--restart` stops, is read again once
the lock is held. A deployment that activated between this run's preflight and
its lock is seen, and is refused without `--restart`.

## Checking a server

What a server runs, without ssh-ing in:

```bash
uqs deploy status --host uqf-server --dest /opt/uqf
uqs deploy status --host uqf-server --dest /opt/uqf --json   # to script against
```

It reports `current` and the release it replaced (revision, build time, dirty or
not, runtime, kdb+ target, bundles) and how many other releases are kept; the
last deployment, which is the newest release whether or not it activated - so a
failed push shows here with its stage, error and rollback outcome; whether
`current`'s processes answer its own verifier within `--health-timeout` (default
10 seconds; `--no-health` skips it); the streaming jobs whose batches are
failing; and whether the deploy lock is held, and by whom.

It is read-only and never takes the lock, so it is safe during a push. It exits
1 when the health check fails, 0 otherwise. The `--json` keys are stable: its
`format` changes when one is renamed or removed, not when one is added.

## Rolling back

To put a server back on an earlier release after it has activated:

```bash
uqs deploy rollback --host uqf-server --dest /opt/uqf --dry-run
uqs deploy rollback --host uqf-server --dest /opt/uqf
```

It returns to the release the current one replaced, as the current release's
report records it, or to `--to RELEASE`. Under the deploy lock it stops the
current release's processes, starts the target's from the target's own release,
and moves `current` only once the target's own verifier passes. A target that
does not verify is stopped, and the current release's processes are started and
verified again, so the server ends where it began. Nothing is deleted.

## Removing old releases

Every push leaves its release, with its own offline `.venv`, in `releases/`.
`uqs deploy prune` removes all but the newest `--keep N`, under the deploy lock:

```bash
uqs deploy prune --host uqf-server --dest /opt/uqf --keep 3 --dry-run
uqs deploy prune --host uqf-server --dest /opt/uqf --keep 3
```

Three releases are never removed, whatever `--keep` is, even 0:

- the one `current` names;
- the one a rollback would return to: the release `current`'s report says it
  replaced;
- any whose report still says `running`, from a push that died part-way.

It prints what stayed and why, what went, and the bytes freed. Only a directory
named like a release id is ever removed. `--dry-run` lists the removals and
removes nothing. `uqs deploy push --keep N` prunes the same way once the new
release is current, while it still holds the lock. If that prune fails, the
deployment still counts as a success, and its report records the failure.

## Deploying as a service user

On many servers the account you log in as is not the one that owns and runs uqf;
an operator would `sudo su - svc` by hand. `--remote-user` does that step
non-interactively (#780):

```bash
uqs deploy push dist/uqf-<release>.tar.gz \
  --host deploy@uqf-server --remote-user svc \
  --dest /srv/uqf --torq-home /opt/torq ... --profile essential --dry-run
```

- **ssh and scp** still log in as the `--host` user.

- **Every deployment step** runs as `svc` through `sudo -n -iu svc bash -s`.
  That covers preflight, prepare, smoke, start, verify, activate and rollback.
  - `-n` means a rule that would ask for a password fails at once, instead of
    hanging.
  - `-i` is a login shell, so q, uv, Python, TorQ, permissions and ports are
    checked in `svc`'s own environment.
  - Settings cross sudo inside the script, never through the login user's
    environment.

- **Before anything changes**, preflight runs `sudo -n -iu svc id -un` and
  refuses unless it answers `svc`. The preflight script also checks it is
  running as `svc`.

- **The archive** cannot be written by scp as `svc`, so:
  1. scp uploads it as the login user into a private `mktemp -d` directory (mode
     0700);
  2. a single `sudo -n -u svc -- python3 ...` call has `svc` write a new file in
     its own staging from stdin;
  3. the upload directory is removed whether the deployment succeeds or fails.

  Nothing is made world-readable, and nothing is `chown`ed. Existing runtime
  data keeps its ownership.

The login user needs a sudo rule that runs commands as `svc` without a password,
for example in `/etc/sudoers.d/uqf`:

```
deploy ALL=(svc) NOPASSWD: ALL
```

Keep `--dest` outside the existing TorQ installation, and owned by `svc`.

## A site-managed TorQ launcher

Some servers keep TorQ's pieces apart: the core in one directory, a launcher the
site manages in another, and the starter pack in a third. The launcher is used
as supplied. `--torq-launcher` names it, and `--torq-home` still names the core:

```bash
uqs deploy push dist/uqf-<release>.tar.gz \
  --host svc@uqf-server --dest /opt/site/uqf \
  --torq-home /opt/site/torq/core/current \
  --torq-app-home /opt/site/torq/TorQApp \
  --torq-launcher /opt/site/torq/bin/torq.sh \
  --launcher-env KDBDB_ORG=uqf \
  --qcmd /opt/site/torq/bin/q.sh --qhome /opt/site/q \
  --profile default --init-data --dry-run
```

- **Preflight** requires `torq.q` in `--torq-home`, but not `torq.sh`. The
  launcher must be an absolute path to an executable file. A relative, missing
  or non-executable launcher is refused before anything changes.

- **deploy.env** records the launcher as `UQS_TORQ_LAUNCHER`. `uqs` runs it in
  place of `$TORQHOME/torq.sh`, with TORQHOME still the core and the generated
  `SETENV` and `TORQPROCESSES`, so the launcher starts this release's processes.
  Start, stop, verification and rollback each source their release's own
  `deploy.env`, so each uses the launcher that release was checked with.

- **The launcher's own variables** are the deployment's, not the operator's
  shell's. `TORQDATAHOME` is set to the runtime data directory, so the launcher
  cannot fall back to shared site data. Anything else it reads, such as
  `KDBDB_ORG`, goes in a repeatable `--launcher-env NAME=VALUE`. That flag also
  overrides `TORQDATAHOME`. It refuses the names the deployment sets itself:
  `TORQHOME`, `TORQAPPHOME`, `SETENV`, `TORQPROCESSES`, `QCMD`, `QHOME` and
  `UQS_*`.

- **Nothing in the TorQ installation changes.** It is not uploaded, written to,
  symlinked into or re-permissioned.

## On the server

```
/opt/uqf/
  current -> releases/20261007T133500Z-0123456789ab
  releases/<release>/       one per deployment, each with its .venv and deploy.env
  shared/data/uqs/          the runtime data: HDB, tickerplant logs, status
  shared/config/            files you supply, linked into every release
  deploy.lock/              held while a deployment runs
```

`deploy.env` holds no secrets, only where things are. Every `uqs` command run by
hand on the server should source it from the release:

```bash
cd /opt/uqf/current && source ./deploy.env && .venv/bin/uqs summary
```

It exports `TORQHOME` and `TORQAPPHOME`, the existing TorQ and starter pack,
which `uqs` uses in place of the vendored `lib/` trees. It exports
`UQS_DATA_ROOT`, the data directory that every release shares, so a second
deployment keeps the first one's HDB. It also exports `QCMD`, `QHOME`,
`UQS_RUNTIME` and `UV_OFFLINE`, and with `--live`, `UQS_REQUIRE_LIVE_SOURCES`.
If `shared/config/secrets.env` exists, it sources that too (see [credentials and
ODBC](sidecar-bundles.md#credentials-and-odbc)). See [the environment
reference](../reference/environment.md).

The demo `gateway_users.csv` is never shipped. Without it the gateway has only
the starter pack's logins. To give it ordinary users, put your own file at
`/opt/uqf/shared/config/scripts/torqconfig/permissions/gateway_users.csv`, and
every release links it in.

## A server on kdb+ 4.0

kdb+ 4.0 has no nested working contexts (`\d .qetl.status`), and this tree uses
them. Build the release for it, and only the artifact changes - never the
checkout (#861):

```bash
uqs deploy build --q-target 4.0
```

The build converts its staged copy with `scripts/portable/flatten_contexts.py`
(#856): each nested block runs at the root, with every name it means written out
in full. It refuses what it cannot convert with certainty, naming the file and
line. The whole tree converts, and CI checks that it stays convertible (#863);
`--q-exclude PATTERN` is the escape hatch, shipping a folder, file or glob as
written - which a 4.0 server can load only if it has no nested context. The
manifest records what was converted. `push` reads the server's `.z.K` and
refuses an unconverted release on a server older than 5.0. Its smoke test and
verification are the proof that the converted release runs there.

To convert a tree by hand, or inspect the conversion:

```bash
python3 scripts/portable/flatten_contexts.py src --out build/portable --dry-run --diff
```

`--debug` logs every rewrite and why, `--exclude` leaves files out, and
`--check` runs a q script in the result, such as
`scripts/portable/checks/status_intervals.q`. `--target 5.0` goes the other way,
nesting flattened blocks again wherever every name keeps its meaning (#859).

## Not in v1

- Installing q, TorQ or licences.
- Copying data.
- Migrations.
- Frontend builds.
- Zero-downtime upgrades.
- A single build-and-deploy command, or the Kafka and Databento feed handlers,
  which still start through `uv run`.

A deployment that died while holding the lock leaves `deploy.lock/` behind. The
next run refuses and names it. Remove the directory by hand once you are sure
nothing is running.
