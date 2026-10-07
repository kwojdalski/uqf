# Deploying to a server

`scripts/deploy.py` puts this tree on a Linux server that already has TorQ and
the finance starter pack installed. It starts a `uqs` profile there and checks
that every process answers before it calls the deployment done (#773).

> **Not yet run end to end.** The tool's stages are unit tested with ssh
> replaced (`python/uqs/tests/test_deploy.py`). The run against a real server is
> `python/uqs/tests/test_deploy_integration.py`, which needs a disposable host
> and is skipped without one. Until it has passed somewhere, treat the commands
> below as the design rather than a recording.

## What the server needs

The tool installs none of these, and checks every one before it changes
anything:

- ssh access from where you run the tool, already working with your own
  `~/.ssh/config` and known hosts. The tool runs `ssh` and `scp` in batch mode,
  so a missing key or an unknown host key fails instead of prompting. It never
  turns host-key checking off.
- `uv` with a Python 3.14 it can find (`uv python find '>=3.14'`), and
  `python3`, `bash`, `tar` and `timeout`.
- A licensed q. Pass `--qcmd` and `--qhome` unless `q` on the server's `PATH`
  already finds its licence.
- An existing TorQ (`--torq-home`, holding `torq.q` and `torq.sh`) and starter
  pack (`--torq-app-home`, holding `database.q` and `appconfig/process.csv`).
  Without these flags the release would need its own `lib/`, which the tool does
  not ship.
- `envsubst` and `rlwrap`, which `torq.sh` needs.
- Access to PyPI (or a warm uv cache) for the first `uv sync` of each release:
  the release-local environment is built from `uv.lock` on the server.

## A first deployment

Show the plan first. `--dry-run` runs only the read-only preflight on the
server, then prints the payload, every step and any restart it would make. It
changes nothing:

```bash
python3 scripts/deploy.py --host uqf-server --dest /opt/uqf \
  --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \
  --qcmd /opt/kx/bin/q --qhome /opt/kx \
  --profile essential --init-data --dry-run
```

Then the same command without `--dry-run`. `--init-data` lets the tool create
the runtime data directory the first time. Without it, a missing data directory
is refused, so a mistyped `--dest` cannot quietly start an empty HDB.

## What it does, in order

Each stage stops the deployment if it fails:

  | Stage     | What happens                                                                                                                                                                                                                                                                                                                                             |
  | ---       | ---                                                                                                                                                                                                                                                                                                                                                      |
  | preflight | ssh works and the destination is writable; uv, Python, q (it must run a script), the TorQ trees and the launcher's tools are there; the data directory exists or `--init-data` was given; another deployment there needs `--restart`; no other deployment holds the lock                                                                                 |
  | package   | the tracked files under an allowlist (`src`, `scripts`, the three Python packages, `pyproject.toml`, `uv.lock`), with secrets, `lib/`, tests, data and logs excluded. A manifest records the revision and every file's sha256. Uncommitted changes are refused without `--allow-dirty`                                                                   |
  | transfer  | `scp` into `staging/<release>/`; the archive's sha256 is checked **on the server** before it is extracted into `releases/<release>/`                                                                                                                                                                                                                     |
  | prepare   | `releases/<release>/deploy.env` (below), and a release-local `.venv` from the committed `uv.lock`                                                                                                                                                                                                                                                        |
  | smoke     | `scripts/deploy_smoke.q` loads the quant library and checks known numbers. It must print `DEPLOY_SMOKE_OK` and exit 0 within `--smoke-timeout`                                                                                                                                                                                                           |
  | restart   | with `--restart`, the previous release's processes stop. Their replacements take the same ports, so an upgrade has downtime                                                                                                                                                                                                                              |
  | ports     | nothing else listens on the profile's ports                                                                                                                                                                                                                                                                                                              |
  | start     | `uqs start --profile <profile>` from the new release                                                                                                                                                                                                                                                                                                     |
  | verify    | `scripts/deploy_verify.py`: within `--verify-timeout`, every process the profile resolves to answers `.proc.procname` with its own name over q IPC. Where the quant library is loaded, a forward prices correctly; where the ETL tree is loaded, every transform's examples pass. A profile with pipeline processes must run the ETL check at least once |
  | activate  | `current` moves to the new release in a single rename. The previous release stays where it was                                                                                                                                                                                                                                                           |

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
cd /opt/uqf/current && source ./deploy.env && uv run --frozen uqs summary
```

It exports `TORQHOME` and `TORQAPPHOME`, the existing TorQ and starter pack,
which `uqs` uses in place of the vendored `lib/` trees. It exports
`UQS_DATA_ROOT`, the data directory that every release shares, so a second
deployment keeps the first one's HDB. It also exports `QCMD`, `QHOME` and
`UQS_RUNTIME`. See [the environment reference](../reference/environment.md).

Never shipped: `.env`, `.envrc`, keys and licences, `lib/`, and
`scripts/torqconfig/permissions/gateway_users.csv`, which holds the demo gateway
passwords. Without it the gateway has only the starter pack's logins. To give it
ordinary users, put your own file at
`/opt/uqf/shared/config/scripts/torqconfig/permissions/gateway_users.csv`, and
every release links it in.

## Not in v1

- Installing q, TorQ or licences.
- Copying data.
- Migrations.
- Frontend builds.
- Zero-downtime upgrades.
- Deleting old releases.

A deployment that died while holding the lock leaves `deploy.lock/` behind. The
next run refuses and names it. Remove the directory by hand once you are sure
nothing is running.
