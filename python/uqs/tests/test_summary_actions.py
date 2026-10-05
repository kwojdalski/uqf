"""`uqs summary -i`'s start/stop/restart keys, and the browser that runs them."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from rich.table import Table
from textual.widgets import DataTable, Input

from uqs.cli import summary_actions, table_browser
from uqs.cli.table_browser import RowAction
from uqs.paths import UqsError
from uqs.stack import runtime


@dataclass
class Done:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def calls(monkeypatch, result=None):
    """Record every runtime lifecycle call instead of running torq.sh."""
    seen: list[tuple[str, str, int, bool]] = []
    for verb in ("start", "stop", "restart"):

        def fake(paths, procs, base_port, capture, verb=verb):
            seen.append((verb, procs, base_port, capture))
            return result or Done()

        monkeypatch.setattr(runtime, verb, fake)
    return seen


def action(key: str, port: int = 6050) -> RowAction:
    return next(a for a in summary_actions.process_actions(port) if a.key == key)


@pytest.mark.parametrize(
    ("key", "verb", "said"),
    [
        ("s", "start", "started rdb1"),
        ("x", "stop", "stopped rdb1"),
        ("r", "restart", "restarted rdb1"),
    ],
)
def test_each_key_runs_its_verb_on_the_rows_process(monkeypatch, key, verb, said):
    seen = calls(monkeypatch)
    assert action(key, 7000).run({"Process": "rdb1", "Status": "up"}) == said
    assert seen == [(verb, "rdb1", 7000, True)]


def test_a_failure_reports_the_last_line_torq_printed(monkeypatch):
    calls(monkeypatch, Done(returncode=1, stderr="starting\nrdb1: port 6052 in use\n"))
    with pytest.raises(UqsError, match="restart rdb1 failed: rdb1: port 6052 in use"):
        action("r").run({"Process": "rdb1"})


def test_a_table_without_a_process_column_is_refused(monkeypatch):
    seen = calls(monkeypatch)
    with pytest.raises(UqsError, match="no Process column"):
        action("s").run({"Status": "down"})
    assert seen == []


def sample(status: str = "down") -> Table:
    table = Table(title="uqs summary")
    for col in ("Process", "Status"):
        table.add_column(col)
    table.add_row("rdb1", f"[red]{status}[/]")
    table.add_row("hdb1", "[green]up[/]")
    return table


def drive(keys: list[str], actions, refresh=None):
    """Press `keys`; the app's result, the rows showing, the filter text, and
    whether the filter has focus."""
    headers, rows = table_browser.table_cells(sample())
    app = table_browser.browser("summary", headers, rows, actions, refresh)

    async def run():
        async with app.run_test() as pilot:
            for key in keys:
                await pilot.press(key)
                await app.workers.wait_for_complete()
                await pilot.pause()
            table = app.query_one(DataTable)
            shown = [[str(c) for c in table.get_row_at(i)] for i in range(table.row_count)]
            box = app.query_one(Input)
            state = (shown, box.value, box.has_focus)
            if app.is_running:
                await pilot.press("escape")
                if app.is_running:
                    await pilot.press("escape")
            return state

    shown, query, filtering = asyncio.run(run())
    return app.return_value, shown, query, filtering


def recording():
    seen: list[dict[str, str]] = []

    def run(row):
        seen.append(row)
        return f"restarted {row['Process']}"

    return seen, [RowAction("r", "Restart", run)]


def test_a_key_acts_on_the_highlighted_row():
    seen, actions = recording()
    drive(["down", "r"], actions)
    assert seen == [{"Process": "hdb1", "Status": "up"}]


def test_with_actions_letters_act_and_do_not_filter():
    seen, actions = recording()
    _, shown, query, _ = drive(["r"], actions)
    assert (len(shown), query, len(seen)) == (2, "", 1)


def test_slash_opens_the_filter_and_enter_returns_to_the_table():
    seen, actions = recording()
    _, shown, query, filtering = drive(["slash", "h", "d", "b", "enter", "r"], actions)
    assert (query, [r[0] for r in shown], filtering) == ("hdb", ["hdb1"], False)
    assert seen == [{"Process": "hdb1", "Status": "up"}]


def test_after_an_action_the_table_is_re_read():
    _, actions = recording()
    _, shown, _, _ = drive(["r"], actions, refresh=lambda: sample("up"))
    assert shown[0] == ["rdb1", "up"]


def test_a_failing_action_leaves_the_app_running():
    def run(row):
        raise UqsError("port in use")

    chosen, shown, _, _ = drive(["r", "enter"], [RowAction("r", "Restart", run)])
    assert chosen == ["rdb1", "down"]


def test_without_actions_letters_still_filter():
    _, shown, query, filtering = drive(["h", "d", "b"], [])
    assert (query, [r[0] for r in shown], filtering) == ("hdb", ["hdb1"], True)
