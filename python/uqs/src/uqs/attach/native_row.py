"""What registering a publisher with the stack's own TorQ writes (#1845): its
process.csv row and the launcher that row starts it through - both DERIVED,
never typed.

THE ROW is this checkout's own row for the publisher, as `uqs start` would
start it (stack/procs.effective_process_rows: the job's declaration, its
locked port offset and this process's own process_overrides.csv entries), with
four changes for a file the SITE's torq.sh reads:

  host          localhost: the publisher runs on this machine, which is the
                stack's (a registry on another machine is refused, native.py)
  port          {KDBBASEPORT}+offset - the offset uqs gives it (plan.publisher_port),
                so a native start and a uqs start use one port
  startwithall  1 or 0: the attachment's saved startup policy
  qcmd          the launcher below

and every reference to this tree (${UQF_SCRIPTS}, ${UQF_ROOT}, ...) written out
as its path: torq.sh substitutes a row's variables from the SITE's environment
(`envsubst`), where this tree's are not set. The site's own (${TORQAPPHOME},
${KDBCODE}) are left for it to substitute. A column the site has and this tree
does not (`taskset_no`) is left empty, as an unset site field is (#1191).

THE LAUNCHER is the narrowest per-process configuration a site's torq.sh
already honours: the row's own `qcmd`, which it puts in front of that one
process's start line. Nothing else - no setenv.sh, no other row - changes. It

  1. runs the start gate (native_gate.py) - verified, under its stack, its
     tables live on the tickerplant - and exits without starting q when any
     fails, so a native `start all` never publishes past it (#1441);
  2. exports what this tree's q reads: UQF_ROOT, UQF_SCRIPTS and the service
     layer (KDBSERVCONFIG, KDBSERVCODE), which only this process sees;
  3. sources the ODBC home's env.sh when one is configured (`uqs setup odbc`);
  4. execs the site's q with the start line torq.sh built, unchanged.

It holds no secret. The stack's password stays in the environment variable the
attachment names, which the gate reads at start; only its NAME is in the
attachment, as everywhere else.

AUTHENTICATION (#1849) is stack/attached_publisher's model, judged against the
SITE's TorQ application - the one a native start resolves ${TORQAPPHOME} in the
row against - never this tree's, which always has the starter pack's list:

  site          KDBSECURE is set, to a directory: the row names no `-U`, and the
                launcher exports UQF_SITE_AUTH=1 and KDBSECURE (Auth.env), so
                uqf's handlers leave the site's loader and .pm alone;
  starter-pack  the process file is in a TorQ application's appconfig/ folder
                (TorQ's KDBAPPCONFIG) and that application has
                appconfig/passwords/accesslist.txt: row and launcher as before;
  neither       refused (UNKNOWN_AUTH), nothing written. Neither TORQAPPHOME in
                this environment (often this tree's own) nor the site's other
                rows (a `-U` names a list, it does not show one exists) decides.

KDBSECURE is a path, not a secret; it stays out of the row, which names only
this tree's variables written out.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import sys
from pathlib import Path

from uqs.attach import config
from uqs.attach.config import Attachment, AttachmentError
from uqs.paths import UqsError, UqsPaths, paths_for_root
from uqs.stack.attached_publisher import STARTER_ACCESS_LIST, Auth, auth_for

#: The file in the attachment's folder a registered row's qcmd names.
LAUNCHER = "native_start.sh"
#: This tree's variables a row may name, written out as paths in the row and
#: exported by the launcher: the site's environment has none of them.
TREE_VARS = ("UQF_ROOT", "UQF_SCRIPTS", "KDBSERVCONFIG", "KDBSERVCODE")
#: The columns a registration writes besides the ones it copies; a registry
#: without one of them cannot hold the row.
NEEDED = ("host", "port", "proctype", "procname", "load", "startwithall", "qcmd")
#: Where the ODBC home is named (stack/odbc_home.ODBC_HOME_ENV).
ODBC_HOME_ENV = "UQS_ODBC_HOME"
#: Why a registration cannot tell how a native start of the publisher authenticates.
UNKNOWN_AUTH = (
    "{proc} cannot be registered: {why}, and KDBSECURE is not set, so how a native start of it "
    "authenticates is not known, and uqf will not fall back to TorQ's demo credentials. If the "
    "site keeps its credentials under KDBSECURE and authenticates with its own .pm code, export "
    "KDBSECURE as the site's processes see it and run the same command again; the site's loader "
    "and .pm then stand. {otherwise} Nothing was written."
)


def paths_of(att: Attachment, root: Path) -> UqsPaths:
    return paths_for_root(root, att.publisher.runtime or None)


def site_auth(att: Attachment) -> Auth:
    """How a native start of the publisher authenticates, judged against the
    site's TorQ application (module docstring); AttachmentError when not known."""
    proc, path = att.publisher.procname, Path(att.native.process_file).absolute()
    app = path.parent.parent if path.parent.name == "appconfig" else None
    try:
        how = auth_for(app, f"{proc} cannot be registered", "Nothing was written.")
    except UqsError as exc:
        raise AttachmentError(str(exc)) from exc
    if how is not None:
        return how
    if app is None:
        why = (
            f"{path} is not in a TorQ application's appconfig/ folder, so which application "
            "${TORQAPPHOME} names at a native start is not known here"
        )
        otherwise = "Otherwise give the process file in its TorQ application's appconfig/ folder."
    else:
        why = f"the stack's TorQ application ({app}) has no {STARTER_ACCESS_LIST}"
        otherwise = f"Otherwise the site's TorQ application needs its {STARTER_ACCESS_LIST}."
    raise AttachmentError(UNKNOWN_AUTH.format(proc=proc, why=why, otherwise=otherwise))


