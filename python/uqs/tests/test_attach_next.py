"""`uqs job attachment add` and `show` end with what to run next (#2696): the
command that follows the publisher and the one that queries its rows, filled in
from the attachment - the rdb's port as discovery lists it, never a guess, and a
credential only by its variable's name.

The stack is attach_support's FakeStack: rdb1 listens on 6002 at testhost.
"""

from __future__ import annotations

import json

import pytest
from test_attach_cli import SECRET, TABLE, flat, new, run, world  # noqa: F401 - the attach world

from uqs.attach import config, publisher
from uqs.cli import attach_launch, attach_next

FOLLOW = "follow it with `uqs logs pb_ledger1 -f`"
ROWS = f"uqs query --host testhost --port 6002 'select from {TABLE} where i >= count[i]-10'"
NO_PORT = "the rdb's host and port come from the stack's discovery at localhost:6001"


def test_a_started_add_ends_with_the_follow_and_query_commands(world):  # noqa: F811
    result = run("add", *new(world, "--yes"))
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert FOLLOW in text
    assert f"query its rows with `{ROWS}`" in text
    assert "the port is rdb1's, as the stack's discovery at" in text
    assert text.index("attached") < text.index(FOLLOW), "after the run's own report"


def test_json_carries_the_commands_as_fields(world):  # noqa: F811
    result = run("add", *new(world, "--yes", "--json"))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["next"]["follow"] == "uqs logs pb_ledger1 -f"
    assert data["next"]["rows"] == ROWS
    assert data["next"]["tables"] == [TABLE]


def test_no_start_names_only_the_query(world):  # noqa: F811
    result = run("add", *new(world, "--yes", "--no-start"))
    assert result.exit_code == 0, result.output
    text = flat(result.output)
    assert "uqs logs" not in text and ROWS in text
    data = json.loads(run("add", "pb-uat", "--yes", "--no-start", "--json").output)
    assert "follow" not in data["next"] and data["next"]["rows"] == ROWS


def test_a_dry_run_names_nothing_to_run(world):  # noqa: F811
    result = run("add", *new(world, "--dry-run"))
    assert result.exit_code == 0, result.output
    assert "uqs logs" not in result.output and "uqs query" not in result.output
    data = json.loads(run("add", *new(world, "--dry-run", "--json")).output)
    assert "next" not in data


def test_a_failed_add_names_nothing_to_run(world, monkeypatch):  # noqa: F811
    def boom(self, att):
        raise config.AttachmentError("pb_ledger1 did not start (exit 3)")

    monkeypatch.setattr(publisher.UqsPublisher, "start", boom)
    result = run("add", *new(world, "--yes"))
    assert result.exit_code == 1
    assert "uqs logs" not in result.output and "uqs query" not in result.output


def test_follow_streams_instead_of_naming_the_commands(world, monkeypatch):  # noqa: F811
    def follow(wanted, already, before, port, level, stop=False):
        before()
        raise SystemExit(0)

    monkeypatch.setattr(attach_launch.start_follow, "follow", follow)
    result = run("add", *new(world, "--yes", "--follow"))
    assert world.started == ["pb_ledger1"], result.output
    assert "uqs logs" not in result.output and "uqs query" not in result.output


def test_show_names_the_commands_and_where_the_port_comes_from(world):  # noqa: F811
    assert run("add", *new(world, "--yes")).exit_code == 0
    text = flat(run("show", "pb-uat").output)
    assert FOLLOW in text
    assert "uqs query --host <rdb-host> --port <rdb-port>" in text
    assert NO_PORT in text, "show asks no stack: the port is said to come from discovery"
    nxt = json.loads(run("show", "pb-uat", "--json").output)["next"]
    assert nxt["follow"] == "uqs logs pb_ledger1 -f"
    assert nxt["rows"].startswith("uqs query --host <rdb-host> --port <rdb-port> ")
    assert NO_PORT in nxt["port_from"]


@pytest.mark.parametrize("command", ["add", "show", "show-json", "add-json"])
def test_the_password_variable_is_named_and_its_value_never_printed(
    world,  # noqa: F811
    monkeypatch,
    command,
):
    monkeypatch.setenv("ATTACH_PW", SECRET)
    first = run("add", *new(world, "--yes", "--password-env", "ATTACH_PW", "--user", "ops"))
    assert first.exit_code == 0, first.output
    assert SECRET not in first.output
    argv = {
        "add": ["add", "pb-uat", "--yes"],
        "add-json": ["add", "pb-uat", "--yes", "--json"],
        "show": ["show", "pb-uat"],
        "show-json": ["show", "pb-uat", "--json"],
    }[command]
    result = run(*argv)
    assert result.exit_code == 0, result.output
    assert SECRET not in result.output
    assert "--user ops --password-env ATTACH_PW" in flat(result.output)


def test_default_login_is_left_out_and_every_other_table_is_named():
    att = config.Attachment(
        name="a",
        stack=config.Stack(id=7000, host="h", discovery_host="h", discovery_port=7001),
        publisher=config.Publisher(job="j", procname="p1"),
        tables=(config.TableSpec("t1", ()), config.TableSpec("t2", ()), config.TableSpec("t3", ())),
    )
    nxt = attach_next.commands(att, None)
    assert "--user" not in nxt["rows"] and "--password-env" not in nxt["rows"]
    assert "select from t1 " in nxt["rows"]
    assert "  it also publishes t2, t3" in attach_next.lines(nxt)
