"""Inspection-only targets: production a workstation may read but not change (#2032).

A target is put in inspection mode in deploy_targets.toml, on the target or
on a layer under it:

    [environments.prod]
    inspection = true        # every target naming environment prod

    [targets.prod-a]
    environment = "prod"

While a target is in inspection mode, every uqs operation that would change
it is refused before anything reaches the server: `uqs --target T <command>`
for a command that may change state (start, stop, restart, a backfill, a
confirms-on-server command with --apply), `uqs target shell`, `uqs deploy
push`, `rollback` and `prune`, `uqs target config restore`, `uqs target adopt
--apply` under any name (#2231), and `uqs query
--tunnel T` (#2179) - a q expression or session is a change wherever it runs,
as `uqs --target T query` is. Reads - a status, `uqs schema --tunnel`, `uqs
data diff`, the tunnel check, a dry run, the config history, `uqs --target T
data compress --report --hdb DIR` (#2219) - still run: each sends only a
program this tree wrote. Every caller that opens a tunnel to a
target's q is held to this by tests/test_tunnel_inspection.py. The frontend reads
the same key, so its API refuses the same target's writes (uqf_frontend's
inspection.py).

The refusal matches a server by its declared name AND by its (ssh, dest):
naming the server by --ssh/--dest, by the history entry `uqs --target` falls
back to, or by a second declared target pointing at the same place (an old
alias, a "-rw" twin) reaches the same release, so it is refused the same way
(#2068). A name is matched through its declared (ssh, dest) as well as by
itself, so a caller that knows only the name is covered too.

THE SAME SERVER, SPELLED ANOTHER WAY (#2074). Both sides are compared by
`same_server`, not as strings:

- the login is dropped: `deploy@prod`, `root@prod` and `prod` are one server.
  Which account ssh logs in as when none is given (ssh_config's User, else
  the local user) is not resolved here; ignoring the login entirely is the
  fail-closed reading, since any account on the server reaches the release.
- the host is lower-cased, with any `ssh://` scheme, `:port`, `[...]` around
  an IPv6 literal and trailing `.` removed.
- a bare name and a qualified one match on the first label: `prod` is
  `prod.example.com`. Two qualified names must be equal, and an IP literal
  matches only itself.
- the dest is compared after posixpath.normpath, with `//`, `/./` and a
  trailing `/` gone: `/srv//uqf`, `/srv/uqf/` and `/srv/uqf/.` are `/srv/uqf`.

What this does not resolve: a Host alias in ~/.ssh/config whose HostName is
another name, or a DNS alias - those are the same server under a name these
rules cannot see.

WHAT IT IS NOT. A policy of the tools - uqs and the frontend - on a
workstation. It does not make ssh, a tunnel, or the q processes on the server
read-only: anyone who can ssh there can still change it, and the key lives in
a file the workstation's operator can edit. It stops a change made by
accident through these tools, not one made on purpose round them.
"""

from __future__ import annotations

import ipaddress
import posixpath
from collections.abc import Callable
from pathlib import Path

from uqs.paths import UqsError

#: The key, in deploy_targets.toml, on a target, an environment or [defaults].
KEY = "inspection"

#: What every refusal ends with: what still works, and what this is not.
_NOTE = (
    "reads still work. Inspection mode is a policy of uqs and the frontend: it does not "
    f"make ssh or the server read-only. Set `{KEY} = false` in the target's declaration "
    "to change the server"
)


class InspectionRefused(UqsError):
    """A change refused because its target is in inspection mode."""


def check_value(where: str, value: object) -> None:
    """Refuse an `inspection` setting that is not a TOML boolean: a string
    "false" must not read as on, nor "yes" as off."""
    if not isinstance(value, bool):
        raise UqsError(f"{where}: {KEY} must be true or false, got {value!r}")


def check_layer(layer: str, settings: dict, at: Callable[[], str]) -> None:
    """A deploy_targets.toml layer's `inspection`, if it sets one, checked as
    `check_value` does - refused as the registry's DeployError, its line
    named by `at()`."""
    if KEY not in settings:
        return
    from uqs.deploy.config import DeployError

    try:
        check_value(layer, settings[KEY])
    except UqsError as exc:
        raise DeployError("arguments", f"{at()}{exc}") from None


def is_on(settings: dict) -> bool:
    """Whether a target's merged settings put it in inspection mode."""
    return settings.get(KEY) is True


