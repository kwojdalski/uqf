"""How `uqs --target` starts uqs in a deployed release (#1642).

A release `uqs deploy push` installed carries its own .venv/bin/uqs and a
deploy.env. One installed by another release process may be source only: the
tree under `current`, no .venv, no deploy.env. Its target says
`launch_mode = "source"` and names the interpreter its operator provisioned
with uqf's dependencies (`launch_python`) and the environment file that sets
q, TorQ and the runtime's data (`launch_env_file`). uqs then runs as
`launch_python -m uqs` with the release's own python/uqs/src first on
PYTHONPATH, so it is the deployed release's CLI, never an installed one.

Nothing falls back from one mode to the other. The remote preflight checks
each prerequisite in turn and exits with its own code (UNLAUNCHABLE), so a
missing release, a missing CLI, a missing environment file, a missing
interpreter, missing source and missing dependencies are told apart.
"""

from __future__ import annotations

import shlex

from uqs.paths import UqsError

#: The remote preflight's exit code when <dest>/current holds no release.
#: Outside what uqs itself exits with, and below the shell's 126 and up.
NO_RELEASE = 64
#: The remote preflight's exit codes when the release is there but cannot
#: launch uqs, each with what is missing and how to provide it.
NO_CLI, NO_ENV_FILE, NO_PYTHON, NO_SOURCE, NO_DEPENDENCIES = 65, 66, 67, 68, 69
UNLAUNCHABLE: dict[int, str] = {
    NO_CLI: "its release has no .venv/bin/uqs - deploy it with `uqs deploy push`, or, "
    'for a source-only release, set launch_mode = "source" on the target',
    NO_ENV_FILE: "its environment file is missing or unreadable - deploy.env for a "
    "pushed release, launch_env_file for a source-only one",
    NO_PYTHON: "its launch_python is not an executable on the server",
    NO_SOURCE: "its release holds no python/uqs/src/uqs to run - is current a uqf tree?",
    NO_DEPENDENCIES: "its launch_python cannot import uqf's dependencies - provision them "
    "into that interpreter (nothing is installed by `uqs --target`)",
}
#: How a target runs uqs: as `uqs deploy push` installs it, or from source.
RELEASE, SOURCE = "release", "source"
LAUNCH_MODES = (RELEASE, SOURCE)
#: Where a release's own source is, under current.
SOURCE_DIR = "python/uqs/src"


def settings(label: str, t: dict) -> tuple[str, str | None, str | None]:
    """(launch_mode, launch_python, launch_env_file) of target `label`'s
    declaration `t`, checked: a source-only target names both paths,
    absolute; a pushed release names neither, so a stray path is refused
    rather than silently ignored."""
    mode = t.get("launch_mode", RELEASE)
    if mode not in LAUNCH_MODES:
        raise UqsError(
            f"target {label}: launch_mode {mode!r} is not one of {', '.join(LAUNCH_MODES)}"
        )
    paths = {k: t.get(k) for k in ("launch_python", "launch_env_file")}
    if mode == RELEASE:
        given = [k for k, v in paths.items() if v]
        if given:
            raise UqsError(
                f"target {label}: {', '.join(given)} set, but launch_mode is {RELEASE} - "
                f'a pushed release brings its own; set launch_mode = "{SOURCE}" to use them'
            )
        return mode, None, None
    absent = [k for k, v in paths.items() if not v]
    if absent:
        raise UqsError(
            f"target {label}: launch_mode {SOURCE} needs {' and '.join(absent)} - the "
            "interpreter provisioned with uqf's dependencies, and the environment file "
            "that sets q, TorQ and the runtime's data"
        )
    for k, v in paths.items():
        if not isinstance(v, str) or not v.startswith("/"):
            raise UqsError(f"target {label}: {k} {v!r} must be an absolute path on the server")
    return mode, str(paths["launch_python"]), str(paths["launch_env_file"])


def uqs_command(mode: str, python: str | None) -> str:
    """How the release's own uqs is started, from inside current."""
    if mode == SOURCE:
        return f"{shlex.quote(str(python))} -m uqs"
    return ".venv/bin/uqs"


def preflight(dest: str, mode: str, python: str | None, env_file: str | None) -> str:
    """The remote shell text that checks each thing the release needs to
    launch uqs, exiting with its code when one is missing, then enters
    current and loads its environment. What follows it runs there."""
    current = shlex.quote(f"{dest.rstrip('/')}/current")
    checks = [(f"[ -L {current} ]", NO_RELEASE, f"no deployed release at {current}")]
    if mode == SOURCE:
        py, env = shlex.quote(str(python)), shlex.quote(str(env_file))
        checks += [
            (f"[ -x {py} ]", NO_PYTHON, f"launch_python {py} is not executable"),
            (f"[ -r {env} ]", NO_ENV_FILE, f"launch_env_file {env} is not readable"),
            (
                f"[ -f {current}/{SOURCE_DIR}/uqs/__main__.py ]",
                NO_SOURCE,
                f"no uqf source at {current}/{SOURCE_DIR}",
            ),
        ]
        load = (
            f"cd {current} && . {env} && "
            f'export PYTHONPATH="$PWD/{SOURCE_DIR}${{PYTHONPATH:+:$PYTHONPATH}}" && '
            f"{{ {py} -c 'import uqs.cli' >/dev/null 2>&1 || "
            f"{{ echo uqs: {py} cannot import uqf and its dependencies >&2; "
            f"exit {NO_DEPENDENCIES}; }}; }} && "
        )
    else:
        checks += [
            (f"[ -x {current}/.venv/bin/uqs ]", NO_CLI, f"no .venv/bin/uqs in {current}"),
            (f"[ -r {current}/deploy.env ]", NO_ENV_FILE, f"no deploy.env in {current}"),
        ]
        load = f"cd {current} && . ./deploy.env && "
    return (
        "".join(
            f"if ! {test}; then echo uqs: {what} >&2; exit {code}; fi; "
            for test, code, what in checks
        )
        + load
    )
