"""The operation manifest, the schema-file block and the publisher's start gate (#1424).

Pure files and functions: no q, no stack. They are the parts reversal rests on,
so each test pins a property reversal needs rather than a code path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from attach_support import make_bundle

from uqs.attach import config, manifest, persist, service
from uqs.attach.config import AttachmentError
from uqs.attach.manifest import Manifest
from uqs.attach.schema import TableSpec
from uqs.paths import UqsError, default_paths, repo_root
from uqs.stack import attach_state, start_policy

ROOT = repo_root()
TABLE = "piggybank_general_ledger"


@pytest.fixture
def att(tmp_path: Path) -> config.Attachment:
    bundle = make_bundle(tmp_path / "piggybank")
    return service.create("pb-uat", "pb_general_ledger", bundle, 6000, ROOT, runtime_only=True)


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "attachments"


def fresh(att: config.Attachment, state: Path) -> Manifest:
    return Manifest.new(att, Manifest.path_for(att.name, state))


# ---------------------------------------------------------------- manifest


def test_a_manifest_round_trips_with_its_operations_and_history(att, state):
    m = fresh(att, state)
    op = m.begin("stp_register", "stp1", TABLE, {"table_present": False})
    m.finish(op, manifest.DONE, manifest.CREATED, {"logs": [{"name": "a", "size": 8}]}, "ok")
    m.set_state("applied", "1 change")
    loaded = Manifest.load("pb-uat", state)
    assert loaded.state == "applied" and loaded.token == m.token
    assert loaded.tables[TABLE]["columns"][0] == ["time", "p"]
    assert loaded.operations == m.operations
    assert [h["event"] for h in loaded.history] == ["created", "applied"]


def test_an_operation_is_on_disk_as_pending_before_it_is_finished(att, state):
    """Write-ahead, at the file level: this is what an interrupted run leaves."""
    m = fresh(att, state)
    m.begin("stp_register", "stp1", TABLE, {"table_present": False})
    on_disk = json.loads(m.path.read_text())["operations"]
    assert [(o["op"], o["status"], o["ownership"]) for o in on_disk] == [
        ("stp_register", "pending", "")
    ]
    assert m.created() == [] or m.created()[0].status == "pending"


def test_a_retry_reuses_the_operation_and_keeps_what_was_true_before_the_first_try(att, state):
    """The record of what was there BEFORE must survive a retry: a second try
    sees the table the first one created, and must not rewrite history to say
    it was always there."""
    m = fresh(att, state)
    first = m.begin("stp_register", "stp1", TABLE, {"table_present": False})
    m.finish(first, manifest.PENDING, note="outcome unknown")
    again = m.begin("stp_register", "stp1", TABLE, {"table_present": True})
    assert again is first and len(m.operations) == 1
    assert again.pre == {"table_present": False}


def test_a_finished_step_starts_a_new_cycle_with_a_new_before(att, state):
    m = fresh(att, state)
    op = m.begin("stp_register", "stp1", TABLE, {"table_present": False})
    m.finish(op, manifest.DONE, manifest.CREATED)
    m.finish(op, manifest.REVERTED)
    op = m.begin("stp_register", "stp1", TABLE, {"table_present": False, "second": True})
    assert op.pre["second"] is True


def test_only_what_the_attachment_created_is_removable_newest_first(att, state):
    m = fresh(att, state)
    a = m.begin("stp_register", "stp1", TABLE, {})
    m.finish(a, manifest.DONE, manifest.CREATED)
    b = m.begin("consumer_attach", "rdb1", TABLE, {})
    m.finish(b, manifest.DONE, manifest.EXISTING)
    c = m.begin("consumer_attach", "wdb1", TABLE, {})
    m.finish(c, manifest.DONE, manifest.SHARED)
    d = m.begin("schema_block", "schema-file", TABLE, {})
    m.finish(d, manifest.DONE, manifest.CREATED)
    f = m.begin("consumer_attach", "wdb2", TABLE, {})
    m.finish(f, manifest.FAILED, manifest.CREATED)
    assert [o.op for o in m.created()] == ["schema_block", "stp_register"]


def test_unknown_states_and_statuses_are_refused(att, state):
    m = fresh(att, state)
    with pytest.raises(ValueError, match="unknown attachment state"):
        m.set_state("done-ish")
    op = m.begin("stp_register", "stp1", TABLE, {})
    with pytest.raises(ValueError, match="unknown operation status"):
        m.finish(op, "complete")


def test_a_write_that_dies_leaves_the_previous_manifest_whole(att, state, monkeypatch):
    m = fresh(att, state)
    m.set_state("applied")
    before = m.path.read_text()

    def die(*args, **kwargs):
        raise OSError("power cut")

    monkeypatch.setattr(os, "replace", die)
    with pytest.raises(OSError, match="power cut"):
        m.begin("stp_register", "stp1", TABLE, {})
    assert m.path.read_text() == before, "a reader sees the old file or the new one, never half"


def test_a_manifest_this_uqs_cannot_read_is_refused_not_guessed(att, state):
    m = fresh(att, state)
    m.save()
    path = m.path
    data = json.loads(path.read_text())
    path.write_text(json.dumps({**data, "schema": 7}))
    with pytest.raises(AttachmentError, match="schema 7"):
        Manifest.load("pb-uat", state)
    path.write_text("{not json")
    with pytest.raises(AttachmentError, match="not a readable manifest"):
        Manifest.load("pb-uat", state)
    with pytest.raises(AttachmentError, match="nothing was applied"):
        Manifest.load("nope", state)


# ------------------------------------------------------------ schema file block


SPECS = (
    TableSpec(TABLE, (("time", "p"), ("sym", "s"), ("amount", "f"), ("note", " "))),
)  # fmt: skip


@pytest.fixture
def schema(tmp_path: Path) -> Path:
    path = tmp_path / "database.q"
    path.write_text(
        "quote:([] time:`timestamp$(); sym:`symbol$())\ntrade:([] time:`timestamp$())\n"
    )
    path.chmod(0o640)
    return path


def test_the_block_holds_the_definition_and_the_ownership_record(att):
    text = persist.render("pb-uat", "tok123", SPECS)
    lines = text.splitlines()
    assert lines[0] == "/ BEGIN uqs-attach pb-uat tok123" and lines[-1] == "/ END uqs-attach pb-uat"
    assert f"{TABLE}:([] time:`timestamp$(); sym:`symbol$(); amount:`float$(); note:())" in lines
    assert any(
        line.startswith(f".uqfattach.owned[`{TABLE}]:") and '\\"token\\":\\"tok123\\"' in line
        for line in lines
    ), "a restarted tickerplant comes back knowing the table is ours"
    assert not [line for line in lines if line.strip() in ("/", "\\")], (
        "no line opens a block comment"
    )


def test_writing_adds_the_block_and_leaves_everything_else_byte_for_byte(att, schema):
    before = schema.read_text()
    record = persist.write(att, "tok123", SPECS, str(schema))
    text = schema.read_text()
    assert text.startswith(before) and persist.outside(text, "pb-uat") == before
    assert record.outside_sha == persist._sha(before) and not record.added_newline
    assert schema.stat().st_mode & 0o777 == 0o640, "the file's permissions are kept"


def test_writing_twice_adds_one_block(att, schema):
    first = persist.write(att, "tok123", SPECS, str(schema))
    once = schema.read_text()
    assert persist.write(att, "tok123", SPECS, str(schema)) == first
    assert schema.read_text() == once


def test_a_block_someone_else_wrote_for_this_name_is_refused(att, schema):
    persist.write(att, "tok123", SPECS, str(schema))
    with pytest.raises(AttachmentError, match="already holds a different uqs-attach block"):
        persist.write(att, "OTHER", SPECS, str(schema))


def test_removal_restores_the_file_exactly_including_a_missing_final_newline(att, schema):
    schema.write_text("trade:([] time:`timestamp$())")  # no final newline
    original = schema.read_text()
    record = persist.write(att, "tok123", SPECS, str(schema))
    assert record.added_newline
    persist.remove(record, att, "tok123", SPECS)
    assert schema.read_text() == original


def test_removal_is_refused_when_the_block_was_edited(att, schema):
    record = persist.write(att, "tok123", SPECS, str(schema))
    schema.write_text(schema.read_text().replace("persisted", "hand edited"))
    assert persist.problems(record, att, "tok123", SPECS) == [f"the block in {schema} was edited"]
    with pytest.raises(AttachmentError, match="was edited"):
        persist.remove(record, att, "tok123", SPECS)


def test_removal_is_refused_when_the_file_changed_outside_the_block(att, schema):
    record = persist.write(att, "tok123", SPECS, str(schema))
    schema.write_text("extra:([] time:`timestamp$())\n" + schema.read_text())
    (found,) = persist.problems(record, att, "tok123", SPECS)
    assert "changed outside the block" in found
    with pytest.raises(AttachmentError):
        persist.remove(record, att, "tok123", SPECS)
    assert "BEGIN uqs-attach" in schema.read_text(), "a refused removal removes nothing"


def test_a_block_that_vanished_or_a_file_that_is_gone_is_reported(att, schema):
    record = persist.write(att, "tok123", SPECS, str(schema))
    schema.write_text(persist.outside(schema.read_text(), "pb-uat"))
    assert "is gone from" in persist.problems(record, att, "tok123", SPECS)[0]
    schema.unlink()
    assert "no longer exists" in persist.problems(record, att, "tok123", SPECS)[0]


def test_half_a_block_is_refused_not_guessed_at(att):
    with pytest.raises(AttachmentError, match="BEGIN marker for pb-uat and no END"):
        persist.locate("x\n/ BEGIN uqs-attach pb-uat tok\nstuff\n", "pb-uat")


def test_a_relative_schema_path_is_refused(att):
    with pytest.raises(AttachmentError, match="not an absolute path"):
        persist.write(att, "tok", SPECS, "database.q")


def test_two_attachments_keep_separate_blocks_in_one_file(tmp_path, schema):
    bundle = make_bundle(tmp_path / "b")
    a = service.create("pb-a", "pb_general_ledger", bundle, 6000, ROOT)
    b = service.create("pb-b", "pb_general_ledger", bundle, 6000, ROOT)
    ra = persist.write(a, "ta", SPECS, str(schema))
    rb = persist.write(b, "tb", SPECS, str(schema))
    persist.remove(ra, a, "ta", SPECS)  # b's block moved the text, a's hash is of what is outside a
    assert "uqs-attach pb-b" in schema.read_text() and "uqs-attach pb-a" not in schema.read_text()
    assert rb.block_sha


# ------------------------------------------- byte for byte, in any order (#1438, #1439)

#: The stack's own text in the shapes a schema file comes in.
ORIGINALS = {
    "lf": b"trade:([] time:`timestamp$(); sym:`symbol$())\nquote:([] time:`timestamp$())\n",
    "crlf": b"trade:([] time:`timestamp$(); sym:`symbol$())\r\nquote:([] time:`timestamp$())\r\n",
    "lf-unterminated": b"trade:([] time:`timestamp$())\nquote:([] time:`timestamp$())",
    "crlf-unterminated": b"trade:([] time:`timestamp$())\r\nquote:([] time:`timestamp$())",
    "not-utf8": b"/ caf\xe9 \xff\ntrade:([] time:`timestamp$())",
}
OTHER = (TableSpec("pb_other", (("time", "p"), ("x", "j"))),)


def two(tmp_path: Path) -> tuple[config.Attachment, config.Attachment]:
    bundle = make_bundle(tmp_path / "two")
    return (
        service.create("pb-a", "pb_general_ledger", bundle, 6000, ROOT),
        service.create("pb-b", "pb_general_ledger", bundle, 6000, ROOT),
    )


def loads(q_binary, schema: Path, tmp_path: Path, *tables: str) -> None:
    """The file loads in q - a restarting tickerplant can start - with the stack's
    own tables and `tables` defined."""
    from attach_support import q_load

    got = q_load(q_binary, schema, tmp_path)
    assert got["loaded"], f"q could not load the schema file: {got['error']!r}"
    assert {"trade", *tables} <= set(got["tables"]), got["tables"]


@pytest.mark.parametrize("shape", sorted(ORIGINALS))
def test_write_then_remove_gives_back_the_file_byte_for_byte(att, tmp_path, shape, q_binary):
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS[shape])
    record = persist.write(att, "tok123", SPECS, str(schema))
    after = schema.read_bytes()
    assert after.startswith(ORIGINALS[shape]), (
        "no byte outside the block changed, line ends included"
    )
    assert persist.problems(record, att, "tok123", SPECS) == []
    loads(q_binary, schema, tmp_path, TABLE)
    persist.remove(record, att, "tok123", SPECS)
    assert schema.read_bytes() == ORIGINALS[shape]


def test_a_line_ending_changed_outside_the_block_is_seen(att, tmp_path):
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS["crlf"])
    record = persist.write(att, "tok123", SPECS, str(schema))
    schema.write_bytes(schema.read_bytes().replace(b"\r\n", b"\n", 1))
    (found,) = persist.problems(record, att, "tok123", SPECS)
    assert "changed outside the block" in found


@pytest.mark.parametrize("shape", sorted(ORIGINALS))
@pytest.mark.parametrize("first_out", ["a", "b"])
def test_two_attachments_added_and_removed_in_either_order_give_back_the_file(
    tmp_path, shape, first_out, q_binary
):
    """A then B into one file; either leaves first. Nothing that is not the
    leaving block moves, the other's block stays removable, and the file loads in
    q after every step (#1438: the newline added for A must not go with A while
    B's block still follows it)."""
    a, b = two(tmp_path)
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS[shape])
    ra = persist.write(a, "ta", SPECS, str(schema))
    rb = persist.write(b, "tb", OTHER, str(schema))
    loads(q_binary, schema, tmp_path, TABLE, "pb_other")
    order = [(a, ra, "ta", SPECS), (b, rb, "tb", OTHER)]
    if first_out == "b":
        order.reverse()
    (who, rec, tok, specs), (last, last_rec, last_tok, last_specs) = order
    persist.remove(rec, who, tok, specs)
    assert persist.problems(last_rec, last, last_tok, last_specs) == []
    loads(q_binary, schema, tmp_path, last_specs[0].name)
    persist.remove(last_rec, last, last_tok, last_specs)
    assert schema.read_bytes() == ORIGINALS[shape]


