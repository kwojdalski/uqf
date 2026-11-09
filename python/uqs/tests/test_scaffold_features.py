"""`uqs job new`'s timer, partition, quality check and profile options.

As in test_scaffold_options.py, each option is read back the way the tree
reads it where there is a reader, and the profiles.py edits are applied to
the real file's text and compiled, so an edit that broke the module fails here
rather than on the next import.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from plain_cli import PlainRunner

from uqs import cli
from uqs.cli import create, create_backfill
from uqs.model.declarations import declaration_calls, symbols
from uqs.paths import UqsError
from uqs.scaffold import jobs, profile, templates
from uqs.scaffold import worker as backfill
from uqs.scaffold.columns import definition_columns, parse_columns
from uqs.scaffold.normalizer import normalizer

UQF_ROOT = Path(__file__).resolve().parents[3]
PROFILES_TEXT = (UQF_ROOT / profile.PROFILES_FILE).read_text()
UNPROFILED_TEXT = (UQF_ROOT / profile.UNPROFILED_FILE).read_text()
KNOWN = ("default", "fx", "crypto", "essential")

runner = PlainRunner()  # CI renders usage errors in colour


def _body(plan: jobs.ScaffoldPlan, suffix: str) -> str:
    return next(a.body for a in plan.actions if str(a.path).endswith(suffix))


def _fields(plan: jobs.ScaffoldPlan, suffix: str) -> dict[str, str]:
    (_, _, fields), *_ = declaration_calls(_body(plan, suffix))
    return fields


# ------------------------------------------------------------------ --period


def test_a_feed_ticks_at_the_period_it_is_given():
    fields = _fields(jobs.streaming_job("f", [], "fo", "px:float", period="0D00:00:00.500"), "f.q")
    assert fields["period"] == "0D00:00:00.500"


def test_a_feed_ticks_every_second_by_default():
    assert _fields(jobs.streaming_job("f", [], "fo", "px:float"), "f.q")["period"] == "0D00:00:01"


def test_an_etl_given_a_period_gets_a_timer_beside_its_batch_handler():
    plan = jobs.streaming_job("m", ["trades"], "mo", "px:float", period="0D00:00:05")
    fields = _fields(plan, "m.q")
    assert list(fields)[3:6] == ["on_batch", "period", "on_timer"], "define's key order"
    body = _body(plan, "m.q")
    assert '\'"m.on_batch: not implemented"' in body
    assert '\'"m.on_timer: not implemented"' in body


def test_an_etl_without_a_period_has_no_timer():
    assert "on_timer" not in _fields(jobs.streaming_job("m", ["trades"], "mo", "px:float"), "m.q")


@pytest.mark.parametrize(
    ("period", "message"),
    [
        ("5x", "must be a DURATION .* or a q timespan"),
        ("0D00:00:00", "must be positive"),
        ("0s", "must be positive"),
        # #1598: a component q refuses to parse, or a day count past q's
        # 64-bit timespans - 106752D wraps negative, 213504D to ~25 minutes.
        ("0D00:60:00", "or a q timespan"),
        ("0D00:00:60", "or a q timespan"),
        ("0D99:99:99", "or a q timespan"),
        ("106752D00:00:00", "or a q timespan"),
        ("213504D00:00:00", "or a q timespan"),
        ("999999999D00:00:00", "or a q timespan"),
        ("\u0661D00:00:01", "or a q timespan"),
        ("0D00:00:00.0000000001", "must be positive"),
    ],
)
def test_a_period_define_would_refuse_is_refused_here(period, message):
    with pytest.raises(UqsError, match=message):
        jobs.streaming_job("f", [], "fo", "px:float", period=period)


@pytest.mark.parametrize(("period", "tick"), [("5s", "0D00:00:05"), ("1D", "1D")])
def test_a_period_is_a_duration_or_a_q_timespan(period, tick):
    """#2121: `--period 5s` was refused on the one command whose value is a
    timer tick, and `1D` too, which `--width` took."""
    plan = jobs.streaming_job("f", [], "fo", "px:float", period=period)
    assert f"\n    {tick};" in next(a.body for a in plan.actions if str(a.path).endswith("f.q"))


# ------------------------------------------------------------ --slice, --check


def test_a_partitioned_worker_declares_its_partition():
    plan = backfill.bounded_worker("fx", "fx_rates", "sym:symbol,px:float", partition="EURUSD")
    assert symbols(_fields(plan, "fx_backfill.q")["partition"]) == ("EURUSD",)


def test_a_partitioned_worker_declares_the_column_holding_its_partition():
    """#1539: define refuses a partition with no partition_column."""
    plan = backfill.bounded_worker("fx", "fx_rates", "sym:symbol,px:float", partition="EURUSD")
    assert symbols(_fields(plan, "fx_backfill.q")["partition_column"]) == ("sym",)
    plan = backfill.bounded_worker(
        "fx", "fx_rates", "pair:symbol,px:float", partition="EURUSD", partition_column="pair"
    )
    assert symbols(_fields(plan, "fx_backfill.q")["partition_column"]) == ("pair",)


