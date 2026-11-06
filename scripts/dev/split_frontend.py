#!/usr/bin/env python3
"""Split the frontend's three shared files one module per area (#2168).

Most merge conflicts between frontend PRs were in three files every change
touches: `uqf_frontend/models.py`, `uqf_frontend/app.py` and
`web/src/api.ts`. This moves them apart, as a pure move:

- models.py becomes `uqf_frontend/models/<area>.py`;
- the routes create_app declares become `uqf_frontend/routes/<area>.py`, each
  an `install(app, ...)` create_app calls in the order the routes were
  declared, so FastAPI registers them in the same order and the OpenAPI
  document is byte for byte the same (the script checks it, and writes the
  document it held the split to as python/uqf_frontend/tests/openapi.json);
  what more than one area and app.py share goes to `routes/common.py`;
- api.ts becomes `web/src/api/<area>.ts`.

There is no facade over the new modules - the repository keeps no
re-exports - so every caller's import is rewritten to name the module that
defines what it uses, and the handful of tests that read the old files as a
whole are patched (PATCHES) to read every module.

It is a script rather than a hand edit so it can be re-run on a master that
has moved: it parses the three files (Python's ast; the TypeScript through
the oxc parser rolldown ships, split_frontend_parse.mjs) and places each
statement by the rules below, so a model, route or API function added since
lands in its section's module. A statement no rule places, a group of routes
that is not contiguous, an import cycle, or a patch whose text has changed
stops it with the reason, before anything is written.

    uv run python scripts/dev/split_frontend.py   # from the repository root

It needs web/node_modules (`npm ci` in web/) for the parser and prettier.
"""

# ruff: noqa: E501 - PATCHES quote the lines they replace verbatim, long or not

from __future__ import annotations

import ast
import builtins
import difflib
import json
import re
import subprocess
import sys
import textwrap
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PKG = Path("python/uqf_frontend/src/uqf_frontend")
WEB = Path("web")
API_TS = WEB / "src" / "api.ts"
API_DIR = WEB / "src" / "api"
SNAPSHOT = Path("python/uqf_frontend/tests/openapi.json")
PARSER = Path("scripts/dev/split_frontend_parse.mjs")


class SplitError(Exception):
    """Why the split stopped, before it wrote anything."""


# --------------------------------------------------------------- the areas

#: A section banner's words -> the area its statements go to: `# ---- services`
#: in models.py, `// --- control (writes) ---` in api.ts.
SECTIONS = {
    "services": "services",
    "config push": "config_push",
    "control": "control",
    "activity": "activity",
    "settings": "settings",
    "charts": "charts",
    "compare": "compare",
}

#: models.py's statements placed by name: the ones before its first banner,
#: which mixes areas, and the few a section holds for another area. Any
#: other statement goes to its section, or, before the first banner, to the
#: area of the statement before it.
MODEL_AREAS = {
    "Tier": "query",
    "RunState": "ops",
    "STRICT_REQUEST": "base",
    "CONFIRMATION_REQUIRED": "base",
    "refuse_unconfirmed": "base",
    "ConfirmedRequest": "base",
    "Filter": "query",
    "CoverageRequirement": "query",
    "QueryRequest": "query",
    "QueryResponse": "query",
    "IntervalOut": "query",
    "CoverageResponse": "query",
    "ColumnType": "query",
    "ColumnInfo": "query",
    "TableInfo": "query",
    "CatalogResponse": "query",
    "Capability": "access",
    "InspectionOut": "access",
    "CapabilitiesResponse": "access",
    "HealthResponse": "health",
    "OpsTableResponse": "ops",
    "ConnectionsResponse": "ops",
    "UsageResponse": "ops",
    "ProcessHealthOut": "ops",
    "FleetHealthResponse": "ops",
    "WorkerStatusOut": "ops",
    "BackfillStatusResponse": "ops",
    # services and config_push both describe `dest` with it; in either, the
    # other would import it back and the two would be a cycle.
    "DEST": "base",
    # the files view's delete, declared among the control routes
    "RemoteDeleteRequest": "services",
    "RemoteDeleteResult": "services",
    "RemoteDeleteResponse": "services",
}

MODEL_DOCS = {
    "base": "What every area's models share: the strict request config, the "
    "confirmation rule (#2244) and the field descriptions more than one "
    "area uses.",
    "query": "/query, /coverage and /catalog: a filtered read, the coverage it "
    "requires, and the queryable surface.",
    "access": "/me/capabilities: what the caller may do, and inspection mode (#2032).",
    "health": "/health: whether the gateway is up, and what the browser polls by.",
    "ops": "/ops/*: the gateway's queue and connections, the fleet's usage and "
    "health, and the backfill workers' status.",
    "services": "/services, /workspaces and /services/{target}/...: the deploy "
    "targets, the workspace a request reads, tunnels, and a target's files.",
    "config_push": "/services/{target}/config/pushes: config push history (#2002).",
    "control": "/control/*: the routes that change something (writes).",
    "activity": "/activity: the activity history and the browser's event batches (#1954).",
    "settings": "/settings: the admin Settings view's forms and answers (#2046).",
    "charts": "/charts: prepared charts (#2044).",
    "compare": "/compare: two endpoints' tables over one window (#2033).",
}

#: api.ts's statements placed by name, as MODEL_AREAS places models.py's.
TS_AREAS = {
    "Row": "client",
    "Pollable": "client",
    "ColumnType": "query",
    "Column": "query",
    "Catalog": "query",
    "CoverageRequest": "query",
    "Interval": "query",
    "Coverage": "query",
    "Tier": "query",
    "RunState": "ops",
    "FilterOp": "query",
    "QueryFilter": "query",
    "QueryInput": "query",
    "QueryResult": "query",
    "ChartInfo": "charts",
    "ChartList": "charts",
    "ChartResult": "charts",
    "CompareBody": "compare",
    "CompareOutcome": "compare",
    "CompareInterval": "compare",
    "CompareCoverageSide": "compare",
    "CompareSchemaDifference": "compare",
    "CompareRowDifference": "compare",
    "CompareTable": "compare",
    "CompareResult": "compare",
    "ChartSchema": "charts",
    "ChartField": "charts",
    "Health": "health",
    "Capability": "access",
    "Capabilities": "access",
    "Inspection": "access",
    "FormData": "settings",
    "SecretSetting": "settings",
    "SettingChange": "settings",
    "SettingsData": "settings",
    "AccessChangeBody": "settings",
    "RuntimeChangeBody": "settings",
    "SettingsChangeResult": "settings",
    "Worker": "ops",
    "Backfill": "ops",
    "ProcessHealth": "ops",
    "ServiceTarget": "services",
    "Services": "services",
    "Workspace": "services",
    "Workspaces": "services",
    "WorkspaceEndpoint": "services",
    "WorkspaceStatus": "services",
    "RemoteEntry": "services",
    "Release": "services",
    "RemoteFiles": "services",
    "remoteFilesPath": "services",
    "TunnelCheckRecord": "services",
    "TunnelMapping": "services",
    "TunnelPlan": "services",
    "TunnelEndpoint": "services",
    "TunnelCheck": "services",
    "tunnelPath": "services",
    "OpsData": "ops",
    "ErrorBody": "client",
    "errorBody": "client",
    "ApiError": "client",
    "DEFAULT_REQUEST_TIMEOUT_MS": "client",
    "requestTimeoutMs": "client",
    "adoptRequestTimeout": "client",
    "requestTimeout": "client",
    "RequestNote": "client",
    "requestObserver": "client",
    "observeRequests": "client",
    "request": "client",
    "filterValue": "query",
    "qTimespan": "query",
    "validateTime": "query",
    "validateRange": "query",
    "instant": "query",
    # declared among the control routes, used by every write
    "mutate": "client",
    "Correlation": "client",
    "WRITE_TOKEN_KEY": "client",
    "heldToken": "client",
    "writeToken": "client",
    # the files view's upload and delete, declared among the control routes
    # and after the activity history
    "RemoteUpload": "services",
    "UPLOAD_MAX_BYTES": "services",
    "UPLOAD_ROOT": "services",
    "UPLOAD_MAX_DEPTH": "services",
    "UPLOAD_MAX_PATH": "services",
    "UPLOAD_TEMP_PREFIX": "services",
    "UPLOAD_TIMEOUT_MS": "services",
    "UPLOAD_MAX_FILES": "services",
    "UPLOAD_MAX_TOTAL_BYTES": "services",
    "remoteUploadPath": "services",
    "uploadPathProblem": "services",
    "UploadFailed": "services",
    "uploadRemoteFile": "services",
    # declared with the push history, read by `request` for a target workspace
    "authorised": "client",
    "RemoteDeleteRequest": "services",
    "RemoteDeleteResult": "services",
    "RemoteDeleteResponse": "services",
}

