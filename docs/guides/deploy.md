# Deploying to a server

This page shows how to build a release, put it on a Linux server, check it there
and roll it back. Deploying is two commands, run separately:

- `uqs deploy build` builds a versioned release artifact. It needs no SSH access
  or credentials. One artifact can go to any number of servers.
- `uqs deploy push` puts that artifact on a server, starts a `uqs` profile there
  and checks that every process answers.

> **Not yet run end to end.** The tools are unit tested with ssh, uv and pip
> replaced, and the run against a real server needs a disposable host. Until
> that run has passed somewhere, treat the commands below as the design.

## What the server needs

`push` installs none of these. It checks each one before it changes anything.

- ssh access from where you run `push`, working with your own `~/.ssh/config`
  and known hosts. A missing key or an unknown host key fails without a prompt.
- `uv`, with the Python the artifact was built for (3.14 by default).
- A licensed q. Without `--q-home` and `--q-cmd`, `push` uses the deploying
  account's `$QHOME` and `$QCMD` on the server, or `q` on its PATH. `--q-cmd` is
  an executable name or an absolute path, so `rlwrap q` is refused.
- TorQ at `--torq-home`, holding `torq.q` and `torq.sh`, and the starter pack at
  `--torq-app-home`, holding `database.q` and `appconfig/process.csv`.