def test_a_partition_column_outside_the_columns_or_without_a_partition_is_refused():
    with pytest.raises(UqsError, match="not among --schema"):
        backfill.bounded_worker("fx", "fx_rates", "px:float", partition="EURUSD")
    with pytest.raises(UqsError, match="--slice-column needs --slice"):
        backfill.bounded_worker("fx", "fx_rates", "px:float", partition_column="sym")


_FX_RATES = [("time", "`timestamp$()"), ("sym", "`g#`symbol$()"), ("px", "`float$()")]


def test_into_an_existing_target_the_partition_column_is_the_targets():
    """#1599: with the target defined already, --schema shape the new source,
    and publish holds the TARGET's rows to the partition column - so a source
    without it is fine for a derivation, and a target without it is not."""
    plan = backfill.bounded_worker(
        "fx", "fx_rates", "pair:symbol,mid:float", partition="EURUSD", transform="derive",
        define_table=False, target_columns=_FX_RATES,
    )  # fmt: skip
    assert symbols(_fields(plan, "fx_backfill.q")["partition_column"]) == ("sym",)
    with pytest.raises(UqsError, match="not a column of fx_rates"):
        backfill.bounded_worker(
            "fx", "fx_rates", "pair:symbol,mid:float", partition="EURUSD",
            partition_column="pair", transform="derive",
            define_table=False, target_columns=_FX_RATES,
        )  # fmt: skip


def test_a_partition_that_is_not_a_symbol_is_refused():
    with pytest.raises(UqsError, match="must be a q symbol"):
        backfill.bounded_worker("fx", "fx_rates", "sym:symbol,px:float", partition="EUR/USD")


def test_check_scaffolds_a_throwing_quality_check_and_declares_it():
    plan = backfill.bounded_worker("fx", "fx_rates", "px:float", check=True)
    assert _fields(plan, "fx_backfill.q")["check"] == ".qpipe.job.fx_backfill.quality_check"
    assert '\'"fx_backfill.quality_check: not implemented"' in _body(plan, "fx_backfill.q")
    assert any("quality_check" in n and "every window fails" in n for n in plan.notes)


def test_without_check_there_is_no_check():
    plan = backfill.bounded_worker("fx", "fx_rates", "px:float")
    assert "check" not in _fields(plan, "fx_backfill.q")
    assert any("--check" in n for n in plan.notes)


def test_a_second_worker_needs_another_partition(tmp_path):
    """Coverage is kept per (dataset, partition): the same pair twice does not load."""
    workers = tmp_path / "src" / "etl" / "workers"
    workers.mkdir(parents=True)
    (workers / "w.q").write_text(
        ".qetl.job.bounded.define[`w;`source`outputs`width`transform`partition!"
        "(`s;enlist `fx;1D;`s_passthrough;`EURUSD)];\n"
    )
    assert create_backfill.workers_filling(tmp_path, "fx", "EURUSD") == ["w"]
    assert create_backfill.workers_filling(tmp_path, "fx", "GBPUSD") == []
    assert create_backfill.workers_filling(tmp_path, "fx") == []


# ------------------------------------------------------------------ profiles