def test_a_block_written_after_the_one_that_added_the_newline_left_still_restores_it(tmp_path):
    a, b = two(tmp_path)
    c = service.create("pb-c", "pb_general_ledger", make_bundle(tmp_path / "c"), 6000, ROOT)
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS["lf-unterminated"])
    ra = persist.write(a, "ta", SPECS, str(schema))
    rb = persist.write(b, "tb", OTHER, str(schema))
    persist.remove(ra, a, "ta", SPECS)
    rc = persist.write(c, "tc", SPECS, str(schema))
    persist.remove(rb, b, "tb", OTHER)
    persist.remove(rc, c, "tc", SPECS)
    assert schema.read_bytes() == ORIGINALS["lf-unterminated"]


def test_a_retried_write_reports_what_the_first_did_and_rollback_restores_the_file(att, tmp_path):
    """apply retried after the block went in but before the manifest said so."""
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS["lf-unterminated"])
    first = persist.write(att, "tok123", SPECS, str(schema))
    retried = persist.write(att, "tok123", SPECS, str(schema))
    assert retried == first and retried.added_newline
    persist.remove(retried, att, "tok123", SPECS)
    assert schema.read_bytes() == ORIGINALS["lf-unterminated"]


# ------------------------------------------------- shared ownership on restart (#1446)


