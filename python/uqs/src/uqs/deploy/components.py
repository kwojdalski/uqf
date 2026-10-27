"""What a release runs on - q, TorQ and Python - as components, each in a
mode (#1557, step 4).

A component is either `existing`, a path already on the server that
preflight resolves and probes, or `bundled`, an artifact shipped with the
release. TorQ can be bundled (step 5, deploy/torq_runtime.py): its fields
are `torq_runtime`, the artifact `uqs deploy build-runtime torq` makes, and
`torq_runtime_sha256` to pin it. So can q (step 6, deploy/q_runtime.py) -
PeachQ only, `q_runtime` and `q_runtime_sha256`, from `uqs deploy
build-runtime q`; KX's q is licensed to its server, and stays `existing`.
So can Python (step 7, deploy/python_runtime.py): `python_runtime` and
`python_runtime_sha256`, the CPython `uqs deploy build-runtime python` packs,
which the release's .venv is made from - or, with no `python_runtime`, the
one the release itself carries (#1646).

`existing` mode's fields are the push options that already exist - qcmd and
qhome for q; torq_home, torq_app_home and torq_launcher for TorQ; Python has
none, uv finds it - so a source-only push is configured exactly as before.
The one new key per component is its mode, `q_mode`, `torq_mode` and
`python_mode`, defaulting to `existing`, layered like any other key.

Switching a mode keeps nothing of the old one. A layer (or flag) that sets a
component's mode to something other than what the layers below it chose
drops the old mode's fields they set (`switch`); a field of another mode set
at or above that layer is refused (`check`). So `[defaults] qcmd = ...` under
a target with `q_mode = "bundled"` is dropped, never inherited.

`observed` is what the report and the deploy history record of each: its
mode, the path preflight resolved, the version it found, and what the server
must provide that no release carries - a q licence above all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uqs.deploy.config import Config

COMPONENTS = ("q", "torq", "python")
EXISTING, BUNDLED = "existing", "bundled"
MODES = (EXISTING, BUNDLED)
#: The push options each mode of each component reads.
FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "q": {EXISTING: ("qcmd", "qhome"), BUNDLED: ("q_runtime", "q_runtime_sha256")},
    "torq": {
        EXISTING: ("torq_home", "torq_app_home", "torq_launcher"),
        BUNDLED: ("torq_runtime", "torq_runtime_sha256"),
    },
    "python": {EXISTING: (), BUNDLED: ("python_runtime", "python_runtime_sha256")},
}
#: The field a mode cannot go without. A bundled Python can do without
#: `python_runtime` when the release carries one (`uqs deploy build
#: --python-runtime`, #1646): only the artifact says, so bundled.attach checks.
REQUIRED = {(c, BUNDLED): f"{c}_runtime" for c in COMPONENTS if c != "python"}


def mode_key(component: str) -> str:
    return f"{component}_mode"


MODE_KEYS = tuple(mode_key(c) for c in COMPONENTS)


def switch(options: dict, sources: dict[str, str], given: dict) -> None:
    """Before a layer's settings `given` go over `options` (what the layers
    below set, with their `sources`): for each component whose mode `given`
    changes, drop the old mode's fields the layers below set."""
    for component in COMPONENTS:
        key = mode_key(component)
        if key not in given:
            continue
        old = options.get(key, EXISTING)
        if given[key] == old:
            continue
        for name in FIELDS[component].get(old, ()):
            if name not in given:
                options.pop(name, None)
                sources.pop(name, None)


def check(modes: dict[str, str], settings: dict[str, object]) -> None:
    """Each component's mode is a mode, and no field of another mode is set
    beside it. Raises ValueError, naming the key."""
    for component, mode in modes.items():
        key = mode_key(component)
        if mode not in MODES:
            raise ValueError(f"{key} {mode!r} is not a mode - {' or '.join(MODES)}")
        others = [m for m in MODES if m != mode]
        stale = [
            f for m in others for f in FIELDS[component][m] if settings.get(f) not in (None, "")
        ]
        if stale:
            raise ValueError(
                f"{key} is {mode}, but {', '.join(stale)} - {' and '.join(others)} mode's - "
                f"{'is' if len(stale) == 1 else 'are'} set too. A layer that switches "
                f"{component}'s mode drops the old mode's settings; none may be set again above it"
            )
        needed = REQUIRED.get((component, mode))
        if needed and settings.get(needed) in (None, ""):
            raise ValueError(
                f"{key} is {mode}, and needs {needed}: the artifact `uqs deploy build-runtime "
                f"{component}` makes"
            )


def observed(
    cfg: Config, facts: dict[str, str], python: str, release: str, bundled: dict[str, dict]
) -> dict[str, dict]:
    """Each component as preflight found it, for the report and the history:
    mode, path, the version observed (None when nothing reads it) and what
    the server must provide. `bundled` holds each bundled component's record,
    its runtime's (torq_runtime.Runtime.record, q_runtime.QRuntime.record),
    in place of the existing one's (python_runtime.PythonRuntime.record too)."""
    found = {
        "q": {
            "mode": cfg.q_mode,
            "path": facts.get("qcmd") or None,
            "home": facts.get("qhome") or None,
            "version": facts.get("qversion") or None,
            "requires": ["q on the server and, for kdb+ or KDB-X, its licence in QHOME"],
        },
        "torq": {
            "mode": cfg.torq_mode,
            "path": cfg.torq_home,
            "app_home": cfg.torq_app_home,
            "launcher": cfg.torq_launcher,
            # preflight checks TorQ's files, and reads no version from them
            "version": None,
            "requires": [
                "TorQ and the finance starter pack on the server",
                "envsubst and rlwrap, for torq.sh",
            ],
        },
        "python": {
            "mode": cfg.python_mode,
            # the release's own environment, made from the Python uv found
            "path": f"{release}/.venv/bin/python",
            "version": facts.get("python") or None,
            "requires": [f"uv, and a Python {python} it finds"],
        },
    }
    for name, record in bundled.items():
        found[name] = {"mode": BUNDLED, **record}
    return found


def plan_lines(cfg: Config, found: dict[str, dict]) -> list[str]:
    """The COMPONENTS block of the plan: each component's mode, where the
    mode came from, what preflight found and what the server provides."""
    lines = ["COMPONENTS - what the release runs on:"]
    for name, c in found.items():
        key = mode_key(name)
        # without a target, a mode that is not the default came from its flag
        flag = "default" if c["mode"] == EXISTING else "--" + key.replace("_", "-")
        source = cfg.sources.get(key, flag)
        version = f"version {c['version']}" if c["version"] else "version not read"
        path = c["path"] or "unset"
        lines.append(f"  {name:<7}{c['mode']} ({source}): {path}, {version}")
        if c.get("sha256"):
            lines.append(f"           runtime sha256 {c['sha256']}: {c['install']}")
        if c.get("carried_by"):
            lines.append(f"           the runtime {c['carried_by']} carries")
        lines += [f"           needs {r}" for r in c["requires"]]
    return [*lines, ""]
