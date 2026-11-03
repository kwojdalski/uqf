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
from enum import StrEnum
from typing import Annotated, Any

import typer


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


def ChoiceOpt(  # noqa: N802 - an Annotated alias, named like the others
    values: Iterable[str],
    flag: str,
    help: str,  # noqa: A002 - typer's own name for it
    *,
    notes: Mapping[str, str] | None = None,
    default: str | None = None,
    required: bool = False,
    case_sensitive: bool = True,
    short: str | None = None,
    metavar: str | None = None,
    many: bool = False,
    envvar: str | None = None,
) -> Any:
    """An option taking one of `values` (#1884): its type, its completion and
    the values its help names, all from the one tuple. `help` names them where
    it says `{values}`, else they follow it; `notes` describe some, and
    `default` is marked - pass the same value as the parameter's default.
    `many` makes it repeatable; `envvar` names the variable it falls back to,
    which the help then names too.

    The type is a Choice (a StrEnum, which Typer turns into one), so a value
    outside `values` is refused by Click before the command runs, and the
    command receives the value as a str."""
    values = tuple(values)
    listed = alternatives(values, notes, default=default)
    text = help.format(values=listed) if "{values}" in help else f"{help}: {listed}"
    if envvar:
        text += f". Default from ${envvar}"
    enum = StrEnum(_enum_name(flag), {f"V{i}": v for i, v in enumerate(values)})  # ty: ignore
    option = typer.Option(
        flag,
        *((short,) if short else ()),
        help=text,
        case_sensitive=case_sensitive,
        show_default=False,
        envvar=envvar,
        # the values are in the help: a metavar listing them too crowds it out
        metavar=metavar or flag.lstrip("-").upper().replace("-", "_"),
    )
    kind = list[enum] if many else enum  # ty: ignore[invalid-type-form]
    # built at run time from `values`, so no static type can name it
    return Annotated[kind if required else kind | None, option]  # ty: ignore[invalid-type-form]


def _enum_name(flag: str) -> str:
    return "".join(w.capitalize() for w in flag.lstrip("-").split("-")) + "Choice"
