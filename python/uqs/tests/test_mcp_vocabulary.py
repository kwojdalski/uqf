"""uqs-mcp speaks the CLI's vocabulary, and fails one way (#2120).

The CLI redesign (#1727-#1740, #1853, #1882) gave each concept one name;
uqs-mcp was not migrated, so `port` was the stack's base port on ten tools and
one process's port on `uqs_query`: a port an agent learned from one tool
started a whole stack on the wrong base in another. It also kept the spellings
the CLI retired (`passwd`, `procname`, `uqs_list(kind)`, `uqs_print`,
`min_level`) and failed four ways - a string, a dict, a list, or an uncaught
exception.

These hold the tools to the CLI's command tree as test_cli_conventions.py
holds the tree to cli/vocabulary.py: each tool mirrors one CLI command and is
named after it, each parameter is one of that command's spelled as
`vocabulary.spelling` spells it with the type the CLI gives it, and every
failure is `{"ok": false, "error"}`. A new tool or parameter that drifts fails
here.
"""

from __future__ import annotations

import dataclasses
import types

import pytest
from mcp_support import call, refused, tools

from uqs import mcp, mcp_server
from uqs.cli import vocabulary
from uqs.paths import UqsError

TOOLS = {t.name: t for t in tools()}

#: A CLI parameter's type as JSON Schema names it.
_CLICK_TO_JSON = {"int": "integer", "int range": "integer", "boolean": "boolean"}


def _cli_params(tool: str) -> dict[str, object]:
    """The mirrored command's parameters a caller can give, by their spelling.
    Its `--yes` is spelled `confirm` (mcp_server.CONFIRM, #2257): a tool has no
    terminal to ask on, so its question is answered in the call."""
    command = mcp.cli_command(mcp.MIRRORS[tool])
    given = {vocabulary.spelling(p): p for p in command.params if not getattr(p, "hidden", False)}
    if "yes" in given:
        given[mcp_server.CONFIRM] = given.pop("yes")
    return given


def _json_type(schema: dict) -> str | None:
    """A property's type, ignoring the `null` an optional one allows."""
    types = [s.get("type") for s in schema.get("anyOf", [schema])]
    return next((t for t in types if t != "null"), None)


def _cli_json_type(param) -> str:
    if param.param_type_name == "argument" and param.nargs == -1:
        return "array"
    return _CLICK_TO_JSON.get(param.type.name, "string")


def test_every_tool_mirrors_a_cli_command_and_is_named_after_it():
    assert set(TOOLS) == set(mcp.MIRRORS)
    misnamed = {n: p for n, p in mcp.MIRRORS.items() if mcp.tool_name(p) != n}
    assert not misnamed
    hidden = {n for n, p in mcp.MIRRORS.items() if getattr(mcp.cli_command(p), "hidden", False)}
    assert not hidden, f"tools mirroring a retired (hidden) CLI spelling: {hidden}"


def test_every_parameter_is_its_cli_commands_spelling_and_type():
    stray, wrong = set(), set()
    for name, t in TOOLS.items():
        cli = _cli_params(name)
        for param, schema in t.input_schema["properties"].items():
            if param not in cli:
                stray.add((name, param))
            elif _json_type(schema) != _cli_json_type(cli[param]):
                wrong.add((name, param, _json_type(schema), _cli_json_type(cli[param])))
    assert not stray, f"(tool, parameter its CLI command does not take): {sorted(stray)}"
    assert not wrong, f"(tool, parameter, its type, the CLI's): {sorted(wrong)}"


def test_every_parameter_a_term_names_has_the_terms_type_and_no_retired_spelling():
    """cli/vocabulary.py's TERMS, directly: `base_port` is an integer wherever
    it is taken, and no tool takes a spelling a term retired unless it is
    another term's live one (PROCS is live; --procs is retired)."""
    terms = {vocabulary.identifier(t.name): t for t in vocabulary.TERMS}
    gone = {vocabulary.identifier(old) for t in vocabulary.TERMS for old in t.retired}
    term_json = {"int": "integer", "flag": "boolean"}
    wrong, retired = set(), set()
    for name, t in TOOLS.items():
        for param, schema in t.input_schema["properties"].items():
            term = terms.get(param)
            if term is not None and term.type in term_json:
                if _json_type(schema) != term_json[term.type]:
                    wrong.add((name, param))
            if param in gone and param not in terms:
                retired.add((name, param))
    assert not wrong
    assert not retired


def test_port_is_one_process_port_and_base_port_the_stacks():
    """The bug: `port` meant a base port on ten tools. Only uqs_query takes
    `port`, and every tool whose command takes --base-port takes base_port."""
    takes_port = {n for n, t in TOOLS.items() if "port" in t.input_schema["properties"]}
    assert takes_port == {"uqs_query"}
    missing = {
        n
        for n, t in TOOLS.items()
        if "base_port" in _cli_params(n) and "base_port" not in t.input_schema["properties"]
    }
    assert not missing


def test_every_tool_returns_an_object():
    assert {n for n, t in TOOLS.items() if (t.output_schema or {}).get("type") != "object"} == set()