def _edited(actions) -> dict:
    """Each file the actions edit, as they leave it, each still compiling."""
    texts = {profile.PROFILES_FILE: PROFILES_TEXT, profile.UNPROFILED_FILE: UNPROFILED_TEXT}
    for action in actions:
        texts[action.path] = profile.apply(texts[action.path], action)
    for path, text in texts.items():
        compile(text, str(path), "exec")
    return texts


def _applied(actions) -> str:
    return "".join(_edited(actions).values())


def test_neither_answer_takes_a_module_past_its_line_budget():
    """profiles.py sat at the package's 400-line budget, so writing the three
    lines of an exemption into it failed test_no_module_is_still_oversized on
    the first `--unprofiled` scaffold; a member edits its profile's one line
    (#2462)."""
    for kwargs in ({"unprofiled": "a diagnostic started by hand"}, {"profile": "fx"}):
        kwargs = {"profile": None, "unprofiled": None, **kwargs}
        actions, _ = profile.membership(
            "tapx1", start_with_all=False, known_profiles=KNOWN, **kwargs
        )
        assert actions
        for path, text in _edited(actions).items():
            assert len(text.splitlines()) <= 400, (path, kwargs)
    actions, _ = profile.membership(
        "tapx1", profile="fx", unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    assert len(_edited(actions)[profile.PROFILES_FILE].splitlines()) == len(
        PROFILES_TEXT.splitlines()
    )


def test_a_profile_takes_the_process_and_the_module_still_compiles():
    actions, notes = profile.membership(
        "pulse1", profile="crypto", unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    assert not notes
    assert '"crypto": ("cryptomock1", "crypto_markout1", "crypto_book_1s1", "pulse1"),' in _applied(
        actions
    )


def test_start_with_all_puts_it_in_default_and_says_what_else_to_check():
    actions, notes = profile.membership(
        "pulse1", profile=None, unprofiled=None, start_with_all=True, known_profiles=KNOWN
    )
    default = next(ln for ln in _applied(actions).splitlines() if ln.startswith('    "default": ('))
    assert '"pulse1"),' in default
    assert any("always_on" in n for n in notes)


def test_an_exemption_is_written_with_its_reason():
    actions, _ = profile.membership(
        "tapx1",
        profile=None,
        unprofiled="a diagnostic started by hand against whichever table is being looked at",
        start_with_all=False,
        known_profiles=KNOWN,
    )
    text = _applied(actions)
    assert '    "tapx1": (\n' in text
    assert "a diagnostic started by hand" in text


@pytest.mark.parametrize(
    "reason",
    [
        "a diagnostic started by hand",
        'quotes " and a backslash \\ survive',
        "café ≥ one",
        # Outside the BMP: json.dumps escaped it as a surrogate pair, which
        # Python source reads back as two lone surrogates, not the character.
        "vol \U0001d70e by hand \U0001f600",
        "a reason long enough that the scaffold wraps it over several lines the way "
        "the existing exemptions in profiles.py are wrapped, every word kept",
    ],
)
def test_an_exemption_reads_back_as_the_reason_given(reason):
    """--unprofiled REASON -> unprofiled.py -> UNPROFILED: the reason the module
    holds is the one given, not an escaped near-copy of it."""
    import ast

    actions, _ = profile.membership(
        "tapx1", profile=None, unprofiled=reason, start_with_all=False, known_profiles=KNOWN
    )
    tree = ast.parse(_edited(actions)[profile.UNPROFILED_FILE])
    (unprofiled,) = [
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and getattr(node.target, "id", None) == "UNPROFILED"
        and node.value is not None
    ]
    assert ast.literal_eval(unprofiled)["tapx1"] == reason


def test_no_choice_leaves_a_note_and_writes_nothing():
    actions, notes = profile.membership(
        "p1", profile=None, unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    assert not actions
    assert any("--profile" in n and "--unprofiled" in n for n in notes)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"profile": "nope"}, "is not a profile"),
        ({"profile": "fx", "unprofiled": "why"}, "pick one"),
        ({"profile": "default"}, "needs --start-with-all"),
        ({"unprofiled": "  "}, "needs the reason"),
    ],
)
def test_a_profile_choice_that_cannot_stand_is_refused(kwargs, message):
    args = {"profile": None, "unprofiled": None, **kwargs}
    with pytest.raises(UqsError, match=message):
        profile.membership("p1", start_with_all=False, known_profiles=KNOWN, **args)