TS_DOCS = {
    "client": "The one request path every API call takes: errors, timeouts, "
    "the workspace header, the write token and one-shot writes.",
    "query": "/query, /coverage and /catalog, and the filter values the Desk sends.",
    "access": "/me/capabilities - models/access.py.",
    "health": "/health - models/health.py.",
    "ops": "/ops/* - models/ops.py.",
    "services": "/services, /workspaces and a target's tunnels and files - models/services.py.",
    "config_push": "/services/{target}/config/pushes (#2002) - models/config_push.py.",
    "control": "/control/* (writes) - models/control.py.",
    "activity": "/activity (#1954) - models/activity.py.",
    "settings": "/settings (#2046) - models/settings.py.",
    "charts": "/charts (#2044) - models/charts.py.",
    "compare": "/compare (#2033) - models/compare.py.",
}

#: A route's path -> its area, first match wins. A path none matches stops
#: the split: a new area needs a line here, and a module doc below.
ROUTE_AREAS = (
    (r"/me/capabilities", "access"),
    (r"/health", "health"),
    (r"/catalog", "catalog"),
    (r"/ops/.*", "ops"),
    (r"/workspaces?", "services"),
    (r"/services/\{target\}/config/.*", "config_push"),
    (r"/services(/.*)?", "services"),
    (r"/coverage|/query", "query"),
    (r"/charts(/.*)?", "charts"),
    (r"/compare", "compare"),
    (r"/control(/.*)?", "control"),
    (r"/activity(/.*)?", "activity"),
)

ROUTE_DOCS = {
    "common": "What app.py and more than one route module share: who is "
    "calling, the policy and scope every route is held to, and a q result as "
    "the rows JSON carries.",
    "access": "GET /me/capabilities: what the caller may do (#2045).",
    "health": "GET /health: the gateway's liveness, as the browser polls it.",
    "catalog": "GET /catalog: the queryable surface.",
    "ops": "GET /ops/*: the gateway's queue and connections, the fleet's usage, "
    "clients and health, and the backfill workers' status.",
    "services": "/workspaces, /workspace and /services: the deploy targets, the "
    "workspace a request reads (#2029), a target's tunnels and its files.",
    "query": "GET /coverage and POST /query: a dataset's coverage, and a filtered read held to it.",
    "charts": "/charts: prepared charts (#2044).",
    "compare": "POST /compare: two endpoints' tables over one window (#2033).",
    "config_push": "/services/{target}/config/pushes: config push history (#2002).",
    "control": "/control/*: the routes that change something. Every one is "
    "refused unless UQF_FRONTEND_ENABLE_WRITES is set, checked inside "
    "`control` so a route added later cannot forget it, and passes `guard` "
    "like every read.",
    "activity": "/activity: the activity history and the browser's event batches (#1954).",
}

#: create_app's locals a route reads, as an install() parameter's type. A
#: route reading any other local stops the split: it needs a line here.
LOCAL_TYPES = {
    "settings": "Settings",
    "live": "Callable[[], Settings]",
    "gateway": "Gateway",
    "fleet": "Fleet",
    "policies": "access.PolicyStore",
    "history": "activity.ActivityStore",
    "tables": "catalog.Catalog",
    "_tunnel_gate": "Callable[[str], None]",
}

HTTP_VERBS = {"get", "post", "put", "delete", "patch"}


# ------------------------------------------------------------ Python, by ast


@dataclass
class ImportSpec:
    """How a module binds one name: `import x [as y]` or `from m import x [as y]`."""

    module: str
    name: str | None  # None for `import module`
    asname: str | None

    def line(self) -> str:
        if self.name is None:
            return f"import {self.module}" + (f" as {self.asname}" if self.asname else "")
        alias = f" as {self.asname}" if self.asname else ""
        return f"from {self.module} import {self.name}{alias}"


def import_map(nodes: Sequence[ast.stmt]) -> dict[str, ImportSpec]:
    found: dict[str, ImportSpec] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for a in node.names:
                bound = a.asname or a.name.split(".")[0]
                found[bound] = ImportSpec(a.name, None, a.asname)
        elif isinstance(node, ast.ImportFrom):
            assert node.module and node.level == 0, ast.unparse(node)
            for a in node.names:
                found[a.asname or a.name] = ImportSpec(node.module, a.name, a.asname)
    return found


def render_imports(specs: list[ImportSpec]) -> str:
    """Import lines for `specs`, `from` imports of one module joined; ruff
    sorts them afterwards."""
    future = "from __future__ import annotations\n"
    plain = sorted({s.line() for s in specs if s.name is None})
    froms: dict[str, list[str]] = defaultdict(list)
    for s in specs:
        if s.name is not None:
            entry = s.name + (f" as {s.asname}" if s.asname else "")
            if entry not in froms[s.module]:
                froms[s.module].append(entry)
    lines = [*plain, *(f"from {m} import {', '.join(sorted(n))}" for m, n in sorted(froms.items()))]
    return future + "\n" + "\n".join(lines) + "\n" if lines else future


class _Binder(ast.NodeVisitor):
    """Every name a statement binds anywhere inside it."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.names.add(node.arg)
        self.generic_visit(node)

    def _named(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.names.add(node.name)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._named(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._named(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._named(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(a.asname or a.name.split(".")[0] for a in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(a.asname or a.name for a in node.names)


def bound_names(node: ast.AST) -> set[str]:
    binder = _Binder()
    binder.visit(node)
    return binder.names


def loaded_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def free_names(node: ast.AST) -> set[str]:
    """The names a statement reads from outside it."""
    return loaded_names(node) - bound_names(node)


def toplevel_names(node: ast.stmt) -> set[str]:
    """What a statement binds in the scope it is in - not inside a def."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    out: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            out.add(child.id)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        out |= bound_names(node)
    return out


def first_line(node: ast.stmt) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([node.lineno, *(d.lineno for d in decorators)])


_BANNER = re.compile(r"^\s*(#|//) -{3,}\s*(?P<words>.*?)\s*-*\s*$")


def banner_area(line: str) -> str | None:
    """The area a section banner line opens, or None for any other line."""
    m = _BANNER.match(line)
    if not m:
        return None
    words = re.sub(r"\(#\d+\)", "", m.group("words")).strip().lower()
    for key, area in SECTIONS.items():
        if key in words:
            return area
    return "?" + words


def drop_banners(text: str) -> str:
    """`text` without its section banners, and the bare comment line a
    banner's own explanation starts with: a module is its area now. Where
    one statement follows two banners, the first opened a section with no
    statement of its own here (app.py's "control (writes)", whose words are
    routes/control.py's docstring now), and goes with its explanation."""
    lines = text.splitlines(keepends=True)
    banners = [i for i, line in enumerate(lines) if _BANNER.match(line)]
    if len(banners) > 1:
        lines = lines[: banners[0]] + lines[banners[-1] :]
    out: list[str] = []
    skip_bare = False
    for line in lines:
        if _BANNER.match(line):
            skip_bare = True
            continue
        if skip_bare and line.strip() in ("#", "//"):
            skip_bare = False
            continue
        skip_bare = False
        out.append(line)
    return "".join(out)


