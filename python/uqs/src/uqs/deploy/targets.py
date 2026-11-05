"""Deployment targets in a file, rather than in a dozen push flags (#873).

A production push carries a host, a remote user, a destination, the TorQ
paths, q, the profile, the jobs and the live checks. Copied between shell
histories, one wrong flag deploys another configuration with nothing to show
it, and a second host means repeating it all. So targets are declared once, in
a file the operator keeps outside the source - like runtime_bundles.json
(#852) - at the repository root, or wherever $UQS_DEPLOY_TARGETS names:

    [targets.prod-a]
    ssh = "deploy@prod-a"
    remote_user = "svc"
    dest = "/srv/uqf"
    runtime = "crypto"
    profile = "essential"
    torq_home = "/opt/torq"
    live = true
    live_check = ["deals_db"]

Each key is a `uqs deploy push` option, spelt as its flag (`torq_home` for
--torq-home, `ssh` for --ssh) and as make_config's parameter and the Config's
field - one name in every place (#2123). The values go through make_config
like the flags', so a file is held to exactly the same checks. A flag given on the command line
wins over the file. Each value's source - the target, or the flag - is kept
in the Config, and the plan prints it. `runtime`, which push otherwise reads
from the artifact, is the one key that is not a flag: it must match the
artifact's, so a crypto target never runs a uqf release.

Settings shared by several targets are layered (#1557), lowest first:
`[defaults]`, then the `[environments.NAME]` a target names with
`environment = "NAME"`, then the target, then the flags. A higher layer
replaces a key's whole value - a table is never merged - and each layer is
held to the target's key checks, its refusals naming the layer. The sources
say which layer gave each setting: "defaults", "environment uat",
"target prod-a", "--profile". One exception to a key's independence: a layer
that switches a component's mode (`q_mode`, deploy/components.py) drops the
old mode's fields the layers below it set, so none is inherited.
"""

from __future__ import annotations

import inspect
import os
import re
import tomllib
from functools import partial
from pathlib import Path
from typing import Any

from uqs.deploy import components, inspection, launch, overrides, target_file
from uqs.deploy.config import Config, DeployError, make_config
from uqs.deploy.target_keys import RENAMED, flag, unknown_target
from uqs.paths import UqsError

DECLARATION = "deploy_targets.toml"
DECLARATION_ENV = "UQS_DEPLOY_TARGETS"
#: The named policy overrides (deploy/overrides.py, #1557): each relaxes a
#: requirement for one push, with its reason, so none is ever a target's
#: standing setting. Derived from overrides.SKIPS, as make_config spells them:
#: one listed here by hand would let an override added there alone become a
#: key a target file may set (#1794).
OVERRIDES = frozenset(name.replace("-", "_") for name in overrides.SKIPS)
#: What push passes configs() itself: what is deployed, and whether it is a
#: rehearsal.
GIVEN_SEPARATELY = frozenset({"artifact", "dry_run"})
#: Options a file may not set: the above and the overrides are this run's to say.
PER_RUN = GIVEN_SEPARATELY | OVERRIDES
#: Options make_config takes as one comma-separated string; a file lists them.
LISTED_AS_TEXT = frozenset({"jobs", "live_check", "process_fields"})
SETTABLE = frozenset(inspect.signature(make_config).parameters) - PER_RUN
#: Keys only `uqs --target` reads, for a release push did not install: how it
#: launches uqs (deploy/control.py, #1642).
LAUNCH_KEYS = frozenset({"launch_mode", "launch_python", "launch_env_file"})
#: Keys a target may hold that are not push options: the runtime it must run
#: (checked against the artifact's), labels, which `uqs target list` shows
#: and nothing deploys (#956), the launch keys, and `inspection` (#2032).
NOT_PUSH_OPTIONS = frozenset({"runtime", "labels", inspection.KEY}) | LAUNCH_KEYS
#: The file's tables. Only a target names its environment.
DEFAULTS, ENVIRONMENTS, TARGETS = "defaults", "environments", "targets"
ENVIRONMENT = "environment"
#: The stage a target's lookup and its file's checks fail at: a refusal,
#: exit 1, like every other unknown name (#1885, #2124) - not `arguments`,
#: a usage error.
TARGET = "target"