def _host_of(ssh: object) -> str | None:
    """The machine an ssh destination names, lower-cased, without the login,
    scheme, port or brackets - None when there is none to compare."""
    if not isinstance(ssh, str):
        return None
    host = ssh.strip().lower().removeprefix("ssh://").rsplit("@", 1)[-1]
    if host.startswith("["):  # [v6]:port
        host = host[1:].partition("]")[0]
    elif host.count(":") == 1:  # host:port; more colons is a bare IPv6 literal
        host = host.partition(":")[0]
    host = host.rstrip(".")
    return host or None


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _same_host(a: str, b: str) -> bool:
    if a == b:
        return True
    if _is_ip(a) or _is_ip(b):
        return False
    # a bare name is the first label of a qualified one: prod ~ prod.example.com
    if "." in a and "." in b:
        return False
    return a.split(".", 1)[0] == b.split(".", 1)[0]


def _dest_of(dest: object) -> str | None:
    """`dest` as one path: `//`, `.` and trailing `/` gone - None when none."""
    if not isinstance(dest, str) or not dest.strip():
        return None
    path = posixpath.normpath(dest.strip())
    # normpath keeps a leading `//` (POSIX leaves it implementation-defined)
    return "/" + path.lstrip("/") if path.startswith("/") else path


def same_server(host_a: object, dest_a: object, host_b: object, dest_b: object) -> bool:
    """Whether two (ssh, dest) pairs name one release, under the rules in
    this module's docstring. A missing host on either side is no match - a
    target with no ssh reaches no server; a missing dest beside a matching
    host is a match, since which release is meant is then unknown."""
    a, b = _host_of(host_a), _host_of(host_b)
    if a is None or b is None or not _same_host(a, b):
        return False
    da, db = _dest_of(dest_a), _dest_of(dest_b)
    return da is None or db is None or da == db


def matching(
    declared: dict[str, dict],
    *,
    name: str | None = None,
    ssh: str | None = None,
    dest: str | None = None,
) -> str | None:
    """The inspection-only target among `declared` (targets.read's answer)
    that `name`, or the server at (`ssh`, `dest`), is - or None. `name` is
    matched by itself and by the server it is declared at, so another target
    at an inspection target's (ssh, dest) is that target (#2068)."""
    servers: list[tuple[object, object]] = []
    if ssh is not None:
        servers.append((ssh, dest))
    if name is not None and name in declared:
        servers.append((declared[name].get("ssh"), declared[name].get("dest")))
    if name is not None and is_on(declared.get(name, {})):
        return name
    for label, settings in sorted(declared.items()):
        if not is_on(settings):
            continue
        at = (settings.get("ssh"), settings.get("dest"))
        if any(same_server(*at, *server) for server in servers):
            return label
    return None


def declared_targets(root: Path) -> dict[str, dict]:
    """Every declared target's merged settings; none when there is no file.
    A file that does not parse is refused by targets.read, as everywhere."""
    from uqs.deploy import targets

    if not targets.declaration_path(root).is_file():
        return {}
    return targets.read(root)


def refuse(
    root: Path,
    what: str,
    *,
    name: str | None = None,
    ssh: str | None = None,
    dest: str | None = None,
) -> None:
    """Raise InspectionRefused when the target `name`, or the server at
    (`ssh`, `dest`), is in inspection mode; `what` is the refused change,
    in words ("`uqs deploy push`")."""
    found = matching(declared_targets(root), name=name, ssh=ssh, dest=dest)
    if found is None:
        return
    if name == found:
        via = ""
    elif ssh is not None:
        via = f" (the server at {ssh}:{dest} is target {found})"
    else:
        via = f" (target {name} is the same server as target {found})"
    raise InspectionRefused(
        f"target {found} is in inspection mode{via}: {what} is refused - {_NOTE}"
    )


def refuse_deploy(
    root: Path, what: str, *, name: str | None, ssh: str, dest: str, dry_run: bool
) -> None:
    """`refuse`, as the DeployError at stage `inspection` a deploy command
    reports; a dry run changes nothing, so it is never refused."""
    if dry_run:
        return
    from uqs.deploy.config import DeployError

    try:
        refuse(root, what, name=name, ssh=ssh, dest=dest)
    except InspectionRefused as exc:
        raise DeployError("inspection", str(exc)) from None


def refuse_push(root: Path, cfgs: list, *, dry_run: bool) -> None:
    """`refuse_deploy` for every Config a push was given, before any is
    pushed, so a list with one inspection target in it pushes to none."""
    for cfg in cfgs:
        refuse_deploy(
            root, "`uqs deploy push`",
            name=cfg.target, ssh=cfg.ssh, dest=cfg.dest, dry_run=dry_run,
        )  # fmt: skip