@dataclass
class Chunk:
    node: ast.stmt
    text: str  # the statement and the comments and blank lines above it
    names: set[str] = field(default_factory=set)
    start: int = 0  # the 0-based line `text` starts on


def chunks_of(lines: list[str], nodes: list[ast.stmt], start: int) -> list[Chunk]:
    """Each statement with everything between it and the one before."""
    out = []
    prev = start
    for node in nodes:
        assert first_line(node) - 1 >= prev, ast.unparse(node)[:80]
        text = "".join(lines[prev : node.end_lineno])
        out.append(Chunk(node, text, toplevel_names(node), prev))
        prev = node.end_lineno or prev
    return out


def module_text(doc: str, imports: str, body: list[str]) -> str:
    text = "".join(body).strip("\n")
    return f'"""{doc}"""\n\n{imports}\n\n{text}\n'


def wrap(doc: str, width: int = 88) -> str:
    """A docstring, wrapped as the tree's are."""
    lines = textwrap.wrap(doc, width)
    return lines[0] if len(lines) == 1 else "\n".join(lines) + "\n"


def resolve(
    names: set[str],
    own: set[str],
    elsewhere: dict[str, str],
    imports: dict[str, ImportSpec],
    where: str,
) -> list[ImportSpec]:
    """The imports `names` need in a module defining `own`: from the module
    a moved name now lives in (`elsewhere`), or as the old module imported it."""
    specs = []
    unknown = []
    for name in sorted(names - own):
        if name in elsewhere:
            specs.append(ImportSpec(elsewhere[name], name, None))
        elif name in imports:
            specs.append(imports[name])
        elif not hasattr(builtins, name) and name != "__name__":
            unknown.append(name)
    if unknown:
        raise SplitError(f"{where}: no definition found for {unknown}")
    return specs


def cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """A cycle in `graph`, as the modules on it, or None."""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt) :] + [nxt]
            if nxt not in state and (found := visit(nxt)):
                return found
        stack.pop()
        state[node] = 2
        return None

    for node in sorted(graph):
        if node not in state and (found := visit(node)):
            return found
    return None


@dataclass
class Plan:
    """Everything the split will write, decided before any of it is."""

    writes: dict[Path, str] = field(default_factory=dict)
    deletes: list[Path] = field(default_factory=list)
    #: a moved Python name -> the dotted module it lives in now
    python_homes: dict[str, dict[str, str]] = field(default_factory=dict)
    #: an api.ts name -> its area
    ts_homes: dict[str, str] = field(default_factory=dict)
    report: list[str] = field(default_factory=list)


def place_in_sections(
    chunks: list[tuple[str, str]], explicit: dict[str, str], what: str, report: list[str]
) -> list[str]:
    """Each (name, text) chunk's area: by name, else by the section banner
    above it, else - before the first banner - the previous chunk's."""
    areas = []
    section: str | None = None
    previous: str | None = None
    for name, text in chunks:
        for line in text.splitlines():
            if (opened := banner_area(line)) is not None:
                if opened.startswith("?"):
                    raise SplitError(f"{what}: no area for the section banner {line.strip()!r}")
                section = opened
        area = explicit.get(name) or section or previous
        if area is None:
            raise SplitError(f"{what}: {name} is before every section and has no area")
        if name not in explicit:
            report.append(f"{what}: {name} -> {area} (by its section)")
        areas.append(area)
        previous = area
    return areas


# ------------------------------------------------------------------ models.py


def split_models(root: Path, plan: Plan) -> dict[str, str]:
    """models.py into models/<area>.py; returns name -> dotted module."""
    path = root / PKG / "models.py"
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    body = tree.body
    assert isinstance(body[0], ast.Expr), "models.py starts with its docstring"
    header = [n for n in body[1:] if isinstance(n, (ast.Import, ast.ImportFrom))]
    nodes = [n for n in body[1:] if not isinstance(n, (ast.Import, ast.ImportFrom))]
    if any(first_line(n) < header[-1].lineno for n in nodes):
        raise SplitError("models.py imports something below its first statement")
    imports = import_map(header)
    imports.pop("annotations", None)
    chunks = chunks_of(lines, nodes, header[-1].end_lineno or 0)
    for c in chunks:
        if len(c.names) != 1:
            raise SplitError(
                f"models.py: a statement binding {sorted(c.names)} at line {c.node.lineno}"
            )
    names = [next(iter(c.names)) for c in chunks]
    areas = place_in_sections(
        [(n, c.text) for n, c in zip(names, chunks, strict=True)],
        MODEL_AREAS,
        "models.py",
        plan.report,
    )
    home = {n: f"uqf_frontend.models.{a}" for n, a in zip(names, areas, strict=True)}
    by_area: dict[str, list[Chunk]] = defaultdict(list)
    for c, a in zip(chunks, areas, strict=True):
        by_area[a].append(c)
    unknown = set(by_area) - set(MODEL_DOCS)
    if unknown:
        raise SplitError(f"models.py: no module doc for {sorted(unknown)}")
    graph: dict[str, set[str]] = {}
    for area, group in by_area.items():
        own = {n for c in group for n in c.names}
        needed = set().union(*(free_names(c.node) for c in group))
        specs = resolve(needed, own, home, imports, f"models/{area}.py")
        graph[area] = {
            s.module.rsplit(".", 1)[1] for s in specs if s.module.startswith("uqf_frontend.models.")
        }
        text = module_text(
            wrap(MODEL_DOCS[area]), render_imports(specs), [drop_banners(c.text) for c in group]
        )
        plan.writes[root / PKG / "models" / f"{area}.py"] = text
    if found := cycle(graph):
        raise SplitError(f"models: an import cycle {' -> '.join(found)}")
    plan.writes[root / PKG / "models" / "__init__.py"] = (
        '"""Request and response shapes for the HTTP surface, one module per area\n'
        "(#2168). There is no facade: a caller imports a model from the module that\n"
        'defines it."""\n'
    )
    plan.deletes.append(path)
    plan.python_homes["uqf_frontend.models"] = home
    return home


# --------------------------------------------------------------------- app.py


def _route_path(node: ast.stmt) -> str | None:
    for d in getattr(node, "decorator_list", []):
        if (
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "app"
            and d.func.attr in HTTP_VERBS
        ):
            arg = d.args[0]
            assert isinstance(arg, ast.Constant), ast.unparse(d)
            return arg.value
    return None


def route_area(path: str) -> str:
    for pattern, area in ROUTE_AREAS:
        if re.fullmatch(pattern, path):
            return area
    raise SplitError(f"app.py: no area for the route {path} (ROUTE_AREAS)")


def _settle(
    symbols: list[str], users: dict[str, set[tuple[str, str]]], fixed: dict[str, str]
) -> dict[str, str]:
    """Where each symbol goes: with the one area that alone uses it, in
    `common` when more than one does or app.py and an area both do (or a
    `common` symbol uses it), in app.py when only app.py does."""
    place: dict[str, str | None] = dict.fromkeys(symbols)
    place.update(fixed)
    for _ in range(len(symbols) + 2):
        changed = False
        for s in symbols:
            if s in fixed:
                continue
            consumers = set()
            for kind, who in users.get(s, ()):
                consumers.add(who if kind == "area" else place.get(who))
            consumers.discard(None)
            if "common" in consumers or len(consumers) > 1:
                new = "common"
            elif consumers:
                new = next(iter(consumers))
            else:
                new = "app"
            if new != place[s]:
                place[s], changed = new, True
        if not changed:
            return {s: p or "app" for s, p in place.items()}
    raise SplitError("app.py: placing the helpers did not settle")