def uqf_row(paths: UqsPaths, procname: str) -> dict[str, str] | None:
    """This checkout's effective process.csv row for `procname`, or None."""
    from uqs.stack.procs import effective_process_rows

    return next((r for r in effective_process_rows(paths) if r["procname"] == procname), None)


def tree_env(paths: UqsPaths) -> dict[str, str]:
    """This tree's variables, as `uqs start` gives them to its processes."""
    from uqs.stack.env import build_env

    env = build_env(paths)
    return {k: env[k] for k in TREE_VARS if k in env}


def launcher_path(att: Attachment, state_dir: Path | None = None) -> Path:
    return (config.directory(att.name, state_dir) / LAUNCHER).absolute()


def row_values(
    att: Attachment, uqf: dict[str, str], offset: int, launcher: Path, env: dict[str, str]
) -> dict[str, str]:
    """The publisher's row, by column name (registry.Registry.row orders it)."""
    values = {k: _written_out(v, env) for k, v in uqf.items()}
    values |= {
        "host": "localhost",
        "port": f"{{KDBBASEPORT}}+{offset}",
        "startwithall": "1" if att.native.start_with_all else "0",
        "qcmd": str(launcher),
    }
    return values


def _written_out(value: str, env: dict[str, str]) -> str:
    for name, path in env.items():
        value = value.replace(f"${{{name}}}", path)
    return value


def launcher_text(
    att: Attachment,
    env: dict[str, str],
    state_dir: Path | None = None,
    python: str | None = None,
    odbc_home: str | None = None,
) -> str:
    """The launcher, as a POSIX sh script. Everything in it is derived: the
    attachment, the state folder the gate reads, this uqs's interpreter."""
    state = str(config.directory(att.name, state_dir).parent.absolute())
    gate = [python or sys.executable, "-I", "-m", "uqs.attach.native_gate", att.name]
    home = odbc_home if odbc_home is not None else os.environ.get(ODBC_HOME_ENV, "")
    lines = [
        "#!/bin/sh",
        f"# Generated by `uqs job attachment add {att.name}` (#1845): how a native TorQ",
        f"# start runs {att.publisher.procname}, the publisher of attachment {att.name}.",
        "# uqs rewrites it and the attachment's manifest records its hash: a changed",
        "# file is refused, never overwritten. It holds no secret.",
        "stackid=",
        "previous=",
        'for word in "$@"; do',
        '  [ "$previous" = "-stackid" ] && stackid=$word',
        "  previous=$word",
        "done",
        "# the start gate: verified, under its stack, its tables on the tickerplant",
        f'{shlex.join(gate)} --state-dir {shlex.quote(state)} --stack-id "$stackid" || exit $?',
        *(f"export {name}={shlex.quote(path)}" for name, path in sorted(env.items())),
        *([f". {shlex.quote(home + '/current/env.sh')}"] if home else []),
        'exec "${QCMD:-q}" "$@"',
    ]
    return "\n".join(lines) + "\n"


def sha(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def refuse_unsafe_path(path: Path) -> None:
    """The launcher's path goes into a row torq.sh splits on commas and evals."""
    if any(c in str(path) for c in " ,\"'\\$`;&|<>()\n\t"):
        raise AttachmentError(
            f"the attachment folder {path.parent} holds a character a process file or a "
            "shell would split on, so a row cannot name its launcher: set UQS_ATTACH_DIR "
            "to a plain path"
        )
