"""uqs-mcp is the CLI, as tools: derived from its command tree, speaking its
vocabulary, failing one way (#2120, #2988).

The CLI redesign (#1727-#1740, #1853, #1882) gave each concept one name;
uqs-mcp was not migrated, so `port` was the stack's base port on ten tools and
one process's port on `uqs_query`, and it failed four ways. Then the CLI grew
to about a hundred commands while uqs-mcp kept fifteen hand-written tools.

The tools are now derived (uqs.mcp): one per visible CLI leaf, named after it,
each parameter one of the command's spelled as `vocabulary.spelling` spells
it, with the type the CLI gives it, and the call runs the command. These hold
the derivation to that: every leaf mirrored or excluded with a reason, every
parameter the command's, every confirming tool a preview unless confirm=true,
and every failure `{"ok": false, "error"}`.
"""

from __future__ import annotations

import pytest
from mcp_support import RAN, call, refused, tools

from uqs import mcp, mcp_server
from uqs.cli.common import vocabulary
from uqs.cli.fleet import cleanup
from uqs.stack import alive

TOOLS = {t.name: t for t in tools()}

#: A CLI parameter's type as JSON Schema names it.
_CLICK_TO_JSON = {"int": "integer", "int range": "integer", "boolean": "boolean", "float": "number"}


def _cli_params(tool: str) -> dict[str, object]:
    """The parameters a caller can give the tool, as the CLI defines them:
    the command's own (uqs.mcp.parameters), `confirm` where it is a preview
    unless confirmed, and the global options."""
    path = mcp.MIRRORS[tool]
    command = mcp.cli_command(path)
    given: dict[str, object] = dict(mcp.parameters(path, command))
    if mcp.confirms(path, command):
        given[mcp_server.CONFIRM] = "confirm"
    for name in mcp.GLOBAL:
        given.setdefault(name, "global")
    return given


def _json_type(schema: dict) -> str | None:
    """A property's type, ignoring the `null` an optional one allows."""
    types = [s.get("type") for s in schema.get("anyOf", [schema])]
    return next((t for t in types if t != "null"), None)


def _cli_json_type(param) -> str:
    if param in ("confirm",):
        return "boolean"
    if param in ("global",):
        return "string"
    many = getattr(param, "multiple", False) or (
        param.param_type_name == "argument" and param.nargs == -1
    )
    return "array" if many else _CLICK_TO_JSON.get(param.type.name, "string")


def test_every_tool_mirrors_a_cli_command_and_is_named_after_it():
    assert set(TOOLS) == set(mcp.MIRRORS)
    misnamed = {n: p for n, p in mcp.MIRRORS.items() if mcp.tool_name(p) != n}
    assert not misnamed
    hidden = {n for n, p in mcp.MIRRORS.items() if getattr(mcp.cli_command(p), "hidden", False)}
    assert not hidden, f"tools mirroring a retired (hidden) CLI spelling: {hidden}"


def test_every_cli_command_is_a_tool_or_excluded_with_its_reason():
    """The drift #2988 fixed: fifteen tools for a hundred commands. A new
    command is a tool with nothing to write; one that cannot be says why."""
    leaves = {path for path, _command in mcp.leaves()}
    assert set(mcp.MIRRORS.values()) == leaves - set(mcp.EXCLUDED)
    assert set(mcp.EXCLUDED) <= leaves, "an exclusion names a command the CLI no longer has"
    assert all(why.strip() for why in mcp.EXCLUDED.values())


def test_every_parameter_is_its_cli_commands_spelling_and_type():
    stray, wrong, missing = set(), set(), set()
    for name, t in TOOLS.items():
        cli = _cli_params(name)
        props = t.input_schema["properties"]
        missing |= {(name, p) for p in cli if p not in props}
        for param, schema in props.items():
            if param not in cli:
                stray.add((name, param))
            elif _json_type(schema) != _cli_json_type(cli[param]):
                wrong.add((name, param, _json_type(schema), _cli_json_type(cli[param])))
    assert not stray, f"(tool, parameter its CLI command does not take): {sorted(stray)}"
    assert not wrong, f"(tool, parameter, its type, the CLI's): {sorted(wrong)}"
    assert not missing, f"(tool, CLI parameter it does not offer): {sorted(missing)}"