def declaration_path(root: Path) -> Path:
    configured = os.environ.get(DECLARATION_ENV, "").strip()
    return Path(configured).expanduser() if configured else root / DECLARATION


def _load(root: Path) -> dict:
    """The declaration's tables, their shapes checked: {defaults, environments,
    targets}. Each key's name is checked by configs, layer by layer."""
    path = declaration_path(root)
    if not path.is_file():
        raise DeployError(TARGET, f"a target needs {path}, which does not exist")
    return _tables(target_file.text_of(path), path)


def _tables(text: str, path: Path) -> dict:
    """_load's work on the file's text, so a change can be checked before it
    is written (deploy/target_file.py)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise DeployError(TARGET, f"{path}: not TOML ({exc})") from None
    shape = (
        f"declare targets as [{TARGETS}.<name>] tables, beside an optional "
        f"[{DEFAULTS}] and [{ENVIRONMENTS}.<name>] tables"
    )
    unknown = sorted(set(data) - {DEFAULTS, ENVIRONMENTS, TARGETS})
    if unknown:
        raise DeployError(TARGET, f"{path}: unknown table(s) {', '.join(unknown)} - {shape}")
    # A file holding only shared layers may have no target yet (`uqs target add`);
    # nor may one every target was removed from.
    found = data.get(TARGETS, {})
    defaults, envs = data.get(DEFAULTS, {}), data.get(ENVIRONMENTS, {})
    tables = (found, envs)
    if not isinstance(defaults, dict) or not all(
        isinstance(t, dict) and all(isinstance(v, dict) for v in t.values()) for t in tables
    ):
        raise DeployError(TARGET, f"{path}: {shape}")
    layers = [
        (DEFAULTS, defaults),
        *((f"environment {n}", e) for n, e in envs.items()),
        *((f"target {n}", t) for n, t in found.items()),
    ]
    for layer, settings in layers:
        _old_keys(layer, settings, text, path)
        _labels(layer, settings, text, path)
        inspection.check_layer(layer, settings, partial(_at, text, path, layer, inspection.KEY))
    shared = layers[: 1 + len(envs)]
    for layer, settings in shared:
        if ENVIRONMENT in settings:
            raise DeployError(
                TARGET,
                f"{_at(text, path, layer)}{layer}: sets environment - only a target names "
                "its environment",
            )
    return {DEFAULTS: defaults, ENVIRONMENTS: envs, TARGETS: found}


def _old_keys(layer: str, settings: dict, text: str, path: Path) -> None:
    """Refuse a key spelt as a setting's name before #1888, naming the key now."""
    old = sorted(set(settings) & set(RENAMED))
    if old:
        raise DeployError(
            TARGET,
            f"{_at(text, path, layer, old[0])}{layer}: "
            + ", ".join(f"{k} is {RENAMED[k]}" for k in old)
            + " - rename the key: the file spells each setting as push's option "
            "(`uqs target edit` opens it)",
        )


def _labels(layer: str, settings: dict, text: str, path: Path) -> None:
    """Refuse a `labels` that is not a table of plain values (#2026)."""
    labels = settings.get("labels", {})
    plain = (str, int, float, bool)
    if not isinstance(labels, dict) or not all(isinstance(v, plain) for v in labels.values()):
        where, eg = _at(text, path, layer, "labels"), 'labels = { region = "ldn" }'
        raise DeployError(TARGET, f"{where}{layer}: labels must be a table, e.g. {eg}")


def _header(layer: str) -> str:
    """The table a layer is declared as: "target prod" -> targets.prod."""
    kind, _, name = layer.partition(" ")
    return {"target": f"{TARGETS}.{name}", "environment": f"{ENVIRONMENTS}.{name}"}.get(kind, kind)


def line_of(text: str, table: str, key: str | None = None) -> int | None:
    """The line (from 1) declaring `table` - `targets.prod` - or, given a
    key, that key's line inside it; None when it is not a [table] header
    (an inline table, say)."""
    parts = [re.escape(p) for p in table.split(".")]
    header = re.compile(r"^\s*\[\s*" + r"\s*\.\s*".join(parts) + r"\s*\]\s*(#.*)?$")
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if header.match(ln)), None)
    if start is None or key is None:
        return None if start is None else start + 1
    setting = re.compile(rf"^\s*{re.escape(key)}\s*[=.]")
    for i in range(start + 1, len(lines)):
        if lines[i].lstrip().startswith("["):
            break
        if setting.match(lines[i]):
            return i + 1
    return start + 1


