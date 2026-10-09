"""sources.csv: what each external source connects to (#718).

One row per source - its transport, the non-secret setting that transport
opens (an HDB path, a host:port, an ODBC connection template) and, when the
setting needs a secret, the NAME of the environment variable holding it. q
reads it in `.qetl.source.read_settings` (src/etl/core/source_credentials.q);
this module reads it for uqs's own commands and the scaffold, with the same
rules, so the two refuse the same files.

WHICH FILE. TorQ's own rule, `.proc.getconfigfile`, which picks process.csv:
the first of the application layer (KDBAPPCONFIG), the service layer
(KDBSERVCONFIG) and TorQ's base (KDBCONFIG) that has the file - a whole file
at a time, no row merging. The layers come from `build_env`, the environment
every process uqs starts gets, so `.qtorq.load_source_settings` inside the
stack and `selected` here read one file. The service layer's is this tree's
scripts/torqconfig/sources.csv; the application layer's is the operator's,
gitignored, and the one `add_stub` writes.

NEVER A SECRET. A row may only name a variable; a password written inline is
refused here and in q, and `describe` reports whether a variable is set,
never its value.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

from uqs.paths import SOURCE_DIR, UqsError, UqsPaths
from uqs.stack.env import build_env

#: The file, in each config layer.
SETTINGS_FILE = "sources.csv"

#: Its columns, in order - exactly these (`.qetl.source.settings_cols`).
COLUMNS = ("source", "transport", "setting", "secret_env")

#: What `add_stub` writes; refused when the source connects.
STUB = "SCAFFOLDED"

#: Where a setting takes its secret (`.qetl.source.secret_placeholder`).
SECRET_PLACEHOLDER = "{secret}"

#: Keys of a key=value setting whose value is a secret (`.qetl.source.secret_keys`).
SECRET_KEYS = frozenset(
    ("pwd", "password", "passwd", "secret", "token", "apikey", "api_key", "key")
)

#: The config layers, most specific first - `.proc.getconfig`'s order.
LAYER_VARS = ("KDBAPPCONFIG", "KDBSERVCONFIG", "KDBCONFIG")

_PATH_VAR = re.compile(r"\$\{([^}]*)\}")


@dataclass(frozen=True)
class Row:
    """One configured source."""

    source: str
    transport: str
    setting: str
    secret_env: str
    line: int


def layers(paths: UqsPaths) -> list[Path]:
    """Where sources.csv may be, most specific first. A runtime without a
    service layer (the pure `torq` one) has two."""
    env = build_env(paths)
    return [Path(env[var]) / SETTINGS_FILE for var in LAYER_VARS if var in env]


def selected(paths: UqsPaths) -> Path | None:
    """The file the stack reads: the first layer that has one, or None."""
    return next((p for p in layers(paths) if p.is_file()), None)


def inline_secret(transport: str, setting: str) -> bool:
    """Does `setting` carry a secret it should be referencing? Mirrors
    `.qetl.source.inline_secret`."""
    for pair in setting.split(";"):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        if key.strip().lower() in SECRET_KEYS and value.strip() != SECRET_PLACEHOLDER:
            return True
    parts = setting.split(":")
    return transport == "ipc" and len(parts) >= 4 and parts[-1] != SECRET_PLACEHOLDER


def read(path: Path, transports: tuple[str, ...]) -> list[Row]:
    """Every row of a settings file, or an error naming the file, the line and
    what is wrong - the checks `.qetl.source.read_settings` makes."""
    who = f"source settings {path}"
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        raise UqsError(f"{who}: empty - it needs a header, {', '.join(COLUMNS)}")
    header = tuple(h.strip() for h in next(csv.reader([lines[0]])))
    if missing := [c for c in COLUMNS if c not in header]:
        raise UqsError(f"{who}: its header lacks {', '.join(missing)}")
    if extra := [c for c in header if c not in COLUMNS]:
        raise UqsError(
            f"{who}: {', '.join(extra)} is not a column. "
            "A secret goes in an environment variable named by secret_env"
        )
    if header != COLUMNS:
        raise UqsError(f"{who}: its columns must be in the order {', '.join(COLUMNS)}")
    rows: list[Row] = []
    seen: dict[str, int] = {}
    for line, fields in enumerate(csv.reader(lines[1:]), start=2):
        fields += [""] * (len(COLUMNS) - len(fields))
        row = Row(*(f.strip() for f in fields[: len(COLUMNS)]), line=line)
        if not (row.source and row.transport and row.setting):
            raise UqsError(f"{who}: line {line} needs a source, a transport and a setting")
        if row.transport not in transports:
            raise UqsError(
                f"{who}: line {line}'s transport {row.transport} is not one of "
                f"{', '.join(transports)}"
            )
        if row.source in seen:
            raise UqsError(
                f"{who}: {row.source} has more than one row - lines {seen[row.source]}, {line}"
            )
        if inline_secret(row.transport, row.setting):
            raise UqsError(
                f"{who}: line {line} holds a secret inline - write {SECRET_PLACEHOLDER} "
                "and name its variable in secret_env"
            )
        seen[row.source] = line
        rows.append(row)
    return rows


def credential_var(source: str) -> str:
    """The per-source override, `.qetl.source.credential_var`."""
    return f"UQF_SOURCE_CRED_{source.upper()}"


def problems(row: Row, path_vars: dict[str, str], environ: dict[str, str]) -> list[str]:
    """What would stop `row` resolving when its source connects - the checks
    `.qetl.source.resolve_setting` makes that need no source declaration.
    Names variables, never their values."""
    found = []
    if row.setting == STUB:
        found.append("still the SCAFFOLDED stub")
    for name in _PATH_VAR.findall(row.setting):
        if name not in path_vars:
            found.append(f"${{{name}}} is not a path it may use")
        elif not path_vars[name]:
            found.append(f"${{{name}}} is not set")
    uses = SECRET_PLACEHOLDER in row.setting
    if uses and not row.secret_env:
        found.append("{secret} with no secret_env")
    elif row.secret_env and not uses:
        found.append("secret_env with no {secret}")
    elif row.secret_env and not environ.get(row.secret_env):
        found.append(f"{row.secret_env} is not set")
    return found


#: `.qtorq.source_settings_path_vars`: the ${VAR}s a setting may use.
PATH_VARS = ("UQF_ROOT", "TORQDATA", "KDBHDB", "KDBWDB")


def describe(paths: UqsPaths, transports: tuple[str, ...]) -> tuple[Path | None, list[dict]]:
    """The selected file and, per row, where its source's credential comes
    from and what would stop it resolving - for `uqs config sources`."""
    path = selected(paths)
    if path is None:
        return None, []
    env = build_env(paths)
    path_vars = {name: env.get(name, "") for name in PATH_VARS}
    out = []
    for row in read(path, transports):
        override = bool(os.environ.get(credential_var(row.source)))
        out.append(
            {
                "source": row.source,
                "transport": row.transport,
                "line": row.line,
                "secret_env": row.secret_env,
                "origin": credential_var(row.source) if override else f"{path.name}:{row.line}",
                "problems": problems(row, path_vars, dict(os.environ)),
            }
        )
    return path, out


def add_stub(paths: UqsPaths, source: str, transport: str, transports: tuple[str, ...]) -> str:
    """Give `source` a SCAFFOLDED row in the application layer's file.

    That layer replaces every lower one whole, so a new application file
    starts as a copy of the file selected now - the stub never hides the
    rows the stack was reading. A source that already has a row keeps it: an
    operator's value is never overwritten. Returns what was done.
    """
    if transport not in transports:
        raise UqsError(f"--transport must be one of {', '.join(transports)}, not {transport!r}")
    app = layers(paths)[0]
    current = selected(paths)
    rows = read(current, transports) if current else []
    if any(r.source == source for r in rows):
        return f"{source} already has a row in {current} - left as it is"
    text = current.read_text(encoding="utf-8") if current else ",".join(COLUMNS) + "\n"
    if not text.endswith("\n"):
        text += "\n"
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(f"{text}{source},{transport},{STUB},\n", encoding="utf-8")
    copied = f", starting from a copy of {current}" if current and current != app else ""
    return f"added a {STUB} row for {source} to {app}{copied}"


_TRANSPORT_LINE = re.compile(r"^transport:`(\w+)", re.MULTILINE)


def declared_transport(repo_root: Path, source: str, default: str) -> str:
    """The transport src/etl/sources/<source>.q declares - its top-level
    `transport:` line, as the scaffold writes it - or `default` when it
    declares none. A source with no such file is refused."""
    path = repo_root / SOURCE_DIR / f"{source}.q"
    if not path.is_file():
        raise UqsError(f"no source {source!r}: {path.relative_to(repo_root)} does not exist")
    found = _TRANSPORT_LINE.search(path.read_text(encoding="utf-8"))
    return found.group(1) if found else default