def test_a_choice_is_an_enum_of_the_clis_choices():
    for name, t in TOOLS.items():
        for param, cli in _cli_params(name).items():
            choices = getattr(getattr(cli, "type", None), "choices", None)
            if choices and not getattr(cli, "multiple", False):
                schema = t.input_schema["properties"][param]
                enums = [s.get("enum") for s in schema.get("anyOf", [schema]) if s.get("enum")]
                assert enums == [[str(c) for c in choices]], (name, param, schema)


def test_terminal_only_options_are_never_offered():
    offered = {
        (n, p)
        for n, t in TOOLS.items()
        for p in t.input_schema["properties"]
        if p in mcp.terminal_only(mcp.MIRRORS[n]) | mcp.SET_BY_THE_TOOL
    }
    assert not offered
    assert "multitail" not in TOOLS["uqs_logs"].input_schema["properties"]
    assert "expr" in TOOLS["uqs_query"].input_schema["required"], "a session needs a terminal"


def test_every_parameter_a_term_names_has_the_terms_type_and_no_retired_spelling():
    """cli/common/vocabulary.py's TERMS, directly: `base_port` is an integer wherever
    it is taken, and no tool takes a spelling a term retired unless it is
    another term's live one (PROCS is live; --procs is retired) - or its
    command's own, which test_cli_conventions.py holds: a tool mirrors it."""
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
            if param in gone and param not in terms and param not in _cli_params(name):
                retired.add((name, param))
    assert not wrong
    assert not retired


def test_port_is_one_process_port_and_base_port_the_stacks():
    """The bug: `port` meant a base port on ten tools. A tool takes `port`
    exactly where its command takes --port, one process's port, and every
    tool whose command takes --base-port takes base_port."""
    takes_port = {n for n, t in TOOLS.items() if "port" in t.input_schema["properties"]}
    assert takes_port == {
        n for n, p in mcp.MIRRORS.items() if "--port" in mcp.flags(mcp.cli_command(p))
    }
    assert "uqs_query" in takes_port and "uqs_start" not in takes_port
    missing = {
        n
        for n, t in TOOLS.items()
        if "base_port" in _cli_params(n) and "base_port" not in t.input_schema["properties"]
    }
    assert not missing


def test_every_tool_returns_an_object():
    assert {n for n, t in TOOLS.items() if (t.output_schema or {}).get("type") != "object"} == set()


def test_read_only_is_what_the_command_declares():
    """readOnlyHint is the command's own Effect, the one `uqs --target` reads."""
    marked = {n for n, t in TOOLS.items() if t.annotations and t.annotations.read_only_hint}
    declared = {n for n, p in mcp.MIRRORS.items() if mcp.read_only(mcp.cli_command(p))}
    assert marked == declared
    assert {"uqs_job_list", "uqs_profile_list"} <= marked
    assert not marked & {"uqs_clean", "uqs_start", "uqs_query", "uqs_job_backfill"}
    assert not {n for n in marked if mcp_server.CONFIRM in TOOLS[n].input_schema["properties"]}


# ------------------------------------------------------------- the command line


@pytest.fixture
def ran(monkeypatch):
    """Record the command line each call runs, and answer with `{}`."""
    seen: list[list[str]] = []

    def run(argv):
        seen.append(argv)
        return mcp_server.Ran(0, "{}", "")

    monkeypatch.setattr(mcp_server, "RUN", run)
    return seen