def _at(text: str, path: Path, layer: str, key: str | None = None) -> str:
    """ "<path>, line N: " for a refusal about `layer`, so a hand edit's
    mistake is found by its line."""
    n = line_of(text, _header(layer), key)
    return f"{path}, line {n}: " if n else f"{path}: "


def _layers(data: dict, name: str, path: Path) -> list[tuple[str, dict]]:
    """Target `name`'s layers, lowest first, as (source, settings)."""
    target = dict(data[TARGETS][name])
    env = target.pop(ENVIRONMENT, None)
    layers = [(DEFAULTS, data[DEFAULTS])] if data[DEFAULTS] else []
    if env is not None:
        if not isinstance(env, str) or env not in data[ENVIRONMENTS]:
            known = ", ".join(sorted(data[ENVIRONMENTS])) or "none"
            raise DeployError(
                TARGET,
                f"target {name}: environment {env!r} is not declared in {path} - "
                f"it declares: {known}",
            )
        layers.append((f"environment {env}", data[ENVIRONMENTS][env]))
    return [*layers, (f"target {name}", target)]


def read(root: Path) -> dict[str, dict]:
    """{target: its settings, over its defaults and environment}, from the
    declaration, every layer checked (validate); refused when absent. A
    target keeps its `environment` key."""
    path = declaration_path(root)
    if not path.is_file():
        raise DeployError(TARGET, f"a target needs {path}, which does not exist")
    return validate(target_file.text_of(path), path)


def _merge(data: dict, path: Path) -> dict[str, dict]:
    out = {}
    for name, declared in data[TARGETS].items():
        merged: dict = {}
        for _, settings in _layers(data, name, path):
            merged.update(settings)
        if ENVIRONMENT in declared:
            merged[ENVIRONMENT] = declared[ENVIRONMENT]
        out[name] = merged
    return out


#: Stand-ins for the three settings push needs and a target may leave to the
#: command line, so validate can hold the rest of a target to make_config.
_STAND_INS = {"ssh": "validate", "dest": "/validate", "profile": "validate"}


def validate(text: str, path: Path) -> dict[str, dict]:
    """The declaration `text` as read() returns it, refused - naming the
    line - unless every layer holds only known keys with values push and
    `uqs --target` accept: an override, an unknown key, an undeclared
    environment, a bad launch_mode or a source-only target missing a path."""
    data = _tables(text, path)
    _options(DEFAULTS, data[DEFAULTS], text, path)
    for env, settings in data[ENVIRONMENTS].items():
        _options(f"environment {env}", settings, text, path)
    for name in data[TARGETS]:
        where = _at(text, path, f"target {name}")
        try:
            options, _, _ = _merged(data, name, path, text)
            merged = _merge({**data, TARGETS: {name: data[TARGETS][name]}}, path)[name]
            checked: dict[str, Any] = {**_STAND_INS, **options}
            make_config(artifact="validate", **checked)
            launch.settings(name, merged)
            remote_shape(name, merged)
        except (DeployError, UqsError) as exc:
            said = str(exc)
            if said.startswith(str(path)):
                raise
            raise DeployError(TARGET, f"{where}target {name}: {_bare(exc, name)}") from None
        except (TypeError, ValueError) as exc:
            # make_config compares and splits; a value of the wrong type fails there
            raise DeployError(
                TARGET, f"{where}target {name}: a setting has the wrong type ({exc})"
            ) from None
    return _merge(data, path)


def _bare(exc: Exception, name: str) -> str:
    """exc's words without a leading "target NAME: ", which validate adds."""
    return str(exc).removeprefix(f"target {name}: ")


def remote_shape(label: str, t: dict) -> None:
    """The shapes `uqs --target` holds ssh and dest to, when set: both reach
    a remote shell."""
    ssh, dest = t.get("ssh"), t.get("dest")
    if ssh is not None and (
        not isinstance(ssh, str) or ssh.startswith("-") or any(c.isspace() for c in ssh)
    ):
        raise UqsError(f"target {label}: ssh {ssh!r} is not an ssh destination")
    if dest is not None and (
        not isinstance(dest, str) or not dest.startswith("/") or any(c.isspace() for c in dest)
    ):
        raise UqsError(f"target {label}: dest {dest!r} must be an absolute path without spaces")