- The tools in the minimum host contract below.
- For live sources, their credentials and, for ODBC, a driver. See [credentials
  and ODBC](sidecar-bundles.md#credentials-and-odbc).

The server needs no access to PyPI. To ship TorQ, q or Python with the push, see
[bundled components](#bundled-components).

### The minimum host contract

A server must provide these even when TorQ, q and Python are all bundled:

- Linux on the artifact's architecture (`x86_64` or `aarch64`).
- glibc at or above the release's floor (`target.glibc` in the manifest).
- `bash`, `tar` with `gzip`, `timeout`, `find`, `sed`, `awk`, `grep`, `nohup`,
  `mktemp`, `chmod`, `ln`, `readlink`, `date`, `id`, `head`, `cut` and `tr`.
- `python3`, 3.8 or later, standard library only, for the push's own checks.
- `envsubst` (gettext), `pgrep` (procps), `lsof`, and a `hostname` that takes
  `-I` and `-A`, for `torq.sh`.
- `sshd` with `scp`. With `--remote-user`, a `sudo -n -iu <user>` rule.
- A writable `--dest`, with three times the artifact plus 1 GiB free (`df -Pk`)
  on its filesystem and the data directory's.

## Building a release

```bash
uqs deploy build
```

This writes the artifact `dist/uqf-<release>.tar.gz`, its `.sha256` and its
`.manifest.json`. `--output DIR` puts them elsewhere. The artifact holds the
tracked source tree and a wheel of `uqs` and of every pinned dependency. It
never ships TorQ, the starter pack, `.env`, keys, licences or runtime data.

- `--arch aarch64` or `--python 3.14` builds for another target. The default is
  Linux on x86_64 with the Python `requires-python` names.
- `--bundle <folder>`, repeatable, adds a [sidecar bundle](sidecar-bundles.md).
- `--kfk-home DIR` and `--kafka-connector` add Kafka. See [Kafka in a
  release](../services/kafka.md#in-a-release).
- `--frontend DIST` adds the app that `npm --prefix web run build` wrote,
  usually `web/dist`, and the backend that serves it. Nothing starts it. Run
  `.venv/bin/uvicorn --factory uqf_frontend.app:create_app --port 8000` from the
  release, with `deploy.env` loaded.

Uncommitted changes are refused unless you pass
`--override allow-dirty --reason TEXT`, to `build` and again to `push`. Building
needs `uv`, and network access to fetch the wheels.

A tag `v*` publishes two artifacts in CI: the plain one, and one converted for
kdb+ 4.0. `push` takes a tag (`uqs deploy push v1.4.0 ...`) or an artifact's URL
in place of a file. A tag is looked up in `--release-repo OWNER/NAME`, or else
in this checkout's `origin`. A tag CI built carries both artifacts, so push that
release by URL: the refusal for its tag names both. For a private repository,
set `GH_TOKEN` or `GITHUB_TOKEN`.

## A first deployment

Show the plan first. `--dry-run` checks the artifact, runs the read-only
preflight on the server, prints every step and changes nothing:

```bash
uqs deploy push dist/uqf-<release>.tar.gz \
  --ssh uqf-server --dest /opt/uqf \
  --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \
  --q-cmd /opt/kx/bin/q --q-home /opt/kx \
  --profile essential --init-data --dry-run
```

Then run the same command without `--dry-run`. `--init-data` lets `push` create
the runtime data directory the first time. Without it, a missing data directory
is refused, so a mistyped `--dest` cannot start an empty HDB. A destination
takes each release once. To redeploy, build a new artifact. An upgrade needs
`--restart`, and has downtime while the processes are replaced.

## Targets in a file

Declare each server once in `deploy_targets.toml` at the repository root, which
is gitignored, or in the file `UQS_DEPLOY_TARGETS` names:

```toml
[targets.prod-a]
ssh = "deploy@prod-a"
remote_user = "svc"
dest = "/srv/uqf"
runtime = "crypto"
profile = "essential"
torq_home = "/opt/torq"
live = true
live_check = ["deals_db"]
```

Then name targets in place of the flags, as in
`uqs deploy push dist/uqf-<release>.tar.gz prod-a prod-b`. `push` deploys them
in order and stops at the first failure. Each key is a `push` option, spelt as
its parameter (`torq_home` for `--torq-home`). `runtime` must match the runtime
the artifact was built for.

Put settings that several targets share in `[defaults]` or in an
`[environments.<name>]` table, which a target picks with
`environment = "<name>"`. An environment overrides `[defaults]`, a target
overrides its environment, and a flag overrides all three. `uqs target list`
shows each target with its layers merged.

### Changing targets

Edit the file by hand, or change it through `uqs`:

```bash
uqs target add prd --ssh svc@prd.example --dest /srv/uqf --label tier=prod --reach
uqs target set prd ssh=svc@prd2.example connect_timeout=20
uqs target rename prd production
uqs target revert last             # or a change's number from the log
```

`uqs` checks the whole file before it writes, and logs each change in
`deploy_targets.log.jsonl` beside it. `--reach` saves nothing when the server
cannot be reached. `--dry-run` (`-n`) shows the diff and writes nothing.
`uqs target log` lists the changes. The config files a push wrote to a server
have their own history under `uqs target config`.

### Inspection-only targets

Set `inspection = true` on a target, its environment or `[defaults]` to read a
server from this machine without changing it. Every `uqs` command that would
change the target is then refused, and `--yes` does not get past it. That covers
`uqs --target NAME start`, `uqs target shell`, and `uqs deploy push`, `rollback`
and `prune`. Read-only commands and every `--dry-run` still run. The
[frontend](../../python/uqf_frontend/README.md#inspection-mode) reads the same
key. This is a policy of these tools on this machine. Anyone who can ssh to the
server can still change it.

### Requirements and named overrides

A target can pin the artifact with `expected_sha256 = "<sha256>"` and the
server's q with `q_version = ">=4.1"` (`"4.1"` means exactly 4.1). A local
artifact also needs its `.sha256` beside it, or an `expected_sha256`. Relax one
requirement on the command line with `--override NAME --reason TEXT`:
`accept-digest` for `expected_sha256`, `accept-q-version` for `q_version`, and
`allow-unpinned` for the `.sha256` file. `uqs override list` names every
override. The plan, the report and the deploy history record each one used.

## Checking a server

```bash
uqs deploy status prod-a
uqs deploy status                                           # the most recent deployment
uqs deploy status --ssh uqf-server --dest /opt/uqf          # a server no target declares
```

It reports the current release, the last deployment (failed or not), whether the
current processes answer, the streaming jobs whose batches are failing, and who
holds the deploy lock. It is read-only, so it is safe during a push. It exits 1
when the health check fails. `--no-health` skips that check.

## Rolling back

```bash
uqs deploy rollback prod-a --dry-run
uqs deploy rollback prod-a
```

This returns the server to the release the current one replaced, or to
`--release RELEASE`. It stops the current release's processes, starts the
earlier release's, and moves `current` once they verify. If they do not verify,
it starts and verifies the current release again. Nothing is deleted. If you
interrupt a rollback, read its log. When it says the current release was not
restored, neither release may be running.

## Removing old releases

Every push leaves its release, with its own `.venv`, in `releases/`. `prune`
removes all but the newest `--keep N`, which defaults to the target's `keep`. It
names each release it would remove and asks first:

```bash
uqs deploy prune prod-a --keep 3 --dry-run
uqs deploy prune prod-a --yes             # from a script, to the target's keep
```

Three releases stay whatever `--keep` is: the one `current` names, the one a
rollback would return to, and any whose push died part-way.
`uqs deploy push --keep N` prunes the same way after it activates a release. A
target that sets no `keep` never prunes, and `push` warns about it.

## What a push does

Each stage stops the deployment if it fails:

1. Artifact. The archive and every member match their checksums.
2. Preflight. The server has what the artifact needs, and matches its target.
3. Transfer. The archive is extracted into `releases/<release>/`.
4. Prepare. `deploy.env` is written, a release-local `.venv` is installed
   offline from the artifact's wheels, and the shared HDB is checked.
5. Smoke. The library and the processes' declarations load in the server's q.
6. Restart, with `--restart`. The previous release's processes stop.
7. Start. Nothing else may listen on the profile's ports. Then
   `uqs start --profile <profile>` runs from the new release.
8. Verify. `uqs deploy check` requires every process of the profile to answer
   within `--verify-timeout`, and every pipeline process to pass its checks.
9. Activate. `deploy-report.json` is written into the release, and `current`
   moves to it. The previous release stays.

An unsuccessful deployment exits 1. With `--json`, stdout is one document and
everything else goes to stderr. `--soak SECONDS` waits after verification and
fails the deployment, before `current` moves, if a streaming job reports
`failing` or wrote no health record. Make it longer than the jobs' `period`.

### When a push fails

A failure before `start` leaves only a new directory under `releases/`, kept for
inspection. From `start` on, the new release's processes are stopped. If
`--restart` stopped the previous ones, they are started again and verified.
`current` still names the previous release, and the report's `rollback` field
says what was done. Ctrl-C is handled the same way.

A push killed outright leaves `deploy.lock/` behind, and the next run stops,
naming the holder. `--override break-lock --reason TEXT` on `push` or `rollback`
removes the lock once its holder is gone.

Releases share one HDB. `push` refuses a release whose tables or columns are
missing from older partitions. `--override fix-hdb --reason TEXT` writes each
missing table empty and each missing column as its type's null. The fill takes
no HDB lock, is not transactional, and does not reload a running HDB. A column
whose type changed is always refused.

## Driving a deployed release from your machine

`uqs --target NAME <command>` runs the release's own `uqs` on that server, as
`remote_user` when the target sets one:

```bash
uqs target check uat                       # ssh works; names the running release
uqs --target uat status
uqs --target uat --yes query rdb1 'select [20] from piggybank_general_ledger'
uqs --target uat start piggybankgeneralledger1   # asks first; --yes skips the question
```

A command that can change the server asks first and names the target. So do
`query` and `status -i`. Without a terminal they need `--yes`. Read-only
commands run directly. `uqs target shell uat` opens a shell in `<dest>/current`
with `deploy.env` loaded. To query through an ssh tunnel instead, see [a
deployed target, from here](connecting.md#a-deployed-target-from-here).

`uqs deploy history` lists every `push` made from this machine. A successful
push to a server no target names registers one, unless you pass `--no-register`.
A server name, given wherever a target name is, means its latest successful
deployment.

### A release that push did not install

A tree copied by hand with `scp` or `rsync` has no `current` link.
`uqs target adopt` reports what driving it needs. `--apply` then moves the tree
under `<dest>/releases/`, links `current` and saves the target. `prune` never
removes an adopted release.

```bash
uqs target adopt lvc --ssh svc@lvc.example --dest /srv/uqf                # a dry run (-n says so)
```

A `uqs deploy build` artifact carries its own installer. Unpack the artifact
into its own release directory and run the installer from inside it. It runs on
`python3` 3.8 or later, creates `.venv` offline and writes nothing outside the
release. Switching `current` and starting processes stay yours.

```bash
python3 scripts/deploy/install_release.py --check              # verify only; writes nothing
python3 scripts/deploy/install_release.py --env-file /opt/efx/uat/uat-runtime.env
python3 scripts/deploy/install_release.py --python /opt/python/3.14/bin/python3   # the site's instead
```

## Bundled components

q, TorQ and Python each have a mode: `existing`, the default, or `bundled`. A
bundled component is a runtime artifact that you build once and many releases
share:

```bash
uqs deploy component build torq      # dist/torq-runtime-<version>-<id>.tar.gz
uqs deploy component build q         # dist/q-runtime-peachq-<commit>-<os>-<arch>.tar.gz
uqs deploy component build python --arch x86_64 --python 3.14
uqs deploy push dist/uqf-<release>.tar.gz --ssh uqf-server --dest /opt/uqf \
  --profile essential --torq-mode bundled --torq-component dist/torq-runtime-<...>.tar.gz \
  --python-mode bundled --python-component dist/python-runtime-3.14.<n>-linux-x86_64.tar.gz
```

- A runtime's `.sha256` must sit beside it. `torq_component_sha256`,
  `q_component_sha256` and `python_component_sha256` pin one in a target.
- A runtime is installed once, under `<dest>/shared/runtimes/`, and is never
  overwritten. A rollback returns to a release on the runtimes it was deployed
  with. `prune` removes a runtime once no remaining release names it.
- A bundled q (`--q-mode bundled --q-component FILE`) is the pinned PeachQ, for
  a release built with `uqs --runtime peachq deploy build`. A release built for
  a runtime declared on KDB-X is refused it. KX's q is not bundled.
- A bundled q or Python must be built for the release's OS and architecture, and
  a Python for its `major.minor` too. Build the q runtime on such a host.
- `uqs deploy build --python-component FILE` makes the release carry the Python,
  so the installer above needs no Python 3.14.

## Deploying as a service user

When the account you log in as does not own uqf, add `--remote-user svc` to the
push. ssh and scp still log in as the `--ssh` user. Every deployment step then
runs as `svc` through `sudo -n -iu svc`, so q, uv, Python and TorQ are checked
in `svc`'s login environment. The login user needs a sudo rule that runs
commands as `svc` without a password, for example
`deploy ALL=(svc) NOPASSWD: ALL` in `/etc/sudoers.d/uqf`. Keep `--dest` outside
the existing TorQ installation, and owned by `svc`.

## A site-managed TorQ launcher

When the site keeps its own TorQ launcher apart from the core, add
`--torq-launcher /opt/site/torq/bin/torq.sh` to the push. `--torq-home` still
names the core, and then needs only `torq.q`. The launcher must be an absolute
path to an executable file. `TORQDATAHOME` is set to the runtime data directory.
Pass anything else the launcher reads as a repeatable
`--launcher-env NAME=VALUE`, such as `--launcher-env KDBDB_ORG=uqf`. Nothing in
the TorQ installation changes.

### Columns the site launcher reads

A site launcher may read a `process.csv` column TorQ does not have, such as
`taskset_no`, the CPUs a process is pinned to. Declare such columns on the
target, as `process_fields = ["taskset_no"]` or `--process-fields taskset_no`.
Then set each process's value on the server:

```bash
uqs --target uat config set stp1 taskset_no=3:5
uqs --target uat config show stp1     # taskset_no 3:5
```

Before the first `config set`, create
`shared/config/python/uqs/process_overrides.csv` on the server with just the
header `procname,field,value`. Without it, the values are kept by that release
only. See [changing a process's
config](runtimes-and-profiles.md#changing-a-processs-config).

## A server on kdb+ 4.0

kdb+ 4.0 has no nested working contexts (`\d .a.b`). Build the release for it
with `uqs deploy build --q-version 4.0`. The build converts the artifact's copy
of the q code and leaves the checkout alone. `push` refuses an unconverted
release on a server older than 5.0, and a server whose q is older than the
release's target. If that q can run the release anyway, add
`--override accept-converted-release --reason TEXT`. The smoke test, port
checks, verification and rollback still run.

## On the server

```text
/opt/uqf/
  current -> releases/20261007T133500Z-0123456789ab
  releases/<release>/       one per deployment, each with its .venv and deploy.env
  shared/data/uqs/          the runtime data: HDB, tickerplant logs, status
  shared/config/            files you supply, linked into every release
  deploy.lock/              held while a deployment runs
```

Source `deploy.env` before every `uqs` command you run by hand on the server:
`cd /opt/uqf/current && source ./deploy.env && .venv/bin/uqs status`. The file
holds no secrets, only where things are. It sources `shared/config/secrets.env`
when that file exists. See [the environment
reference](../reference/environment.md). The gateway has only the starter pack's
logins. To add users, put your own file at
`/opt/uqf/shared/config/scripts/torqconfig/permissions/gateway_users.csv`.

## Not in v1

- Installing q, TorQ or licences, and copying data.
- Migrations. A column whose type changed is refused.
- Building or starting the frontend on the server.
- Zero-downtime upgrades. Each report records the upgrade's `downtime`. See
  [`architecture/zero-downtime.md`](../architecture/zero-downtime.md).
- A single build-and-deploy command, or the Kafka and Databento feed handlers,
  which start through `uv run`.
