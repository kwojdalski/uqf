"""How deploy_targets.toml and the command line spell make_config's options,
and the one refusal for a target name nothing declares.

One name per setting, everywhere (#1888, #2123): make_config's parameter, the
Config field, the key in deploy_targets.toml and - with `-` for `_` - push's
flag. `ssh` for `--ssh`, `q_cmd` for `--q-cmd`, `torq_component` for
`--torq-component`. There is no second key space to translate between.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from uqs import duration
from uqs.paths import UqsError

#: Keys a target file held before #1888 -> what each is now. Refused when a
#: file still holds one, naming the new key (deploy/targets.py): `host` is a q
#: host everywhere else (#1768).
RENAMED = {
    "host": "ssh",
    "qcmd": "q_cmd",
    "qhome": "q_home",
    **{
        f"{c}_runtime{sha}": f"{c}_component{sha}"
        for c in ("q", "torq", "python")
        for sha in ("", "_sha256")
    },
}


def flag(option: str) -> str:
    """How make_config's `option` is given on the command line: `--ssh` for
    `ssh`, `--torq-home` for `torq_home`."""
    return "--" + option.replace("_", "-")


def unknown_target(
    names: list[str] | str, declared: Iterable[str], path: Path, *, deployed: bool = False
) -> UqsError:
    """The one refusal for a target name the declaration does not hold
    (#2124), beside stack.procs.unknown_processes: the names asked for, the
    ones `path` declares and where to list them. `deployed`: the command also
    takes a server a push recorded (`uqs --target HOST`), so it says that too."""
    names = [names] if isinstance(names, str) else names
    what = "target or deployed server" if deployed else "target"
    known = ", ".join(sorted(declared)) or "none"
    then = "; `uqs deploy history` shows the servers deployed to" if deployed else ""
    return UqsError(
        f"no {what} {', '.join(names)} - {path} declares: {known}; "
        f"`uqs target list` names them{then}"
    )


#: make_config's spans of time, in whole seconds: push takes each as a
#: DURATION flag, and the file as a DURATION string (`"5m"`) or a number of
#: seconds, both read by uqs/duration.py into the same value (#2121).
DURATIONS = (
    "connect_timeout",
    "command_timeout",
    "smoke_timeout",
    "verify_timeout",
    "live_check_timeout",
    "soak",
)


def read_durations(layer: str, settings: dict, at: Callable[[str], str]) -> None:
    """A deploy_targets.toml layer's spans of time as the whole seconds push's
    flags give make_config, in place - read by the flags' own parser, so the
    file takes what `--verify-timeout` does. A value that is no DURATION is
    refused as the registry's DeployError, naming its key, its line by `at`."""
    from uqs.deploy.config import DeployError

    for option in DURATIONS:
        if option in settings:
            try:
                settings[option] = duration.whole_seconds(settings[option])
            except duration.DurationError as exc:
                raise DeployError("arguments", f"{at(option)}{layer}: {option} {exc}") from None