@pytest.mark.parametrize(
    ("tool", "arguments", "says"),
    [
        ("uqs_start", {"port": 6012}, "`port` is now `base_port`"),
        ("uqs_status", {"port": 6012}, "`port` is now `base_port`"),
        ("uqs_query", {"expr": "1", "port": 1, "passwd": "x"}, "`passwd` is now `password_env`"),
        ("uqs_config_show", {"procname": "rdb1"}, "`procname` is now `proc`"),
        ("uqs_config_show", {"proc": "rdb1", "resolve": False}, "`resolve` is now `unresolved`"),
        ("uqs_logs", {"min_level": "WARNING"}, "`min_level` is now `level`"),
        ("uqs_clean", {"dry_run": True}, "`dry_run` is now `confirm`"),
    ],
)
def test_a_retired_parameter_is_refused_naming_its_replacement(tool, arguments, says):
    assert says in refused(call(tool, **arguments))


@pytest.mark.parametrize("old", sorted(mcp.RETIRED_TOOLS))
def test_a_retired_tool_is_refused_naming_its_replacement(old):
    assert refused(call(old)).startswith(f"{old} is now ")
    assert old not in TOOLS


def test_every_retired_tool_names_live_tools():
    named = {
        word.rstrip(",")
        for why in mcp.RETIRED_TOOLS.values()
        for word in why.split()
        if word.startswith("uqs_")
    }
    assert named <= set(TOOLS)


def _minimal(t) -> dict:
    """Arguments for each parameter `t` requires, valid in shape."""
    values = {"string": "rdb1", "integer": 1, "boolean": False, "array": ["port=1"]}
    props = t.input_schema["properties"]
    return {p: values[str(_json_type(props[p]))] for p in t.input_schema.get("required", [])}


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_failure_is_one_refusal(tool, monkeypatch):
    """Each tool, failing - on its arguments, or on the stack's paths, which
    refuse as with no runtime here. Every one says so as `{"ok": false,
    "error"}`, none as text or a raise."""

    def no_paths():
        raise UqsError("no stack here")

    monkeypatch.setattr(mcp, "_paths", no_paths)
    refused(call(tool, **_minimal(TOOLS[tool])))


def test_a_query_that_cannot_connect_is_refused_not_raised(monkeypatch):
    """uqs_query let its exception through (#2120)."""

    def refused_connection(*_a, **_k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(mcp.runtime, "query", refused_connection)
    assert "connection refused" in refused(call("uqs_query", expr="1", port=1))


def test_an_unknown_tool_and_a_bad_argument_are_refused_too():
    assert "nosuchtool" in refused(call("uqs_nosuchtool"))
    assert "nosuch" in refused(call("uqs_status", nosuch=1))


def test_a_failed_torq_verb_is_a_refusal_not_text(monkeypatch):
    """A start torq.sh failed was returned as its text, indistinguishable from
    one that worked but for an `ERROR:` prefix."""

    class Failed:
        stdout, stderr, returncode = "", "rdb1 did not start", 1

    monkeypatch.setattr(mcp.runtime, "stop", lambda *_a, **_k: Failed())
    monkeypatch.setattr(mcp, "_paths", lambda: None)
    assert refused(call("uqs_stop")) == "rdb1 did not start"


def test_a_connector_starts_with_its_own_options_and_refuses_anothers(monkeypatch):
    """uqs_crypto_start is uqs_connector_start(connector="crypto"): the options
    are every connector's, as `uqs connector start` builds them, and one that
    is not the named connector's is refused as the CLI refuses it."""
    seen = {}

    def start(_paths, options):
        seen.update(options)
        return 4242

    crypto = dataclasses.replace(mcp.FEEDS["crypto"], start=start)
    monkeypatch.setitem(mcp.FEEDS, "crypto", crypto)
    done = call("uqs_connector_start", connector="crypto", top_n_levels=3, base_port=7000)
    assert not done.is_error, done
    assert done.structured_content["pid"] == 4242
    assert seen["top_n_levels"] == 3 and seen["base_port"] == 7000
    error = refused(call("uqs_connector_start", connector="crypto", brokers="localhost:9092"))
    assert "brokers does not apply to connector crypto" in error


def test_clean_is_a_dry_run_unless_confirmed(tmp_path, monkeypatch):
    """uqs_clean deleted the data directory, the HDB with it, in one call
    (#2257). Without confirm=true it lists each entry with its bytes and
    removes nothing; confirm=true removes exactly what that listed."""
    data = tmp_path / "data"
    (data / "hdb" / "2026.01.02").mkdir(parents=True)
    (data / "hdb" / "2026.01.02" / "price").write_bytes(b"x" * 10)
    (data / "logs").mkdir()
    (data / "logs" / "rdb1.log").write_bytes(b"y" * 3)
    monkeypatch.setattr(mcp, "_paths", lambda: types.SimpleNamespace(torqdata=data))

    dry = call("uqs_clean").structured_content
    assert dry == {"dry_run": True, "entries": [{"path": str(data), "bytes": 13}], "bytes": 13}
    assert (data / "hdb" / "2026.01.02" / "price").exists()

    logs = call("uqs_clean", match="^logs$").structured_content
    assert logs["entries"] == [{"path": str(data / "logs"), "bytes": 3}]
    assert (data / "logs").exists()

    done = call("uqs_clean", match="^logs$", confirm=True).structured_content
    assert done == {"dry_run": False, "entries": logs["entries"], "bytes": 3}
    assert not (data / "logs").exists()
    assert (data / "hdb").exists()
