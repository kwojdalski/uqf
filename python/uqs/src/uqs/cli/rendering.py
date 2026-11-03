"""How the CLI renders: its console, and its help (#1883).

Two policies every command shares, apart from `shared` so that module stays a
registry of the app and its options:

- the console prints a table and a line whole into a pipe - neither cut to an
  80-column guess nor wrapped - and to a terminal as Rich would;
- help is text, not Rich markup, so a bracketed word in it survives.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text
from typer import rich_utils

#: How wide a table may grow when stdout is not a terminal: past any row, so
#: nothing is cut to fit.
UNBOUNDED_WIDTH = 10_000


def console() -> Console:
    """The console every command prints through.

    On a terminal it is Rich's own: as wide as the window, wrapping to fit.
    Piped or captured - `uqs status | grep`, `$(uqs config show ...)` - an
    80-column guess cut `discovery1` to `dis…` and broke a path over three
    lines, so a pipe gets no width to fit: a table is as wide as its widest
    row, and a line is never wrapped."""
    if sys.stdout.isatty():
        return Console()
    return Console(width=UNBOUNDED_WIDTH, soft_wrap=True)


_rich_help_text = rich_utils._make_rich_text


def _literal_help_text(
    *, text: str, style: str = "", markup_mode: rich_utils.MarkupModeStrict
) -> Markdown | Text:
    """Typer's help renderer, reading help as text rather than Rich markup.

    Typer parses every help string as markup, so a bracketed word vanished:
    `"silent": [process]` rendered as `"silent": ,`, `findings: [text]}]` as
    `findings: }]`, and `[defaults]` was gone from `target unset` - the JSON
    shapes `--help` documents were wrong. No help here means markup,
    so all of it is escaped; the panels and colours Typer adds itself stay.
    test_cli_conventions holds the rendered help to every `[` in the source."""
    if markup_mode == rich_utils.MARKUP_MODE_RICH:
        text = escape(text)
    return _rich_help_text(text=text, style=style, markup_mode=markup_mode)


# typer has no hook for this; replacing its renderer is the one seam (the test pins it)
rich_utils._make_rich_text = _literal_help_text  # ty: ignore[invalid-assignment]
