"""Tests for the interactive half of `uqf-stack new-process`.

test_wizard.py covers the pure helpers - pair parsing, pip sizes, the literal
q a skeleton contains. scaffold/wizard.py still sat at 31%, because everything a user
actually goes through was untested: the prompts that refuse a bad name, the
two blank skeletons, the decline paths, the check that a started process is
really up, and the four recipes end to end.

Prompts are scripted by replacing rich's Prompt/IntPrompt/FloatPrompt/Confirm
with answer queues, and every orchestrator call that would touch a real stack
is recorded instead. A test that runs out of scripted answers fails loudly
rather than hanging on stdin, so a prompt added to a flow cannot slip past.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from uqf_stack.model import plant_schema
from uqf_stack.paths import UqfStackPaths
from uqf_stack.scaffold import wizard
from uqf_stack.stack import procs as stack_procs
from uqf_stack.stack import runtime


@pytest.fixture
def paths(tmp_path: Path) -> UqfStackPaths:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (tmp_path / "scripts" / "output" / "uqf-stack" / "logs").mkdir(parents=True)
    return UqfStackPaths(
        repo_root=tmp_path,
        torqhome=tmp_path / "lib" / "torq",
        torqapphome=tmp_path / "lib" / "torq-finance-starter-pack",
        torqdata=tmp_path / "scripts" / "output" / "uqf-stack",
        scripts_dir=scripts_dir,
        orchestrator_dir=tmp_path / "python" / "uqf_stack",
    )


class Script:
    """Answer queues for each rich prompt type, and a log of what was asked."""

    def __init__(self, monkeypatch, **answers: list[Any]) -> None:
        self.queues = {k: deque(v) for k, v in answers.items()}
        self.asked: list[str] = []
        for kind in ("Prompt", "IntPrompt", "FloatPrompt", "Confirm"):
            monkeypatch.setattr(wizard, kind, self._make(kind))

    def _make(self, kind: str):
        script = self

        class _Fake:
            @staticmethod
            def ask(text: str, **kwargs: Any) -> Any:
                script.asked.append(text)
                queue = script.queues.get(kind)
                if not queue:
                    raise AssertionError(f"no scripted {kind} answer left for {text!r}")
                return queue.popleft()

        return _Fake

    def exhausted(self) -> bool:
        return all(not q for q in self.queues.values())


@pytest.fixture
def stack(monkeypatch):
    """Record every call that would touch a real stack."""
    calls: dict[str, list[Any]] = {"start": [], "registered": [], "schema": []}
    state = {"summary": "", "existing": ["rdb1", "hdb1"]}
    monkeypatch.setattr(stack_procs, "list_process_names", lambda paths: state["existing"])
    monkeypatch.setattr(stack_procs, "next_free_port_offset", lambda paths: 40)
    monkeypatch.setattr(runtime, "start", lambda paths, **kw: calls["start"].append(kw))
    monkeypatch.setattr(
        runtime, "summary", lambda paths, **kw: SimpleNamespace(stdout=state["summary"])
    )
    monkeypatch.setattr(
        stack_procs, "add_extra_process", lambda paths, row: calls["registered"].append(row)
    )
    monkeypatch.setattr(
        plant_schema, "add_extra_table_schema", lambda paths, schema: calls["schema"].append(schema)
    )
    monkeypatch.setattr(wizard.time, "sleep", lambda s: None)
    return SimpleNamespace(calls=calls, state=state)


# -------------------------------------------------------------- the prompts


def test_a_process_name_is_asked_again_until_it_is_usable(monkeypatch, paths, stack):
    """Empty, not a q identifier, and already taken are each refused - the
    name becomes a q namespace and a file name, and a clash would overwrite
    another process's registration."""
    s = Script(monkeypatch, Prompt=["", "1bad", "rdb1", "myfeed1"])
    assert wizard._prompt_procname(paths) == "myfeed1"
    assert s.exhausted()


