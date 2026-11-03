"""Help that names an option's values, rendered from the tuple that declares
them (#1893).

An option whose help lists its values - `upsert, replace, ignore, append or
fail` - used to type them by hand beside the tuple the command validates
against, so a value q added was accepted and never shown, and a default
changed in one place went on being advertised in the other. `alternatives`
renders the list from the authority itself; test_cli_conventions.py holds
every such option to its source.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


def alternatives(
    values: Iterable[str],
    notes: Mapping[str, str] | None = None,
    *,
    default: str | None = None,
    quote: str = "",
) -> str:
    """`a, b or c`, each value followed by its note in parentheses, and
    `default` marked "(the default)". A note for a value not in `values` is
    refused: it would describe a value the option does not take."""
    values = tuple(values)
    notes = dict(notes or {})
    if stray := sorted(set(notes) - set(values)):
        raise ValueError(f"notes for values the option does not take: {stray}")
    if default is not None and default not in values:
        raise ValueError(f"default {default!r} is not one of {values}")

    def one(value: str) -> str:
        said = [n for n in (notes.get(value, ""), "the default" if value == default else "") if n]
        return f"{quote}{value}{quote}" + (f" ({'; '.join(said)})" if said else "")

    words = [one(v) for v in values]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} or {words[-1]}"
