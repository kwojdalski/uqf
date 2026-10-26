"""Deployment targets in a file, rather than in a dozen push flags (#873).

A production push carries a host, a remote user, a destination, the TorQ
paths, q, the profile, the jobs and the live checks. Copied between shell
histories, one wrong flag deploys another configuration with nothing to show
it, and a second host means repeating it all. So targets are declared once, in
a file the operator keeps outside the source - like runtime_bundles.json
(#852) - at the repository root, or wherever $UQS_DEPLOY_TARGETS names:

    [targets.prod-a]
    host = "deploy@prod-a"
    remote_user = "svc"
    dest = "/srv/uqf"
    runtime = "crypto"
    profile = "essential"
    torq_home = "/opt/torq"
    live = true
    live_check = ["deals_db"]

Each key is a `uqs deploy push` option, spelt as its parameter (`torq_home`
for --torq-home). The values go through make_config like the flags', so a
file is held to exactly the same checks. A flag given on the command line
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
import json
import os
import re
import tomllib
from pathlib import Path

from uqs.deploy import components
from uqs.deploy.config import Config, DeployError, make_config

DECLARATION = "deploy_targets.toml"
DECLARATION_ENV = "UQS_DEPLOY_TARGETS"
#: The named policy overrides (deploy/overrides.py, #1557): each relaxes a
#: requirement for one push, with its reason, so none is ever a target's
#: standing setting.
OVERRIDES = frozenset(
    {"accept_digest", "accept_q_version", "allow_unpinned", "accept_converted_release"}
)
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
#: (checked against the artifact's), labels, which `uqs list targets` shows
#: and nothing deploys (#956), and the launch keys.
NOT_PUSH_OPTIONS = frozenset({"runtime", "labels"}) | LAUNCH_KEYS
#: The file's tables. Only a target names its environment.
DEFAULTS, ENVIRONMENTS, TARGETS = "defaults", "environments", "targets"
ENVIRONMENT = "environment"


def declaration_path(root: Path) -> Path:
    configured = os.environ.get(DECLARATION_ENV, "").strip()
    return Path(configured).expanduser() if configured else root / DECLARATION


def _load(root: Path) -> dict:
    """The declaration's tables, their shapes checked: {defaults, environments,
    targets}. Each key's name is checked by configs, layer by layer."""
    path = declaration_path(root)
    if not path.is_file():
        raise DeployError("arguments", f"--target needs {path}, which does not exist")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise DeployError("arguments", f"{path}: not TOML ({exc})") from None
    shape = (
        f"declare targets as [{TARGETS}.<name>] tables, beside an optional "
        f"[{DEFAULTS}] and [{ENVIRONMENTS}.<name>] tables"
    )
    unknown = sorted(set(data) - {DEFAULTS, ENVIRONMENTS, TARGETS})
    if unknown:
        raise DeployError("arguments", f"{path}: unknown table(s) {', '.join(unknown)} - {shape}")
    # A file holding only shared layers may have no target yet (`uqs target add`).
    layered = DEFAULTS in data or ENVIRONMENTS in data
    found = data.get(TARGETS, {} if layered else None)
    defaults, envs = data.get(DEFAULTS, {}), data.get(ENVIRONMENTS, {})
    tables = (found, envs)
    if not isinstance(defaults, dict) or not all(
        isinstance(t, dict) and all(isinstance(v, dict) for v in t.values()) for t in tables
    ):
        raise DeployError("arguments", f"{path}: {shape}")
    shared = [(DEFAULTS, defaults), *((f"environment {n}", e) for n, e in envs.items())]
    for layer, settings in shared:
        if ENVIRONMENT in settings:
            raise DeployError(
                "arguments", f"{layer}: sets environment - only a target names its environment"
            )
    return {DEFAULTS: defaults, ENVIRONMENTS: envs, TARGETS: found}


def _layers(data: dict, name: str, path: Path) -> list[tuple[str, dict]]:
    """Target `name`'s layers, lowest first, as (source, settings)."""
    target = dict(data[TARGETS][name])
    env = target.pop(ENVIRONMENT, None)
    layers = [(DEFAULTS, data[DEFAULTS])] if data[DEFAULTS] else []
    if env is not None:
        if not isinstance(env, str) or env not in data[ENVIRONMENTS]:
            known = ", ".join(sorted(data[ENVIRONMENTS])) or "none"
            raise DeployError(
                "arguments",
                f"target {name}: environment {env!r} is not declared in {path} - "
                f"it declares: {known}",
            )
        layers.append((f"environment {env}", data[ENVIRONMENTS][env]))
    return [*layers, (f"target {name}", target)]


