"""Every copy of a tool's version pin agrees with the copy that is its authority (#1838).

A tool version that CI, install.sh and the attestation script must all run is
written in several files, and the files cannot all read one: a workflow's
`with:` input is a literal, install.sh is bash, a README is prose. So each
copy stays where it is, and this rule holds them equal. A version bump that
misses one copy otherwise leaves CI, a developer's install and
scripts/dev/attest_kdbx.py running different tools, and nothing says so.

PINS IS THE ONE LIST. Each pin names its copies in order, and the FIRST copy
found is the pin's authority: what `pinned()` returns, and what every other
copy is compared with. Code that can read the authority does (the install.sh
test reads `pinned("pre-commit")` rather than repeating it); the rest is
listed here.

    uv          the setup-uv `version:` in .github/workflows/ci.yml
    pre-commit  `pre-commit==` in .github/workflows/ci.yml, which the hooks job runs
    python      requires-python's lower bound in the root pyproject.toml - the
                Python every workflow sets up, ruff targets and a release builds for
    ruff        `ruff==` in the root pyproject.toml, which the pre-commit hook's rev follows
    node        web/package.json "engines", written shorter in install.sh and the READMEs
    node-ci     the Node CI's web job sets up, which web/README.md quotes

Not here, because each already has one home that the others read: qlinter
(.qlinter-version), d2 (diagrams.py's D2_VERSION) and PeachQ
(scripts/peachq/pin.json). Action SHAs are pinned per use on purpose.

A copy that disappears fails the rule too, unless it is marked optional (a
passing mention in an agent's notes): a site that stopped matching - a step
reworded, a file moved - would otherwise drop out of the comparison silently,
and the rule would hold fewer copies equal than it claims to.

    python3 scripts/gates/check.py tool-pins
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: (line number, value) pairs found in one file's text.
Found = list[tuple[int, str]]


def _regex(pattern: str) -> Callable[[str], Found]:
    """Group 1 of `pattern` on every line it matches."""
    rx = re.compile(pattern)
    return lambda text: [
        (n, m.group(1)) for n, line in enumerate(text.splitlines(), 1) if (m := rx.search(line))
    ]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _in_block(anchor: str, key: str) -> Callable[[str], Found]:
    """`key`'s value inside each YAML list item whose line matches `anchor`:
    the setup-uv step's `version:`, a pre-commit repo's `rev:`. The item ends
    at the next line indented no deeper than its dash, so a key belonging to
    the following step is never taken for this one's."""
    head, value = re.compile(anchor), re.compile(rf'^\s*{key}:\s*"?([^"\s#]+)"?')

    def read(text: str) -> Found:
        lines, out = text.splitlines(), []
        for i, line in enumerate(lines):
            if not head.search(line):
                continue
            dash = _indent(line) if line.lstrip().startswith("- ") else _indent(line) - 2
            for n in range(i + 1, len(lines)):
                body = lines[n]
                if not body.strip() or body.lstrip().startswith("#"):
                    continue
                if _indent(body) <= dash:
                    break
                if m := value.match(body):
                    out.append((n + 1, m.group(1)))
                    break
        return out

    return read


def _node_range(text: str) -> Found:
    """web/package.json's engines.node, at the line that writes it."""
    node = json.loads(text).get("engines", {}).get("node")
    return [(n, node) for n, ln in enumerate(text.splitlines(), 1) if node and f'"{node}"' in ln]


def _short_range(value: str) -> str:
    """`^22.13.0 || ^24.0.0` and the table cell `^22.13 \\|\\| ^24` alike, as `^22.13 || ^24`."""
    terms = value.replace("\\|", "|").split("||")
    return " || ".join(re.sub(r"(\.0)+$", "", t.strip()) for t in terms)


def _py_target(value: str) -> str:
    """ruff's `py314` as `3.14`."""
    return f"3.{value}"


@dataclass(frozen=True)
class Site:
    #: Files holding a copy: a path or a glob, relative to the repository root.
    pattern: str
    #: The copies in one file's text.
    read: Callable[[str], Found]
    #: How a copy is written here, turned into the form the authority uses.
    normal: Callable[[str], str] = str
    #: False for a passing mention that may be removed without replacing it.
    required: bool = True


@dataclass(frozen=True)
class Pin:
    tool: str
    #: Where its copies are. The first copy found is the authority.
    sites: tuple[Site, ...]


WORKFLOWS = ".github/workflows/*.yml"
CI = ".github/workflows/ci.yml"
_SETUP_UV = r"uses:\s*astral-sh/setup-uv@"
_PRE_COMMIT = _regex(r"pre-commit==(\d[\w.]*)")

