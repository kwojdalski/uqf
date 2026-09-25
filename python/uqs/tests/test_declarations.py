"""Tests for the process registry being DERIVED from q declarations.

Two properties carry the change. A job file is the whole registration - one
dropped into the streaming or workers directory becomes a process with no
other edit. And a process's port never moves when others are added around
it, which is what the port lock is for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs.model import registry
from uqs.model.declarations import (
    dictionary_fields,
    read_declarations,
    read_file_text,
    split_top_level,
)
from uqs.model.pipeline import PipelineKind
from uqs.model.registry import PIPELINES
from uqs.paths import PROCESS_PORTS_FILE, STREAM_DIR, TABLES_FILE, WORKER_DIR, UqsError

# ------------------------------------------------------------------ parsing


def test_a_semicolon_inside_a_string_or_bracket_does_not_split():
    """A note is prose, and prose has semicolons in it - cross1's does."""
    assert split_top_level('`a; "x; y"; f[1;2]; (3;4)') == ["`a", '"x; y"', "f[1;2]", "(3;4)"]


def test_a_newline_between_bang_and_paren_is_read():
    """The worker files put one there; a `!(` split read them as nothing."""
    fields = dictionary_fields("`source`dataset!\n    (`s;`d)")
    assert fields == {"source": "`s", "dataset": "`d"}


def _one(source: str):
    (declaration,) = read_file_text(source, Path("x.q"))
    return declaration


def test_a_feed_is_a_job_that_subscribes_to_nothing():
    d = _one(
        ".qetl.job.stream.define[`f;`procname`subscribe_to`publishes`period`on_timer!(\n"
        "    `f1;`symbol$();enlist `t;0D00:00:01;.qpipe.job.f.on_timer)];"
    )
    assert (d.procname, d.kind, d.subscribe_to, d.publishes) == (
        "f1",
        PipelineKind.FEED,
        (),
        ("t",),
    )
    assert not d.start_with_all, "absent start_with_all means on demand"


def test_autostart_and_note_are_read_and_the_note_unescaped():
    d = _one(
        ".qetl.job.stream.define[`j;`procname`subscribe_to`publishes`on_batch`start_with_all`note!(\n"
        '    `j1;enlist `a;`symbol$();.qpipe.job.j.on_batch;1b;"says \\"hi\\"; twice")];'
    )
    assert (d.kind, d.start_with_all, d.note) == (PipelineKind.ETL, True, 'says "hi"; twice')


def test_an_autostart_that_is_not_a_boolean_is_refused():
    with pytest.raises(UqsError, match="start_with_all must be 1b or 0b"):
        _one(
            ".qetl.job.stream.define[`j;`procname`subscribe_to`publishes`start_with_all!(`j1;`a;`b;`yes)];"
        )


def test_a_worker_with_no_procname_runs_as_its_name_and_1():
    """The same default q applies, so the two sides cannot disagree."""
    d = _one(".qetl.job.bounded.define[`w;`source`dataset`width`transform!(`s;`d;1D;`x)];")
    assert (d.procname, d.worker, d.kind) == ("w1", "w", PipelineKind.BACKFILL)


def test_a_worker_may_not_ask_to_start_with_the_stack():
    with pytest.raises(UqsError, match="never starts with the stack"):
        _one(
            ".qetl.job.bounded.define[`w;`source`dataset`width`transform`start_with_all!(`s;`d;1D;`x;1b)];"
        )


def test_a_commented_out_declaration_is_not_a_process():
    assert (
        read_file_text("/ .qetl.job.bounded.define[`w;`source!(enlist `s)];\n", Path("x.q")) == []
    )


# ------------------------------------------------------------------- ports