def test_a_call_runs_its_command_with_json(ran):
    assert not call("uqs_job_list").is_error
    assert ran == [["job", "list", "--json"]]


def test_runtime_is_the_global_option_before_the_command(ran):
    call("uqs_profile_list", runtime="torq")
    assert ran == [["--runtime", "torq", "profile", "list", "--json"]]


def test_options_then_positionals_in_the_commands_order(ran):
    call("uqs_job_backfill", job="demo_deals_backfill", range_from="2026-09-13",
         range_to="2026-09-15", source_version="v1", wait=True)  # fmt: skip
    argv = ran[0]
    assert argv[:2] == ["job", "backfill"] and argv[-1] == "demo_deals_backfill"
    assert argv[argv.index("--from") + 1] == "2026-09-13"
    assert argv[argv.index("--to") + 1] == "2026-09-15"
    assert "--wait" in argv and "--json" in argv


def test_a_positional_that_starts_with_a_dash_follows_double_dash(ran):
    call("uqs_query", expr="-1+2", port=5000)
    assert ran[0][-2:] == ["--", "-1+2"]


def test_a_confirming_tool_is_a_preview_unless_confirmed(ran):
    """#2257, for every command that asks: without confirm, --dry-run (or no
    --apply); with it, --yes (and --apply). No dry_run or apply beside it."""
    call("uqs_clean")
    call("uqs_clean", confirm=True)
    call("uqs_data_alter", table="t", rename=["a=b"])
    call("uqs_data_alter", table="t", rename=["a=b"], confirm=True)
    clean, clean_yes, alter, alter_yes = ran
    assert "--dry-run" in clean and "--yes" not in clean
    assert "--yes" in clean_yes and "--dry-run" not in clean_yes
    assert "--apply" not in alter and "--yes" not in alter
    assert {"--apply", "--yes"} <= set(alter_yes)
    for name, t in TOOLS.items():
        props = t.input_schema["properties"]
        if mcp_server.CONFIRM in props:
            assert props[mcp_server.CONFIRM].get("default") is False, name
            assert not {"dry_run", "apply", "yes"} & set(props), name


def test_deploy_push_is_a_preview_unless_confirmed(ran):
    call("uqs_deploy_push", artifact="release.tar.gz")
    assert "--dry-run" in ran[0]


# ---------------------------------------------------------------- the answers


def test_a_documents_nonzero_exit_is_kept_beside_it(monkeypatch):
    """No work (6) is an outcome a scheduler reads, not a failure."""
    monkeypatch.setattr(
        mcp_server, "RUN", lambda _a: mcp_server.Ran(6, '{"outcome": "no_work"}', "")
    )
    done = call("uqs_job_run", job="x", once=True)
    assert not done.is_error
    assert done.structured_content == {"outcome": "no_work", "exit_code": 6}


def test_a_refusal_with_details_comes_back_whole(monkeypatch):
    doc = '{"ok": false, "error": "refused", "findings": [{"code": "active_date"}]}'
    monkeypatch.setattr(mcp_server, "RUN", lambda _a: mcp_server.Ran(1, doc, ""))
    done = call("uqs_job_backfill", job="x")
    assert done.is_error and done.structured_content["findings"] == [{"code": "active_date"}]


def test_a_command_without_json_answers_with_what_it_printed(monkeypatch):
    monkeypatch.setattr(mcp_server, "RUN", lambda _a: mcp_server.Ran(0, "stopped rdb1\n", ""))
    assert call("uqs_stop", procs=["rdb1"]).structured_content == {"output": "stopped rdb1"}


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_failure_is_one_refusal(tool, monkeypatch):
    """Each tool, its command failing - with a refusal document under --json,
    or an ERROR line without it. Every one says so as `{"ok": false,
    "error"}`, none as text or a raise."""

    def failed(argv):
        if "--json" in argv:
            return mcp_server.Ran(1, '{"ok": false, "error": "no stack here"}', "")
        return mcp_server.Ran(1, "", "INFO     | starting\nERROR    | no stack here\n")

    monkeypatch.setattr(mcp_server, "RUN", failed)
    required = TOOLS[tool].input_schema.get("required", [])
    values = {"string": "rdb1", "integer": 1, "boolean": False, "array": ["x"]}
    props = TOOLS[tool].input_schema["properties"]
    arguments = {p: values[str(_json_type(props[p]))] for p in required}
    for p in required:  # an enum's own value
        enums = [s["enum"] for s in props[p].get("anyOf", [props[p]]) if s.get("enum")]
        if enums:
            arguments[p] = enums[0][0]
    assert refused(call(tool, **arguments)) == "no stack here"