def test_a_table_name_is_asked_again_until_it_is_an_identifier(monkeypatch):
    s = Script(monkeypatch, Prompt=["my-table", "my_table"])
    assert wizard._prompt_table_name("Table", "quotes") == "my_table"
    assert s.exhausted()


def test_pairs_are_asked_again_when_malformed_or_empty(monkeypatch):
    s = Script(monkeypatch, Prompt=["EURUSD, NOPE", "  ", "eurusd gbpusd"])
    assert wizard._prompt_pairs("Pairs", "EURUSD") == ["EURUSD", "GBPUSD"]
    assert s.exhausted()


def test_a_spot_rate_must_be_positive(monkeypatch):
    """A zero or negative starting rate would publish nonsense that every
    downstream pricing function then divides by."""
    s = Script(monkeypatch, FloatPrompt=[0, -1.2, 1.0842])
    assert wizard._prompt_spot_rates(["EURUSD"]) == {"EURUSD": 1.0842}
    assert s.exhausted()


@pytest.mark.parametrize(
    ("kind", "verb"), [("publisher", "publish into"), ("subscriber", "subscribe to")]
)
def test_the_table_prompt_names_the_direction(monkeypatch, kind, verb):
    s = Script(monkeypatch, Prompt=["quotes"])
    wizard._prompt_table(kind)
    assert verb in s.asked[0]


def test_the_port_offset_defaults_to_the_next_free_one(monkeypatch, paths, stack):
    seen: dict[str, Any] = {}

    class _Int:
        @staticmethod
        def ask(text: str, default: int) -> int:
            seen["default"] = default
            return default

    monkeypatch.setattr(wizard, "IntPrompt", _Int)
    assert wizard._prompt_port_offset(paths) == 40
    assert seen["default"] == 40


# ------------------------------------------------------- the blank skeletons


@pytest.mark.parametrize("kind", ["publisher", "subscriber"])
def test_a_blank_skeleton_is_written_with_its_names_filled_in(paths, kind):
    """The templates are str.format strings full of q braces. A brace that
    is not doubled raises KeyError the first time anyone picks that recipe -
    which, for the two blank recipes, had never happened in a test."""
    dest = wizard._write_skeleton(paths, "myproc1", kind, "quotes", 41)
    text = dest.read_text()
    assert dest.name == "torq_myproc1.q"
    assert "myproc1" in text and "quotes" in text
    # Any `{name}` left over means a placeholder the template forgot to fill.
    import re

    assert not re.search(r"\{(procname|table|port_offset)\}", text)


# ---------------------------------------------------------- _verify_running


def test_a_clean_start_that_shows_up_in_summary_passes(paths, stack):
    stack.state["summary"] = "| myfeed1 | up | 1234 |"
    assert wizard._verify_running(paths, "myfeed1", 6050) is True
    assert stack.calls["start"] == [{"procs": "myfeed1", "base_port": 6050, "capture": True}]


def test_up_is_detected_whatever_the_column_padding(paths, stack):
    stack.state["summary"] = "10:00|  myfeed1   |   UP    |  99"
    assert wizard._verify_running(paths, "myfeed1", 6050) is True


def test_a_non_empty_error_log_fails_even_if_the_process_is_up(paths, stack):
    """A process can be `up` and still have thrown during init - which is
    exactly the case a PID check alone would miss."""
    (paths.torqdata / "logs" / "err_myfeed1.log").write_text("'type in init\n")
    stack.state["summary"] = "| myfeed1 | up |"
    assert wizard._verify_running(paths, "myfeed1", 6050) is False


def test_a_process_missing_from_summary_fails(paths, stack):
    stack.state["summary"] = "| rdb1 | up |"
    assert wizard._verify_running(paths, "myfeed1", 6050) is False


def test_a_down_process_fails(paths, stack):
    stack.state["summary"] = "| myfeed1 | down |"
    assert wizard._verify_running(paths, "myfeed1", 6050) is False


# -------------------------------------------------------- the four recipes