def test_start_with_all_and_unprofiled_are_refused_together():
    """#1591: --start-with-all puts the process in the default profile, so an
    exemption beside it contradicts it - refused, as --profile is."""
    with pytest.raises(UqsError, match="pick one"):
        profile.membership(
            "p1", profile=None, unprofiled="why", start_with_all=True, known_profiles=KNOWN
        )


def test_a_process_already_in_the_profile_is_refused():
    actions, _ = profile.membership(
        "cryptomock1", profile="crypto", unprofiled=None, start_with_all=False, known_profiles=KNOWN
    )
    with pytest.raises(UqsError, match="already in the crypto profile"):
        _applied(actions)


def test_a_normalizer_takes_a_profile_too():
    quote = "quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$())"
    plan = normalizer(
        "ticks",
        ["quote"],
        parse_columns("sym:symbol"),
        {"quote": definition_columns(quote)},
        known_tables={"quote"},
        profile="fx",
        known_profiles=KNOWN,
    )
    assert any(a.anchor == "profile:fx" and a.body == "ticks1" for a in plan.actions)


# ----------------------------------------------------------------- the command


@pytest.mark.parametrize(
    ("argv", "option"),
    [
        (
            [
                "x",
                "--kind",
                "backfill",
                "--outputs",
                "x",
                "--schema",
                "a:float",
                "--period",
                "0D00:00:01",
            ],
            "--period",
        ),
        (["x", "--outputs", "x", "--schema", "a:float", "--slice", "EURUSD"], "--slice"),
        (["x", "--outputs", "x", "--schema", "a:float", "--check"], "--check"),
        (["x", "--outputs", "x", "--schema", "a:float", "--width", "2D"], "--width"),
        (["x", "--outputs", "x", "--schema", "a:float", "--source", "s"], "--source"),
        (["x", "--kind", "normalizer", "--outputs", "x", "--schema", "a:float"], "--outputs"),
        (["x", "--kind", "normalizer", "--schema", "a:float", "--width", "2D"], "--width"),
        (
            [
                "x",
                "--kind",
                "backfill",
                "--outputs",
                "x",
                "--schema",
                "a:float",
                "--profile",
                "fx",
            ],
            "--profile",
        ),
    ],
)
def test_an_option_for_another_kind_is_refused_by_name(argv, option, monkeypatch):
    refused: list[str] = []

    def record(exc: Exception) -> None:
        refused.append(str(exc))
        raise SystemExit(1)

    monkeypatch.setattr(create, "_die", record)
    result = runner.invoke(cli.app, ["job", "new", *argv, "--dry-run"])
    assert result.exit_code == 1
    assert refused and refused[0].startswith(f"{option} does not apply to --kind")


@pytest.mark.parametrize(
    ("old", "new"), [("--partition", "`--slice`"), ("--partition-column", "`--slice-column`")]
)
def test_the_old_partition_spellings_are_refused_naming_slice(old, new):
    """#2239: the coverage slice is `--slice`; "partition" is an HDB date."""
    argv = ["job", "new", "x", "--kind", "backfill", "--outputs", "x", old, "EURUSD", "--dry-run"]
    result = runner.invoke(cli.app, argv)
    assert result.exit_code == 2
    said = " ".join(re.sub("[│╭╮╰╯─]", " ", result.output).split())
    assert f"`{old}` is now {new}" in said


# ------------------------------------------------------------------ --twin-of


def _plant() -> dict[str, str]:
    from uqs.model.schemas import _DEFINITION  # noqa: PLC0415
    from uqs.paths import TABLES_FILE  # noqa: PLC0415

    text = (UQF_ROOT / TABLES_FILE).read_text()
    return {m.group(1): m.group(0) for m in _DEFINITION.finditer(text)}


