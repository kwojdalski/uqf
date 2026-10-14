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

Requests are held the same way (#1361). Pydantic ignores a key it does not
know and fills a default for one it does not get, so a field renamed on one
side used to return 200: a renamed `procs` made Stop read as `procs="all"`
and stop the whole fleet. Each body the browser sends is therefore paired
with its request model here too, and the request models forbid extra keys
(a stale field is a 422, not a default). The process-health rows the Fleet
and Control views read by key are paired as well, since a renamed `up` showed
every process as Down.
"""

from __future__ import annotations

import re
import types
import typing
from pathlib import Path

import pytest

from uqf_frontend import models

API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"

#: TypeScript interface -> the request model it is sent to (#1361).
REQUESTS = {
    "QueryInput": "QueryRequest",
    "QueryFilter": "Filter",
    "CoverageRequest": "CoverageRequirement",
    "LifecycleBody": "LifecycleRequest",
    "ProcessConfigBody": "ProcessConfigRequest",
    "WorkerConfigBody": "WorkerConfigRequest",
    "BackfillBody": "BackfillRequest",
}

#: TypeScript interface -> the model it mirrors: every response, the
#: process-health row the browser reads by key, and every request.
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
    "ProcessHealth": "ProcessHealthOut",
    **REQUESTS,
}

#: What `extends Pollable` adds - every polled response carries it.
_POLLABLE = {"poll_seconds"}


def _declared(text: str) -> dict[str, dict[str, tuple[str, bool]]]:
    """`name -> {field: (TypeScript type, optional)}` for each interface in
    `text`, inherited Pollable fields included."""
    found: dict[str, dict[str, tuple[str, bool]]] = {}
    pattern = r"export interface (\w+)(?: extends (\w+))? \{\n(.*?)\n\}"
    for name, parent, body in re.findall(pattern, text, re.S):
        fields = {
            field: (ts_type, mark == "?")
            for field, mark, ts_type in re.findall(r"^  (\w+)(\??): ([^;]+);", body, re.M)
        }
        if parent == "Pollable":
            fields.update(dict.fromkeys(_POLLABLE, ("number", False)))
        found[name] = fields
    return found


def _interfaces(text: str | None = None) -> dict[str, dict[str, str]]:
    """`name -> {field: TypeScript type}`, inherited Pollable fields included."""
    declared = _declared(API_TS.read_text() if text is None else text)
    return {name: {f: t for f, (t, _) in fields.items()} for name, fields in declared.items()}


def _wire_fields(model: str) -> dict[str, typing.Any]:
    """`name on the wire -> FieldInfo`: the alias where the model has one, so
    `ProcessConfigRequest.field_name` is compared as the `field` it is sent as."""
    return {
        (info.alias or name): info for name, info in getattr(models, model).model_fields.items()
    }


def _disagreement(interface: str, model: str, text: str | None = None) -> str | None:
    """Why `interface` and `model` disagree on their field names, or None."""
    ts_fields = set(_interfaces(text)[interface])
    py_fields = set(_wire_fields(model))
    if ts_fields == py_fields:
        return None
    return (
        f"{interface} (web/src/api.ts) and {model} (models.py) disagree: "
        f"only in TypeScript {sorted(ts_fields - py_fields)}, "
        f"only in the model {sorted(py_fields - ts_fields)}"
    )


def _type_aliases() -> dict[str, str]:
    """`name -> TypeScript type` for each `export type X = ...;` in api.ts, so a
    field typed by a named union (`tier: Tier`) is compared by its words, as an
    inline union is (#820)."""
    return dict(re.findall(r"^export type (\w+) =\s*([^;]+);", API_TS.read_text(), re.M))


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
    problem = _disagreement(interface, model)
    assert problem is None, problem


def test_a_field_renamed_in_a_request_type_is_caught():
    """The failure #1361 describes, staged: `procs` renamed in the browser's
    lifecycle body. Before requests were paired this compiled, passed, and
    the server read the missing field as "all"."""
    renamed = API_TS.read_text().replace("  procs: string;", "  selector: string;")
    assert renamed != API_TS.read_text(), "LifecycleBody.procs is not declared as expected"
    problem = _disagreement("LifecycleBody", "LifecycleRequest", renamed)
    assert problem is not None
    assert "only in TypeScript ['selector']" in problem
    assert "only in the model ['procs']" in problem


@pytest.mark.parametrize(("interface", "model"), sorted(REQUESTS.items()))
def test_a_field_the_request_model_requires_is_not_optional_in_typescript(interface, model):
    """A required request field the browser's type marks optional can be left
    out by a caller that compiles - the model then refuses, but only at run
    time. `?` is allowed only where the model has a default."""
    declared = _declared(API_TS.read_text())[interface]
    for name, info in _wire_fields(model).items():
        if info.is_required():
            assert not declared[name][1], f"{interface}.{name} is optional; {model} requires it"


@pytest.mark.parametrize("model", sorted(REQUESTS.values()))
def test_every_request_model_refuses_a_key_it_does_not_know(model):
    """A stale field must be a 422, not silently dropped in favour of a default."""
    assert getattr(models, model).model_config.get("extra") == "forbid", model


def test_a_lifecycle_request_has_no_default_selector():
    """A destructive action is never the default: no `procs`, no request."""
    assert models.LifecycleRequest.model_fields["procs"].is_required()


@pytest.mark.parametrize(("interface", "model"), sorted(PAIRS.items()))
def test_a_literal_field_offers_the_same_words_on_both_sides(interface, model):
    """The drift that happened: a word the server sends that the browser's
    type does not have, so a branch for it can never be written."""
    ts_fields = _interfaces()[interface]
    aliases = _type_aliases()
    for name, field in _wire_fields(model).items():
        words = _literal_words(field.annotation)
        if words is None:
            continue
        ts_type = ts_fields[name].strip()
        ts_words = set(re.findall(r'"([^"]+)"', aliases.get(ts_type, ts_type)))
        assert ts_words == words, (
            f"{interface}.{name}: TypeScript allows {sorted(ts_words)}, "
            f"{model}.{name} allows {sorted(words)}"
        )