def split_app(root: Path, plan: Plan, model_home: dict[str, str]) -> None:
    path = root / PKG / "app.py"
    source = path.read_text()
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    body = tree.body
    header = [n for n in body if isinstance(n, (ast.Import, ast.ImportFrom))]
    nodes = [n for n in body[1:] if not isinstance(n, (ast.Import, ast.ImportFrom))]
    if any(first_line(n) < header[-1].lineno for n in nodes):
        raise SplitError("app.py imports something below its first statement")
    imports = import_map(header)
    imports.pop("annotations", None)
    for name, spec in list(imports.items()):
        if spec.module == "uqf_frontend.models":
            imports[name] = ImportSpec(model_home[spec.name or name], spec.name, spec.asname)
    chunks = chunks_of(lines, nodes, header[-1].end_lineno or 0)
    create = next(c for c in chunks if getattr(c.node, "name", None) == "create_app")
    fn = create.node
    assert isinstance(fn, ast.FunctionDef)

    # -- create_app's routes, in the order it declares them
    inner = fn.body[1:] if isinstance(fn.body[0], ast.Expr) else fn.body
    route_at = [i for i, n in enumerate(inner) if _route_path(n) is not None]
    first, last = route_at[0], route_at[-1]
    region_start = (inner[first - 1].end_lineno or 0) if first else (fn.body[0].end_lineno or 0)
    region = chunks_of(lines, inner[first : last + 1], region_start)
    area_of: dict[int, str] = {}
    for i, c in enumerate(region):
        if (p := _route_path(c.node)) is not None:
            area_of[i] = route_area(p)
    # a closure between the routes goes with the one area whose routes use it
    closures = {
        next(iter(c.names)): i
        for i, c in enumerate(region)
        if i not in area_of and isinstance(c.node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    residual_nodes = inner[:first] + inner[last + 1 :]
    residual_free = set().union(*(free_names(n) for n in residual_nodes))
    users: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for i, c in enumerate(region):
        for n in free_names(c.node):
            if n in closures and closures[n] != i:
                who = (
                    ("area", area_of[i])
                    if i in area_of
                    else ("sym", f"closure:{next(iter(c.names))}")
                )
                users[n].add(who)
    for n in residual_free & set(closures):
        users[n].add(("area", "app"))
    closure_place = _settle(
        list(closures),
        {k: {(kind, w.removeprefix("closure:")) for kind, w in v} for k, v in users.items()},
        {},
    )
    hoisted = []
    for i, c in enumerate(region):
        if i in area_of:
            continue
        name = next(iter(c.names)) if len(c.names) == 1 else None
        where = closure_place.get(name) if name in closures else "app"
        if where in (None, "app", "common"):
            hoisted.append(c)
            plan.report.append(f"app.py: {name or c.node.lineno} stays in create_app (shared)")
        else:
            area_of[i] = where
    order: list[str] = []
    for i in range(len(region)):
        if i in area_of and (not order or order[-1] != area_of[i]):
            if area_of[i] in order:
                raise SplitError(
                    f"app.py: the {area_of[i]} routes are not contiguous - another area's "
                    f"route is declared between them (line {region[i].node.lineno}), so "
                    "installing them as one module would reorder the OpenAPI paths"
                )
            order.append(area_of[i])
    groups = {a: [region[i] for i in sorted(area_of) if area_of[i] == a] for a in order}
    unknown = set(groups) - set(ROUTE_DOCS)
    if unknown:
        raise SplitError(f"app.py: no module doc for the route areas {sorted(unknown)}")

    # -- create_app's locals, as install() parameters
    local_names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
    for n in residual_nodes + [c.node for c in hoisted]:
        local_names |= toplevel_names(n)
    group_free = {}
    params: dict[str, list[str]] = {}
    for area, group in groups.items():
        free = set().union(*(free_names(c.node) for c in group)) - {
            n for c in group for n in c.names
        }
        wanted = sorted(
            (free & local_names) - {"app"},
            key=lambda n: list(LOCAL_TYPES).index(n) if n in LOCAL_TYPES else -1,
        )
        missing = [n for n in wanted if n not in LOCAL_TYPES]
        if missing:
            raise SplitError(f"app.py: the {area} routes read create_app's {missing} (LOCAL_TYPES)")
        params[area] = wanted
        group_free[area] = free - local_names

    # -- the module-level helpers: where each one goes
    symbols = [next(iter(c.names)) for c in chunks if c is not create]
    for c in chunks:
        if c is not create and len(c.names) != 1:
            raise SplitError(
                f"app.py: a statement binding {sorted(c.names)} at line {c.node.lineno}"
            )
    sym_chunk = {next(iter(c.names)): c for c in chunks if c is not create}
    musers: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for s, c in sym_chunk.items():
        for n in free_names(c.node) & set(sym_chunk):
            if n != s:
                musers[n].add(("sym", s))
    app_free = free_names(
        ast.Module(body=residual_nodes + [c.node for c in hoisted], type_ignores=[])
    )
    app_free |= set().union(
        *(
            free_names(d)
            for d in [*fn.args.defaults, *(a.annotation for a in fn.args.args if a.annotation)]
        )
    )
    for n in app_free & set(sym_chunk):
        musers[n].add(("area", "app"))
    for area, free in group_free.items():
        for n in free & set(sym_chunk):
            musers[n].add(("area", area))
    place = _settle(symbols, musers, {})
    homes = {s: f"uqf_frontend.routes.{p}" for s, p in place.items() if p != "app"}

    # -- the route modules
    module_graph: dict[str, set[str]] = defaultdict(set)
    for area in [*order, "common"]:
        helpers = [sym_chunk[s] for s in symbols if place[s] == area]
        group = groups.get(area, [])
        if not helpers and not group:
            continue
        own = {n for c in helpers + group for n in c.names}
        needed = set().union(*(free_names(c.node) for c in helpers), group_free.get(area, set()))
        signature = ""
        if group:
            annotations = ["FastAPI", *(LOCAL_TYPES[p] for p in params[area])]
            for text in annotations:
                needed |= loaded_names(ast.parse(text, mode="eval"))
            args = "".join(f", {p}: {LOCAL_TYPES[p]}" for p in params[area])
            star = ", *" if params[area] else ""
            signature = (
                f"\n\ndef install(app: FastAPI{star}{args}) -> None:\n"
                '    """Add these routes to *app*. Called by create_app in the order its\n'
                "    routes are declared: FastAPI lists the OpenAPI paths in registration\n"
                '    order, and test_openapi_snapshot.py holds that order."""\n'
            )
        specs = resolve(needed, own, homes, imports, f"routes/{area}.py")
        for s in specs:
            if s.module.startswith("uqf_frontend.routes."):
                module_graph[area].add(s.module.rsplit(".", 1)[1])
        parts = [drop_banners(c.text) for c in helpers]
        if group:
            parts.append(signature)
            parts.extend(drop_banners(c.text) for c in group)
        plan.writes[root / PKG / "routes" / f"{area}.py"] = module_text(
            wrap(ROUTE_DOCS[area]), render_imports(specs), parts
        )
    if found := cycle(module_graph):
        raise SplitError(f"routes: an import cycle {' -> '.join(found)}")
    plan.writes[root / PKG / "routes" / "__init__.py"] = (
        '"""The HTTP routes, one module per area (#2168). Each area\'s `install(app, ...)`\n'
        "adds its routes to the app create_app builds, in the order app.py calls them.\n"
        'There is no facade: import a name from the module that defines it."""\n'
    )

    # -- app.py, with create_app installing the areas where its routes were
    calls = [
        f"    {area}_routes.install(app" + "".join(f", {p}={p}" for p in params[area]) + ")\n"
        for area in order
    ]
    region_end = inner[last].end_lineno or 0
    create_text = (
        "".join(lines[create.start : region_start])
        + "".join(c.text for c in hoisted)
        + "\n"
        + "".join(calls)
        + "".join(lines[region_end : fn.end_lineno])
    )
    kept = []
    for c in chunks:
        if c is create:
            kept.append(create_text)
        elif place[next(iter(c.names))] == "app":
            kept.append(c.text)
    head = "".join(lines[: header[-1].end_lineno])
    app_uses = set(app_free) | set().union(
        *(free_names(sym_chunk[s].node) for s in symbols if place[s] == "app")
    )
    extra = [ImportSpec("uqf_frontend.routes", a, f"{a}_routes") for a in order]
    extra += [ImportSpec(homes[s], s, None) for s in symbols if s in homes and s in app_uses]
    imports_text = "\n".join(s.line() for s in extra) + "\n"
    tail = "".join(lines[(chunks[-1].node.end_lineno or 0) :])
    plan.writes[path] = head + imports_text + "".join(kept) + tail
    plan.python_homes["uqf_frontend.app"] = homes
    for area in order:
        plan.report.append(f"routes/{area}.py: install({', '.join(params[area])})")
    for s in symbols:
        plan.report.append(f"app.py: {s} -> {place[s]}")


# ------------------------------------------------------------ callers, Python


def tracked(root: Path, *patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", *patterns], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    return [root / p for p in out if not p.startswith("lib/") and (root / p).is_file()]


def _import_lines(path: Path, lines: list[str], node: ast.ImportFrom) -> tuple[int, int, str]:
    """The lines `node` spans, and the indent it is written at."""
    start, end = node.lineno - 1, node.end_lineno or node.lineno
    before = lines[start][: node.col_offset]
    after = lines[end - 1][node.end_col_offset :]
    if before.strip() or after.strip():
        raise SplitError(f"{path}:{node.lineno}: an import sharing its line")
    if "#" in "".join(lines[start:end]):
        raise SplitError(f"{path}:{node.lineno}: a comment inside an import")
    return start, end, before


def _scopes(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    """Each node -> the function (or module) it is in."""
    scope: dict[ast.AST, ast.AST] = {}

    def visit(node: ast.AST, within: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            scope[child] = within
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else within
            visit(child, inner)

    visit(tree, tree)
    return scope


def rewrite_python_callers(root: Path, plan: Plan) -> list[Path]:
    """Every `from uqf_frontend.models import X` and `from uqf_frontend.app
    import X` names the module X is in now; every `models.X` read through
    `from uqf_frontend import models` is `X`, imported from its module."""
    model_home = plan.python_homes["uqf_frontend.models"]
    touched = []
    for path in tracked(root, "*.py"):
        text = plan.writes.get(path, None)
        if text is None:
            if path in plan.deletes:
                continue
            text = path.read_text()
        if "uqf_frontend.models" not in text and "uqf_frontend" not in text:
            continue
        tree = ast.parse(text)
        lines = text.splitlines(keepends=True)
        scope = _scopes(tree)
        edits: list[tuple[int, int, str]] = []
        attrs: dict[tuple[int, int], tuple[int, str]] = {}
        through_models = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "uqf_frontend" and any(a.name == "models" for a in node.names):
                through_models = True
                if any(a.name == "models" and a.asname for a in node.names):
                    raise SplitError(f"{path}:{node.lineno}: models imported under another name")
                start, end, before = _import_lines(path, lines, node)
                within = scope[node]
                used: set[str] = set()
                for n in ast.walk(within):
                    if (
                        isinstance(n, ast.Attribute)
                        and isinstance(n.value, ast.Name)
                        and n.value.id == "models"
                    ):
                        if n.attr not in model_home:
                            raise SplitError(
                                f"{path}:{n.lineno}: models.{n.attr} is not in models.py"
                            )
                        used.add(n.attr)
                        attrs[(n.lineno - 1, n.col_offset)] = (n.end_col_offset or 0, n.attr)
                rest = [
                    a.name + (f" as {a.asname}" if a.asname else "")
                    for a in node.names
                    if a.name != "models"
                ]
                grouped: dict[str, list[str]] = defaultdict(list)
                for name in sorted(used):
                    grouped[model_home[name]].append(name)
                new = f"{before}from uqf_frontend import {', '.join(rest)}\n" if rest else ""
                new += "".join(
                    f"{before}from {m} import {', '.join(n)}\n" for m, n in sorted(grouped.items())
                )
                edits.append((start, end, new))
                continue
            if node.module not in plan.python_homes:
                continue
            homes = plan.python_homes[node.module]
            if node.module == "uqf_frontend.models" and any(a.name == "*" for a in node.names):
                raise SplitError(f"{path}: `from uqf_frontend.models import *`")
            if node.module == "uqf_frontend.app" and not any(a.name in homes for a in node.names):
                continue
            start, end, before = _import_lines(path, lines, node)
            grouped = defaultdict(list)
            for a in node.names:
                module = homes.get(a.name, node.module)
                if node.module == "uqf_frontend.models" and a.name not in homes:
                    raise SplitError(f"{path}:{node.lineno}: models.py defines no {a.name}")
                grouped[module].append(a.name + (f" as {a.asname}" if a.asname else ""))
            new = "".join(
                f"{before}from {m} import {', '.join(n)}\n" for m, n in sorted(grouped.items())
            )
            edits.append((start, end, new))
        if not edits:
            continue
        for (line, col), (end_col, name) in sorted(attrs.items(), reverse=True):
            lines[line] = lines[line][:col] + name + lines[line][end_col:]
        for start, end, new in sorted(edits, reverse=True):
            lines[start:end] = [new]
        result = "".join(lines)
        if through_models:
            left = [
                n.lineno
                for n in ast.walk(ast.parse(result))
                if isinstance(n, ast.Name) and n.id == "models"
            ]
            if left:
                raise SplitError(
                    f"{path}: still reads the models module as a whole at lines {left} - "
                    "give it a patch (PATCHES)"
                )
        plan.writes[path] = result
        touched.append(path)
    return touched


# ------------------------------------------------------------- TypeScript


def parse_ts(root: Path, mode: str, files: list[Path]) -> dict:
    out = subprocess.run(
        ["node", str(root / PARSER), mode, *(str(f.relative_to(root / WEB)) for f in files)],
        cwd=root / WEB,
        capture_output=True,
        text=True,
    )
    if out.returncode:
        raise SplitError(f"the TypeScript parser failed: {out.stderr.strip()}")
    return json.loads(out.stdout)


def _ts_import(names: list[tuple[str, bool]], source: str, all_type: bool) -> str:
    if all_type or all(t for _, t in names):
        return f'import type {{ {", ".join(n for n, _ in names)} }} from "{source}";\n'
    return (
        "import { "
        + ", ".join(("type " if t else "") + n for n, t in names)
        + f' }} from "{source}";\n'
    )


def split_api(root: Path, plan: Plan) -> None:
    parsed = parse_ts(root, "statements", [root / API_TS])[str(API_TS.relative_to(WEB))]
    statements = parsed["statements"]
    imports = [s for s in statements if s["type"] == "ImportDeclaration"]
    decls = [s for s in statements if s["type"] != "ImportDeclaration"]
    if any(statements.index(i) > len(imports) - 1 for i in imports):
        raise SplitError("api.ts imports something below its first declaration")
    imported: dict[str, tuple[str, bool]] = {}
    for i in imports:
        source = i["source"]
        if source.startswith("./"):
            source = "../" + source[2:]
        for s in i["specifiers"]:
            if s["type"] != "ImportSpecifier":
                raise SplitError(f"api.ts: an import of a kind not handled: {i['text']!r}")
            imported[s["local"]] = (
                source,
                i["importKind"] == "type" or s["importKind"] == "type",
            )
    for d in decls:
        if len(d["names"]) != 1:
            raise SplitError(f"api.ts: a statement declaring {d['names']}: {d['text'][:80]!r}")
    names = [d["names"][0] for d in decls]
    areas = place_in_sections(
        [(n, d["text"]) for n, d in zip(names, decls, strict=True)], TS_AREAS, "api.ts", plan.report
    )
    home = dict(zip(names, areas, strict=True))
    kind = {d["names"][0]: d["typeOnly"] for d in decls}
    unknown = set(areas) - set(TS_DOCS)
    if unknown:
        raise SplitError(f"api.ts: no module doc for {sorted(unknown)}")
    exported_needed = set()
    texts: dict[str, list[str]] = defaultdict(list)
    needs: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for d, name, area in zip(decls, names, areas, strict=True):
        for ref in d["refs"]:
            if ref == name:
                continue
            if ref in home and home[ref] != area:
                needs[area][home[ref]].add(ref)
                exported_needed.add(ref)
            elif ref in imported:
                needs[area][imported[ref][0]].add(ref)
    graph: dict[str, set[str]] = defaultdict(set)
    for d, name, area in zip(decls, names, areas, strict=True):
        text = d["text"]
        if name in exported_needed and not d["exported"]:
            text = text[: d["lead"]] + "export " + text[d["lead"] :]
            plan.report.append(
                f"api.ts: {name} is exported now - {area} is not where its users are"
            )
        texts[area].append(drop_banners(text))
    for area in sorted(set(areas)):
        lines = []
        for source, refs in sorted(needs[area].items()):
            if source.startswith("../"):
                pairs = [(r, imported[r][1]) for r in sorted(refs)]
                lines.append(_ts_import(pairs, source, False))
            else:
                pairs = [(r, kind[r]) for r in sorted(refs)]
                if not all(t for _, t in pairs):
                    graph[area].add(source)
                lines.append(_ts_import(pairs, f"./{source}", False))
        doc = "".join(f"// {line}\n" for line in textwrap.wrap(TS_DOCS[area], 77))
        body = "".join(texts[area]).strip("\n")
        plan.writes[root / API_DIR / f"{area}.ts"] = (
            doc + ("\n" + "".join(lines) if lines else "") + "\n" + body + "\n"
        )
    if found := cycle(graph):
        raise SplitError(f"web/src/api: a value import cycle {' -> '.join(found)}")
    plan.deletes.append(root / API_TS)
    plan.ts_homes = home


def rewrite_ts_callers(root: Path, plan: Plan) -> list[Path]:
    """Every import from "./api" names the module each name is in now."""
    files = [
        p
        for p in dict.fromkeys(tracked(root, "web/*.ts", "web/*.tsx"))
        if p != root / API_TS and (root / API_DIR) not in p.parents
    ]
    parsed = parse_ts(root, "imports", files)
    touched = []
    api = (root / API_TS).with_suffix("")
    for path in files:
        decls = parsed[str(path.relative_to(root / WEB))]
        text = path.read_text()
        changed = False
        for d in decls:
            source = d["source"]
            if not source.startswith("."):
                continue
            if (path.parent / source).resolve() != api.resolve():
                continue
            if d["type"] != "ImportDeclaration":
                raise SplitError(f"{path}: {d['type']} from the api module: {d['text']!r}")
            grouped: dict[str, list[tuple[str, bool]]] = defaultdict(list)
            for s in d["specifiers"]:
                if s["type"] != "ImportSpecifier":
                    raise SplitError(f"{path}: {d['text']!r} is not a named import")
                name = s["imported"]
                if name not in plan.ts_homes:
                    raise SplitError(f"{path}: api.ts declares no {name}")
                spelled = name if s["local"] == name else f"{name} as {s['local']}"
                grouped[plan.ts_homes[name]].append((spelled, s["importKind"] == "type"))
            prefix = "./" if path.parent == root / WEB / "src" else "../"
            new = "".join(
                _ts_import(pairs, f"{prefix}api/{area}", d["importKind"] == "type")
                for area, pairs in sorted(grouped.items())
            ).rstrip("\n")
            if text.count(d["text"]) != 1:
                raise SplitError(f"{path}: the import {d['text']!r} is not unique")
            text = text.replace(d["text"], new)
            changed = True
        if changed:
            plan.writes[path] = text
            touched.append(path)
    return touched


# ------------------------------------------------------------------ patches

_MODELS_ENUM_OLD = """        name: obj
        for name, obj in vars(models).items()
        if isinstance(obj, type)
        and issubclass(obj, pydantic.BaseModel)
        and obj.__module__ == models.__name__
    }"""

#: The tests that read the old files as wholes, each held to the text it
#: replaces: a patch whose text has changed on master stops the split, so it
#: is redone by hand rather than applied to code it was not written for.
#: (path, old, new) - old must occur exactly once.
PATCHES: tuple[tuple[str, str, str], ...] = (
    # the tests of every model and every api module read them through api_support
    (
        "python/uqf_frontend/tests/test_api_types.py",
        "\nfrom uqf_frontend import models\n",
        "\nfrom api_support import api_text, defined_models, model_named\n"
        "from uqf_frontend import models\n",
    ),
    (
        "python/uqf_frontend/tests/test_api_conventions.py",
        "\nfrom uqf_frontend import access, models\n",
        "\nfrom api_support import defined_models, route_modules\n"
        "from uqf_frontend import access, models\n",
    ),
    (
        "python/uqf_frontend/tests/test_config_push_naming.py",
        "\nfrom uqf_frontend import models, remote_helper\n",
        "\nfrom api_support import api_text, defined_models\n"
        "from uqf_frontend import models, remote_helper\n",
    ),
    (
        "python/uqf_frontend/tests/test_api_partition_vocabulary.py",
        "\nfrom uqf_frontend import models\n",
        "\nfrom api_support import defined_models\nfrom uqf_frontend import models\n",
    ),
    (
        "python/uqf_frontend/tests/test_api_partition_vocabulary.py",
        "        for obj in vars(models).values()\n"
        "        if isinstance(obj, type)\n"
        "        and issubclass(obj, models.BaseModel)\n"
        "        and obj.__module__ == models.__name__\n"
        "        and obj.model_config",
        "        for obj in defined_models().values()\n        if obj.model_config",
    ),
    # the routes they read as source are in routes/ now, beside app.py
    (
        "python/uqf_frontend/tests/test_api_vocabulary.py",
        "\nfrom uqf_frontend import app as app_module\n",
        "\nfrom api_support import route_modules\nfrom uqf_frontend import app as app_module\n",
    ),
    (
        "python/uqf_frontend/tests/test_api_vocabulary.py",
        '    tree = ast.parse(Path(app_module.__file__).read_text(encoding="utf-8"))\n',
        "    tree = ast.Module(\n"
        "        body=[\n"
        "            node\n"
        "            for module in (app_module, *route_modules())\n"
        '            for node in ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8")).body\n'
        "        ],\n"
        "        type_ignores=[],\n"
        "    )\n",
    ),
    (
        "python/uqf_frontend/tests/test_settings_names.py",
        "\nfrom uqf_frontend import app as app_module\n",
        "\nfrom api_support import route_modules\nfrom uqf_frontend import app as app_module\n",
    ),
    (
        "python/uqf_frontend/tests/test_settings_names.py",
        '    source = Path(app_module.__file__).read_text(encoding="utf-8")\n',
        '    source = "".join(\n'
        '        Path(module.__file__ or "").read_text(encoding="utf-8")\n'
        "        for module in (app_module, *route_modules())\n"
        "    )\n",
    ),
    (
        "python/uqf_frontend/tests/test_instants.py",
        "\nfrom uqf_frontend import charts, instants, models\n",
        "\nfrom api_support import model_modules\nfrom uqf_frontend import charts, instants, models\n",
    ),
    (
        "python/uqf_frontend/tests/test_instants.py",
        "    for module in (models, charts):",
        "    for module in (*model_modules(), charts):",
    ),
    (
        "python/uqf_frontend/tests/test_target_field_names.py",
        "\nfrom uqf_frontend import models, services\n",
        "\nfrom api_support import defined_models\nfrom uqf_frontend import models, services\n",
    ),
    # test_api_types: every model module, every api module
    (
        "python/uqf_frontend/tests/test_api_types.py",
        '\nAPI_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"\n',
        "",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        "declared = _declared(API_TS.read_text() if text is None else text)",
        "declared = _declared(api_text() if text is None else text)",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        "        (info.alias or name): info for name, info in getattr(models, model).model_fields.items()\n",
        "        (info.alias or name): info for name, info in model_named(model).model_fields.items()\n",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        'f"{interface} (web/src/api.ts) and {model} (models.py) disagree: "',
        'f"{interface} (web/src/api/) and {model} (models/) disagree: "',
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        'return dict(re.findall(r"^export type (\\w+) =\\s*([^;]+);", API_TS.read_text(), re.M))',
        'return dict(re.findall(r"^export type (\\w+) =\\s*([^;]+);", api_text(), re.M))',
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        "    assert all(hasattr(models, model) for model in PAIRS.values())",
        "    assert all(model_named(model) for model in PAIRS.values())",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        '    renamed = API_TS.read_text().replace("  procs: string;", "  selector: string;")\n'
        "    assert renamed != API_TS.read_text(),",
        '    renamed = api_text().replace("  procs: string;", "  selector: string;")\n'
        "    assert renamed != api_text(),",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        "    declared = _declared(API_TS.read_text())[interface]",
        "    declared = _declared(api_text())[interface]",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        '    assert getattr(models, model).model_config.get("extra") == "forbid", model',
        '    assert model_named(model).model_config.get("extra") == "forbid", model',
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        "    return {\n" + _MODELS_ENUM_OLD + "\n\n\ndef _losing_keys",
        "    return defined_models()\n\n\ndef _losing_keys",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        '    staged = API_TS.read_text().replace("  proctype: string;", "  role: string;", 1)\n'
        "    assert staged != API_TS.read_text()",
        '    staged = api_text().replace("  proctype: string;", "  role: string;", 1)\n'
        "    assert staged != api_text()",
    ),
    (
        "python/uqf_frontend/tests/test_api_types.py",
        '"""The browser\'s API types (web/src/api.ts) against the models that define them.',
        '"""The browser\'s API types (web/src/api/) against the models that define them.',
    ),
    # test_api_conventions
    (
        "python/uqf_frontend/tests/test_api_conventions.py",
        "    return {\n" + _MODELS_ENUM_OLD,
        "    return defined_models()",
    ),
    (
        "python/uqf_frontend/tests/test_api_conventions.py",
        "    import inspect\n\n    from uqf_frontend import app as app_module\n\n"
        '    assert ".confirmed" not in inspect.getsource(app_module)',
        "    import inspect\n\n    from uqf_frontend import app as app_module\n\n"
        "    for module in (app_module, *route_modules()):\n"
        '        assert ".confirmed" not in inspect.getsource(module), module.__name__',
    ),
    # test_config_push_naming
    (
        "python/uqf_frontend/tests/test_config_push_naming.py",
        'API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"\n',
        "",
    ),
    (
        "python/uqf_frontend/tests/test_config_push_naming.py",
        "    text = API_TS.read_text()",
        "    text = api_text()",
    ),
    (
        "python/uqf_frontend/tests/test_config_push_naming.py",
        "        for c in vars(models).values()\n"
        "        if inspect.isclass(c) and issubclass(c, BaseModel) and c.__module__ == models.__name__",
        "        for c in defined_models().values()\n        if inspect.isclass(c) and issubclass(c, BaseModel)",
    ),
    # test_target_field_names
    (
        "python/uqf_frontend/tests/test_target_field_names.py",
        "        for _, m in inspect.getmembers(models, inspect.isclass)\n"
        "        if issubclass(m, BaseModel) and m.__module__ == models.__name__",
        "        for m in defined_models().values()\n        if issubclass(m, BaseModel)",
    ),
    # test_remote_upload, test_request_timeout: the constants' module
    (
        "python/uqf_frontend/tests/test_remote_upload.py",
        'API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"',
        'API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "services.ts"',
    ),
    (
        "python/uqf_frontend/tests/test_request_timeout.py",
        'API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"',
        'API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api" / "client.ts"',
    ),
    (
        "python/uqf_frontend/tests/test_request_timeout.py",
        '    assert m, "web/src/api.ts no longer declares DEFAULT_REQUEST_TIMEOUT_MS"',
        '    assert m, "web/src/api/client.ts no longer declares DEFAULT_REQUEST_TIMEOUT_MS"',
    ),
    (
        "python/uqf_frontend/tests/test_request_timeout.py",
        '        "web/src/api.ts\'s fallback differs from config.Settings.request_timeout"',
        '        "web/src/api/client.ts\'s fallback differs from config.Settings.request_timeout"',
    ),
    # test_settings_admin: the regenerate command in a docstring
    (
        "python/uqf_frontend/tests/test_settings_admin.py",
        "from uqf_frontend.models import *;",
        "from uqf_frontend.models.settings import *;",
    ),
    # ty resolves api_support as pytest does, by its directory
    (
        "pyproject.toml",
        '    "python/uqf_frontend/src",\n    "python/uqs/tests",\n',
        '    "python/uqf_frontend/src",\n    "python/uqs/tests",\n'
        "    # api_support.py, the API tests' view of every model and api module (#2168).\n"
        '    "python/uqf_frontend/tests",\n',
    ),
    # ci_scope: a module under web/src/api/ is named by the directory
    (
        "scripts/dev/tests/test_ci_scope.py",
        '        ([("M", "web/src/api.ts")], (False, False, True, True)),',
        '        ([("M", "web/src/api/query.ts")], (False, False, True, True)),',
    ),
    (
        "scripts/dev/tests/test_ci_scope.py",
        '        ([("M", "python/uqf_frontend/src/uqf_frontend/models.py")], (False, False, False, True)),',
        '        ([("M", "python/uqf_frontend/src/uqf_frontend/models/query.py")], (False, False, False, True)),',
    ),
    (
        "scripts/dev/tests/test_ci_scope.py",
        '        ([("D", "web/src/api.ts")], (False, False, True, True)),',
        '        ([("D", "web/src/api/query.ts")], (False, False, True, True)),',
    ),
    (
        "scripts/dev/tests/test_ci_scope.py",
        '    """The cross-language edge: web/src/api.ts mirrors the API models, and',
        '    """The cross-language edge: web/src/api/ mirrors the API models, and',
    ),
    (
        "scripts/dev/tests/test_ci_scope.py",
        '    named = scope.named_by_python(["web/src/api.ts", "web/src/App.tsx"])\n'
        '    assert set(named) == {"web/src/api.ts"}\n'
        '    s = scope.decide([("M", "web/src/api.ts")], None)\n'
        '    assert s.why["python"] == [f"web/src/api.ts: named by {named[\'web/src/api.ts\']}"]',
        '    named = scope.named_by_python(["web/src/api/query.ts", "web/src/App.tsx"])\n'
        '    assert set(named) == {"web/src/api/query.ts"}\n'
        '    s = scope.decide([("M", "web/src/api/query.ts")], None)\n'
        '    assert s.why["python"] == [\n'
        "        f\"web/src/api/query.ts: named by {named['web/src/api/query.ts']}\"\n"
        "    ]",
    ),
    (
        "scripts/dev/ci_scope.py",
        """    for path in paths:
        name = Path(path).name
        hit = next(
            (
                f
                for f, text in texts.items()
                if path in text or (name in text and _WEB_WORD.search(text))
            ),
            None,
        )""",
        """    for path in paths:
        name = Path(path).name
        hit = next(
            (
                f
                for f, text in texts.items()
                if path in text
                or (name in text and _WEB_WORD.search(text))
                or _names_directory(text, Path(path).parent)
            ),
            None,
        )""",
    ),
    (
        "scripts/dev/ci_scope.py",
        '_NOT_READERS = {"scripts/dev/ci_scope.py", "scripts/dev/tests/test_ci_scope.py"}',
        "_NOT_READERS = {\n"
        '    "scripts/dev/ci_scope.py",\n'
        '    "scripts/dev/tests/test_ci_scope.py",\n'
        "    # the one-shot split of api.ts (#2168), whose rules name the old paths\n"
        '    "scripts/dev/split_frontend.py",\n'
        "}",
    ),
    (
        "scripts/dev/ci_scope.py",
        """def named_by_python(paths: Iterable[str], root: Path = REPO) -> dict[str, str]:""",
        '''def _names_directory(text: str, directory: Path) -> bool:
    """Does `text` name `directory` itself - `"web/src/api"` or
    `/ "web" / "src" / "api"`, a reader of every file in it - rather than a
    path below it? web/src/api/ is read whole by the API-type tests (#2168).
    Never `web` alone: a bare `"web"` is a word, not a read of the package."""
    if len(directory.parts) < 2:
        return False
    joined = " / ".join(f'"{part}"' for part in directory.parts)
    slashed = re.escape(directory.as_posix())
    return bool(
        re.search(re.escape(joined) + r"(?!\\s*/)", text)
        or re.search(rf"[\\"']{slashed}[\\"']", text)
    )


def named_by_python(paths: Iterable[str], root: Path = REPO) -> dict[str, str]:''',
    ),
)

#: Prose naming the old files, outside the files PATCHES and the split write.
TEXT_RENAMES = (
    ("web/src/api.ts", "web/src/api/"),
    ("uqf_frontend/models.py", "uqf_frontend/models/"),
)

#: Prose renames skip these: history, and this script.
TEXT_RENAME_SKIP = (
    "CHANGELOG.md",
    "docs/migrations/",
    "scripts/dev/split_frontend",
    # written for the split, naming the files it replaced on purpose
    "python/uqf_frontend/tests/test_module_size.py",
    "python/uqf_frontend/tests/test_openapi_snapshot.py",
)


#: Every occurrence replaced, where a file reads the old file the same way in
#: many places: (path, old, new) - old must occur at least once.
REPLACE_ALL: tuple[tuple[str, str, str], ...] = (
    ("python/uqf_frontend/tests/test_api_types.py", "API_TS.read_text()", "api_text()"),
)


def apply_patches(root: Path, plan: Plan) -> list[Path]:
    touched = []
    for rel, old, new in PATCHES:
        path = root / rel
        text = plan.writes.get(path) or path.read_text()
        count = text.count(old)
        if count != 1:
            raise SplitError(
                f"{rel}: the patch's text occurs {count} times, not once - redo it by hand:\n{old}"
            )
        plan.writes[path] = text.replace(old, new)
        touched.append(path)
    for rel, old, new in REPLACE_ALL:
        path = root / rel
        text = plan.writes.get(path) or path.read_text()
        if old not in text:
            raise SplitError(f"{rel}: {old!r} no longer occurs - redo the patch by hand")
        plan.writes[path] = text.replace(old, new)
    return touched


def rename_prose(root: Path, plan: Plan) -> list[Path]:
    touched = []
    for path in tracked(root, "*.py", "*.md", "*.ts", "*.tsx", "*.toml", "*.qmd"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(TEXT_RENAME_SKIP) or path in plan.deletes:
            continue
        text = plan.writes.get(path) or path.read_text()
        new = text
        for old, replacement in TEXT_RENAMES:
            new = new.replace(old, replacement)
        if new != text:
            plan.writes[path] = new
            touched.append(path)
    return touched


# ----------------------------------------------------------------- OpenAPI

OPENAPI = """
import json, sys
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
sys.stdout.write(json.dumps(create_app(gateway=FakeGateway(), settings=Settings(max_rows=5000)).openapi(), indent=2) + "\\n")
"""


def openapi(root: Path) -> str:
    out = subprocess.run(
        ["uv", "run", "--quiet", "python", "-c", OPENAPI],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if out.returncode:
        raise SplitError(f"the app did not build: {out.stderr.strip()[-2000:]}")
    return out.stdout


# --------------------------------------------------------------------- main


def run(cmd: list[str], cwd: Path) -> None:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if out.returncode:
        raise SplitError(f"{' '.join(cmd[:4])} ... failed:\n{out.stdout}{out.stderr}")


def main(root: Path = REPO) -> int:
    if not (root / PKG / "models.py").exists():
        print("already split: python/uqf_frontend/src/uqf_frontend/models.py is gone")
        return 0
    if not (root / WEB / "node_modules").is_dir():
        raise SplitError("web/node_modules is missing - run `npm ci` in web/ first")
    before = openapi(root)
    plan = Plan()
    model_home = split_models(root, plan)
    split_app(root, plan, model_home)
    split_api(root, plan)
    apply_patches(root, plan)
    py = rewrite_python_callers(root, plan)
    ts = rewrite_ts_callers(root, plan)
    rename_prose(root, plan)
    support = root / "python/uqf_frontend/tests/api_support.py"
    plan.writes[support] = API_SUPPORT
    plan.writes[root / SNAPSHOT] = before
    for path, text in plan.writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    for path in plan.deletes:
        path.unlink()
    written = sorted(p for p in plan.writes if p.suffix == ".py")
    rel = [str(p.relative_to(root)) for p in written]
    run(
        ["uv", "run", "--quiet", "ruff", "check", "--fix", "--select", "I,F401", "--quiet", *rel],
        root,
    )
    run(["uv", "run", "--quiet", "ruff", "format", "--quiet", *rel], root)
    lint = subprocess.run(
        ["uv", "run", "--quiet", "ruff", "check", "--select", "F821,F811,F401", *rel],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if lint.returncode:
        raise SplitError(f"the split left undefined or unused names:\n{lint.stdout}")
    web = sorted(str(p.relative_to(root / WEB)) for p in plan.writes if p.suffix in (".ts", ".tsx"))
    run(["npx", "prettier", "--write", "--log-level", "warn", *web], root / WEB)
    after = openapi(root)
    if after != before:
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                "before",
                "after",
                n=2,
            )
        )
        raise SplitError(f"the OpenAPI document changed:\n{diff[:4000]}")
    for line in plan.report:
        print(line)
    print(
        f"split: {len(plan.writes)} files written, {len(plan.deletes)} removed; "
        f"{len(py)} Python and {len(ts)} TypeScript callers rewritten; OpenAPI unchanged"
    )
    return 0


API_SUPPORT = '''"""The API's whole surface, for the tests that hold every part of it.

models.py and web/src/api.ts were split one module per area (#2168), with no
facade over the modules. A test of every model, or of every interface the
browser declares, reads them all through here.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path
from types import ModuleType
from typing import Any

import pydantic

from uqf_frontend import models as _package
from uqf_frontend import routes as _routes

#: Every module under web/src/api/, read as text (test_api_types.py).
API_DIR = Path(__file__).resolve().parents[3] / "web" / "src" / "api"


def _modules(package: ModuleType) -> list[ModuleType]:
    return [
        importlib.import_module(f"{package.__name__}.{m.name}")
        for m in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name)
    ]


def model_modules() -> list[ModuleType]:
    """Every module under uqf_frontend/models/."""
    return _modules(_package)


def route_modules() -> list[ModuleType]:
    """Every module under uqf_frontend/routes/."""
    return _modules(_routes)


def defined_models() -> dict[str, type[pydantic.BaseModel]]:
    """Every pydantic model the models modules define, by name."""
    found: dict[str, type[pydantic.BaseModel]] = {}
    for module in model_modules():
        for name, obj in vars(module).items():
            if (
                isinstance(obj, type)
                and issubclass(obj, pydantic.BaseModel)
                and obj.__module__ == module.__name__
            ):
                assert name not in found, f"{name} is defined twice"
                found[name] = obj
    return found


def _defines(module: ModuleType) -> set[str]:
    """The names `module` itself binds at its top level - not the ones it imports."""
    tree = ast.parse(Path(module.__file__ or "").read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def model_named(name: str) -> Any:
    """The model, Literal or constant `name`, from the module defining it."""
    for module in model_modules():
        if name in _defines(module):
            return vars(module)[name]
    raise LookupError(f"no models module defines {name}")


def api_text() -> str:
    """Every web/src/api/ module's text, in name order."""
    return "\\n".join(p.read_text() for p in sorted(API_DIR.glob("*.ts")))
'''


if __name__ == "__main__":
    try:
        sys.exit(main(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO))
    except SplitError as exc:
        print(f"split_frontend: {exc}", file=sys.stderr)
        sys.exit(1)
