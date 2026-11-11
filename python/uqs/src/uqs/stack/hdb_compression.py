"""What `uqs data compress --report` reads: the HDB's compression, now (#2149).

Nothing here computes a ratio or walks a partition. scripts/hdb/
hdb_compression_report.q loads TorQ's own `.cmp` and sort.csv reader and uqf's
wrapper over them (scripts/processes/torq_compress.q), and prints
`.qtorq.compress.report` - per table and column the algorithm, block size and
ratio `-21!` reads, the largest uncompressed partitions, and the partitions not
in the order, or without the attribute, that sort.csv declares. This module
picks the files that script is pointed at and parses what it prints.

WHICH sort.csv. The one the wdb sorts by at end of day: TorQ's
`.proc.getconfigfile` rule, the first config layer that has the file
(stack/source_settings.py). The plant schema (src/etl/plant_tables.q) declares
only `g#sym`, an in-memory index the tickerplant and RDB keep and a partition on
disk does not; the attribute a partition carries is the one sort.csv names.
A caller may name another (`--sort-csv`), or none: sorting is then reported as
not checked (`sort_checked` false, `unsorted` null), never as found valid.

ON A TARGET, WITHOUT UQF THERE (#2219). `uqs --target T data compress --report
--hdb DIR` reads a directory on a server that has q and an HDB but need not
have uqf - an existing TorQ deployment. The same script runs there, by the
transport stack/hdb_remote.py already uses for hdb_alter.q: one ssh session
(BatchMode, the target's own ssh, connect timeout and account) runs a bash
script on stdin that writes the report script, torq_compress.q and TorQ's two
files it loads (from this checkout's TorQ, the ones a local report loads) into
a temporary directory, runs the server's q on them, and removes the directory
on every exit, a failure's included. Nothing is installed, no release or
deploy.env is read, and DIR and the sort.csv are paths on the server, checked
there - never against this machine's filesystem. The q is the target's
`q_cmd` (with its `q_home`), else the account's $QCMD, else `q` on its PATH,
else $QHOME's own; none is a named refusal.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from uqs.interpreter import q_interpreter
from uqs.paths import UqsError, UqsPaths
from uqs.stack import hdb_remote, source_settings
from uqs.stack.env import with_interpreter

REPORT_SCRIPT = Path("hdb") / "hdb_compression_report.q"
#: What the report script loads besides itself, relative to the repository, and
#: TorQ's own files relative to its home: what a remote run sends.
WRAPPER = Path("scripts") / "processes" / "torq_compress.q"
TORQ_FILES = (Path("code") / "common" / "compress.q", Path("code") / "common" / "dbwriteutils.q")
SORT_FILE = "sort.csv"
#: The script's sort.csv argument when there is none: sorting is not checked.
NO_SORT = "-"
DONE = "DONE"
PREFIX = "report|"
ERROR = "error|"
TIMEOUT_S = 3600
#: The remote script's exit when the HDB argument is not a readable directory.
NO_HDB = 96

Runner = Callable[..., subprocess.CompletedProcess]


def sort_csv(paths: UqsPaths) -> Path:
    """The sort.csv the stack's wdb reads; refused when no layer has one."""
    found = source_settings.selected(paths, SORT_FILE)
    if found is None:
        searched = ", ".join(str(p) for p in source_settings.layers(paths, SORT_FILE))
        raise UqsError(f"no {SORT_FILE} in any config layer - looked in {searched}")
    return found