def _options(layer: str, declared: dict, text: str = "", path: Path | None = None) -> dict:
    """One layer's settings - "defaults", "environment uat", "target prod-a" -
    as make_config's keyword arguments."""
    where = ""
    overridden = sorted(set(declared) & OVERRIDES)
    if overridden:
        if path is not None:
            where = _at(text, path, layer, overridden[0])
        raise DeployError(
            TARGET,
            f"{where}{layer}: {', '.join(overridden)} - an override is given on the command "
            "line, with its reason, for one push; the file never sets one",
        )
    unknown = sorted(set(declared) - SETTABLE - NOT_PUSH_OPTIONS - {ENVIRONMENT})
    if unknown:
        if path is not None:
            where = _at(text, path, layer, unknown[0])
        raise DeployError(
            TARGET,
            f"{where}{layer}: unknown setting(s) {', '.join(unknown)} - the file sets push's "
            "options, spelt as their flags (torq_home for --torq-home, ssh for --ssh)",
        )
    options = {}
    for key, value in declared.items():
        if key in NOT_PUSH_OPTIONS or key == ENVIRONMENT:
            continue
        if key in LISTED_AS_TEXT and isinstance(value, list):
            value = ",".join(str(v) for v in value)
        options[key] = value
    return options


def push_options(label: str, t: dict) -> dict:
    """The push options among `t`'s settings - a target read() merged, or a
    history entry - as make_config's keyword arguments: what a command that
    reaches the server makes its Config from, so rollback and prune honour
    a target's command_timeout and verify_timeout as push does (#1892)."""
    return _options(label, {k: v for k, v in t.items() if k in SETTABLE})


def _merged(
    data: dict, name: str, path: Path, text: str = ""
) -> tuple[dict, dict[str, str], str | None]:
    """Target `name`'s push options over its layers, lowest first, with the
    layer each came from and the runtime it must run."""
    options: dict = {}
    sources: dict[str, str] = {}
    runtime = None
    for layer, settings in _layers(data, name, path):
        given = _options(layer, settings, text, path if text else None)
        components.switch(options, sources, given)
        options.update(given)
        sources.update(dict.fromkeys(given, layer))
        runtime = settings.get("runtime", runtime)
    return options, sources, runtime


def configs(names: str, artifact: str, explicit: dict, dry_run: bool, root: Path) -> list[Config]:
    """A Config per target in `names` (comma-separated), in order: the file's
    settings, layered, with `explicit` - the flags given on the command line -
    over them, each checked by make_config."""
    data = _load(root)
    declared, path = data[TARGETS], declaration_path(root)
    # Every shared layer's keys are checked, whichever targets use it.
    _options(DEFAULTS, data[DEFAULTS])
    for env, settings in data[ENVIRONMENTS].items():
        _options(f"environment {env}", settings)
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    if not wanted:
        raise DeployError("arguments", f"no target named in {names!r}")
    missing = [n for n in wanted if n not in declared]
    if missing:
        raise DeployError(TARGET, str(unknown_target(missing, declared, path)))
    out = []
    for name in wanted:
        options, sources, runtime = _merged(data, name, path)
        components.switch(options, sources, explicit)
        options.update(explicit)
        sources.update({k: _flag(k) for k in explicit})
        absent = [k for k in ("ssh", "dest", "profile") if options.get(k) is None]
        if absent:
            raise DeployError(
                "arguments",
                f"target {name} sets no {', '.join(absent)}, and no flag gives "
                + ("it" if len(absent) == 1 else "them"),
            )
        cfg = make_config(artifact=artifact, dry_run=dry_run, **options)
        cfg.target = name
        cfg.sources = sources
        cfg.runtime = runtime
        out.append(cfg)
    return out


#: A target's name, as `[targets.NAME]` and `uqs --target NAME` spell it.
NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")


def _flag(option: str) -> str:
    """How `option` is given on the command line: `--override NAME` for one of
    the named overrides (#1732), config.flag's spelling for anything else."""
    return f"--override {option.replace('_', '-')}" if option in OVERRIDES else flag(option)
