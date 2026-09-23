"""Tests for `uqf-stack new-job` (scaffold/jobs.py, scaffold/templates.py).

WHAT IS WORTH TESTING HERE. Not that the templates produce a particular
string - that would pin the prose and break on every wording change. What
matters is that what they produce is still READABLE BY THE TREE:

  * the generated `.qstream.register` block parses with the same regex
    `pipeline_edges` reads real jobs with, so a template that drifts out of
    what the tree can parse fails the build rather than rotting quietly;
  * the generated table definition parses with the same regex `model/schemas.py`
    reads `uqf_stack_tables.q` with;
  * the plan refuses rather than half-writing.

WHAT THESE CANNOT CATCH. Whether the generated q LOADS. That needs a q
process and a whole tree, and both templates have already been caught
failing it once: a fixture that threw stopped src/etl/init.q loading, and an
empty one was refused by `.qxf.define`, which requires at least one example
with rows. The comments in scaffold/templates.py record both.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqf_stack.model.pipeline_edges import _REGISTER_RE, _register_fields, _symbol_field
from uqf_stack.model.schemas import _DEFINITION
from uqf_stack.paths import UqfStackError
from uqf_stack.scaffold import jobs, write
from uqf_stack.scaffold.plan import WriteMode


def _body(plan: jobs.ScaffoldPlan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


# ------------------------------------------------------- the parse contract


def test_a_scaffolded_job_declares_edges_the_tree_can_read():
    """The anti-rot test. `_declared_stream_edges` finds a job's procname,
    subscribes and publishes by regex; a template that stopped matching it
    would generate a job the registry could never resolve."""
    plan = jobs.streaming_job("markout2", ["trades", "quote"], "my_metric", "value:float")
    match = _REGISTER_RE.search(_body(plan, "markout2.q"))
    assert match, "the generated register call does not parse"
    fields = _register_fields(match.group(2))
    assert _symbol_field(fields["procname"]) == ("markout21",)
    assert _symbol_field(fields["subscribes"]) == ("trades", "quote")
    assert _symbol_field(fields["publishes"]) == ("my_metric",)


def test_a_scaffolded_feed_declares_no_subscription():
    """`symbol$()` and a one-table list are the two cases the registry most
    needs to tell apart, so the empty one is spelled explicitly."""
    plan = jobs.streaming_job("tickfeed", [], "ticks", "value:float")
    match = _REGISTER_RE.search(_body(plan, "tickfeed.q"))
    assert match
    fields = _register_fields(match.group(2))
    assert _symbol_field(fields["subscribes"]) == ()
    assert "timer_period" in fields, "a feed publishes on a timer, not on a batch"


def test_a_scaffolded_table_parses_as_a_definition():
    """model/schemas.py reads uqf_stack_tables.q with this regex, and Pipeline.schema
    resolves through it - a definition it cannot see is a table the plant
    never defines."""
    plan = jobs.streaming_job("j", [], "my_metric", "sym:symbol, value:float")
    body = _body(plan, "uqf_stack_tables.q")
    found = _DEFINITION.search(body)
    assert found and found.group(1) == "my_metric"


# ------------------------------------------------------------ the columns


def test_time_is_added_when_the_caller_forgets_it():
    """Every plant table has one, `.u.upd` stamps it, and a table without it
    is refused later by a publish path that assumes it."""
    assert jobs.parse_columns("value:float")[0][0] == "time"


def test_sym_keeps_its_grouped_attribute():
    """Every table in uqf_stack_tables.q groups sym. A missing `g#` is a
    performance cliff with no error attached."""
    cols = dict(jobs.parse_columns("sym:symbol"))
    assert cols["sym"] == "`g#`symbol$()"


@pytest.mark.parametrize("spec", ["value", "value:nosuchtype", ""])
def test_a_malformed_column_spec_is_refused(spec):
    with pytest.raises(UqfStackError):
        jobs.parse_columns(spec)


def test_publishing_without_columns_is_refused():
    """The plant must define a table before anything writes to it: `.u.upd`
    onto an undefined table discards the rows in silence (#288)."""
    with pytest.raises(UqfStackError, match="columns"):
        jobs.streaming_job("j", [], "my_metric", None)


def test_columns_without_publishing_is_refused():
    with pytest.raises(UqfStackError, match="no --publishes"):
        jobs.streaming_job("j", ["trades"], None, "value:float")


# --------------------------------------------------------- the bounded path


def test_a_scaffolded_worker_declares_its_source_and_dataset():
    plan = jobs.bounded_worker("fx_rates", "fx_rates", "sym:symbol, mid:float")
    worker = _body(plan, "fx_rates_backfill.q")
    assert ".qbw.define[`fx_rates_backfill;" in worker
    assert "`fx_rates;`fx_rates;1D;" in worker


def test_a_scaffolded_fixture_carries_a_row():
    """Neither a throw nor empty, and both were caught the hard way: a
    throwing fixture stops src/etl/init.q loading, because the worker's
    .qxf.passthrough reads it AT LOAD TIME - and an empty one is refused by
    .qxf.define, which requires at least one example with rows."""
    plan = jobs.bounded_worker("fx_rates", "fx_rates", "mid:float")
    source = _body(plan, "sources/fx_rates.q")
    assert "enlist" in source.split("fixture:")[1], "the fixture must carry a row"
    assert "not implemented" not in source.split("fixture:")[1].split("register")[0]


def test_a_scaffolded_worker_names_the_process_that_runs_it():
    """Without `worker=`, the link lives only in UQF_BACKFILL_WORKER at
    runtime and a declared worker with no process is invisible (#283)."""
    plan = jobs.bounded_worker("fx_rates", "fx_rates", "mid:float")
    assert 'worker="fx_rates_backfill"' in _body(plan, "model/registry.py")


# ------------------------------------------------------------- refusing


def test_a_bad_name_is_refused_before_anything_is_planned():
    for name in ("Markout", "2fast", "with-dash", ""):
        with pytest.raises(UqfStackError):
            jobs.streaming_job(name, [], None, None)


def test_a_plan_refuses_wholesale_rather_than_half_writing(tmp_path: Path):
    """A scaffold that created three files and then refused the fourth would
    leave a tree that neither loads nor reverts cleanly - and the half that
    landed registers itself on load."""
    plan = jobs.streaming_job("j", ["trades"], None, None)
    (tmp_path / "src" / "etl" / "streaming").mkdir(parents=True)
    (tmp_path / "src" / "etl" / "streaming" / "j.q").write_text("already here")
    with pytest.raises(UqfStackError, match="already exists"):
        write.apply_plan(plan, tmp_path)
    assert (tmp_path / "src" / "etl" / "streaming" / "j.q").read_text() == "already here"
    assert not (tmp_path / "tests").exists(), "nothing else was written"


def test_appending_to_a_missing_file_is_refused(tmp_path: Path):
    plan = jobs.streaming_job("j", ["trades"], None, None)
    with pytest.raises(UqfStackError, match="nothing to append to"):
        write.apply_plan(plan, tmp_path)


def test_a_registry_that_does_not_end_in_the_tuple_is_refused(tmp_path: Path):
    """The entry goes INSIDE the PIPELINES tuple. If the file no longer ends
    with its closing paren, the scaffold cannot tell where - and appending at
    the end would be valid Python that registers nothing."""
    action = next(
        a
        for a in jobs.streaming_job("j", [], None, None).actions
        if str(a.path).endswith("model/registry.py")
    )
    with pytest.raises(UqfStackError, match="closing paren"):
        write._appended("PIPELINES = (\n)\nsomething_else = 1\n", action)


# ------------------------------------------- registering the test namespace


def test_a_scaffolded_job_registers_its_test_namespace():
    """The bug this closes (#350): the runner globs test FILES but keeps their
    namespaces by hand, so writing the file was not enough. The scaffolded test
    loaded and none of its tests ran - the one red the scaffold exists to leave
    was invisible, and the reader saw three unrelated ones instead."""
    plan = jobs.streaming_job("markout2", ["trades"], None, None)
    paths = [str(a.path) for a in plan.actions]
    assert str(jobs.RUN_TESTS_FILE) in paths


def test_the_registered_namespace_is_the_one_the_test_file_declares():
    """The drift this guards against is why `test_namespace` exists as one
    function: the namespace is needed by the stub's own `\\d` line and by the
    nsList entry, and a mismatch means the runner registers a namespace nothing
    declares while the real one never runs - the original bug, wearing a
    different hat."""
    for plan in (
        jobs.streaming_job("markout2", ["trades"], None, None),
        jobs.bounded_worker("fx_rates", "fx_rates", "mid:float"),
    ):
        entry = _body(plan, "run_tests.q").strip()
        # The TEST file, not the job file - both end in .q, and the job file
        # declares its own `.qsub.<name>` namespace. Nor the q table list,
        # which the plan appends to and whose name also starts `test_`.
        test_body = next(
            a.body
            for a in plan.actions
            if a.path.name.startswith("test_") and a.path != jobs.STACK_TABLES_TEST
        )
        declared = [
            line.split()[1]
            for line in test_body.splitlines()
            if line.startswith("\\d .") and line.strip() != "\\d ."
        ]
        assert entry == f"`{declared[0]}", (entry, declared)


def test_a_bounded_worker_registers_its_own_namespace():
    """A backfill and a streaming job of the same base name must not claim one
    namespace, which is what the `bf` suffix is for."""
    assert jobs.test_namespace("fx_rates") == "fx_ratestest"
    assert jobs.test_namespace("fx_rates", bounded=True) == "fx_ratesbftest"


def test_the_namespace_goes_inside_the_symbol_list():
    """Before the terminating `;`, not after it - appended at the end of the
    file it would be a separate statement that registers nothing."""
    before = "\\l x.q\nnsList:`.atest`.btest;\nres:1\n"
    after = write._with_nslist_entry(before, "`.ctest")
    assert "nsList:`.atest`.btest`.ctest;" in after
    assert after.endswith("res:1\n"), "the rest of the file is untouched"


@pytest.mark.parametrize(
    "content",
    [
        "no list here\n",
        "nsList:`.atest;\nnsList:`.btest;\n",
        "nsList:`.atest\n",
    ],
)
def test_a_run_tests_file_that_does_not_look_right_is_refused(content):
    """Refuse rather than guess. Appending to the wrong place produces a file
    that loads, runs exactly the suites it ran before, and reports nothing
    missing - which is the failure mode being fixed, reintroduced silently."""
    with pytest.raises(UqfStackError):
        write._with_nslist_entry(content, "`.ctest")


def test_a_namespace_already_listed_is_refused():
    with pytest.raises(UqfStackError, match="already in"):
        write._with_nslist_entry("nsList:`.atest`.btest;\n", "`.btest")


# ------------------------------------------ registering the owned table


def test_a_job_that_owns_a_table_adds_it_to_the_q_table_list():
    """test_stack_tables.q's `expected` is a gate every new table passes
    through. Before the scaffold appended to it, every job that owned a table
    left a red suite that was not about the job."""
    for plan, table in (
        (jobs.bounded_worker("fx_rates", "fx_rates", "mid:float"), "fx_rates"),
        (jobs.streaming_job("markout2", ["trades"], "my_metric", "value:float"), "my_metric"),
    ):
        assert _body(plan, "test_stack_tables.q") == f"`{table}"


def test_a_job_that_owns_no_table_leaves_the_q_table_list_alone():
    plan = jobs.streaming_job("markout2", ["trades"], None, None)
    assert jobs.STACK_TABLES_TEST not in [a.path for a in plan.actions]


def test_the_table_goes_at_the_end_of_the_expected_list():
    before = "\\d .tabletest\nexpected:`quotes`trades\nnext:1\n"
    after = write._with_expected_table(before, "`fx_rates")
    assert "expected:`quotes`trades`fx_rates\n" in after
    assert after.endswith("next:1\n"), "the rest of the file is untouched"


@pytest.mark.parametrize(
    "content",
    ["no list here\n", "expected:`a\nexpected:`b\n", "expected:`quotes`fx_rates\n"],
)
def test_a_table_list_that_does_not_look_right_is_refused(content):
    """No list, two lists, or the table already listed - which means the q
    file already defines it, and the scaffold would be redefining it."""
    with pytest.raises(UqfStackError):
        write._with_expected_table(content, "`fx_rates")


def test_a_table_whose_name_extends_a_listed_one_is_not_a_duplicate():
    """Matched as a whole symbol, not a substring: `fx_rates_old` being listed
    says nothing about `fx_rates`."""
    after = write._with_expected_table("expected:`fx_rates_old\n", "`fx_rates")
    assert after == "expected:`fx_rates_old`fx_rates\n"


# ------------------------------------------- a worker on what already exists


def test_a_worker_on_an_existing_source_does_not_rewrite_it():
    """The second worker over a source someone already wrote - another width,
    another target - used to be refused: the plan always CREATED the source
    file, and apply_plan refuses a file that exists."""
    plan = jobs.bounded_worker(
        "fx_rates_1h", "fx_rates", None, source="fx_rates", reuse_source=True, define_table=False
    )
    paths = [str(a.path) for a in plan.actions]
    assert not any(p.startswith(str(jobs.SOURCE_DIR)) for p in paths)
    assert str(jobs.WORKER_DIR / "fx_rates_1h_backfill.q") in paths


def test_an_existing_table_is_not_defined_twice():
    plan = jobs.bounded_worker("fx_rates_1h", "fx_rates", "mid:float", define_table=False)
    paths = [a.path for a in plan.actions]
    assert jobs.TABLES_FILE not in paths
    assert jobs.STACK_TABLES_TEST not in paths
    assert jobs.SOURCE_DIR / "fx_rates_1h.q" in paths, "a new source still needs its file"


def test_columns_are_required_when_something_new_is_written():
    with pytest.raises(UqfStackError, match="needs --columns"):
        jobs.bounded_worker("fx_rates", "fx_rates", None)
    with pytest.raises(UqfStackError, match="needs --columns"):
        jobs.bounded_worker("fx_rates", "fx_rates", None, reuse_source=True)


def test_columns_with_nothing_to_shape_are_refused_not_ignored():
    with pytest.raises(UqfStackError, match="nothing to shape"):
        jobs.bounded_worker(
            "fx_rates", "fx_rates", "mid:float", reuse_source=True, define_table=False
        )


def test_every_plan_names_the_readme_line_it_cannot_write():
    """docs/integrations/torq/README.md is authored prose and pytest fails until
    it names the process - so the plan says so, rather than leaving that red to
    be discovered."""
    for plan, proc in (
        (jobs.bounded_worker("fx_rates", "fx_rates", "mid:float"), "fx_rates_backfill1"),
        (jobs.streaming_job("markout2", ["trades"], None, None), "markout21"),
    ):
        assert any(proc in n and "README.md" in n for n in plan.notes), plan.notes


# --------------------------------------------------------------- write mode


def test_every_action_declares_a_known_write_mode():
    """`mode` was a bare string, and the branching read `if create ... else
    append`. A typo therefore did not raise: it fell past the guard that checks
    the target exists - which tests for "append" exactly - and reached
    `read_text()` on a file that might not be there."""
    for plan in (
        jobs.streaming_job("markout2", ["trades"], "my_metric", "value:float"),
        jobs.bounded_worker("fx_rates", "fx_rates", "mid:float"),
    ):
        assert all(a.mode in tuple(WriteMode) for a in plan.actions)


def test_the_mode_still_renders_as_its_own_word():
    """It reaches `describe()`, which --dry-run prints, so a bare Enum would
    turn the plan into `WriteMode.APPEND`."""
    action = jobs.FileAction(Path("x.q"), "body", mode=WriteMode.APPEND)
    assert action.describe().startswith("append to x.q")
    assert jobs.FileAction(Path("x.q"), "body").describe().startswith("create x.q")


def test_a_one_line_body_is_described_in_the_singular():
    """The nsList entry is one symbol, so --dry-run used to print "1 lines"."""
    assert "(1 line)" in jobs.FileAction(Path("x"), "`.atest").describe()