#: The one list.
PINS: tuple[Pin, ...] = (
    Pin(
        "uv",
        (
            Site(CI, _in_block(_SETUP_UV, "version")),
            Site(WORKFLOWS, _in_block(_SETUP_UV, "version")),
        ),
    ),
    Pin(
        "pre-commit",
        (
            Site(CI, _PRE_COMMIT),
            Site(WORKFLOWS, _PRE_COMMIT, required=False),
            Site("install.sh", _PRE_COMMIT),
            Site("scripts/dev/attest_kdbx.py", _PRE_COMMIT),
            Site("scripts/dev/hooks_full.py", _PRE_COMMIT),
            Site(".claude/agents/*.md", _PRE_COMMIT, required=False),
        ),
    ),
    Pin(
        "python",
        (
            Site("pyproject.toml", _regex(r'^requires-python\s*=\s*">=\s*(3\.\d+)')),
            Site("python/*/pyproject.toml", _regex(r'^requires-python\s*=\s*">=\s*(3\.\d+)')),
            Site("python/*/.python-version", _regex(r"^(3\.\d+)\s*$"), required=False),
            Site("pyproject.toml", _regex(r'^target-version\s*=\s*"py3(\d+)"'), _py_target),
            Site(WORKFLOWS, _in_block(_SETUP_UV, "python-version")),
            Site(WORKFLOWS, _regex(r"uv run [^\n]*--python (3\.\d+)"), required=False),
        ),
    ),
    Pin(
        "ruff",
        (
            Site("pyproject.toml", _regex(r'"ruff==(\d[\w.]*)"')),
            Site(
                ".pre-commit-config.yaml",
                _in_block(r"repo:\s*https://github\.com/astral-sh/ruff-pre-commit", "rev"),
                lambda v: v.removeprefix("v"),
            ),
        ),
    ),
    Pin(
        "node",
        (
            Site("web/package.json", _node_range, _short_range),
            Site("install.sh", _regex(r'^NODE_RANGE="([^"]+)"'), _short_range),
            Site("README.md", _regex(r"\*\*Node\*\*[^`]*`([^`]+)`"), _short_range),
            Site("web/README.md", _regex(r"Requires Node \*\*`([^`]+)`\*\*"), _short_range),
        ),
    ),
    Pin(
        "node-ci",
        (
            Site(CI, _in_block(r"uses:\s*actions/setup-node@", "node-version")),
            Site("web/README.md", _regex(r"CI uses Node (\d+)")),
        ),
    ),
)


@dataclass(frozen=True)
class Copy:
    path: str
    line: int
    #: As written in the file.
    written: str
    #: In the authority's form.
    value: str

    def where(self) -> str:
        return f"{self.path}:{self.line}"


def _files(root: Path, pattern: str) -> list[Path]:
    return sorted(p for p in root.glob(pattern) if p.is_file())


def copies(root: Path, pin: Pin) -> tuple[list[Copy], list[Site]]:
    """(every copy of `pin`, in site order without repeats; the required
    sites that hold none)."""
    found: dict[tuple[str, int], Copy] = {}
    empty: list[Site] = []
    for site in pin.sites:
        here = 0
        for path in _files(root, site.pattern):
            rel = path.relative_to(root).as_posix()
            for line, written in site.read(path.read_text(encoding="utf-8")):
                here += 1
                found.setdefault((rel, line), Copy(rel, line, written, site.normal(written)))
        if not here and site.required:
            empty.append(site)
    return list(found.values()), empty


def problems(root: Path, pins: tuple[Pin, ...] = PINS) -> list[str]:
    """One line per copy that disagrees with its authority, naming both by
    file and line, and one per required site that holds no copy."""
    out: list[str] = []
    for pin in pins:
        found, empty = copies(root, pin)
        for site in empty:
            out.append(
                f"{site.pattern}: holds no {pin.tool} pin any more - if it was moved or "
                "reworded, point scripts/gates/policy/tool_pins.py at the new copy"
            )
        if not found:
            continue
        authority, *rest = found
        for c in rest:
            if c.value != authority.value:
                out.append(
                    f"{c.where()}: {pin.tool} {c.written!r}, but its authority "
                    f"{authority.where()} pins {authority.written!r} - bump every copy together"
                )
    return out


def pinned(tool: str, root: Path | None = None) -> str:
    """The version the authority for `tool` pins (`3.14`, `4.6.2`)."""
    root = root or Path(__file__).resolve().parents[3]
    pin = next(p for p in PINS if p.tool == tool)
    found, _ = copies(root, pin)
    if not found:
        raise LookupError(f"no copy of the {tool} pin in {root}")
    return found[0].value