def test_a_twin_takes_its_jobs_table_and_columns():
    dataset, cols = create_backfill.twin_target(UQF_ROOT, "crypto_markout", None, None, _plant())
    assert dataset == "crypto_markout", "the one table crypto_markout publishes"
    assert [c for c, _ in cols][:2] == ["time", "source_time"], "and that table's own columns"


def test_a_job_publishing_several_tables_needs_dataset_to_choose():
    with pytest.raises(UqsError, match="crypto_book, crypto_trades - name the one"):
        create_backfill.twin_target(UQF_ROOT, "crypto_mock", None, None, _plant())
    dataset, _ = create_backfill.twin_target(
        UQF_ROOT, "crypto_mock", "crypto_trades", None, _plant()
    )
    assert dataset == "crypto_trades"


@pytest.mark.parametrize(
    ("job", "dataset", "columns", "message"),
    [
        ("nope", None, None, "'nope' is not a streaming job"),
        ("crypto_markout", "trades", None, "trades is not a table crypto_markout publishes"),
        ("crypto_markout", None, "a:float", "drop --schema"),
        ("cross", None, None, "publishes nothing"),
    ],
)
def test_a_twin_that_would_not_be_one_is_refused(job, dataset, columns, message):
    with pytest.raises(UqsError, match=message):
        create_backfill.twin_target(UQF_ROOT, job, dataset, columns, _plant())


def test_a_twin_applies_the_transform_its_job_declares():
    """#884: a job that names its transform hands it to its twin, so a
    refill re-derives what the job publishes rather than a copy of it."""
    assert create_backfill.shared_transform(UQF_ROOT, "crypto_markout", None) == (
        "crypto_markout",
        "crypto_markout_score",
    )
    body = templates.worker_body(
        "mk_backfill", "mk", "crypto_markout", "1D", "mk_backfill1",
        shared=("crypto_markout", "crypto_markout_score"),
    )  # fmt: skip
    assert "`mk;enlist `crypto_markout;1D;`crypto_markout_score;" in body
    assert "passthrough" not in body, "no transform of its own is written"


def test_a_scaffolded_twin_declares_the_job_it_repairs():
    """#2103: --twin-of used to read the job once and leave nothing behind, so a
    later change to the job's output could not be told from the backfill's."""
    plain = templates.worker_body(
        "mk_backfill", "mk", "arb_out", "1D", "mk_backfill1", twin_of="arbitrage"
    )
    assert "`twin_of`procname`note!" in plain
    assert ";`arbitrage;\n" in plain
    assert "twin_calculation" not in plain, "no shared transform, so no promise is written"
    assert "calculation equivalence is not established" in plain
    shared = templates.worker_body(
        "mk_backfill", "mk", "crypto_markout", "1D", "mk_backfill1",
        shared=("crypto_markout", "crypto_markout_score"), twin_of="crypto_markout",
    )  # fmt: skip
    assert "`twin_of`twin_calculation`procname`note!" in shared
    assert ";`crypto_markout;`shared;\n" in shared, "the promise to apply the one transform"
    assert "twin_of" not in templates.worker_body("w_backfill", "w", "d", "1D", "w1")


def test_a_planned_twin_writes_its_declaration():
    fields = _fields(_twin("arb_refill", "arbitrage"), "arb_refill_backfill.q")
    assert symbols(fields["twin_of"]) == ("arbitrage",)
    assert "twin_calculation" not in fields, "arbitrage declares no transform"


def test_transform_beside_a_declared_one_is_refused():
    with pytest.raises(UqsError, match="drop --transform"):
        create_backfill.shared_transform(UQF_ROOT, "crypto_markout", "derive")


def test_a_job_that_declares_no_transform_leaves_its_twin_the_usual_scaffold():
    assert create_backfill.shared_transform(UQF_ROOT, "arbitrage", None) is None


# ----------------------------------------------- what the generated names are


