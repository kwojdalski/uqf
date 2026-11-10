"""Inspection mode holds for every tunnel to a target's q (#2032, #2179).

`uqs --target prod query EXPR` was refused on an inspection-only target while
`uqs query --tunnel prod EXPR` ran the same q there: the tunnel path never
asked uqs.deploy.inspection. Two halves here:

- the reproduction, through the issue's stand-in ssh (it serves `-L` forwards
  on this machine), with the q call recorded instead of made;
- a gate over the source: every call that opens a tunnel to a target's q
  processes either passes through `inspection.refuse` or is listed in
  READ_ONLY with why it only reads. A new caller fails here until it says
  which it is.
"""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from uqs import runtimes
from uqs.cli import app
from uqs.cli import query as query_mod
from uqs.cli import target as target_cli
from uqs.deploy import control, inspection, targets

# --------------------------------------------------------------- the gate

PYTHON = Path(__file__).resolve().parents[2]
SOURCES = [PYTHON / "uqs" / "src", PYTHON / "uqf_frontend" / "src"]

#: (module the helper lives in, its name): each opens a tunnel to a target's q.
HELPERS = {
    ("uqs.deploy.query_target", "connect"),
    ("uqs.deploy.tunnel", "tunnel"),
    ("uqs.deploy.tunnel", "forwarded"),
    ("uqs.deploy.workspace", "open_connection"),
}

#: The helpers' own implementations: a call inside one is the helper itself,
#: held at ITS callers. (query_target.connect is not here: it refuses.)
IMPLEMENTATION = {"uqs/deploy/tunnel.py", "uqs/deploy/workspace.py"}

#: Callers that reach an inspection-only target on purpose, each with why
#: nothing it sends can change the server. "path:function".
READ_ONLY: dict[str, str] = {
    "uqs/cli/inspect.py:schema": (
        "`uqs schema --tunnel` sends only schema_view's own programs - the table "
        "list, a table's meta and its row count - never the caller's q"
    ),
    "uqs/deploy/diff_endpoints.py:reach": (
        "`uqs data diff` and the frontend's /compare send only compare.q's "
        "program; a filter's column, operator and value go as typed arguments"
    ),
    "uqs/deploy/tunnel_check.py:run": (
        "the tunnel check asks each process a fixed identity probe "
        "(verify.IDENTITY_EXPR) and a gateway its servers (GATEWAY_READY_EXPR)"
    ),
    "uqf_frontend/workspaces.py:open_with_uqs": (
        "the frontend's workspace (deploy/workspace.py forwards the target's ports): "
        "its app.guard refuses every write route for an "
        "inspection-only target (uqf_frontend/inspection.py), and its read routes "
        "send only queries.py's server-authored programs"
    ),
}