@pytest.mark.parametrize("creator_first", [True, False])
def test_the_uses_block_puts_a_user_back_on_the_record_whichever_block_q_reads_first(
    tmp_path, creator_first, q_binary
):
    from attach_support import q_load

    a, b = two(tmp_path)
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS["lf"])
    writes = [
        lambda: persist.write(a, "ta", SPECS, str(schema)),
        lambda: persist.write_uses(b, "tb", (TABLE,), str(schema)),
    ]
    for w in writes if creator_first else writes[::-1]:
        w()
    got = q_load(q_binary, schema, tmp_path)
    assert got["loaded"], got["error"]
    assert got["owned"][TABLE]["attachment"] == "pb-a"
    assert got["owned"][TABLE]["users"] == ["pb-b"], "the restarted plant knows pb-b uses it"


def test_a_uses_block_without_the_creators_block_loads_and_claims_nothing(tmp_path, q_binary):
    from attach_support import q_load

    _, b = two(tmp_path)
    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS["lf"])
    record = persist.write_uses(b, "tb", (TABLE,), str(schema))
    got = q_load(q_binary, schema, tmp_path)
    assert got["loaded"] and got["owned"] == {}
    persist.remove(record, b, "tb", ())
    assert schema.read_bytes() == ORIGINALS["lf"]


