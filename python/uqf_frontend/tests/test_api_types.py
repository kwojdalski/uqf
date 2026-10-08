"""The browser's API types (web/src/api.ts) against the models that define them.

`api.ts` is written by hand, and it had drifted: the server's `gateway` field
could say `wrong_process`, the browser's type did not know the word, and the
header showed "Connecting" forever for exactly the misconfiguration that state
was added to explain (fixed in #638). Nothing compared the two (#637).

Generating `api.ts` from FastAPI's OpenAPI would make drift impossible, at the
cost of a code generator in the web build. This test makes it a failure
instead, with no new dependency: each interface below must declare exactly its
model's fields, and a field the model types as a `Literal` must offer exactly
the same words. A new interface for a response needs a line in PAIRS.
"""

from __future__ import annotations

import re
import types
import typing
from pathlib import Path

import pytest

from uqf_frontend import models

API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"

#: TypeScript interface -> the response model it mirrors.
PAIRS = {
    "Catalog": "CatalogResponse",
    "Column": "ColumnInfo",
    "Interval": "IntervalOut",
    "Coverage": "CoverageResponse",
    "QueryResult": "QueryResponse",
    "Health": "HealthResponse",
    "Worker": "WorkerStatusOut",
    "Backfill": "BackfillStatusResponse",
    "ControlProcess": "ControlProcessOut",
    "ControlStatus": "ControlStatusResponse",
    "CommandResult": "CommandResponse",
    "ProcessConfigResult": "ProcessConfigResponse",
    "WorkerConfigResult": "WorkerConfigResponse",
    "BackfillStarted": "BackfillStartedResponse",
}

#: What `extends Pollable` adds - every polled response carries it.
_POLLABLE = {"poll_seconds"}


def _interfaces() -> dict[str, dict[str, str]]:
    """`name -> {field: TypeScript type}`, inherited Pollable fields included."""
    found: dict[str, dict[str, str]] = {}
    pattern = r"export interface (\w+)(?: extends (\w+))? \{\n(.*?)\n\}"
    for name, parent, body in re.findall(pattern, API_TS.read_text(), re.S):
        fields = dict(re.findall(r"^  (\w+)\??: ([^;]+);", body, re.M))
        if parent == "Pollable":
            fields.update(dict.fromkeys(_POLLABLE, "number"))
        found[name] = fields
    return found


def _type_aliases() -> dict[str, str]:
    """`name -> TypeScript type` for each `export type X = ...;` in api.ts, so a
    field typed by a named union (`tier: Tier`) is compared by its words, as an
    inline union is (#820)."""
    return dict(re.findall(r"^export type (\w+) = ([^;]+);", API_TS.read_text(), re.M))


def _literal_words(annotation: object) -> set[str] | None:
    """The words a `Literal[...]` (or an optional one) allows, else None."""
    if typing.get_origin(annotation) is typing.Literal:
        return {str(a) for a in typing.get_args(annotation)}
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        for arm in typing.get_args(annotation):
            words = _literal_words(arm)
            if words is not None:
                return words
    return None


def test_every_pair_names_an_interface_and_a_model_that_exist():
    interfaces = _interfaces()
    assert set(PAIRS) <= set(interfaces), set(PAIRS) - set(interfaces)
    assert all(hasattr(models, model) for model in PAIRS.values())


@pytest.mark.parametrize(("interface", "model"), sorted(PAIRS.items()))
def test_the_interface_declares_exactly_its_models_fields(interface, model):
    ts_fields = set(_interfaces()[interface])
    py_fields = set(getattr(models, model).model_fields)
    assert ts_fields == py_fields, (
        f"{interface} (web/src/api.ts) and {model} (models.py) disagree: "
        f"only in TypeScript {sorted(ts_fields - py_fields)}, "
        f"only in the model {sorted(py_fields - ts_fields)}"
    )


@pytest.mark.parametrize(("interface", "model"), sorted(PAIRS.items()))
def test_a_literal_field_offers_the_same_words_on_both_sides(interface, model):
    """The drift that happened: a word the server sends that the browser's
    type does not have, so a branch for it can never be written."""
    ts_fields = _interfaces()[interface]
    aliases = _type_aliases()
    for name, field in getattr(models, model).model_fields.items():
        words = _literal_words(field.annotation)
        if words is None:
            continue
        ts_type = ts_fields[name].strip()
        ts_words = set(re.findall(r'"([^"]+)"', aliases.get(ts_type, ts_type)))
        assert ts_words == words, (
            f"{interface}.{name}: TypeScript allows {sorted(ts_words)}, "
            f"{model}.{name} allows {sorted(words)}"
        )