def _modules() -> dict[str, tuple[Path, ast.Module]]:
    found = {}
    for src in SOURCES:
        for path in sorted(src.rglob("*.py")):
            found[path.relative_to(src).as_posix()] = (path, ast.parse(path.read_text()))
    return found


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Each local name bound to a helper's module: `from uqs.deploy import
    query_target as via_target` binds via_target to uqs.deploy.query_target."""
    modules = {m for m, _ in HELPERS}
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                full = f"{node.module}.{a.name}"
                if full in modules:
                    bound[a.asname or a.name] = full
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name in modules:
                    bound[a.asname or a.name] = a.name
    return bound


def _refuses(fn: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr.startswith("refuse")
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "inspection"
        for n in ast.walk(fn)
    )


def _call_sites() -> list[tuple[str, str, ast.Call, ast.AST]]:
    """(path:function, the helper, the call, its function) for every helper call."""
    sites = []
    for rel, (_path, tree) in _modules().items():
        if rel in IMPLEMENTATION:
            continue
        bound = _aliases(tree)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                owner = call.func.value
                if not isinstance(owner, ast.Name) or owner.id not in bound:
                    continue
                if (bound[owner.id], call.func.attr) in HELPERS:
                    sites.append((f"{rel}:{fn.name}", call.func.attr, call, fn))
    # a nested function is walked by its parent too: keep the innermost
    inner: dict[int, tuple[str, str, ast.Call, ast.AST]] = {}
    for site in sites:
        inner[id(site[2])] = site
    return list(inner.values())


def test_no_helper_is_imported_by_its_bare_name():
    """`from uqs.deploy.tunnel import forwarded` would hide a call from the gate."""
    named = {f"{m}.{n}" for m, n in HELPERS}
    for rel, (_path, tree) in _modules().items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    assert f"{node.module}.{a.name}" not in named, (
                        f"{rel} imports {a.name} from {node.module}: import the module, "
                        "so the inspection gate sees the call"
                    )


def test_every_tunnel_to_a_target_is_refused_or_listed_as_a_read():
    sites = _call_sites()
    assert sites, "the gate found no caller at all - it is looking in the wrong place"
    unheld = []
    for where, helper, call, fn in sites:
        if helper == "connect":
            given = {k.arg: k.value for k in call.keywords}
            assert "change" in given, f"{where} calls connect without saying `change=`"
            if not (isinstance(given["change"], ast.Constant) and given["change"].value is None):
                continue  # connect refuses it
        elif _refuses(fn):
            continue
        if where not in READ_ONLY:
            unheld.append(where)
    assert not unheld, (
        "these open a tunnel to a target without inspection.refuse: refuse a change "
        f"(`change=` for connect), or list a read in READ_ONLY with why: {unheld}"
    )


def test_every_read_only_exemption_is_still_a_caller():
    callers = {site[0] for site in _call_sites()}
    assert set(READ_ONLY) <= callers, f"stale READ_ONLY entries: {set(READ_ONLY) - callers}"
    assert all(len(why) > 40 for why in READ_ONLY.values())


def test_connect_refuses_what_it_is_told_is_a_change():
    tree = _modules()["uqs/deploy/query_target.py"][1]
    connect = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "connect"
    )
    assert _refuses(connect)


# ------------------------------------------------------ the reproduction

#: The issue's stand-in ssh: `-L` forwards are served on this machine, and
#: every call is logged.
STAND_IN_SSH = f"""#!{sys.executable}
import sys, socket, threading, os
open(os.environ["FAKE_SSH_LOG"], "a").write(" ".join(sys.argv[1:]) + "\\n")
fw = [a for i, a in enumerate(sys.argv) if i and sys.argv[i-1] == "-L"]
if not fw:
    sys.exit(255)
srvs = []
for spec in fw:
    _, l, _, r = spec.split(":")
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", int(l))); s.listen(5); srvs.append(s)
def serve(s):
    while True:
        c, _ = s.accept(); c.close()
ts = [threading.Thread(target=serve, args=(s,), daemon=True) for s in srvs]
[t.start() for t in ts]; [t.join() for t in ts]
"""

REGISTRY = """\
[targets.prod]
ssh = "deploy@prod"
dest = "/srv/uqf"
inspection = true

[targets.prod-rw]
ssh = "root@PROD"
dest = "/srv/uqf"

[targets.uat]
ssh = "deploy@uat"
dest = "/srv/uqf"
"""


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(STAND_IN_SSH)
    (bin_dir / "ssh").chmod(0o755)
    log = tmp_path / "ssh.log"
    log.touch()
    monkeypatch.setenv("FAKE_SSH_LOG", str(log))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv(runtimes.RUNTIME_ENV, raising=False)
    (tmp_path / "deploy_targets.toml").write_text(REGISTRY)
    monkeypatch.setenv(targets.DECLARATION_ENV, str(tmp_path / "deploy_targets.toml"))
    monkeypatch.setenv("UQS_DEPLOY_HISTORY", str(tmp_path / "deploy_history.json"))
    return log


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each q expression that reached a process, in place of sending it."""
    exprs: list[str] = []

    def query(expr, port, **_):
        exprs.append(expr)
        return 42

    monkeypatch.setattr(query_mod.runtime, "query", query)
    monkeypatch.setattr(query_mod.via_target, "run_qcon", lambda *a: exprs.append("<qcon>"))
    return exprs


def _query(*args: str):
    return CliRunner().invoke(app, ["query", *args])


@pytest.mark.parametrize(
    "args",
    [
        ["--tunnel", "prod", "--port", "47811", "--no-gateway", "changed:42"],
        ["--tunnel", "prod", "--port", "47811"],  # no EXPR: a q session there
        ["--tunnel", "prod", "rdb1", "delete from `trade"],
    ],
)
def test_a_tunnel_query_to_an_inspection_target_is_refused_before_ssh(
    stand_in, sent, log_text, args
):
    result = _query(*args)
    assert result.exit_code != 0
    assert "target prod is in inspection mode: `uqs query --tunnel prod` is refused" in log_text()
    assert sent == []
    assert stand_in.read_text() == "", "ssh was opened to an inspection-only target"


def test_a_tunnel_query_to_the_same_server_by_another_name_is_refused(stand_in, sent, log_text):
    """#2093: the server is matched by its (ssh, dest), not the name alone."""
    result = _query("--tunnel", "prod-rw", "--port", "47811", "--no-gateway", "changed:42")
    assert result.exit_code != 0
    assert (
        "target prod is in inspection mode (the server at root@PROD:/srv/uqf is target prod)"
        in log_text()
    )
    assert sent == [] and stand_in.read_text() == ""


def test_a_tunnel_query_to_another_target_still_runs(stand_in, sent):
    result = _query("--tunnel", "uat", "--port", "47811", "--no-gateway", "1+1")
    assert result.exit_code == 0, result.output
    assert sent == ["1+1"]
    assert "-L 127.0.0.1:" in stand_in.read_text()


def test_the_on_server_query_is_still_refused(stand_in, monkeypatch):
    monkeypatch.setattr(control, "run", lambda *a, **k: pytest.fail("prod was reached"))
    with pytest.raises(
        inspection.InspectionRefused, match="target prod is in inspection mode: `uqs query"
    ):
        target_cli.forward("prod", True, ["query", "--port", "47811", "changed:1b"],
                           interactive=False)  # fmt: skip