@pytest.mark.parametrize("twin_of", [None, "arbitrage"])
def test_a_name_already_ending_in_backfill_is_refused(twin_of):
    """#1228: the worker is NAME_backfill, so NAME foo_backfill made a worker
    foo_backfill_backfill and a source foo_backfill. Refused, saying what to pass."""
    with pytest.raises(UqsError, match="pass foo, and the worker is foo_backfill"):
        create_backfill.backfill_plan(
            UQF_ROOT, "foo_backfill", None if twin_of else "foo", None if twin_of else "px:float",
            width=None, source=None, procname=None, transport=None, partition=None,
            check=False, transform=None, start_with_all=False, twin_of=twin_of,
            definitions=_plant() if twin_of else None,
        )  # fmt: skip


def _declared_namespaces(text: str) -> list[str]:
    """tests/q/test_namespaces.q's declared_namespaces, for one file: every
    `\\d .X` line except `\\d .`, as `.X`."""
    lines = [ln for ln in text.splitlines() if ln.startswith("\\d .") and ln != "\\d ."]
    return [ln[3:] for ln in lines]


def _plans():
    quote = "quote:([]time:`timestamp$(); sym:`g#`symbol$(); bid:`float$())"
    yield backfill.bounded_worker("fx", "fx_rates", "px:float")
    yield backfill.bounded_worker("fx", "fx_rates", "px:float", transport="local", check=True)
    yield backfill.bounded_worker("fx", "fx_rates", "px:float", transform="derive")
    yield jobs.streaming_job("f", [], "fo", "px:float", period="0D00:00:01")
    yield jobs.streaming_job("m", ["trades"], "mo", "px:float", period="0D00:00:05")
    yield normalizer(
        "ticks", ["quote"], parse_columns("sym:symbol"), {"quote": definition_columns(quote)},
        known_tables={"quote"}, unprofiled="test",
    )  # fmt: skip


def test_every_generated_q_namespace_passes_the_prefix_rule():
    """#1228: test_namespaces.q holds every `\\d` in src/ and scripts/ to the
    .q prefix, and the backfill example declared `.<name>ex`, so each
    scaffold left the tree failing that gate."""
    seen = []
    for plan in _plans():
        for action in plan.actions:
            if action.path.parts[0] not in ("src", "scripts") or action.path.suffix != ".q":
                continue
            for ns in _declared_namespaces(action.body):
                seen.append(ns)
                assert ns.startswith(".q"), f"{action.path} declares {ns}"
    assert any(ns.endswith("ex") for ns in seen), "the example script was among them"


def _twin(name: str, twin_of: str, source: str | None = None) -> jobs.ScaffoldPlan:
    return create_backfill.backfill_plan(
        UQF_ROOT, name, None, None,
        width=None, source=source, procname=None, transport=None, partition=None,
        check=False, transform=None, start_with_all=False, twin_of=twin_of,
        definitions=_plant(),
    )  # fmt: skip


def test_a_twin_does_not_shape_a_new_source_like_its_shared_transforms_output():
    """#1600: crypto_markout's transform reads real_fills and books, not the
    crypto_execution_quality it publishes. A source generated from that table
    could never feed it; the worker was refused only when the tree loaded."""
    # The planner now refuses crypto_markout earlier (#1579: its transform loads
    # after workers), so the worker-level refusal is exercised directly.
    from uqs.scaffold.columns import parse_columns
    from uqs.scaffold.worker import bounded_worker

    with pytest.raises(UqsError, match="crypto_execution_quality, which reads its own inputs"):
        bounded_worker(
            "crypto_refill",
            "crypto_execution_quality",
            parse_columns("sym:symbol, px:float"),
            shared=("crypto_markout", "crypto_execution_quality"),
            define_table=False,
        )


def test_a_twin_of_a_job_without_a_transform_still_gets_a_new_source():
    plan = _twin("arb_refill", "arbitrage")
    assert any(str(a.path).endswith("sources/arb_refill.q") for a in plan.actions)


def test_a_twin_whose_transform_registers_after_workers_is_refused():
    """#1579: posbook's `position` transform is registered inside the streaming
    job's file, which src/etl/init.q loads after workers/. A twin naming it
    stopped the whole tree loading with `transform ... is not registered`."""
    with pytest.raises(UqsError, match="Move position's .qetl.transform.define"):
        create_backfill.require_loaded_before_workers(UQF_ROOT, "posbook", "position")


