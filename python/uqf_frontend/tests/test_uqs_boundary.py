"""The web server never imports uqs's command line (#2655).

`from uqs.cli import anything` runs uqs/cli/__init__.py, which assembles the
whole `uqs` app - every command module, typer and rich - inside the web
server, and couples a route to the CLI's churn. What the frontend needs from
uqs lives outside `uqs.cli` (stack/, deploy/, timefmt...), and the command and
the route both call it. Most of the frontend's uqs imports sit inside
functions, so a break would surface as a 500 on one route, not at startup:
this reads every module's source instead of importing it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

FORBIDDEN = "uqs.cli"


def _is_cli(module: str) -> bool:
    return module == FORBIDDEN or module.startswith(f"{FORBIDDEN}.")


def _cli_imports(tree: ast.Module) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(node.lineno, a.name) for a in node.names if _is_cli(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _is_cli(node.module):
                found.append((node.lineno, node.module))
            elif node.module == "uqs":
                found += [(node.lineno, "uqs.cli") for a in node.names if a.name == "cli"]
    return found


def test_no_frontend_module_imports_uqs_cli():
    modules = sorted(SRC.rglob("*.py"))
    assert modules, f"no sources under {SRC}"
    hits = [
        f"{path.relative_to(SRC)}:{line} imports {name}"
        for path in modules
        for line, name in _cli_imports(ast.parse(path.read_text(), str(path)))
    ]
    assert not hits, "the frontend imports uqs's command line:\n" + "\n".join(hits)


def test_the_scan_sees_every_spelling():
    spellings = [
        "import uqs.cli",
        "import uqs.cli.data_diff as d",
        "from uqs.cli import data_diff",
        "from uqs.cli.json_out import emit",
        "from uqs import cli",
        "def f():\n    from uqs.cli import data_diff",
    ]
    for text in spellings:
        assert _cli_imports(ast.parse(text)), text
    for text in ["from uqs.stack import data_diff", "import uqs.client", "from uqs import timefmt"]:
        assert not _cli_imports(ast.parse(text)), text
