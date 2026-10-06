"""--interactive's browser, driven headless through Textual's pilot."""

from __future__ import annotations

import asyncio

import pytest
from rich.console import Console
from rich.table import Table
from textual.widgets import DataTable

from uqs.cli import table_browser
from uqs.paths import UqsError


def sample() -> Table:
    table = Table(title="processes (3)")
    for col in ("Process", "Status"):
        table.add_column(col)
    table.add_row("rdb1", "[green]up[/]")
    table.add_row("hdb1", "[red]down[/]")
    table.add_row("uqs_plant_q", "[green]up[/]")
    return table


def drive(keys: list[str]) -> tuple[list[str] | None, int]:
    """Press `keys` in a browser over sample(); its result and the rows left showing."""
    headers, rows = table_browser.table_cells(sample())
    app = table_browser.browser("processes", headers, rows)

    async def run() -> int:
        async with app.run_test() as pilot:
            await pilot.press(*keys)
            await pilot.pause()
            shown = app.query_one(DataTable).row_count
            if app.is_running:
                await pilot.press("escape")
            return shown

    shown = asyncio.run(run())
    return app.return_value, shown


def test_cells_are_read_back_as_plain_text_without_markup():
    headers, rows = table_browser.table_cells(sample())
    assert headers == ["Process", "Status"]
    assert [[cell.plain for cell in row] for row in rows] == [
        ["rdb1", "up"],
        ["hdb1", "down"],
        ["uqs_plant_q", "up"],
    ]


def test_typing_filters_the_rows():
    _, shown = drive(["u"])
    assert shown == 2


def test_typing_matches_markup_free_text():
    # "green" is in the markup of two cells, and in the text of none.
    _, shown = drive(list("green"))
    assert shown == 0


def test_enter_returns_the_highlighted_filtered_row():
    chosen, _ = drive(["d", "o", "w", "n", "enter"])
    assert chosen == ["hdb1", "down"]


def test_down_then_enter_picks_the_next_row():
    chosen, _ = drive(["down", "enter"])
    assert chosen == ["hdb1", "down"]


def test_escape_returns_nothing():
    chosen, _ = drive(["escape"])
    assert chosen is None


def test_matched_characters_are_highlighted():
    cell = table_browser.highlighted(table_browser.as_text("[green]up[/]"), {0})
    styles = [str(span.style) for span in cell.spans if span.start == 0 and span.end == 1]
    assert table_browser.MATCH_STYLE in styles


def test_off_a_terminal_interactive_is_refused():
    # pytest captures stdout, so this runs off a terminal as a pipe would.
    with pytest.raises(UqsError, match="needs a terminal"):
        table_browser.show(sample(), True, Console())


def test_without_the_flag_the_table_is_printed():
    console = Console(record=True, width=80)
    table_browser.show(sample(), False, console)
    assert "uqs_plant_q" in console.export_text()


def test_every_re_reads_the_table_on_a_timer_keeping_the_filter():
    reads = []

    def refresh() -> Table:
        reads.append(1)
        table = sample()
        table.add_row("rdb2", "[green]up[/]")
        return table

    headers, rows = table_browser.table_cells(sample())
    app = table_browser.browser("processes", headers, rows, refresh=refresh, every=0.05)

    async def run() -> tuple[int, str]:
        async with app.run_test() as pilot:
            await pilot.press("r", "d", "b")
            count = ""
            for _ in range(50):
                await pilot.pause(0.05)
                count = str(app.query_one("#count").render())
                if "refreshed " in count:
                    break
            shown = app.query_one(DataTable).row_count
            await pilot.press("escape")
            return shown, count

    shown, count = asyncio.run(run())
    assert reads, "the timer never re-read the table"
    assert shown == 2  # rdb1 and the new rdb2, still filtered by "rdb"
    assert "refreshed" in count and "every 0.05s" in count


def test_without_every_nothing_re_reads_on_its_own():
    reads = []

    def refresh() -> Table:
        reads.append(1)
        return sample()

    headers, rows = table_browser.table_cells(sample())
    app = table_browser.browser("processes", headers, rows, refresh=refresh)

    async def run() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            await pilot.press("escape")

    asyncio.run(run())
    assert reads == []