def read(root: Path) -> dict[str, dict]:
    """{target: its settings, over its defaults and environment}, from the
    declaration; refused when absent. A target keeps its `environment` key."""
    data = _load(root)
    path = declaration_path(root)
    out = {}
    for name, declared in data[TARGETS].items():
        merged: dict = {}
        for _, settings in _layers(data, name, path):
            merged.update(settings)
        if ENVIRONMENT in declared:
            merged[ENVIRONMENT] = declared[ENVIRONMENT]
        out[name] = merged
    return out


def _options(layer: str, declared: dict) -> dict:
    """One layer's settings - "defaults", "environment uat", "target prod-a" -
    as make_config's keyword arguments."""
    overridden = sorted(set(declared) & OVERRIDES)
    if overridden:
        raise DeployError(
            "arguments",
            f"{layer}: {', '.join(overridden)} - an override is given on the command "
            "line, with its reason, for one push; the file never sets one",
        )
    unknown = sorted(set(declared) - SETTABLE - NOT_PUSH_OPTIONS)
    if unknown:
        raise DeployError(
            "arguments",
            f"{layer}: unknown setting(s) {', '.join(unknown)} - the file sets push's "
            "options, spelt as parameters (torq_home for --torq-home)",
        )
    options = {}
    for key, value in declared.items():
        if key in NOT_PUSH_OPTIONS:
            continue
        if key in LISTED_AS_TEXT and isinstance(value, list):
            value = ",".join(str(v) for v in value)
        options[key] = value
    return options


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
    missing = [n for n in wanted if n not in declared]
    if not wanted or missing:
        known = ", ".join(sorted(declared)) or "none"
        asked = ", ".join(missing) or repr(names)
        raise DeployError("arguments", f"no target {asked} in {path} - it declares: {known}")
    out = []
    for name in wanted:
        options: dict = {}
        sources: dict[str, str] = {}
        runtime = None
        for layer, settings in _layers(data, name, path):
            given = _options(layer, settings)
            components.switch(options, sources, given)
            options.update(given)
            sources.update(dict.fromkeys(given, layer))
            runtime = settings.get("runtime", runtime)
        components.switch(options, sources, explicit)
        options.update(explicit)
        sources.update({k: "--" + k.replace("_", "-") for k in explicit})
        absent = [k for k in ("host", "dest", "profile") if options.get(k) is None]
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


def _toml(value: object) -> str:
    """A TOML value for what append writes: strings, ints, string tables.
    JSON's string escapes are TOML's basic-string escapes."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


def append(root: Path, name: str, entry: dict) -> Path:
    """Add [targets.NAME] at the end of the declaration, creating it if need
    be. Appended, never rewritten: the operator's comments and other entries
    are not touched (#956). Empty values are left out."""
    if not NAME.fullmatch(name):
        raise DeployError("arguments", f"target name {name!r}: lower-case letters, digits, - and _")
    path = declaration_path(root)
    before = path.read_text() if path.is_file() else ""
    if before and name in read(root):
        raise DeployError("arguments", f"target {name} is already declared in {path}")
    lines = [f"[targets.{name}]"] + [f"{k} = {_toml(v)}" for k, v in entry.items() if v]
    sep = "" if not before or before.endswith("\n\n") else "\n" if before.endswith("\n") else "\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(before + sep + "\n".join(lines) + "\n")
    return path


def remove(root: Path, name: str) -> Path:
    """Delete [targets.NAME]'s lines - its header to the next table - and no
    others."""
    path = declaration_path(root)
    if name not in read(root):
        raise DeployError("arguments", f"no target {name!r} in {path}")
    lines = path.read_text().splitlines(keepends=True)
    header = re.compile(rf"^\s*\[targets\.{re.escape(name)}\]\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines) if header.match(ln)), None)
    if start is None:
        raise DeployError("arguments", f"{name} is not a [targets.{name}] table in {path}")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines)
    )
    path.write_text("".join(lines[:start] + lines[end:]))
    return path
