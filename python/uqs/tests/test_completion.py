"""Tests for tab completion (cli/completion.py).

Driven through the same protocol a shell uses - `_UQS_COMPLETE` plus
`COMP_WORDS`/`COMP_CWORD` - rather than by calling the completers directly,
because what can break is the wiring: an argument whose completer is never
reached, or a completer that sees an empty `ctx.params` because Click stopped
parsing before the word it needs.

Nothing here starts a process: every completer reads the registry, statically.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from uqs import cli
from uqs import paths as stack_paths
from uqs.model.pipelines import PROCESS_CSV_FIELDS
from uqs.model.profiles import PROFILES
from uqs.stack import listing
from uqs.stack import logs as stack_logs

runner = CliRunner()


def complete(line: str) -> list[str]:
    """What bash would offer at the end of `line`. A trailing space means
    TAB was pressed on a new, empty word."""
    words = line.split()
    cword = len(words) if line.endswith(" ") else len(words) - 1
    result = runner.invoke(
        cli.app,
        [],
        prog_name="uqs",
        env={"_UQS_COMPLETE": "complete_bash", "COMP_WORDS": line, "COMP_CWORD": str(cword)},
    )
    assert result.exit_code == 0, result.output
    return result.output.split()


def registry_procnames() -> list[str]:
    return list(listing.configured_ports(stack_paths.default_paths()))


def test_process_names_come_from_the_registry_not_a_list_of_their_own():
    """A second list would drift from process.csv the first time a pipeline
    was added, and TAB would stop offering it."""
    assert complete("uqs start ") == ["all", *registry_procnames()]


@pytest.mark.parametrize("command", ["start", "stop", "restart", "print", "logs", "multitail"])
def test_every_command_taking_processes_completes_them(command):
    assert "posbook1" in complete(f"uqs {command} pos")


def test_a_process_already_typed_is_not_offered_again():
    assert complete("uqs start posbook1 pos") == []
    assert "all" not in complete("uqs start posbook1 ")


def test_process_names_still_complete_after_an_option():
    assert complete("uqs start posbook1 --port 6050 marko") == ["markout1"]


def test_several_processes_reach_the_stack_as_one_space_separated_string(monkeypatch):
    """Each name is its own word so TAB can complete it - and `logs stp1 rdb1`,
    which the command's own docstring has always shown, now parses."""
    seen = []
    monkeypatch.setattr(
        stack_logs, "print_recent_logs", lambda _p, procs, **_kw: seen.append(procs)
    )
    assert runner.invoke(cli.app, ["logs", "stp1", "rdb1"]).exit_code == 0
    assert seen == ["stp1 rdb1"]


def test_profiles_complete_the_last_element_of_a_comma_list():
    assert complete("uqs start --profile ") == list(PROFILES)
    offered = complete("uqs start --profile fx,")
    assert "fx,arbitrage" in offered
    assert "fx,fx" not in offered


def test_list_offers_every_listable_kind():
    assert complete("uqs list ") == sorted(listing.LISTABLE_KINDS)


def test_sort_offers_the_columns_of_the_kind_already_typed():
    """The line ends in `--sort`, still waiting for its value - which is where
    Click stops parsing and leaves `processes` unassigned. The completer has to
    find it anyway, or `--sort` offers nothing."""
    assert complete("uqs list processes --sort ") == list(
        listing.list_items(stack_paths.default_paths(), "processes")[0]
    )
    assert complete("uqs list env --sort ") == ["name", "value"]


def test_config_fields_complete_after_the_process():
    assert complete("uqs config-get rdb1 ") == list(PROCESS_CSV_FIELDS)
    assert complete("uqs config-set rdb1 ") == list(PROCESS_CSV_FIELDS)


def test_a_port_is_offered_with_the_process_it_belongs_to():
    ports = listing.configured_ports(stack_paths.default_paths())
    assert ports["fxpositions1"] in complete("uqs query --port ")


def test_fixed_choices_complete():
    assert complete("uqs logs --level W") == ["WARNING"]
    assert complete("uqs multitail --stream ") == ["out", "err", "both"]
    assert complete("uqs new-job x --kind ") == ["streaming", "backfill", "normalizer"]


def test_plant_tables_complete_for_a_job_s_inputs():
    offered = complete("uqs new-job x --subscribes quote,")
    assert "quote,executions" in offered
    assert "quote,quote" not in offered


def test_a_completer_that_fails_offers_nothing_rather_than_a_traceback(monkeypatch):
    """A traceback from a completer lands in the middle of the line being
    typed. Nothing to offer is the worst a TAB may do."""

    def broken(*_a, **_kw):
        raise OSError("process.csv unreadable")

    monkeypatch.setattr(listing, "configured_ports", broken)
    assert complete("uqs start ") == []
