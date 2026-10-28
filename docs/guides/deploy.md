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
> `test_offline_install.py` runs the push's server scripts and the installer for
> real, on this machine with its network cut off: see [an offline
> install](#an-offline-install-tested).

## What the server needs

`push` installs none of these, and checks every one before it changes anything:

- ssh access from where you run `push`, already working with your own
  `~/.ssh/config` and known hosts. It runs `ssh` and `scp` in batch mode, so a
  missing key or an unknown host key fails instead of prompting. It never turns
  host-key checking off.

- `uv`, with the Python the artifact was built for (3.14 by default) where
  `uv python find` sees it. Or, with `--python-mode bundled`, neither: the push
  installs a CPython and makes the release's `.venv` from it, see [a bundled
  Python](#a-bundled-python). The server's OS, architecture and Python must
  match the artifact's manifest, and its glibc must meet the floor the
  artifact's wheels set.

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
  shows each value and where it came from. Or, with `--q-mode bundled`, the push
  installs the pinned PeachQ and needs no q or licence on the server: see [a
  bundled q](#a-bundled-q).

- An existing TorQ (`--torq-home`, holding `torq.q` and `torq.sh`, or only
  `torq.q` with `--torq-launcher`, below) and starter pack (`--torq-app-home`,
  holding `database.q` and `appconfig/process.csv`). Without these flags the
  release would need its own `lib/`, which a release does not ship. Or, with
  `--torq-mode bundled`, the push installs the vendored one: see [a bundled
  TorQ](#a-bundled-torq).

- The tools `torq.sh` and the push's own steps run, and a `python3` of 3.8 or
  later: the [minimum host contract](#the-minimum-host-contract) lists them.

- For live sources, their credentials and, for ODBC, a driver: see [credentials
  and ODBC](sidecar-bundles.md#credentials-and-odbc).

The server needs no access to PyPI. Every Python dependency arrives as a wheel
inside the artifact.

### The minimum host contract

What a server must still provide when every component that can be bundled is
bundled: `--torq-mode`, `--q-mode` (PeachQ) and `--python-mode` all `bundled`.
Each item is checked before anything changes: by preflight
(`python/uqs/src/uqs/deploy/preflight.py`) for a push, and by
`scripts/install_release.py` for a release installed by hand. The tools are
`HOST_TOOLS` in `python/uqs/src/uqs/deploy/artifact.py`, which a test holds
equal to the list preflight checks. Every release's manifest records them as
`host.tools`, for the installer to check.

  | Item                                                                                                                                                                                                  | Why                                                                                                                                                                                                                                                                                                        | Checked by                                                                      |
  | ---                                                                                                                                                                                                   | ---                                                                                                                                                                                                                                                                                                        | ---                                                                             |
  | Linux, on the architecture the artifact was built for (`x86_64` or `aarch64`)                                                                                                                         | the wheels, the bundled CPython and PeachQ are built for one OS and architecture                                                                                                                                                                                                                           | `uname -s`/`uname -m` in preflight; `platform` in the installer                 |
  | glibc at or above the release's floor                                                                                                                                                                 | the highest `manylinux` floor among the release's wheels, which `uqs deploy build` records as `target.glibc` in the manifest: 2.17 or 2.28 for the tags it allows. A bundled CPython asks for 2.17 itself, and records that in its manifest. No kernel floor is checked: it is whatever that glibc runs on | `getconf GNU_LIBC_VERSION` in preflight; `platform.libc_ver()` in the installer |
  | `bash`, `tar` with `gzip`, `timeout`, `find`, `sed`, `awk`, `grep`, `nohup`, and the coreutils every POSIX shell script uses (`mktemp`, `chmod`, `ln`, `readlink`, `date`, `id`, `head`, `cut`, `tr`) | the push's server scripts, and `torq.sh`                                                                                                                                                                                                                                                                   | preflight, by name for each but the plain coreutils                             |
  | `python3`, 3.8 or later, standard library only                                                                                                                                                        | the push's own checks on the server (sha256s, the runtime check, prune's listing, atomic renames), and running the installer. It is never the release's Python                                                                                                                                             | preflight runs it                                                               |
  | `envsubst` (gettext), `pgrep` (procps), `lsof`, `hostname` that takes `-I` and `-A`                                                                                                                   | `torq.sh start` substitutes `process.csv` fields with `envsubst`, finds a running process with `pgrep` and matches its host against `hostname -I`/`-A`; `torq.sh stop` finds the pid listening on a port with `lsof`                                                                                       | preflight, `hostname -I` by running it                                          |
  | `rlwrap`                                                                                                                                                                                              | uqs refuses to run `torq.sh` without it (`uqs.paths.check_prerequisites`), though `torq.sh` uses it only for `qcon` and `debug`                                                                                                                                                                            | preflight                                                                       |
  | `sshd`, with `scp`, reachable from where you run `push`; for `--remote-user`, a `sudo -n -iu <user>` rule                                                                                             | how the push reaches the server and runs as the service account                                                                                                                                                                                                                                            | ssh itself; `check_sudo` in preflight                                           |
  | a writable `--dest`                                                                                                                                                                                   | the releases, the runtimes and the lock live there                                                                                                                                                                                                                                                         | preflight                                                                       |

With a component left `existing`, it adds its own requirement: `uv` and a Python
of the artifact's version for Python; a q and, for KDB-X or kdb+, **its
licence** in `QHOME` for q (KX's q is never bundled, and no licence is in any
artifact); TorQ and the starter pack at `--torq-home`/`--torq-app-home` for
TorQ. A release built with `--kafka-home` loads its `kfk` in the server's q
before starting, and `--odbc-home` needs that setup on the server.

Outside the contract: `netstat` and `column`, which `torq.sh summary` uses to
show each process's port and lay the table out. Without them `uqs summary` lacks
those columns; nothing the push or a process needs depends on them.

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
  three Python packages, `pyproject.toml`, `uv.lock`, and `lib/kx-dbmaint`), at
  their repository paths, because the q loaders and `uqs` find each other by
  layout. `lib/kx-dbmaint` is KX's `dbmaint.q` with its Apache-2.0 licence and
  notice, the one part of `lib/` a release carries: the HDB repair below loads
  it, offline, from `scripts/gates/fill_hdb_partitions.q`'s own location, and
  the licence has to travel with the copy. Never shipped: the rest of `lib/`
  (TorQ, the starter pack and the other vendored trees), `.env`, `.envrc`, keys
  and licences, virtual environments, runtime data, logs, tests, build output,
  and `scripts/torqconfig/permissions/gateway_users.csv`, which holds the demo
  gateway passwords.
- **`.release/`.** `requirements.txt`, the dependencies of `uqs` pinned with
  hashes from the committed `uv.lock`. `wheels/` holds the `uqs` wheel and a
  binary wheel of every pinned dependency for the target.
- **`RELEASE_MANIFEST.json`.** The revision, whether the tree had uncommitted
  changes, the target OS, architecture and Python, and every member's sha256.

Kafka is opt-in, and a default release carries none of it: `--kafka-home DIR`
adds the native client under `.release/kafka/`, and `--with-kafka-connector`
adds confluent-kafka's locked wheels (the `uqs` `kafka` extra). See [Kafka in a
release](../services/kafka.md#in-a-release).

The browser frontend is opt-in too. `--frontend DIST` adds the app that
`npm --prefix web run build` wrote, usually `web/dist`, and the backend that
serves it:

```bash
npm --prefix web ci && npm --prefix web run build
uqs deploy build --frontend web/dist
```

- **The assets** go into the release at `web/dist/`, file for file and each
  checksummed, which is where `uqf_frontend` serves them from, at `/ui/`. The
  server needs no Node or npm. `DIST` is refused without an `index.html`, with a
  symbolic link, or with a file no release ships (an `.env`, a key,
  `node_modules`).
- **The backend.** uqf-frontend's wheel joins uqs's, and its locked dependencies
  (FastAPI, uvicorn and the rest) join the release's wheels. The push's prepare,
  or the installer, installs it into the release's `.venv` after uqs, offline.
  The manifest lists it in `python.extra_wheels`, and records a `frontend`
  entry.
- **Starting it** stays yours: from the release, with `deploy.env` loaded,
  `.venv/bin/uvicorn --factory uqf_frontend.app:create_app --port 8000`. See
  `python/uqf_frontend/README.md` for its settings, and for the write controls
  that are off by default.

`--bundle <folder>`, repeatable, adds a [sidecar bundle](sidecar-bundles.md).

The target defaults to Linux on x86_64 with the Python `requires-python` names.
Pass `--arch aarch64` or `--python 3.14` for others. Uncommitted changes are
refused unless you pass `--allow-dirty`, which the manifest records. Building
needs `uv`, and network access to fetch the wheels.

## Artifacts built by CI

`.github/workflows/release.yml` builds the artifacts for a tag `v*`, or for a
tag given to its manual run. It builds them only after `ci.yml`'s checks pass on
that commit, and never with `--allow-dirty`. Each release gets two: the plain
artifact, and one with its q converted for kdb+ 4.0 (`--q-target 4.0`). Each
comes with its `.sha256` and `.manifest.json`. Deploy one by tag or by URL:

```bash
uqs deploy push v1.4.0 --host uqf-server --dest /opt/uqf ... --dry-run
uqs deploy push https://github.com/<owner>/uqf/releases/download/v1.4.0/uqf-<release>.tar.gz ...
```

- **By tag.** The release's assets are found through the GitHub API, in
  `--release-repo OWNER/NAME`, or else in this checkout's `origin`. A release
  carrying two artifacts is refused by tag, and the error names both URLs.
- **The download.** The archive and its published `.sha256` go into
  `~/.cache/uqf/releases/`. They are then checked exactly as a local artifact
  is, before anything reaches the server. A token in `GH_TOKEN` or
  `GITHUB_TOKEN` is sent for a private repository.
- **Dirty artifacts.** `push` refuses an artifact built from uncommitted changes
  (`dirty: true`) unless given `--allow-dirty`.

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

## Targets in a file

Instead of repeating a dozen flags, declare each server once in
`deploy_targets.toml` at the repository root. The file is gitignored, like
`runtime_bundles.json`. Point `UQS_DEPLOY_TARGETS` at it to keep it elsewhere:

```toml
[targets.prod-a]
host = "deploy@prod-a"
remote_user = "svc"
dest = "/srv/uqf"
runtime = "crypto"
profile = "essential"
torq_home = "/opt/torq"
live = true
live_check = ["deals_db"]
```

```bash
uqs deploy push dist/uqf-<release>.tar.gz prod-a --dry-run
uqs deploy push dist/uqf-<release>.tar.gz prod-a prod-b
uqs deploy status prod-a
uqs deploy rollback prod-a --dry-run
uqs deploy prune prod-a --keep 3
uqs deploy history prod-a
```

Every `deploy` command that reaches a server takes the target as its TARGET:
`push ARTIFACT TARGET...`, deploying each in order and stopping at the first
failure, and `status`, `rollback`, `prune` and `history TARGET`. `status`,
`rollback` and `prune` also take a server a push from here registered (see
`uqs deploy history`). `--host`, `--dest`, `--remote-user` and
`--connect-timeout` remain for a server no target declares, and read the same on
every command. Given beside a TARGET, they override its values.
`push --target A,B`, the spelling before #1736, still works for one release and
prints the new one.

- **The keys.** Each key is a `push` option, spelt as its parameter (`torq_home`
  for `--torq-home`). The values go through the same checks as the flags, and an
  unknown key is refused.
- **`runtime`.** The one key that is not a flag. It must match the runtime the
  artifact was built for.
- **Flags win.** A flag given on the command line overrides the file. The
  dry-run plan says which settings came from the target and which from a flag.
- **Several targets.** `--target a,b` deploys them in order, host by host, and
  stops at the first failure.
- **`labels`.** Free-form `key = "value"` pairs that `uqs list targets` shows.
  Nothing deploys them.

### Shared settings: defaults and environments

Settings that several targets share can be declared once. A target picks an
environment with `environment = "<name>"`:

```toml
[defaults]
remote_user = "svc"
profile = "essential"

[environments.uat]
profile = "fx"

[targets.uat-a]
environment = "uat"
host = "deploy@uat-a"
dest = "/srv/uqf"
```

Each layer overrides the one above it in this table:

  | Layer                      | Plan shows it as  | May set                               |
  | -------------------------  | ----------------- | ------------------------------------- |
  | `[defaults]`               | `defaults`        | any target key except `environment`   |
  | `[environments.<name>]`    | `environment uat` | any target key except `environment`   |
  | `[targets.<name>]`         | `target uat-a`    | any target key, and `environment`     |
  | a flag on the command line | `--profile`       | any `push` option                     |

- **Whole values.** A higher layer replaces a key's whole value. Tables such as
  `labels` are never merged.
- **Same checks.** Every layer's keys are checked, and a refusal names the layer
  and the key. An undeclared environment is refused. The merged values go
  through the same checks as the flags.
- **Never in a file.** No layer may set the artifact, `--dry-run`, or a named
  override.
- **Listing.** `uqs list targets` shows each target with its layers merged. It
  adds an `environment` column when a target names one.

## Requirements and named overrides

A target can pin what it deploys and what it deploys onto:

```toml
expected_sha256 = "<the artifact's sha256>"
q_version = ">=4.1"  # a minimum; "4.1" means exactly 4.1
```

Push refuses an artifact with any other sha256, and a server whose q, as
preflight runs it, is outside `q_version`. A local artifact also needs its
`.sha256` beside it, or an `expected_sha256`. Each requirement has one named
override that relaxes only it. An override is given on the command line, never
in a target, and needs a reason:

  | Override                    | Relaxes                                    |
  | --------------------------- | ------------------------------------------ |
  | `--accept-digest REASON`    | `expected_sha256`                          |
  | `--accept-q-version REASON` | `q_version`                                |
  | `--allow-unpinned REASON`   | the `.sha256` a local artifact must have   |

- An override with nothing to relax is refused: `--accept-digest` without
  `expected_sha256`, `--accept-q-version` without `q_version`,
  `--allow-unpinned` with `expected_sha256`.
- Accepting a digest is not trusting the artifact. Its actual sha256 is
  recorded, and the server checks the bytes it receives against it.
- No override bypasses the archive's member checks, the OS, architecture and
  Python match, the smoke test or verification.
- Every override asked for, `--accept-converted-release` included, opens the
  dry-run plan in an `EXCEPTIONS` block. Each is also recorded in the report's
  `exceptions` and in the deploy history, with its reason, what was required,
  what was found and whether it was needed. The report records the server's q as
  `qversion`, override or not.
- The report and the deploy history also record the release artifact itself as
  `artifact`: its file name and the sha256 the server checked its bytes against,
  whether a pin, an override or only its `.sha256` named it.

## Components

A release runs on three components: q, TorQ and Python. Each has a mode, set by
`q_mode`, `torq_mode` and `python_mode` (or `--q-mode`, `--torq-mode`,
`--python-mode`):

  | Mode       | Means                                                                                                    |
  | ---------- | -------------------------------------------------------------------------------------------------------  |
  | `existing` | already on the server; the default                                                                       |
  | `bundled`  | shipped by the push: [TorQ](#a-bundled-torq), [q (PeachQ)](#a-bundled-q) and [Python](#a-bundled-python) |

KX's q is never bundled.

`existing` mode's settings are the options above: `qcmd` and `qhome` for q;
`torq_home`, `torq_app_home` and `torq_launcher` for TorQ. Python has none, uv
finds it. A push that sets no mode is the source-only push it always was.

- **Switching drops the old mode.** A layer that changes a component's mode
  drops the old mode's settings from the layers below it. Setting one at or
  above that layer is refused.
- **Recorded.** The report and the deploy history keep a `components` entry:
  each component's mode, the path preflight resolved, the version it observed,
  and what the server must provide, such as a q licence. An existing TorQ's
  version is not read, so it is `null`.
- **Shown.** The dry-run plan opens with a `COMPONENTS` block, after any
  `EXCEPTIONS`.

### A bundled TorQ

With `torq_mode = "bundled"`, the push ships TorQ itself: `lib/torq` and
`lib/torq-finance-starter-pack` exactly as vendored, in a runtime artifact of
their own. Many releases share one runtime, so it is built separately, by
`uqs deploy component build`. A bundled component's runtime is not a stack
runtime (`uqs --runtime`), which is why the command is not named for it. Its
name before #1740, `uqs deploy build-runtime`, still works for one release.

```bash
uqs deploy component build torq      # dist/torq-runtime-<version>-<id>.tar.gz
uqs deploy push dist/uqf-<release>.tar.gz --host uqf-server --dest /opt/uqf \
  --profile essential --torq-mode bundled \
  --torq-runtime dist/torq-runtime-5.2.12-<id>.tar.gz
```

- **Built from the tree, unedited.** The build packs the two trees' tracked
  files, byte for byte, and refuses when `git status` shows any change under
  them. Its manifest records TorQ's and the starter pack's versions, from their
  `dependency.csv`, each tree's git tree id and last commit, and every file's
  sha256. The same trees always make the same archive, so its sha256 names that
  TorQ.
- **Pinned like a release.** The runtime's `.sha256` must sit beside it, and
  `torq_runtime_sha256` (or `--torq-runtime-sha256`) pins it as
  `expected_sha256` pins a release. `--accept-digest` and `--allow-unpinned`
  relax these pins too, and are recorded. Its members are checked as a release's
  are, and the server checks the bytes it receives.
- **Installed once, never rewritten.** It goes to
  `<dest>/shared/runtimes/torq/<sha256>/`, beside a marker naming that sha256.
  If the directory is already there, holding exactly that runtime, it is reused
  and nothing is written. If it holds anything else, the push stops before
  changing anything. A runtime directory is never overwritten.
- **Each release names its own.** The release's `deploy.env` sets `TORQHOME` and
  `TORQAPPHOME` to the runtime's two trees, and `torq.sh` is `TORQHOME`'s. A
  rollback starts the release it returns to on the TorQ that release was
  deployed with. `uqs deploy prune` keeps every runtime a remaining release's
  `deploy.env` names, and removes the others.
- **Exclusive.** `torq_home`, `torq_app_home` and `torq_launcher` are refused
  beside it. A layer that switches to bundled drops those settings from the
  layers below it.

The `components` entry records `bundled`, the runtime's sha256, TorQ's version,
its path on the server, and whether this push installed or reused it.

### A bundled q

With `q_mode = "bundled"`, the push ships the q too: the PeachQ that
`scripts/peachq.json` pins, in a runtime artifact of its own. KX's q (KDB-X or
kdb+) is licensed to the server it runs on, so it is never bundled. It stays
`existing`, with its licence in `QHOME`.

```bash
uqs deploy component build q         # dist/q-runtime-peachq-<commit>-<os>-<arch>.tar.gz
uqs --runtime peachq deploy build
uqs deploy push dist/uqf-<release>.tar.gz --host uqf-server --dest /opt/uqf \
  --profile capture --torq-mode bundled --torq-runtime dist/torq-runtime-<...>.tar.gz \
  --q-mode bundled --q-runtime dist/q-runtime-peachq-<commit>-linux-x86_64.tar.gz
```

- **Built from the pin.** The build takes the binary `scripts/peachq.py`
  resolves: its cache, or a fresh build of the pinned commit into it. It refuses
  `UQF_PEACHQ`, whose binary has no recorded origin. A `UQF_PEACHQ_COMMIT` build
  is recorded in the manifest with its reason. The binary is run before it is
  packed, and must answer that it is PeachQ.
- **One file, never a licence.** The archive holds the binary and its manifest,
  nothing else. The manifest records the repository, commit, platform, build
  options and the `.z.K` the binary reported. Every artifact this tool reads,
  release or runtime, is refused if it carries a `.lic` file.
- **For a PeachQ runtime, on its platform.** A release built for a runtime
  declared on KDB-X (`uqf`, `crypto`, `fx`, `torq`) is refused a bundled q:
  nothing assumes PeachQ runs a workload until that workload's tests pass on it.
  The runtime must also be built for the release's OS and architecture, so build
  it on such a host.
- **Pinned like a release.** `q_runtime_sha256` (or `--q-runtime-sha256`) pins
  it, and `--accept-digest` and `--allow-unpinned` relax that pin as they do the
  others, recorded. `q_version` holds it too.
- **Installed once, run there.** It goes to `<dest>/shared/runtimes/q/<sha256>/`
  under the same write-once rules as a TorQ runtime. Preflight looks for no q on
  the server. Once installed, the push runs the binary there; if it does not
  answer as PeachQ, the push stops before the release is transferred.
- **Each release names its own.** `deploy.env` sets `QCMD` to the runtime's `q`
  and `QHOME` to its directory, with `UQF_Q_IMPL=peachq`. It also sets
  `UQF_PEACHQ` (and its reason) to that binary, so the release's PeachQ stack
  never builds one on the server. Rollback and prune treat it as they treat a
  TorQ runtime.
- **Exclusive.** `qcmd` and `qhome` are refused beside it. A layer that switches
  q to bundled drops them from the layers below it, and one that switches back
  drops `q_runtime` and its pin.

The `components` entry records `bundled`, the runtime's sha256, the version the
installed q reported, PeachQ's commit, its path on the server, and whether this
push installed or reused it.

### A bundled Python

With `python_mode = "bundled"`, the push ships the interpreter too: the CPython
uv installs for any project (python-build-standalone), for the release's OS,
architecture and Python version, in a runtime artifact of its own. The server
then needs no `uv` and no Python 3.14.

```bash
uqs deploy component build python --arch x86_64 --python 3.14
                                   # dist/python-runtime-3.14.<n>-linux-x86_64.tar.gz
uqs deploy push dist/uqf-<release>.tar.gz --host uqf-server --dest /opt/uqf \
  --profile essential --torq-mode bundled --torq-runtime dist/torq-runtime-<...>.tar.gz \
  --python-mode bundled --python-runtime dist/python-runtime-3.14.<n>-linux-x86_64.tar.gz
```

- **Installed by uv, packed as installed.** The build runs
  `uv python install --no-bin --no-registry cpython-<python>-linux-<arch>-gnu`,
  which puts nothing on your PATH, and packs that installation from
  `uv python dir`. `--python` defaults to `requires-python`'s, `--arch` to
  `x86_64`; it needs the network once, as uv does.
- **Regular files only.** The installation's symbolic links (`bin/python3`, the
  unversioned `libpython`, terminfo aliases) are left out and listed in the
  manifest as `links_left_out`. The interpreter is the versioned file,
  `bin/python3.<minor>`. Each file keeps its mtime, so CPython's `.pyc` files
  stay valid and are never rewritten inside an installed runtime. CPython's own
  `LICENSE.txt` is a notice and ships; a licence key (`.lic`) is refused, as in
  every artifact.
- **Recorded.** The manifest records uv's name for the installation, the
  version, the python-build-standalone release, the platform, the glibc its ELF
  files ask for, and every file's sha256.
- **For this release.** It must be built for the release's OS, architecture and
  Python `major.minor`, or the push is refused before anything runs.
- **Pinned like a release.** `python_runtime_sha256` (or
  `--python-runtime-sha256`) pins it; `--accept-digest` and `--allow-unpinned`
  relax that pin as they do the others, recorded.
- **Installed once, run there.** It goes to
  `<dest>/shared/runtimes/python/<sha256>/` under the same write-once rules as
  the other runtimes. Preflight looks for no `uv` and no Python on the server;
  once installed, the push runs the interpreter isolated (`-I`), loading `ssl`,
  `sqlite3`, `zlib`, `venv` and `ensurepip`. It must report the version it was
  built as.
- **`.venv` from it, with no uv.** prepare runs
  `<runtime>/bin/python3.<minor> -m venv .venv`, then `pip install --no-index`
  from the release's wheels with the lock's hashes required, as the installer
  does. `deploy.env` names the runtime as `UQF_PYTHON_RUNTIME`, so prune keeps
  it while a release's `.venv` runs on it, and a rollback returns to a release
  on the Python it was deployed with.

The `components` entry records `bundled`, the runtime's sha256, the version the
installed interpreter reported, the python-build-standalone release, its path on
the server, and whether this push installed or reused it.

A release can also **carry** a Python runtime
(`uqs deploy build --python-runtime FILE`), for a server where it is installed
without `push`: see [installing an artifact on the server
yourself](#installing-an-artifact-on-the-server-yourself). A push of such a
release with `--python-mode bundled` and no `--python-runtime` uses the one it
carries:

- **Extracted, then checked.** The push copies it out of the release into
  `~/.cache/uqf/runtimes/python/<sha256>/` (under `XDG_CACHE_HOME` when set),
  and refuses a copy whose sha256 is not the one the release's manifest records.
  The release's own pin and checks therefore pin it: it needs no `.sha256` of
  its own, and `python_runtime_sha256` still holds it.
- **Installed as any other.** From there it is installed, probed, recorded and
  pruned exactly as a runtime given with `--python-runtime`. Its `components`
  entry adds `carried_by`, the release it came from, and the plan says so.
- **A given runtime wins.** `--python-runtime` names another, and nothing is
  extracted. A release that carries none is refused, naming both ways to give
  one.

### An offline install, tested

`python/uqs/tests/test_offline_install.py` installs a real artifact with the
network cut off, twice: once through the push's own server scripts (the
write-once installs of a bundled Python and a bundled PeachQ, their probes,
transfer and prepare), and once through `scripts/install_release.py` from the
unpacked release, on the Python it carries. Each `.venv` must be the bundled
interpreter's and run `uqs --version`.

- **What is real.** The CPython is the one uv installed that runs the tests,
  packed by the same code as `component build python`, so the host's OS and
  architecture rather than Linux. The artifact is `build_artifact` over this
  checkout's release files, with the `uqs` wheel built by `uv build --offline`.
  Its dependencies are the wheels of what `uqs --version` imports, packed from
  the test environment's installed distributions, with their hashes in
  `requirements.txt`. The PeachQ is a stand-in binary.
- **How the network is cut.** Every server step runs inside a sandbox with no
  network where the host has one (macOS `sandbox-exec`, Linux `unshare --net`).
  It also runs with a fresh `HOME`, no proxy variables, `PIP_NO_INDEX=1` and
  `UV_OFFLINE=1`, and behind tripwires: a `sitecustomize` that logs and refuses
  every socket connect and name lookup in each Python started, the bundled one
  included, and `uv`, `uvx`, `curl` and `wget` stand-ins first on `PATH`. The
  test asserts nothing was attempted, then proves the tripwires are live. A
  fetch from the bundled Python must be caught, and with the sandbox on, the
  same fetch without the tripwire must fail too.
- **What it does not prove.** A process started with `-I` or `-E`, such as
  ensurepip's own pip, does not load the tripwire. Without the sandbox, its
  silence rests on `--no-index` alone. The host is the test machine, not a clean
  Linux server. That run is still `test_deploy_integration.py`'s.

## Driving a deployed release from your machine

Once a target is declared, `uqs --target NAME <command>` runs any `uqs` command
on that server. It runs the release's own `<dest>/current/.venv/bin/uqs`, with
that release's `deploy.env` loaded, as `remote_user` when the target sets one.
Processes, ports, jobs and configuration are therefore the server's, not your
checkout's.

```bash
uqs target add uat --ssh svc@uat.example --dest /opt/efx/uat/uqf --label env=uat
uqs target check uat                       # ssh works; names the running release
uqs --target uat summary
uqs --target uat logs piggybankgeneralledger1 -f
uqs --target uat --yes query --proc rdb1 'select [20] from piggybank_general_ledger'
uqs --target uat start piggybankgeneralledger1   # asks first; --yes skips the question
```

- **The same entries.** `uqs target add` appends a `[targets.NAME]` table to
  `deploy_targets.toml`, so one entry serves both `deploy push ARTIFACT TARGET`
  and `uqs --target`. `uqs list targets` and `uqs target remove` read and edit
  that file, on this machine only: `--target` refuses them. `remove` leaves
  every other line, comments included, as it was.
- **Confirmation.** A command that can change the server, such as `start`,
  `stop`, `restart`, `raw`, `job backfill`, `config set` or `data seed`, asks
  first and names the target. Without a terminal it needs `--yes`. So do
  `query`, which can send any q, and `summary -i`, whose browser starts and
  stops processes. Read-only commands run directly: `summary`, `logs`, `schema`,
  `list`, `graph`, `job gaps` and `job runs show`.
- **ssh.** It runs with `BatchMode`, so a missing key or an unknown host key
  fails rather than prompts. Host-key checking is whatever your `ssh_config`
  says, and credentials stay on the server.
- **Failures.** Three kinds are reported separately:
  - ssh could not connect;
  - the target has no deployed release under its `dest`, or has one that cannot
    launch `uqs`. The error names what is missing: the CLI, the environment
    file, the interpreter, the source, or the interpreter's dependencies;
  - the remote command failed, with its own output and exit code.
- **Querying from here instead.** `uqs query --target NAME` runs the query on
  this machine through an ssh tunnel to the target, with ports from the target's
  release. See [A deployed target, from
  here](uqs.md#a-deployed-target-from-here). `uqs --target X query --target Y`
  is refused.

### A release `uqs deploy push` did not install

Some servers get uqf from another release process, which installs the source
tree and switches `current` but creates no `.venv` and no `deploy.env`. Set
`launch_mode = "source"` on such a target, and name the interpreter and
environment file its operator provisioned:

```toml
[targets.uat]
host = "svc@uat.example"
dest = "/opt/efx/uat/uqf"
launch_mode = "source"
launch_python = "/opt/python/3.12/bin/python3"
launch_env_file = "/opt/efx/uat/uat-runtime.env"
```

`uqs --target uat <command>` then:

1. enters `<dest>/current`;
2. loads `launch_env_file`;
3. runs `launch_python -m uqs` with the release's own `python/uqs/src` first on
   `PYTHONPATH`, so it is that release's CLI that runs, not an installed one.

Quoting, `remote_user`, confirmation and exit codes work as for a pushed
release.

Nothing is installed for you:

- `launch_python` must already have uqf's dependencies; a missing one is refused
  before the command runs.
- `launch_env_file` must set what a pushed release's `deploy.env` would: where
  q, TorQ and the runtime's data are.

Both paths must be absolute. A target without `launch_mode`, or with
`launch_mode = "release"`, is a pushed release as before. Its missing `.venv` is
reported as such, never run from source instead. Setting either path without
`launch_mode = "source"` is refused. `uqs deploy push` ignores these three keys.

### Installing an artifact on the server yourself

The other way, when the release process delivers a `uqs deploy build` artifact
rather than a bare source tree: the artifact already carries every Python
dependency as a locked wheel, and an installer that needs only the server's
`python3` (3.8 or later, standard library only). Built with `--python-runtime`,
it carries the interpreter too, so the server needs no Python 3.14:

```bash
uqs deploy component build python --arch x86_64
uqs deploy build --arch x86_64 --python-runtime dist/python-runtime-3.14.<n>-linux-x86_64.tar.gz
```

Unpack the artifact into its own release directory, then, from inside it:

```bash
python3 scripts/install_release.py --check              # verify only; writes nothing
python3 scripts/install_release.py --env-file /opt/efx/uat/uat-runtime.env
python3 scripts/install_release.py --python /opt/python/3.14/bin/python3   # the site's instead
```

In order, each step refusing with what to do before the next one writes:

1. every file `RELEASE_MANIFEST.json` lists has its recorded sha256;
2. the host's OS, architecture and glibc meet the artifact's target. The glibc
   floor is the one `uqs deploy build` recorded from the wheels' `manylinux`
   tags (`target.glibc`), none for a set of pure-Python wheels;
3. every tool the manifest's `host.tools` names is on `PATH`: the [host
   contract](#the-minimum-host-contract)'s, as preflight checks them on a push.
   A missing one is refused, naming it;
4. the interpreter is the version the artifact was built for. Without
   `--python`, a release that carries a Python runtime unpacks it into
   `.release/python` (with each file's mtime) once, checks it file by file
   against its own manifest, and uses its interpreter; a release that carries
   none uses `python3`. `--check` unpacks nothing: it names the carried version;
5. `.venv` is created in the release by `python -m venv`. The locked
   dependencies, then `uqs`, are installed from `.release/wheels` with pip
   `--no-index` and the lock's hashes required. No registry is reached, and a
   missing or altered wheel is refused rather than fetched. A failed install
   removes the `.venv` it made;
6. `deploy.env` is written when there is something to put in it. With
   `--env-file`, it loads that site file, which stays outside the release, so no
   setting is copied in. For a release built with `--kafka-home`, it then sets
   `UQF_KFK_HOME` to the release's own `.release/kafka` and puts its `lib/`
   first on `LD_LIBRARY_PATH`, as a push's `deploy.env` does.

Nothing outside the release directory is written: not the system Python, not q,
not TorQ, not `current`. The installer needs no `uv`. Switching `current` and
starting processes stay separate steps for the release process.
`.release/INSTALLED.json` records the interpreter `.venv` was made from. Once
installed, the release has `.venv/bin/uqs` and a `deploy.env`, so a target
without `launch_mode` drives it like a pushed release. The manifest's
`packaging` entry records the mode (`offline`) and the installer.

### Deployments and the default per server

Every `uqs deploy push` from this machine, deployed or failed, is recorded in
`deploy_history.json`, beside `deploy_targets.toml` (or wherever
`UQS_DEPLOY_HISTORY` points). A successful push to a server that no target names
yet registers one, `<server>-<dest's last folder>`, so it can be driven straight
away.

```bash
uqs deploy history              # newest first; * marks each server's default
uqs deploy history uat.example  # one server, or one target
uqs --target uat.example summary   # a server name: its latest successful deployment
uqs target shell                # a shell in the most recent deployment
uqs target shell uat            # ... or in a given target or server
```

- **The default per server** is its latest successful deployment. A server name,
  given wherever a target name is, means that deployment.

- **`uqs target shell`** works like `poetry shell`. It opens an interactive
  shell on the server, in `<dest>/current`, with:
  - `deploy.env` loaded;
  - the release's `uqs` first on `PATH`;
  - `UQS_TARGET` set;
  - the target's `remote_user` as the account.

  `exit` returns to your machine.

- **This machine's record.** The history is what you pushed from here. Each
  release on the server keeps its own `deploy-report.json`, and
  `uqs deploy status` reads that.

## What it does, in order

Each stage stops the deployment if it fails:

  | Stage     | What happens                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
  | ---       | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | artifact  | the archive matches its `.sha256`, and every member matches the checksum its manifest lists, with nothing missing or extra. This runs before anything reaches the server                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | preflight | ssh works and the destination is writable; uv and the release's Python (unless bundled), q (it must run a script, unless bundled), the TorQ trees and the [host contract](#the-minimum-host-contract)'s tools are there; the server's OS, architecture, glibc and Python match the artifact's target; the release is not already there; the data directory exists or `--init-data` was given; another deployment there needs `--restart`; no other deployment holds the lock                                                                                                                                                                        |
  | transfer  | `scp` into `staging/<release>/`; the archive's sha256 is checked **on the server** before it is extracted into `releases/<release>/`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | prepare   | `releases/<release>/deploy.env` (below), and a release-local `.venv` installed **offline** from the artifact's wheels: the dependencies with their locked hashes required, then `uqs` itself, by uv or, with a bundled Python, by its `-m venv` and pip. `UV_OFFLINE=1` stays set, so nothing later fetches either                                                                                                                                                                                                                                                                                                                                  |
  | kafka     | only for a release built with `--kafka-home`: `kfk.q` is loaded in `$QCMD` from the release's copy, with no broker, so a library that does not resolve on the server fails here, before any process starts                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | smoke     | `scripts/deploy_smoke.q` loads the quant library and checks known numbers, then loads the declarations of the processes the deployment starts (theirs only) against the server's `$TORQAPPHOME/database.q`, so a job whose table that schema lacks fails here, naming the job, table and schema path. It must print `DEPLOY_SMOKE_OK` and exit 0 within `--smoke-timeout`                                                                                                                                                                                                                                                                           |
  | restart   | with `--restart`, the previous release's processes stop. Their replacements take the same ports, so an upgrade has downtime: `downtime` in the report                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
  | ports     | nothing else listens on the profile's ports                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
  | start     | `uqs start --profile <profile>` from the new release                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
  | verify    | `uqs deploy verify`: within `--verify-timeout`, every process the profile resolves to, except a one-shot that exits by design (`tpreplay1`), answers `.proc.procname` with its own name over q IPC. **Every pipeline process** must pass both the library check (a forward prices correctly) and the ETL check (every transform's examples pass); other processes are checked for whichever they load                                                                                                                                                                                                                                               |
  | report    | `deploy-report.json` is written into the release **before** activation, and a failed write fails the deployment: the next upgrade reads it to know which processes to stop                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
  | activate  | `current` moves to the new release in a single rename. The previous release stays where it was                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

The tool prints a report as JSON when it finishes, and leaves it in the release
as `deploy-report.json`. The report names the release, the source revision, each
process and what it answered, the stage that failed if one did, and, separately,
how the rollback went. Any unsuccessful deployment exits 1.

## The shared HDB and a new schema

Releases share the HDB in the data directory. A release that adds a table or a
column meets older partitions without it, and a partitioned query then fails on
the first short one. Once the release's `.venv` exists, prepare runs that
release's own `uqs data hdb-check --json` against the shared HDB:

- **Missing tables or columns** are refused, naming the partitions. With
  `--fix-hdb`, prepare runs `hdb-check --fix` and checks again. The fix is
  additive: each missing table is written empty, and each missing column is
  written as its type's null. It is a thin wrapper over KX's `dbmaint.q` (it
  decides what the declared schema lacks, `dbmaint` writes the columns), checks
  the database afterwards and exits 1 if anything is still missing. It takes no
  HDB lock, is not transactional, and does not reload a running HDB.
- **A column whose type changed** is always refused, naming it. That is a
  migration, which no fill makes right.

`--dry-run` cannot run the release, because none of it is on the server yet. It
compares a listing of the server's HDB with the tables and columns
`uqs deploy build` recorded in the manifest, and says what `--fix-hdb` would
fill. Column types need q, so they are checked in prepare.

## Soaking before activation

Verification proves each process answers. It does not prove a streaming job
processes what arrives: a job whose every batch throws still answers, and still
verifies. `--soak SECONDS` waits that long after verification, and the live
check, while data flows. It then reads each started streaming job's
`stream_health` record (#832) on the server, using the release's own code:

```bash
uqs deploy push dist/uqf-<release>.tar.gz ... --profile essential --soak 120
```

A job fails the soak if it reports `failing`, or if it has written no record
since the soak began, judged on the server's clock. Either fails the deployment
at stage `soak`, before `current` moves, and the usual rollback runs.
Infrastructure processes and bounded workers are not judged: neither has
batches. The report records the soak's length, its verdict, and each job's. Make
the soak longer than the jobs' `period`, so every job has beaten at least once.

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

The same rollback runs when the push is interrupted rather than failed: Ctrl-C
in the verify wait, or an unexpected error. The report then says stage
`interrupted`, and the push exits with the interruption once the rollback is
done. A report saying `running` is written into the release before anything is
stopped or started, so a push killed outright leaves a release that prune keeps.

What `current` names, and so which release `--restart` stops, is read again once
the lock is held. A deployment that activated between this run's preflight and
its lock is seen, and is refused without `--restart`.

## Checking a server

What a server runs, without ssh-ing in:

```bash
uqs deploy status prod-a
uqs deploy status prod-a --json                             # to script against
uqs deploy status --host uqf-server --dest /opt/uqf         # a server no target declares
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
uqs deploy rollback prod-a --dry-run
uqs deploy rollback prod-a
```

It returns to the release the current one replaced, as the current release's
report records it, or to `--to RELEASE`. Under the deploy lock it stops the
current release's processes, starts the target's from the target's own release,
and moves `current` only once the target's own verifier passes. A target that
does not verify is stopped, and the current release's processes are started and
verified again, so the server ends where it began. Nothing is deleted.

A target is verified with `uqs deploy verify`, so a release from before that
command existed (#835) cannot be a rollback target: its verification fails and
the rollback is undone as above. Nor can an artifact whose manifest names no
runtime, or one this uqs does not know: `push` refuses it at stage `artifact`.
Rebuild such a release with `uqs deploy build`.

## Removing old releases

Every push leaves its release, with its own offline `.venv`, in `releases/`.
`uqs deploy prune` removes all but the newest `--keep N`, under the deploy lock:

```bash
uqs deploy prune prod-a --keep 3 --dry-run
uqs deploy prune prod-a --keep 3
```

Three releases are never removed, whatever `--keep` is, even 0:

- the one `current` names;
- the one a rollback would return to: the release `current`'s report says it
  replaced;
- any whose report still says `running`, from a push that died part-way.

It prints what stayed and why, what went, and the bytes freed. Only a directory
named like a release id is ever removed. A [bundled TorQ](#a-bundled-torq) or
[q](#a-bundled-q) runtime goes once no remaining release's `deploy.env` names
it. `--dry-run` lists the removals and removes nothing.
`uqs deploy push --keep N` prunes the same way once the new release is current,
while it still holds the lock. If that prune fails, the deployment still counts
as a success, and its report records the failure.

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

### Columns the site launcher reads

A site launcher may read a `process.csv` column TorQ does not have. One reads
`taskset_no`, the CPUs its q wrapper pins a process to (`3`, or `3:5` for CPUs 3
and 5). It finds the column by its header, so when the column is missing it
builds `{if(NR == 2) print $}` and prints an awk syntax error for every process
it starts.

Declare those columns on the target, as `process_fields = ["taskset_no"]` in
`deploy_targets.toml` or `--process-fields taskset_no`. `deploy.env` exports
them as `UQS_PROCESS_FIELDS`, and every row of the generated `process.csv`
carries each one, empty unless it is configured. Without the declaration the
columns are TorQ's own.

Set each process's value on the server:

```bash
uqs --target uat config set stp1 taskset_no 3:5
uqs --target uat start --print stp1
```

`config set` writes `python/uqs/process_overrides.csv` (see [changing a
process's config](uqs.md#changing-a-processs-config)). Every release links that
file from `shared/config/python/uqs/process_overrides.csv` when it exists, so
create it there, with just the header `procname,field,value`, before the first
`config set`. Values set without it are kept by that release only. A process
with no value keeps the column empty, and nothing assigns CPUs on its own.
`start --print` shows the start lines the launcher builds, without starting
anything.

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
Without `--live`, a backfill whose source has no credential is still refused
unless run with `--fixture`, so a missing credential cannot quietly fill a
server's tables with fixture rows. If `shared/config/secrets.env` exists, it
sources that too (see [credentials and
ODBC](sidecar-bundles.md#credentials-and-odbc)). See [the environment
reference](../reference/environment.md).

The demo `gateway_users.csv` is never shipped. Without it the gateway has only
the starter pack's logins. To give it ordinary users, put your own file at
`/opt/uqf/shared/config/scripts/torqconfig/permissions/gateway_users.csv`, and
every release links it in.

## A server on kdb+ 4.0

kdb+ 4.0 has no nested working contexts (`\d .a.b`). This tree opens none (no
`\d` names more than one level, #1411), so the build mostly has nothing to
rewrite; it still converts a sidecar bundle's own nested declarations. Build the
release for it, and only the artifact changes - never the checkout (#861):

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

`uqs start` on the server recognises the conversion from the release itself, not
from the runtime's `q_tree` (#936). It reads `RELEASE_MANIFEST.json` and starts
the release when all of these hold:

- the target and the conversion record name the same kdb+;
- every q file is on disk with the sha256 the manifest records;
- the server's q is at least that version.

The packaged code is loaded as shipped and is never converted again. Evidence
that doesn't hold is refused by name, and the tree as written is still refused
on 4.0.

For a server whose q is older than the release's target, push refuses at
preflight. If that server's q can run the release anyway, deploy it with an
explicit reason:

```bash
uqs deploy push dist/uqf-<release>.tar.gz ... \
  --accept-converted-release "4.0 release, q 3.6 smoke-tested by the desk"
```

That means "start this verified, already-converted release", not "skip the
checks":

- After the smoke test passes on the server's q, push writes
  `CONVERTED_RELEASE_OVERRIDE.json` into the release. It records who asked, why,
  the release's target, the server's q and the passing smoke test. The report
  keeps it as an entry in `exceptions` ([named
  overrides](#requirements-and-named-overrides)).
- `uqs start` honours it only for that deployment attempt, through a one-time
  token, and still checks the conversion record and every file's integrity.
- Port checks, verification and rollback run as usual.
- The flag is refused for a release built as written.

A failed remote step's diagnostic keeps the command's own output and drops ssh's
notices, so a startup refusal isn't hidden behind a host-key or key-exchange
warning.

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
- Migrations: a column whose type changed is refused, never converted.
- Frontend builds on the server: the frontend ships prebuilt
  (`uqs deploy build --frontend`), and nothing starts it.
- Zero-downtime upgrades. Each upgrade's report records its `downtime`: when the
  previous processes began to stop, when the new ones verified, and the seconds
  between. The design for removing it is
  [`architecture/zero-downtime.md`](../architecture/zero-downtime.md).
- A single build-and-deploy command, or the Kafka and Databento feed handlers,
  which still start through `uv run`.

## A deployment that died holding the lock

A push, a rollback or a prune holds `deploy.lock/` while it runs. It records
what it is doing, its release, and the user, host and pid that started it. It
also keeps a heartbeat on the server's clock, refreshed between stages. One
killed mid-run leaves the lock behind. The next run refuses it, naming the
holder and how long ago it last beat. Preflight and `uqs deploy status` show the
same.

`--break-lock` on `push` or `rollback` removes the lock, but only once its
holder is gone. It is refused while either of these holds:

- **The heartbeat is fresh.** The holder records how long it may go between
  beats, from its own longest step: `--command-timeout`, by default, plus a
  minute.
- **The holder is alive here.** Its pid is still running, and this is the
  machine it ran on.

When it does break a lock, it says what it broke and why, then takes the lock
itself. A lock written before this recorded no limit, so it is held to the
current run's limit instead.