def test_a_transform_registered_under_transforms_is_loaded_before_workers():
    create_backfill.require_loaded_before_workers(
        UQF_ROOT, "book_liquidity_snapshot", "book_liquidity_snapshot"
    )


def test_a_twin_reuses_an_existing_source_without_columns_it_never_passed():
    """#1582: a twin's columns come from its job's table, not from --schema.
    When its source and table both exist already, nothing is shaped by them,
    so they are not a reason to refuse the plan. A derive: hdb_executions'
    fixture is not executions' shape, so a passthrough is refused (#2501)."""
    plan = create_backfill.backfill_plan(
        UQF_ROOT, "exec_refill", None, None,
        width=None, source="hdb_executions", procname=None, transport=None, partition=None,
        check=False, transform="derive", start_with_all=False, twin_of="executions",
        definitions=_plant(),
    )  # fmt: skip
    written = [str(a.path) for a in plan.actions]
    assert not any(p.endswith("sources/hdb_executions.q") for p in written), "source reused"
    assert not any(p.endswith("plant_tables.q") for p in written), "table not redefined"
    fields = _fields(plan, "exec_refill_backfill.q")
    assert symbols(fields["source"]) == ("hdb_executions",)
    assert symbols(fields["outputs"]) == ("executions",)


# ---------------------------------- a passthrough between two different shapes

_AUDIT_TARGET = {
    "audit_t": ".qetl.plant.audit_t:([]time:`timestamp$(); sym:`g#`symbol$(); px:`float$())"
}


def _backfill_into_audit_t(shape: str, transform: str | None):
    return create_backfill.backfill_plan(
        UQF_ROOT, "audit_bf", "audit_t", shape,
        width=None, source=None, procname=None, transport=None, partition=None,
        check=False, transform=transform, start_with_all=False,
        definitions=_AUDIT_TARGET,
    )  # fmt: skip


@pytest.mark.parametrize("shape", ["sym:symbol, qty:long", "sym:symbol, px:long", "px:float"])
def test_a_passthrough_from_a_new_source_into_another_shape_is_refused(shape):
    """#1593: the passthrough declares its input and output as the target's
    shape and its example from the new source's fixture - two shapes, refused
    by .qetl.transform.define when the tree next loads. Refused here instead."""
    with pytest.raises(UqsError, match="--transform derive"):
        _backfill_into_audit_t(shape, None)


def test_a_passthrough_from_a_new_source_of_the_targets_shape_is_planned():
    plan = _backfill_into_audit_t("sym:symbol, px:float", None)
    assert plan.name == "audit_bf_backfill"


def test_a_derivation_between_two_shapes_is_still_planned():
    plan = _backfill_into_audit_t("sym:symbol, qty:long", "derive")
    assert plan.name == "audit_bf_backfill"


def _second_worker(name: str, source: str, dataset: str, shape: str | None, transform=None):
    return create_backfill.backfill_plan(
        UQF_ROOT, name, dataset, shape,
        width=None, source=source, procname=None, transport=None, partition="EURUSD",
        check=False, transform=transform, start_with_all=False, definitions=_plant(),
    )  # fmt: skip


def test_a_passthrough_from_a_reused_source_into_another_shape_is_refused():
    """#2501: over an existing source the shape check was skipped, and the
    passthrough declared demo_deals' plant shape - `time` and all - against a
    fixture with none, so the whole tree stopped loading."""
    with pytest.raises(UqsError, match=r"source demo_deals's columns .*--transform derive"):
        _second_worker("deals_eu", "demo_deals", "demo_deals", None)


def test_a_passthrough_from_a_reused_source_of_the_targets_shape_is_planned():
    shape = "sym:symbol, action:symbol, side:long, size:float, price:float, order_id:long, pip_factor:long"  # noqa: E501
    plan = _second_worker("tape_copy", "demo_events", "tape_copy", shape)
    assert plan.name == "tape_copy_backfill"


def test_a_derivation_from_a_reused_source_is_still_planned():
    plan = _second_worker("deals_eu", "demo_deals", "demo_deals", None, "derive")
    assert plan.name == "deals_eu_backfill"