def test_an_unparseable_answer_is_refused_with_what_the_command_said(monkeypatch):
    monkeypatch.setattr(
        mcp_server, "RUN", lambda _a: mcp_server.Ran(1, "Traceback ...", "ERROR    | it broke")
    )
    assert refused(call("uqs_status")) == "it broke"


def test_an_unknown_tool_and_a_bad_argument_are_refused_too():
    assert "nosuchtool" in refused(call("uqs_nosuchtool"))
    assert "nosuch" in refused(call("uqs_status", nosuch=1))


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
        ("uqs_data_alter", {"table": "t", "apply": True}, "`apply` is now `confirm`"),
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


# ------------------------------------------------------ through the real CLI


def test_the_server_runs_the_cli_in_a_child():
    """The server's own runner, once: a child process, its document parsed."""
    ran = mcp_server.run_child(["override", "list", "--json"])
    assert ran.exit_code == 0, ran.stderr
    assert "overrides" in mcp_server.outcome(ran, as_json=True)


def test_a_query_that_cannot_connect_is_refused_not_raised(monkeypatch):
    """uqs_query let its exception through (#2120)."""
    from uqs.stack import runtime

    def refused_connection(*_a, **_k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(runtime, "query", refused_connection)
    assert "connection refused" in refused(call("uqs_query", expr="1", port=1))


def test_clean_is_a_dry_run_unless_confirmed(tmp_path, monkeypatch):
    """uqs_clean deleted the data directory, the HDB with it, in one call
    (#2257). Without confirm=true it lists each entry with its bytes and
    removes nothing; confirm=true removes exactly what that listed."""
    data = tmp_path / "data"
    (data / "hdb" / "2026.01.02").mkdir(parents=True)
    (data / "hdb" / "2026.01.02" / "price").write_bytes(b"x" * 10)
    (data / "logs").mkdir()
    (data / "logs" / "rdb1.log").write_bytes(b"y" * 3)
    monkeypatch.setattr(cleanup, "_paths", lambda: _Paths(data))
    monkeypatch.setattr(alive, "stoppable", lambda *_a, **_k: ({}, {}))

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


def test_clean_confirmed_is_refused_while_the_stack_runs(tmp_path, monkeypatch):
    """#2742: confirm=true answers the question, not whether the stack is up."""
    data = tmp_path / "data"
    (data / "tplogs").mkdir(parents=True)
    (data / "tplogs" / "tp1.log").write_bytes(b"x" * 10)
    monkeypatch.setattr(cleanup, "_paths", lambda: _Paths(data))
    monkeypatch.setattr(alive, "stoppable", lambda *_a, **_k: ({"tp1": [4242]}, {}))

    error = refused(call("uqs_clean", confirm=True))
    assert "the stack is running (tp1)" in error and "Stop it first (`uqs stop`)" in error
    assert (data / "tplogs" / "tp1.log").exists()


class _Paths:
    """The stack paths clean reads: its data directory."""

    def __init__(self, torqdata):
        self.torqdata = torqdata


def test_the_in_process_runner_is_what_the_tests_use():
    RAN.clear()
    call("uqs_override_list")
    assert RAN == [["override", "list", "--json"]]