def test_a_created_block_alone_records_no_users(att, tmp_path, q_binary):
    from attach_support import q_load

    schema = tmp_path / "database.q"
    schema.write_bytes(ORIGINALS["lf"])
    persist.write(att, "tok123", SPECS, str(schema))
    got = q_load(q_binary, schema, tmp_path)
    assert got["loaded"] and "users" not in got["owned"][TABLE]


# --------------------------------------------------------------------- the gate


def saved(att: config.Attachment, state: Path, status: str | None) -> None:
    config.save(att, state)
    if status is not None:
        m = fresh(att, state)
        m.state = status
        m.save()


def test_the_start_path_refuses_a_publisher_that_is_not_verified(att, state, monkeypatch):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    paths = default_paths()
    saved(att, state, None)
    with pytest.raises(UqsError, match="which is planned: it may start once"):
        attach_state.refuse_unverified_start(paths, "pb_ledger1", 6000)
    for status in ("applying", "applied", "partial", "detaching", "detached", "rolled_back"):
        saved(att, state, status)
        with pytest.raises(UqsError, match=f"which is {status}"):
            attach_state.refuse_unverified_start(paths, "pb_ledger1", 6000)
    saved(att, state, "verified")
    attach_state.refuse_unverified_start(paths, "pb_ledger1", 6000)


