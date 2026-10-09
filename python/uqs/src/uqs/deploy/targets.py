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
"""

from __future__ import annotations

import inspect
import json
import os
import re
import tomllib
from pathlib import Path

from uqs.deploy.config import Config, DeployError, make_config

DECLARATION = "deploy_targets.toml"
DECLARATION_ENV = "UQS_DEPLOY_TARGETS"
#: Options a file may not set: what is deployed, and whether it is a rehearsal,
#: are this run's to say.
PER_RUN = frozenset({"artifact", "dry_run"})
#: Options make_config takes as one comma-separated string; a file lists them.
LISTED_AS_TEXT = frozenset({"jobs", "live_check"})
SETTABLE = frozenset(inspect.signature(make_config).parameters) - PER_RUN
#: Keys a target may hold that are not push options: the runtime it must run
#: (checked against the artifact's), and labels, which `uqs target list` shows
#: and nothing deploys (#956).
NOT_PUSH_OPTIONS = frozenset({"runtime", "labels"})


def declaration_path(root: Path) -> Path:
    configured = os.environ.get(DECLARATION_ENV, "").strip()
    return Path(configured).expanduser() if configured else root / DECLARATION


def read(root: Path) -> dict[str, dict]:
    """{target: its settings}, from the declaration; refused when absent."""
    path = declaration_path(root)
    if not path.is_file():
        raise DeployError("arguments", f"--target needs {path}, which does not exist")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise DeployError("arguments", f"{path}: not TOML ({exc})") from None
    found = data.get("targets")
    if not isinstance(found, dict) or not all(isinstance(v, dict) for v in found.values()):
        raise DeployError("arguments", f"{path}: declare targets as [targets.<name>] tables")
    return found


def _options(name: str, declared: dict) -> dict:
    """One target's settings, as make_config's keyword arguments."""
    unknown = sorted(set(declared) - SETTABLE - NOT_PUSH_OPTIONS)
    if unknown:
        raise DeployError(
            "arguments",
            f"target {name}: unknown setting(s) {', '.join(unknown)} - a target sets push's "
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
    settings, with `explicit` - the flags given on the command line - over
    them, each checked by make_config."""
    declared = read(root)
    wanted = [n.strip() for n in names.split(",") if n.strip()]
    missing = [n for n in wanted if n not in declared]
    if not wanted or missing:
        known = ", ".join(sorted(declared)) or "none"
        asked = ", ".join(missing) or repr(names)
        raise DeployError(
            "arguments", f"no target {asked} in {declaration_path(root)} - it declares: {known}"
        )
    out = []
    for name in wanted:
        options = _options(name, declared[name])
        sources = {k: f"target {name}" for k in options}
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
        cfg.runtime = declared[name].get("runtime")
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
