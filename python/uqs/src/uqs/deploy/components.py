"""What a release runs on - q, TorQ and Python - as components, each in a
mode (#1557, step 4).

A component is either `existing`, a path already on the server that
preflight resolves and probes, or `bundled`, an artifact shipped with the
release. Only `existing` is supported yet: `bundled` comes with TorQ, q and
Python's own steps of #1557, and until then it is refused.

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
#: The modes a push accepts today; `bundled` joins as each step lands.
SUPPORTED = frozenset({EXISTING})
#: The issue step that adds each component's bundled mode.
BUNDLED_IN = {"torq": "step 5", "q": "step 6", "python": "step 7"}
#: The push options each mode of each component reads.
FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "q": {EXISTING: ("qcmd", "qhome"), BUNDLED: ()},
    "torq": {EXISTING: ("torq_home", "torq_app_home", "torq_launcher"), BUNDLED: ()},
    "python": {EXISTING: (), BUNDLED: ()},
}


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
    """Each component's mode is one this push supports, and no field of
    another mode is set beside it. Raises ValueError, naming the key."""
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
        if mode not in SUPPORTED:
            raise ValueError(
                f"{key} {mode} is not yet supported - #1557 {BUNDLED_IN[component]} adds it; "
                f"use {EXISTING}, its paths on the server"
            )


def observed(cfg: Config, facts: dict[str, str], python: str, release: str) -> dict[str, dict]:
    """Each component as preflight found it, for the report and the history:
    mode, path, the version observed (None when nothing reads it) and what
    the server must provide."""
    return {
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


def plan_lines(cfg: Config, found: dict[str, dict]) -> list[str]:
    """The COMPONENTS block of the plan: each component's mode, where the
    mode came from, what preflight found and what the server provides."""
    lines = ["COMPONENTS - what the release runs on:"]
    for name, c in found.items():
        source = cfg.sources.get(mode_key(name), "default")
        version = f"version {c['version']}" if c["version"] else "version not read"
        path = c["path"] or "unset"
        lines.append(f"  {name:<7}{c['mode']} ({source}): {path}, {version}")
        lines += [f"           needs {r}" for r in c["requires"]]
    return [*lines, ""]
