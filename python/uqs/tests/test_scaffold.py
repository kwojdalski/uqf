"""Tests for `uqs new-job` (scaffold/jobs.py, scaffold/templates.py).

WHAT IS WORTH TESTING HERE. Not that the templates produce a particular
string - that would pin the prose and break on every wording change. What
matters is that what they produce is still READABLE BY THE TREE:

  * the generated `.qstream.register` block parses with the same regex
    `pipeline_edges` reads real jobs with, so a template that drifts out of
    what the tree can parse fails the build rather than rotting quietly;
  * the generated table definition parses with the same regex `model/schemas.py`
    reads `uqs_tables.q` with;
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

from uqs.model.declarations import Declaration, read_file_text
from uqs.model.pipeline import PipelineKind
from uqs.model.schemas import _DEFINITION
from uqs.paths import UqsError
from uqs.scaffold import jobs, write
from uqs.scaffold.plan import WriteMode


def _body(plan: jobs.ScaffoldPlan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


# ------------------------------------------------------- the parse contract


def _declared(plan: jobs.ScaffoldPlan, suffix: str) -> list[Declaration]:
    """What the registry would read from a scaffolded file - the same reader
    the process registry is built from, so a template that stopped matching
    it would generate a job no process runs."""
    return read_file_text(_body(plan, suffix), Path(suffix))


def test_a_scaffolded_job_declares_edges_the_tree_can_read():
    """The anti-rot test: the process registry is built by reading these
    declarations, so the generated one has to read back as what was asked."""
    plan = jobs.streaming_job("markout2", ["trades", "quote"], "my_metric", "value:float")
    (d,) = _declared(plan, "markout2.q")
    assert (d.procname, d.subscribes, d.publishes) == (
        "markout21",
        ("trades", "quote"),
        ("my_metric",),
    )
    assert d.kind is PipelineKind.ETL
    assert not d.autostart, "a scaffolded job is on demand until someone decides otherwise"


def test_a_scaffolded_feed_declares_no_subscription():
    """`symbol$()` and a one-table list are the two cases the registry most
    needs to tell apart, so the empty one is spelled explicitly."""
    plan = jobs.streaming_job("tickfeed", [], "ticks", "value:float")
    (d,) = _declared(plan, "tickfeed.q")
    assert d.subscribes == ()
    assert d.kind is PipelineKind.FEED, "a job that subscribes to nothing is a feed"


def test_a_scaffolded_worker_declares_its_process():
    """A worker's process is read from its own `.qbw.define`, so the
    scaffolded one must name the process the plan says it runs."""
    plan = jobs.bounded_worker("fx_rates", "fx_rates", "mid:float")
    (d,) = _declared(plan, "fx_rates_backfill.q")
    assert (d.procname, d.worker, d.kind) == (
        "fx_rates_backfill1",
        "fx_rates_backfill",
        PipelineKind.BACKFILL,
    )


def test_a_scaffolded_table_parses_as_a_definition():
    """model/schemas.py reads uqs_tables.q with this regex, and Pipeline.schema
    resolves through it - a definition it cannot see is a table the plant
    never defines."""
    plan = jobs.streaming_job("j", [], "my_metric", "sym:symbol, value:float")
    body = _body(plan, "uqs_tables.q")
    found = _DEFINITION.search(body)
    assert found and found.group(1) == "my_metric"


# ------------------------------------------------------------ the columns


def test_time_is_added_when_the_caller_forgets_it():
    """Every plant table has one, `.u.upd` stamps it, and a table without it
    is refused later by a publish path that assumes it."""
    assert jobs.parse_columns("value:float")[0][0] == "time"


def test_sym_keeps_its_grouped_attribute():
    """Every table in uqs_tables.q groups sym. A missing `g#` is a
    performance cliff with no error attached."""
    cols = dict(jobs.parse_columns("sym:symbol"))
    assert cols["sym"] == "`g#`symbol$()"


@pytest.mark.parametrize("spec", ["value", "value:nosuchtype", ""])
def test_a_malformed_column_spec_is_refused(spec):
    with pytest.raises(UqsError):
        jobs.parse_columns(spec)


def test_publishing_without_columns_is_refused():
    """The plant must define a table before anything writes to it: `.u.upd`
    onto an undefined table discards the rows in silence (#288)."""
    with pytest.raises(UqsError, match="columns"):
        jobs.streaming_job("j", [], "my_metric", None)


def test_columns_without_publishing_is_refused():
    with pytest.raises(UqsError, match="no --publishes"):
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


# ------------------------------------------------------------- refusing


def test_a_bad_name_is_refused_before_anything_is_planned():
    for name in ("Markout", "2fast", "with-dash", ""):
        with pytest.raises(UqsError):
            jobs.streaming_job(name, [], None, None)


def test_a_plan_refuses_wholesale_rather_than_half_writing(tmp_path: Path):
    """A scaffold that created three files and then refused the fourth would
    leave a tree that neither loads nor reverts cleanly - and the half that
    landed registers itself on load."""
    plan = jobs.streaming_job("j", ["trades"], None, None)
    (tmp_path / "src" / "etl" / "streaming").mkdir(parents=True)
    (tmp_path / "src" / "etl" / "streaming" / "j.q").write_text("already here")
    with pytest.raises(UqsError, match="already exists"):
        write.apply_plan(plan, tmp_path)
    assert (tmp_path / "src" / "etl" / "streaming" / "j.q").read_text() == "already here"
    assert not (tmp_path / "tests").exists(), "nothing else was written"


def test_appending_to_a_missing_file_is_refused(tmp_path: Path):
    plan = jobs.streaming_job("j", ["trades"], None, None)
    with pytest.raises(UqsError, match="nothing to append to"):
        write.apply_plan(plan, tmp_path)


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
    with pytest.raises(UqsError):
        write._with_nslist_entry(content, "`.ctest")


def test_a_namespace_already_listed_is_refused():
    with pytest.raises(UqsError, match="already in"):
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
    with pytest.raises(UqsError):
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
    with pytest.raises(UqsError, match="needs --columns"):
        jobs.bounded_worker("fx_rates", "fx_rates", None)
    with pytest.raises(UqsError, match="needs --columns"):
        jobs.bounded_worker("fx_rates", "fx_rates", None, reuse_source=True)


def test_columns_with_nothing_to_shape_are_refused_not_ignored():
    with pytest.raises(UqsError, match="nothing to shape"):
        jobs.bounded_worker(
            "fx_rates", "fx_rates", "mid:float", reuse_source=True, define_table=False
        )


def test_every_plan_names_the_stack_page_line_it_cannot_write():
    """docs/architecture/stack.md is authored prose and pytest fails until
    it names the process - so the plan says so, rather than leaving that red to
    be discovered."""
    for plan, proc in (
        (jobs.bounded_worker("fx_rates", "fx_rates", "mid:float"), "fx_rates_backfill1"),
        (jobs.streaming_job("markout2", ["trades"], None, None), "markout21"),
    ):
        assert any(proc in n and "stack.md" in n for n in plan.notes), plan.notes


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


def test_a_job_publishing_a_new_table_is_told_about_the_desk_catalog():
    """Both pytest failures a scaffold leaves are prose, and the notes are the
    only warning of either.

    The catalog note was missing, so publishing a new table left TWO pytest
    failures while `new-job` named one of them - and the guide described one
    too. An unannounced failure in a tree with 1030 passing tests reads as
    "the scaffold is broken", not "your turn".
    """
    plan = jobs.streaming_job("catprobe", ["quote"], "cat_probe", "sym:symbol, v:float")
    notes = " ".join(plan.notes)
    assert "uqs_catalog.q" in notes
    assert "cat_probe" in notes
    assert ".qcat.hidden" in notes, "the opt-out has to be offered, not just the entry"


def test_a_job_publishing_nothing_is_not_sent_to_the_catalog():
    """A job that keeps its output local adds no table, so the catalog has
    nothing to describe. A note here would be advice that does not apply,
    which is how notes stop being read."""
    plan = jobs.streaming_job("localprobe", ["quote"], None, None)
    assert "uqs_catalog.q" not in " ".join(plan.notes)


def test_a_bounded_worker_is_told_about_its_dataset():
    """A backfill's new table is its `dataset`, not a `publishes`, so it needs
    the note by a different name - and the first version of this only wired it
    into the streaming path."""
    plan = jobs.bounded_worker("fxprobe", "fx_probe", "sym:symbol, mid:float")
    notes = " ".join(plan.notes)
    assert "uqs_catalog.q" in notes and "fx_probe" in notes


# ------------------------------------------- publishes, subscribes, the plant

#: What `new-job` passes as the plant's tables: vendored `quote` and this
#: tree's `trades` and `orders`.
_PLANT = {"quote", "trades", "orders"}


def _writes_to(plan: jobs.ScaffoldPlan, path: Path) -> bool:
    return any(a.path == path for a in plan.actions)


@pytest.mark.parametrize("publishes", ["My_Table", "a b", "1st"])
def test_a_published_table_name_is_checked_like_every_other_name(publishes):
    """It was not, and `--publishes a,b` wrote `enlist `a,b` into the job and
    `a,b:([]...)` into uqs_tables.q - q that does not load."""
    with pytest.raises(UqsError, match="published table"):
        jobs.streaming_job("j", ["quote"], publishes, "v:float", known_tables=_PLANT)


def test_a_job_publishes_onto_an_existing_table_without_redefining_it():
    """A second producer of a plant table was refused ("needs --columns") and,
    given columns, would have defined the table twice."""
    plan = jobs.streaming_job("orders2", [], "orders", None, known_tables=_PLANT)
    (d,) = _declared(plan, "orders2.q")
    assert d.publishes == ("orders",)
    assert not _writes_to(plan, jobs.TABLES_FILE)
    assert not _writes_to(plan, jobs.STACK_TABLES_TEST)
    assert "uqs_catalog.q" not in " ".join(plan.notes), "the table is already described"


def test_a_job_can_publish_an_existing_and_a_new_table():
    plan = jobs.streaming_job(
        "both", ["quote"], "orders, both_out", "sym:symbol, v:float", known_tables=_PLANT
    )
    (d,) = _declared(plan, "both.q")
    assert d.publishes == ("orders", "both_out")
    tables = _body(plan, str(jobs.TABLES_FILE))
    assert "both_out:([]" in tables and "orders:([]" not in tables
    assert "both_out" in " ".join(plan.notes) and "orders" not in " ".join(plan.notes)


def test_two_new_tables_are_refused_because_columns_shapes_one():
    with pytest.raises(UqsError, match="can shape only one"):
        jobs.streaming_job("j", ["quote"], "a_out,b_out", "v:float", known_tables=_PLANT)


def test_columns_for_a_table_that_already_exists_are_refused_not_ignored():
    with pytest.raises(UqsError, match="already defined by the plant"):
        jobs.streaming_job("j", [], "orders", "v:float", known_tables=_PLANT)


def test_a_subscription_to_a_table_nothing_defines_is_refused():
    """It scaffolded happily and the job sat idle after `uqs start`."""
    with pytest.raises(UqsError) as excinfo:
        jobs.streaming_job("j", ["quote", "no_such_table"], None, None, known_tables=_PLANT)
    assert "no_such_table" in str(excinfo.value)
    assert "quote" not in str(excinfo.value).split(" - ")[0], "only the unknown one is named"


def test_without_the_plant_every_published_table_is_new():
    """The plan-only call, as the tests above this section make it."""
    plan = jobs.streaming_job("j", ["anything"], "orders", "v:float")
    assert "orders:([]" in _body(plan, str(jobs.TABLES_FILE))


# ------------------------------------------------------------ the desk catalog


def test_a_new_table_gets_its_catalog_entry_with_a_placeholder_description():
    """One line of q, fully qualified, appended to uqs_catalog.q.

    Only the prose: the columns used to be written here too, as a row each in
    columns.csv, and are `meta`'s answer now.
    """
    plan = jobs.streaming_job("cat2", ["quote"], "cat_two", "sym:symbol, v:float, n:long")
    entry = _body(plan, "uqs_catalog.q")
    assert entry.startswith(".qcat.describe[`cat_two]:")
    assert "SCAFFOLDED:" in entry
    assert not any("csv" in str(a.path) for a in plan.actions)


def test_the_catalog_entry_is_qualified_so_it_lands_in_the_namespace():
    """It is appended past uqs_catalog.q's `\\d .`, so a bare `describe[...]`
    would make a ROOT-level dictionary and the table would silently not be
    catalogued."""
    plan = jobs.streaming_job("cat4", ["quote"], "cat_four", "sym:symbol")
    assert _body(plan, "uqs_catalog.q").startswith(".qcat.describe[`")


def test_a_worker_dataset_gets_a_catalog_entry_only_when_it_is_new():
    new = jobs.bounded_worker("fxprobe", "fx_probe", "sym:symbol, mid:float")
    assert _body(new, "uqs_catalog.q").startswith(".qcat.describe[`fx_probe]:")
    old = jobs.bounded_worker("fxprobe", "fx_probe", "mid:float", define_table=False)
    assert not any(str(a.path).endswith("uqs_catalog.q") for a in old.actions)


def test_a_type_the_old_catalog_could_not_hold_is_now_catalogued_anyway():
    """The writer used to refuse a table with a column it had no catalog type
    for, because it had to write that type into columns.csv. It does not write
    types any more, so `date` - which has no QType - no longer blocks the
    description."""
    plan = jobs.streaming_job("cat3", ["quote"], "cat_three", "sym:symbol, d:date")
    assert _body(plan, "uqs_catalog.q").startswith(".qcat.describe[`cat_three]:")


# ------------------------------------------------ the output-contract driver


def _test_file(plan: jobs.ScaffoldPlan, name: str) -> str:
    return _body(plan, f"test_{name}.q")


def test_a_job_that_subscribes_and_publishes_gets_a_throwing_contract_driver():
    """test_job_output_contracts.q finds it by this exact name, in this
    namespace, and it must throw until written rather than pass on nothing."""
    body = _test_file(jobs.streaming_job("zz", ["quote"], "zz_out", "v:float"), "zz")
    assert "\\d .zztest" in body and "contract_driver:{[]" in body
    driver = body[body.index("contract_driver:") :]
    assert "'\"zz: write .zztest.contract_driver" in driver, "it throws"
    assert "SCAFFOLDED" in body[body.index("test_zz_is_implemented") :], "and is marked"


@pytest.mark.parametrize(
    ("subscribes", "publishes", "columns"),
    [([], "feed_out", "v:float"), (["quote"], None, None)],
    ids=["a-feed-runs-on-its-timer", "a-job-publishing-nothing-has-nothing-to-check"],
)
def test_no_driver_where_the_contract_test_needs_none(subscribes, publishes, columns):
    body = _test_file(jobs.streaming_job("zz", subscribes, publishes, columns), "zz")
    assert "contract_driver" not in body


# ------------------------------------------------------- requirement citations


def test_the_query_note_cites_the_rule_that_actually_says_it():
    """`parameterised, never concatenated` is FE-14, not ETL-08.

    It cited ETL-08 for months, and that number resolves - to the wrong rule.
    The requirements document's ETL-08 is half-open intervals; the
    parameterised-query rule has no ETL-nn of its own, and
    `src/etl/core/source_contract.q` records both facts. A reader who
    followed the number landed on interval arithmetic while reading about
    query construction.
    """
    plan = jobs.bounded_worker("citeprobe", "cite_probe", "sym:symbol, mid:float")
    query_notes = [note for note in plan.notes if ".query" in note]
    assert query_notes, "the scaffold no longer tells you to write query"
    assert "FE-14" in query_notes[0]
    assert "ETL-08" not in query_notes[0], "ETL-08 is the interval rule, not this one"


def test_the_source_template_keeps_the_two_rules_apart():
    """Both apply to `query`, and they are different rules with different
    numbers. One sentence carrying both invites exactly the confusion the
    note above had."""
    plan = jobs.bounded_worker("citeprobe", "cite_probe", "sym:symbol, mid:float")
    source = _body(plan, "citeprobe.q")
    assert "FE-14" in source, "the parameterised-query rule"
    assert "ETL-08" in source, "the half-open interval rule"
    assert "DIFFERENT rule" in source, "and the template says they are not the same one"
