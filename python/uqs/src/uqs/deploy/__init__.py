"""Deploying uqf: `uqs deploy build`, `uqs deploy push` and `uqs deploy
verify` (#773, #778, #835). Put a uqf release on a Linux server that already
runs TorQ, and prove it works before calling it deployed.

    uqs deploy build
    uqs deploy push dist/uqf-<release>.tar.gz \\
        --host uqf-server --dest /opt/uqf \\
        --torq-home /opt/torq --torq-app-home /opt/torq-finance-starter-pack \\
        --qcmd /opt/kx/bin/q --qhome /opt/kx --profile essential --dry-run

THE MODULES, in the order a deployment meets them: artifact (the format,
read back whole), payload and build (making one), config (what push is
asked to do), selection (sidecar jobs), remote (ssh and scp), server
(preflight and the lock), stages (everything that changes the server),
driver (the run and the rollback) and verify (run on the server).

BUILDING IS NOT DEPLOYING. `uqs deploy build` makes the artifact once -
the tree, the uqs wheel and every pinned dependency's wheel, and a manifest
of the target and every member's sha256 - and this deploys that same file to
as many servers as it is pointed at. The artifact is checked whole here,
against its .sha256 and its manifest, before anything touches the server.

THE STAGES, in order, each stopping the deployment when it fails:

  artifact   the archive matches its .sha256, and every member its manifest
             hash, with nothing missing or extra.
  preflight  ssh works; the destination is writable; uv and the release's
             Python are there; the OS, architecture and Python match what the
             artifact was built for; q runs and is licensed; the TorQ and starter-pack trees
             hold what torq.sh needs; envsubst and rlwrap are on PATH; the
             runtime data directory exists (or --init-data was given); and a
             deployment already there is only replaced with --restart.
  transfer   scp into a staging directory of its own; the checksum is checked
             ON THE SERVER before anything is extracted.
  prepare    releases/<id>/ gets a release-local .venv installed OFFLINE
             from the artifact's own wheels - hashes required, no index, no
             download - and a deploy.env naming the external TorQ, q and the
             stable data directory (UQS_DATA_ROOT), with UV_OFFLINE=1 so
             nothing later fetches either. The ports this profile listens on
             are checked free.
  smoke      scripts/deploy_smoke.q: the quant library loads and computes known
             numbers, then the started processes' declarations load - theirs
             alone - against the server's starter-pack schema (#902). Needs
             its success marker AND exit 0, within a timeout.
  restart    with --restart, the previous deployment's processes stop - their
             replacements take the same ports, so v1 has downtime.
  start      `uqs start --profile <profile>` from the new release.
  verify     `uqs deploy verify`: every process the profile promises, bar a
             one-shot that exits by design (tpreplay1), answers over q IPC
             as itself, and the library and ETL checks pass where they are
             loaded, before a deadline.
  activate   `current` moves to the new release in one rename. The previous
             release stays where it was.

SIDECAR JOBS (#800). An artifact built with `uqs deploy build --bundle`
carries each bundle's jobs, and its manifest names them. A deployment starts
none of them unless asked: --jobs piggy_spread,mw_quotes starts those
streaming jobs, and every uqf process each one needs (the closure recorded at
build time), beside the profile - the profile is never assumed to include
them. A bounded worker is installed and never run: naming one in --jobs is
refused, and a backfill is its own deliberate `uqs backfill` afterwards. The
plan (--dry-run) prints the resolved process list; verification checks those
processes and that the tickerplant carries every bundle table.

--live writes UQS_REQUIRE_LIVE_SOURCES=1 into deploy.env, so a source with no
credential is REFUSED rather than read as its fixture - no synthetic row, and
no coverage recorded for it - and verification proves the pipeline processes
saw the setting. Credentials stay on the server, outside the artifact: in
each source's environment variable, or a sources.csv the server keeps (see
docs/guides/sidecar-bundles.md).

A failure after the previous deployment was stopped stops the new one's
processes and starts the previous ones again; `current` is only ever moved
by activate, so it still names the previous release. The report says what
failed, at which stage, and - separately - whether the rollback worked.

Layout under --dest:

    releases/<id>/        one per deployment, kept; <id> is UTC time + revision
    current -> releases/<id>
    shared/data/          the runtime data (HDB, logs, status), unless --data-dir
    shared/config/        operator-supplied files linked into each release
    staging/<id>/         the archive while it is checked; removed after
    deploy.lock/          held while a deployment runs; serialises them

WHICH q (#782). --qhome and --qcmd are optional and resolved ON THE SERVER,
in the environment of the account that deploys - the --remote-user's login
environment, or the ssh login's - never from this machine's:

    QHOME   --qhome, else that account's $QHOME, else refused (pass --qhome)
    QCMD    --qcmd, else that account's $QCMD, else `q` on that account's PATH

QCMD is an executable name or an absolute path, never a command with
arguments. Preflight resolves both, runs a script with them, and writes them
into the release's deploy.env, so the smoke test, start, verification and
any later rollback of this release use exactly what was checked; a rollback
to the previous release uses that release's own deploy.env. Only those two
values are read from the remote environment.

Nothing here disables host-key checking or reads a secret: ssh and scp run
with the operator's own configuration, in batch mode so a missing key fails
rather than prompts.

A SERVICE USER (#780). With --remote-user svc, ssh and scp still log in as
the --host user, but every deployment step runs as svc through
`sudo -n -iu svc bash -s` - non-interactive, so a sudo that would ask for a
password fails instead of hanging, and a login shell, so q, uv, TorQ and the
ports are checked in svc's own environment. Settings cross sudo inside the
script, never through the login user's environment. scp cannot write as svc,
so the archive lands in a private mktemp directory of the login user's, and
one narrowly scoped sudo call - svc writing a new file from stdin - copies it
into svc's staging; nothing is made world-readable or re-owned. The upload
directory is removed whether the deployment succeeds or fails. Preflight
proves the sudo rule and the effective identity before anything changes.
"""