def test_the_publisher_is_never_started_under_the_runtimes_default_port(att, state, monkeypatch):
    """The incident: no --port, so uqf's own base port, so discovery at +1 of that."""
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    saved(att, state, "verified")
    paths = default_paths()
    default_port = paths.runtime_declaration.base_port
    with pytest.raises(UqsError) as caught:
        attach_state.refuse_unverified_start(paths, "pb_ledger1", None)
    text = str(caught.value)
    assert f"discovery at {default_port + 1}, not 6001" in text
    assert "uqs job attach start pb-uat" in text and "--port 6000" in text
    with pytest.raises(UqsError, match="--port 6000"):
        attach_state.refuse_unverified_start(paths, "pb_ledger1", 6100)


def test_other_processes_and_start_all_are_not_gated(att, state, monkeypatch):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    saved(att, state, "applied")
    paths = default_paths()
    attach_state.refuse_unverified_start(paths, "rdb1 wdb1", None)
    attach_state.refuse_unverified_start(paths, "all", None)
    attach_state.refuse_unverified_start(paths, "", None)


def test_an_unreadable_attachment_does_not_stop_an_unrelated_start(att, state, monkeypatch):
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    (state / "broken").mkdir(parents=True)
    (state / "broken" / config.CONFIG_FILE).write_text("{nope")
    saved(att, state, "verified")
    assert [p.attachment for p in attach_state.publishers(state)] == ["pb-uat"]


def test_every_start_goes_through_the_gate(att, state, monkeypatch):
    """The gate is in refuse_start, below `uqs start`, the MCP server and the
    browser's start route - not in one front end."""
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(state))
    saved(att, state, "applied")
    paths = default_paths()
    monkeypatch.setattr(start_policy.stack_procs, "assert_known_procnames", lambda *a: None)
    monkeypatch.setattr(start_policy.qtree, "refuse_unloadable", lambda *a: None)
    monkeypatch.setattr(start_policy.occupancy, "refuse_if_taken", lambda *a: None)
    for verb in start_policy.STARTING_VERBS:
        with pytest.raises(UqsError, match="publisher of attachment pb-uat"):
            start_policy.refuse_start(paths, [verb, "pb_ledger1"], 6000)
    start_policy.refuse_start(paths, ["stop", "pb_ledger1"], None)
    start_policy.refuse_start(paths, ["start", "rdb1"], None)


def test_the_state_directory_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.delenv(config.ATTACH_DIR_ENV, raising=False)
    monkeypatch.setenv("UQS_DATA_ROOT", str(tmp_path))
    assert attach_state.state_root() == tmp_path / "attachments"
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(tmp_path / "elsewhere"))
    assert attach_state.state_root() == tmp_path / "elsewhere"
    monkeypatch.delenv("UQS_DATA_ROOT")
    monkeypatch.delenv(config.ATTACH_DIR_ENV)
    assert attach_state.state_root() == ROOT / "output" / "attachments"