def test_a_locked_offset_is_kept_and_a_new_one_goes_after_every_locked_one():
    """After RETIRED ones too: a stopped process's offset is never reused, so a
    stale config pointing at it cannot reach a different process."""
    locked = {"a1": 24, "retired1": 30}
    assert registry.allocate_offsets(["a1", "new2", "new1"], locked) == {
        "a1": 24,
        "new1": 31,
        "new2": 32,
    }


def test_the_lock_keeps_every_row_it_had():
    text = registry.render_port_lock({"retired1": 30, "a1": 24}, {"a1": 24, "b1": 31})
    rows = [line for line in text.splitlines() if not line.startswith("#")]
    assert rows == ["procname,offset", "a1,24", "retired1,30", "b1,31"]


def test_the_committed_lock_covers_every_process():
    """What generate_operational_docs.py --check enforces in CI, held here too:
    a process the lock lacks has an offset that moves as others are added."""
    locked = registry.read_port_lock(Path(__file__).resolve().parents[3])
    assert {p.procname for p in PIPELINES} <= set(locked)


# --------------------------------------------------------- plug and play


def test_every_declaration_in_the_tree_is_a_process():
    root = Path(__file__).resolve().parents[3]
    declared = {d.procname for d in read_declarations(root)}
    non_job = {p.procname for p in registry.NON_JOB_PIPELINES}
    assert {p.procname for p in PIPELINES} == declared | non_job


def _tree(tmp_path: Path) -> Path:
    (tmp_path / STREAM_DIR).mkdir(parents=True)
    (tmp_path / WORKER_DIR).mkdir(parents=True)
    (tmp_path / TABLES_FILE).parent.mkdir(parents=True)
    (tmp_path / TABLES_FILE).write_text("ticks:([]time:`timestamp$(); px:`float$())\n")
    (tmp_path / PROCESS_PORTS_FILE).write_text("procname,offset\ntap1,28\n")
    return tmp_path


def test_a_job_file_is_the_whole_registration(tmp_path):
    """The point of the change: drop a job and a worker into the tree and both
    are processes, with ports, and nothing else was edited."""
    root = _tree(tmp_path)
    (root / STREAM_DIR / "tick.q").write_text(
        ".qetl.job.stream.define[`tick;`procname`subscribe_to`publishes`period`on_timer`start_with_all!(\n"
        "    `tick1;`symbol$();enlist `ticks;0D00:00:01;.qpipe.job.tick.on_timer;1b)];\n"
    )
    (root / WORKER_DIR / "w.q").write_text(
        ".qetl.job.bounded.define[`w;`source`dataset`width`transform!(`s;`d;1D;`x)];\n"
    )
    built = {p.procname: p for p in registry.build_pipelines(root)}
    assert set(built) == {"tap1", "tick1", "w1"}
    assert (built["tick1"].table, built["tick1"].startwithall) == ("ticks", "1")
    assert (built["w1"].worker, built["w1"].startwithall) == ("w", "0")
    assert (built["tap1"].offset, built["tick1"].offset, built["w1"].offset) == (28, 29, 30)


def test_two_declarations_claiming_one_process_are_refused(tmp_path):
    root = _tree(tmp_path)
    for name in ("a", "b"):
        (root / STREAM_DIR / f"{name}.q").write_text(
            f".qetl.job.stream.define[`{name};`procname`subscribe_to`publishes`on_batch!("
            f"`same1;enlist `t;`symbol$();.qpipe.job.{name}.on_batch)];\n"
        )
    with pytest.raises(ValueError, match="same1"):
        registry.build_pipelines(root)


def test_a_single_source_normalizer_reads_its_one_source():
    """`(enlist `a)!enlist `xf` is how one source is written; the parentheses
    used to reach `symbols`, which returned `(enlist` and `a)`."""
    (d,) = read_file_text(
        ".qetl.job.stream.normalize[`n;`procname`output`input!"
        "(`n1;.qpipe.job.n.n;(enlist `quote)!enlist `xf)];",
        Path("n.q"),
    )
    assert d.subscribe_to == ("quote",)
