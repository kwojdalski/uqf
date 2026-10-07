# Deploying to a server

Deploying is two tools, run separately (#773, #778):

- `scripts/build_release.py` builds a versioned **release artifact** once, with
  no SSH access or credentials, so CI can build it.
- `scripts/deploy.py` puts that artifact on a Linux server that already has TorQ
  and the finance starter pack installed. It starts a `uqs` profile there and
  checks that every process answers before it calls the deployment done.

One artifact can go to any number of servers. The server installs it without a
network.

> **Not yet run end to end.** The tools are unit tested with ssh, uv and pip
> replaced (`python/uqs/tests/test_deploy.py`, `test_build_release.py`). The run
> against a real server is `python/uqs/tests/test_deploy_integration.py`, which
> needs a disposable host and is skipped without one. Until it has passed
> somewhere, treat the commands below as the design rather than a recording.

## What the server needs

The tool installs none of these, and checks every one before it changes
anything:

- ssh access from where you run the tool, already working with your own
  `~/.ssh/config` and known hosts. The tool runs `ssh` and `scp` in batch mode,
  so a missing key or an unknown host key fails instead of prompting. It never
  turns host-key checking off.

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

- An existing TorQ (`--torq-home`, holding `torq.q` and `torq.sh`) and starter
  pack (`--torq-app-home`, holding `database.q` and `appconfig/process.csv`).
  Without these flags the release would need its own `lib/`, which the tool does
  not ship.

- `envsubst` and `rlwrap`, which `torq.sh` needs.

The server needs no access to PyPI. Every Python dependency arrives as a wheel
inside the artifact.

## Building a release

```bash
python3 scripts/build_release.py --output dist/
```

This writes three files, named after the release, which is the UTC build time
and the first 12 characters of the revision:

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

The target defaults to Linux on x86_64 with the Python `requires-python` names.
Pass `--arch aarch64` or `--python 3.14` for others. Uncommitted changes are
refused unless you pass `--allow-dirty`, which the manifest records. Building
needs `uv`, and network access to fetch the wheels.

## A first deployment

Show the plan first. `--dry-run` checks the artifact and runs only the read-only
preflight on the server. It then prints the artifact, its target beside what the
server reports, every step, and any restart it would make. It changes nothing:

```bash
python3 scripts/deploy.py --artifact dist/uqf-<release>.tar.gz \
  --host uqf-server --dest /opt/uqf \
  --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \
  --qcmd /opt/kx/bin/q --qhome /opt/kx \
  --profile essential --init-data --dry-run
```

Then the same command without `--dry-run`. `--init-data` lets the tool create
the runtime data directory the first time. A destination takes each release
once: to redeploy, build a new artifact. Without it, a missing data directory is
refused, so a mistyped `--dest` cannot quietly start an empty HDB.

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
  | verify    | `scripts/deploy_verify.py`: within `--verify-timeout`, every process the profile resolves to answers `.proc.procname` with its own name over q IPC. Where the quant library is loaded, a forward prices correctly; where the ETL tree is loaded, every transform's examples pass. A profile with pipeline processes must run the ETL check at least once                                       |
  | activate  | `current` moves to the new release in a single rename. The previous release stays where it was                                                                                                                                                                                                                                                                                                 |

The tool prints a report as JSON when it finishes, and leaves it in the release
as `deploy-report.json`. The report names the release, the source revision, each
process and what it answered, the stage that failed if one did, and, separately,
how the rollback went. Any unsuccessful deployment exits 1.

## When something fails

Before `start`, nothing on the server has changed apart from a new directory
under `releases/`, which is kept for inspection.

From `start` on, the new release's processes are stopped. If `--restart` had
stopped the previous ones, they are started again from the previous release.
`current` only moves in the activate stage, so it still names the previous
release. The report's `rollback` field says what was done and whether it worked.

## Deploying as a service user

On many servers the account you log in as is not the one that owns and runs uqf;
an operator would `sudo su - svc` by hand. `--remote-user` does that step
non-interactively (#780):

```bash
python3 scripts/deploy.py --artifact dist/uqf-<release>.tar.gz \
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
`UQS_RUNTIME` and `UV_OFFLINE`. See [the environment
reference](../reference/environment.md).

The demo `gateway_users.csv` is never shipped. Without it the gateway has only
the starter pack's logins. To give it ordinary users, put your own file at
`/opt/uqf/shared/config/scripts/torqconfig/permissions/gateway_users.csv`, and
every release links it in.

## Not in v1

- Installing q, TorQ or licences.
- Copying data.
- Migrations.
- Frontend builds.
- Zero-downtime upgrades.
- Deleting old releases.
- A single build-and-deploy command, or the Kafka and Databento sidecars, which
  still start through `uv run`.

A deployment that died while holding the lock leaves `deploy.lock/` behind. The
next run refuses and names it. Remove the directory by hand once you are sure
nothing is running.