def report(
    paths: UqsPaths, top: int, *, hdb: Path | None = None, sort: Path | None = None
) -> dict[str, Any]:
    """`.qtorq.compress.report` over the HDB, as a dict of three lists of rows -
    columns, largest_uncompressed and unsorted (None when sorting was not
    checked) - and sort_checked and sort_csv. `sort` is the sort.csv to judge
    by, None for none. Refused when there is no q or the report does not finish."""
    env = with_interpreter(paths)
    q = q_interpreter(env)
    if q is None:
        raise UqsError("the compression report needs q: no interpreter found")
    root = hdb or paths.hdb_dir
    argv = [str(root), NO_SORT if sort is None else str(sort), str(top), str(paths.torqhome)]
    try:
        r = subprocess.run(
            [str(q), str(paths.scripts_dir / REPORT_SCRIPT), *argv],
            capture_output=True,
            text=True,
            check=False,
            cwd=paths.repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise UqsError(f"the compression report did not finish within {TIMEOUT_S}s") from None
    return parse(r, "")


def parse(r: subprocess.CompletedProcess, where: str) -> dict[str, Any]:
    """The report the script printed, or its refusal (`error|<why>`) as an
    error; `where` says which machine, for a remote run."""
    lines = r.stdout.splitlines()
    errors = [ln[len(ERROR) :] for ln in lines if ln.startswith(ERROR)]
    if errors:
        raise UqsError(f"the compression report{where} refused: {errors[0]}")
    body = [ln[len(PREFIX) :] for ln in lines if ln.startswith(PREFIX)]
    if DONE not in lines or len(body) != 1:
        said = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise UqsError(f"the compression report{where} did not finish: {' '.join(said)}")
    doc = json.loads(body[0])
    if not doc.get("sort_checked"):
        doc["unsorted"] = None  # not judged: never an empty list, which reads as all sorted
    return doc


@dataclass(frozen=True)
class SshHost:
    """How to reach a target's shell, from its declaration (deploy/control.py's
    Remote, and the target's q_cmd and q_home) - plain values, so this layer
    needs nothing of deploy/."""

    name: str
    host: str
    remote_user: str | None = None
    connect_timeout: int = hdb_remote.CONNECT_TIMEOUT_S
    q_cmd: str | None = None
    q_home: str | None = None

    def shell(self) -> str:
        """The command ssh runs: a login shell reading the script on stdin, as
        the target's account when it names one - as `uqs --target` runs it."""
        if self.remote_user:
            return f"sudo -n -iu {shlex.quote(self.remote_user)} bash -s"
        return "bash -l -s"

    def argv(self) -> list[str]:
        opts = ["-o", "BatchMode=yes", "-o", f"ConnectTimeout={self.connect_timeout}"]
        return ["ssh", *opts, self.host, self.shell()]


def remote_script(paths: UqsPaths, hdb: str, top: int, sort: str | None, ssh: SshHost) -> str:
    """The bash script the target runs: write the helper into a temporary
    directory, check the HDB and find q there, run the report, and remove the
    directory on any exit. Every value is quoted with shlex."""
    files = {
        f"scripts/{REPORT_SCRIPT.as_posix()}": (paths.scripts_dir / REPORT_SCRIPT).read_bytes(),
        WRAPPER.as_posix(): (paths.repo_root / WRAPPER).read_bytes(),
        **{f"torq/{f.as_posix()}": (paths.torqhome / f).read_bytes() for f in TORQ_FILES},
    }
    q_hdb = shlex.quote(hdb)
    body = [
        "set -u",
        'tmp=$(mktemp -d "${TMPDIR:-/tmp}/uqf-compress-report.XXXXXX") || exit '
        + str(hdb_remote.NO_TEMP),
        "trap 'rm -rf \"$tmp\"' EXIT",
        "trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM",
        'mkdir -p "$tmp/scripts/hdb" "$tmp/scripts/processes" "$tmp/torq/code/common"',
        *(hdb_remote.heredoc(target, data) for target, data in files.items()),
        f"hdb={q_hdb}",
        'if [ ! -d "$hdb" ]; then',
        '  echo "no directory $hdb on $(hostname)" >&2; exit ' + str(NO_HDB),
        'elif [ ! -r "$hdb" ] || [ ! -x "$hdb" ]; then',
        '  echo "$hdb on $(hostname) is not readable by $(id -un)" >&2; exit ' + str(NO_HDB),
        "fi",
        # absolute before the cd into $tmp: a relative path is the account's home's
        'hdb=$(cd "$hdb" && pwd -P)',
        f"sort={shlex.quote(sort or NO_SORT)}",
        "case $sort in /*|" + NO_SORT + ') ;; *) sort="$PWD/$sort" ;; esac',
        f"q_cmd={shlex.quote(ssh.q_cmd or '')}",
        '[ -n "$q_cmd" ] || q_cmd="${QCMD:-}"',
        'if [ -z "$q_cmd" ] && command -v q >/dev/null 2>&1; then q_cmd=q; fi',
        'if [ -z "$q_cmd" ] && [ -n "${QHOME:-}" ]; then',
        "  for a in l64 l64arm m64 m64arm; do",
        '    if [ -x "$QHOME/$a/q" ]; then q_cmd="$QHOME/$a/q"; break; fi',
        "  done",
        "fi",
        'if [ -z "$q_cmd" ] || ! command -v "${q_cmd%% *}" >/dev/null 2>&1; then',
        '  echo "no q for $(id -un) on $(hostname): ${q_cmd:-none} is not runnable - set the '
        "target's q_cmd, or QCMD or PATH in the account's login profile\" >&2; exit "
        + str(hdb_remote.NO_Q),
        "fi",
        *([f"export QHOME={shlex.quote(ssh.q_home)}"] if ssh.q_home else []),
        'cd "$tmp" || exit ' + str(hdb_remote.NO_TEMP),
        f'$q_cmd scripts/{REPORT_SCRIPT.as_posix()} "$hdb" "$sort" {int(top)} "$tmp/torq" '
        "< /dev/null",
    ]
    return "\n".join(body) + "\n"


def remote_report(
    paths: UqsPaths,
    ssh: SshHost,
    hdb: str,
    top: int,
    *,
    sort: str | None = None,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    """report() of directory `hdb` on target `ssh`, by its own q, over ssh;
    `sort` a sort.csv on the target, or None for sorting not checked."""
    try:
        r = runner(
            ssh.argv(),
            input=remote_script(paths, hdb, top, sort, ssh),
            capture_output=True,
            text=True,
            check=False,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise UqsError(
            f"the compression report on target {ssh.name} did not finish within {TIMEOUT_S}s"
        ) from None
    except OSError as exc:
        raise UqsError(f"could not run ssh: {exc}") from None
    said = " ".join((r.stderr or "").strip().splitlines()[-2:])
    if r.returncode == hdb_remote.SSH_FAILED:
        raise UqsError(
            f"cannot reach target {ssh.name} ({ssh.host}) over ssh: {said or 'ssh exited 255'} - "
            f"check that `ssh -o BatchMode=yes {ssh.host} true` works: a key loaded, the host "
            "key known, the host up. Nothing was read."
        )
    if r.returncode in (hdb_remote.NO_TEMP, hdb_remote.NO_Q, NO_HDB):
        raise UqsError(f"target {ssh.name} cannot run the compression report: {said}")
    return parse(r, f" on target {ssh.name}")
