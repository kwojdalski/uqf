"""`--interactive`: browse a table in a Textual app, filtering its rows as you type.

Every table-printing command builds a Rich `Table` and hands it to `show`,
which prints it - or, with `--interactive`, opens it here. Reading the cells
back off the Rich table, rather than asking each command for its rows again,
keeps one rendering per command: the colours and dimming a command chose
(`down` in red, a configured port dimmed) are what the browser shows too.

Typing filters (see table_filter.py); up/down and page keys move through the
rows; Enter exits and prints the highlighted row, tab-separated, so a run id
or a process name can be picked and piped; Escape exits printing nothing.

Textual is imported only when a browser actually opens, so the plain path -
every command without the flag - starts no slower than before.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console, RenderableType
from rich.table import Table
from rich.text import Text

from uqs.cli.table_filter import Hits, filter_rows
from uqs.paths import UqsError

if TYPE_CHECKING:
    from textual.app import App

MATCH_STYLE = "bold reverse"


def as_text(cell: RenderableType) -> Text:
    """A Rich table cell as styled text: markup strings parsed, Text copied."""
    if isinstance(cell, Text):
        return cell.copy()
    if isinstance(cell, str):
        return Text.from_markup(cell)
    return Text(str(cell))


def table_cells(table: Table) -> tuple[list[str], list[list[Text]]]:
    """A Rich table's headers and rows, read back off its columns."""
    headers = [str(column.header) for column in table.columns]
    columns = [list(column.cells) for column in table.columns]
    rows = [
        [as_text(cells[index]) if index < len(cells) else Text() for cells in columns]
        for index in range(table.row_count)
    ]
    return headers, rows


def highlighted(cell: Text, positions: set[int] | None) -> Text:
    """The cell with the characters a filter matched on picked out."""
    if not positions:
        return cell
    shown = cell.copy()
    for position in positions:
        shown.stylize(MATCH_STYLE, position, position + 1)
    return shown


def browser(title: str, headers: list[str], rows: list[list[Text]]) -> App[list[str] | None]:
    """The Textual app for one table. Its result is the chosen row's cells, or None."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.widgets import DataTable, Footer, Input, Static

    plain = [[cell.plain for cell in row] for row in rows]

    class TableBrowser(App[list[str] | None]):
        TITLE = title
        CSS = "#count { height: 1; color: $text-muted; padding: 0 1; }"
        # priority: the filter box keeps focus throughout, and would otherwise
        # take Enter as its own submit and the arrows as nothing at all.
        BINDINGS = [
            Binding("escape", "quit_browser", "Quit"),
            Binding("enter", "choose", "Print row", priority=True),
            Binding("down", "move(1)", "Down", show=False, priority=True),
            Binding("up", "move(-1)", "Up", show=False, priority=True),
            Binding("pagedown", "move(20)", show=False, priority=True),
            Binding("pageup", "move(-20)", show=False, priority=True),
        ]

        def compose(self) -> ComposeResult:
            yield Input(placeholder="filter: type to fuzzy-match any cell, space for AND")
            yield Static(id="count")
            yield DataTable(zebra_stripes=True, cursor_type="row")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one(DataTable).add_columns(*headers)
            self.refill("")
            self.query_one(Input).focus()

        def on_input_changed(self, event: Input.Changed) -> None:
            self.refill(event.value)

        def refill(self, query: str) -> None:
            table = self.query_one(DataTable)
            table.clear()
            kept: list[tuple[int, Hits]] = filter_rows(plain, query)
            for index, hits in kept:
                cells = [highlighted(cell, hits.get(col)) for col, cell in enumerate(rows[index])]
                table.add_row(*cells, key=str(index), height=None)
            self.query_one("#count", Static).update(f"{len(kept)} of {len(rows)} rows")

        def action_move(self, delta: int) -> None:
            table = self.query_one(DataTable)
            if table.row_count:
                row = min(max(table.cursor_row + delta, 0), table.row_count - 1)
                table.move_cursor(row=row)

        def action_choose(self) -> None:
            table = self.query_one(DataTable)
            if not table.row_count:
                return
            key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
            self.exit(plain[int(key or 0)])

        def action_quit_browser(self) -> None:
            self.exit(None)

    return TableBrowser()


def show(table: Table, interactive: bool, console: Console) -> None:
    """Print `table`, or with `interactive` browse it and print the row chosen.

    Refused off a terminal: a browser cannot draw into a pipe, and a script
    that passed the flag by mistake should fail rather than hang.
    """
    if not interactive:
        console.print(table)
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise UqsError("--interactive needs a terminal; drop it to print the table")
    headers, rows = table_cells(table)
    chosen = browser(str(table.title or ""), headers, rows).run()
    if chosen is not None:
        console.print("\t".join(chosen), markup=False, highlight=False, soft_wrap=True)