def test_quotes_feed_end_to_end_registers_the_process_and_its_table(monkeypatch, paths, stack):
    stack.state["summary"] = "| fxq1 | up |"
    s = Script(
        monkeypatch,
        Prompt=["1", "fxq1", "EURUSD, USDJPY", "fxq"],
        FloatPrompt=[1.0842, 149.82],
        IntPrompt=[41],
        Confirm=[True, True],
    )
    wizard.run(paths, base_port=6050)
    assert s.exhausted(), "every scripted answer was asked for"

    (row,) = stack.calls["registered"]
    assert row["procname"] == "fxq1" and row["port"] == "{KDBBASEPORT}+41"
    assert row["proctype"] == "feed"
    assert stack.calls["schema"] == [wizard._quotes_table_schema("fxq")]
    assert (paths.scripts_dir / "processes" / "torq_fxq1.q").is_file()
    assert stack.calls["start"], "it was started, as confirmed"


def test_cross_etl_end_to_end_registers_a_credentialed_subscriber(monkeypatch, paths, stack):
    stack.state["summary"] = "| cross2 | up |"
    s = Script(
        monkeypatch,
        Prompt=["2", "cross2", "quotes", "EURJPY"],
        FloatPrompt=[2000000],
        IntPrompt=[42],
        Confirm=[True, True],
    )
    wizard.run(paths, base_port=6050)
    assert s.exhausted()
    (row,) = stack.calls["registered"]
    assert row["proctype"] == "metrics"
    assert "accesslist" in row["U"], "a subscriber needs credentials to reach discovery"
    assert stack.calls["schema"] == [], "an ETL adds no table schema"


@pytest.mark.parametrize(("choice", "proctype"), [("3", "feed"), ("4", "metrics")])
def test_a_blank_recipe_registers_and_runs_stage_one_checks(
    monkeypatch, paths, stack, choice, proctype
):
    stack.state["summary"] = "| blank1 | up |"
    s = Script(
        monkeypatch, Prompt=[choice, "blank1", "quotes"], IntPrompt=[43], Confirm=[True, True]
    )
    wizard.run(paths, base_port=6050)
    assert s.exhausted()
    assert stack.calls["registered"][0]["proctype"] == proctype
    assert stack.calls["start"]


@pytest.mark.parametrize(
    ("answers", "extra"),
    [
        ({"Prompt": ["1", "fxq1", "EURUSD", "fxq"], "FloatPrompt": [1.08], "IntPrompt": [41]}, {}),
        (
            {
                "Prompt": ["2", "cross2", "quotes", "EURJPY"],
                "FloatPrompt": [1e6],
                "IntPrompt": [42],
            },
            {},
        ),
        ({"Prompt": ["3", "blank1", "quotes"], "IntPrompt": [43]}, {}),
    ],
)
def test_declining_registration_registers_nothing_and_starts_nothing(
    monkeypatch, paths, stack, answers, extra
):
    """The file is kept - the user may want to edit it - but nothing is
    added to extra_processes.csv, so the next `start all` is unaffected."""
    s = Script(monkeypatch, Confirm=[False], **answers)
    wizard.run(paths, base_port=6050)
    assert s.exhausted()
    assert stack.calls["registered"] == []
    assert stack.calls["start"] == []
    assert list((paths.scripts_dir / "processes").glob("torq_*.q")), "the skeleton itself is kept"


@pytest.mark.parametrize(
    "answers",
    [
        {"Prompt": ["1", "fxq1", "EURUSD", "fxq"], "FloatPrompt": [1.08], "IntPrompt": [41]},
        {"Prompt": ["2", "cross2", "quotes", "EURJPY"], "FloatPrompt": [1e6], "IntPrompt": [42]},
        {"Prompt": ["4", "blank1", "quotes"], "IntPrompt": [43]},
    ],
)
def test_declining_to_start_registers_but_does_not_start(monkeypatch, paths, stack, answers):
    s = Script(monkeypatch, Confirm=[True, False], **answers)
    wizard.run(paths, base_port=6050)
    assert s.exhausted()
    assert len(stack.calls["registered"]) == 1
    assert stack.calls["start"] == []
