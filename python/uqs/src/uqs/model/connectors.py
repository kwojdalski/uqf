"""Connector modules read as text: what each external feed is called and produces.

`model/` sits below `external/` and may not import it (test_module_split.py),
yet the dependency warnings need to know which tables a connector outside the
plant feeds (#1776). So the connector modules - the tree's own under
uqs.external, and every installed bundle's under src/etl/connectors/ - are
parsed, never imported: a feed's name and `produces=` must be string literals
or module-level string constants. `external.feeds.discover`, which does
import them, refuses a module whose text and import disagree.
"""

from __future__ import annotations

import ast
import functools
import json
from pathlib import Path

from uqs.paths import BUNDLE_LEDGER, PACKAGE_DIR

#: Where this tree's own connectors sit: the uqs.external package, whose
#: `*_feed.py` modules each declare a FEED (external/feeds.py).
CONNECTOR_DIR = PACKAGE_DIR / "src" / "uqs" / "external"


def bundle_connectors(root: Path) -> dict[str, Path]:
    """{bundle/module: path} for every `*_feed.py` an installed bundle's ledger
    entry lists under `connectors` - the copies in the tree, never the
    bundle's own folder, and nothing a folder merely holds."""
    ledger = root / BUNDLE_LEDGER
    if not ledger.is_file():
        return {}
    try:
        entries = json.loads(ledger.read_text())
    except ValueError:
        return {}  # stack/bundles.py's read_ledger names this to whoever installs
    return {
        f"{bundle}/{Path(rel).stem}": root / rel
        for bundle, entry in sorted(entries.items())
        for rel in entry.get("connectors", [])
        if rel.endswith("_feed.py")
    }


def feed_produces(path: Path) -> dict[str, str]:
    """{table: who} from every `ExternalFeed(..., produces={...})` in `path`'s text.

    Raises ValueError for a `produces=` that is not a dict of string literals
    or module-level string constants - this module's own, or one it imports
    by name from a sibling module."""
    return {table: who for _name, table, who in _read(path) if table}


def feed_names(path: Path) -> list[str]:
    """The name of every feed `path`'s text declares - a literal or a module
    constant, or ValueError."""
    names = list(dict.fromkeys(name for name, _table, _who in _read(path)))
    if None in names:
        raise ValueError(f"{path}: a feed's name must be a string literal or module constant")
    return [n for n in names if n is not None]


def _read(path: Path) -> tuple[tuple[str | None, str, str], ...]:
    stat = path.stat()
    return _feeds_text(str(path), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=64)
def _feeds_text(path: str, _mtime: int, _size: int) -> tuple[tuple[str | None, str, str], ...]:
    """(feed name, table, who) per produced table - ("name", "", "") for a feed
    producing nothing - for every ExternalFeed call in the file."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names = _constants(tree, Path(path).parent)
    out: list[tuple[str | None, str, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _callee(node.func) == "ExternalFeed"):
            continue
        given = node.args[0] if node.args else next(
            (k.value for k in node.keywords if k.arg == "name"), None
        )  # fmt: skip
        try:
            name: str | None = _text(given, names, path)
        except ValueError:
            name = None
        out.append((name, "", ""))
        for keyword in node.keywords:
            if keyword.arg != "produces":
                continue
            value = keyword.value
            if not isinstance(value, ast.Dict) or any(k is None for k in value.keys):
                raise ValueError(f"{path}: a feed's produces= must be a dict literal")
            for k, v in zip(value.keys, value.values, strict=True):
                out.append((name, _text(k, names, path), _text(v, names, path)))
    return tuple(out)


def _callee(func: ast.expr) -> str:
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _text(node: ast.expr | None, names: dict[str, str], path: str) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    raise ValueError(
        f"{path}: a feed's produces= holds {ast.unparse(node) if node else '**'}, which is "
        "not a string literal or a module-level string constant - model/connectors.py "
        "reads it without importing the connector"
    )


def _constants(tree: ast.Module, folder: Path, follow: bool = True) -> dict[str, str]:
    """Module-level `NAME = "text"` bindings, and names imported from a sibling."""
    out: dict[str, str] = {}
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else (
            node.target if isinstance(node, ast.AnnAssign) else None
        )  # fmt: skip
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                out[target.id] = value.value
        elif follow and isinstance(node, ast.ImportFrom) and node.module:
            sibling = folder / f"{node.module.rsplit('.', 1)[-1]}.py"
            if node.module.startswith("uqs.external.") and sibling.is_file():
                theirs = _constants(ast.parse(sibling.read_text(encoding="utf-8")), folder, False)
                for alias in node.names:
                    if alias.name in theirs:
                        out[alias.asname or alias.name] = theirs[alias.name]
    return out
