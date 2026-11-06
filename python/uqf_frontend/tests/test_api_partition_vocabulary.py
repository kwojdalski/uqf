"""In the API, "partition" means only a date partition (#2261).

`uqs data diff` and `uqs job new` call the slice of a dataset that coverage is
recorded under `--slice`, and keep "partition" for an HDB date partition
(#2239, test_cli_conventions.py's WORDS). The frontend API said `partition`
for the slice on /compare, /coverage and /query's `require_coverage`, so one
concept had two names depending on the client. It is `slice` on every route
now, and these tests hold the API to the CLI's vocabulary:

- no request or response field, and no query or path parameter, is named
  "partition", and a `slice` is described as the coverage slice;
- every text the API publishes (its OpenAPI document) that says "partition"
  means a date one;
- each surface that takes the slice refuses the old name with a 422 naming
  `slice`, rather than reading it as the slice or as a missing one.

The coverage ledger's q column is still `partition`: the API maps `slice` onto
it at the q boundary rather than migrating the ledger file (#2261). That
boundary is held in test_api.py and test_compare.py.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from uqf_frontend import models
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway

#: What a use of "partition" must say to mean a date partition: the CLI's
#: pattern (#2239), plus the HDB's "completed partitions".
DATE_PARTITION = re.compile(r"\b(date|day|hdb|completed)\b", re.I)


def _spec() -> dict[str, Any]:
    return create_app(gateway=FakeGateway({}), settings=Settings()).openapi()


def _names(spec: dict[str, Any]) -> Iterator[tuple[str, str, str]]:
    """`(where, name, description)` for every schema property and every
    route parameter the API publishes."""
    for model, schema in spec["components"]["schemas"].items():
        for name, prop in (schema.get("properties") or {}).items():
            yield f"schema {model}", name, prop.get("description", "")
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            for param in op.get("parameters", []):
                where = f"{method.upper()} {path} ({param['in']})"
                yield where, param["name"], param.get("description", "")


def _texts(node: object, at: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _texts(value, f"{at}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _texts(value, f"{at}/{i}")
    elif isinstance(node, str):
        yield at, node


def _partition_named(spec: dict[str, Any]) -> list[str]:
    return [f"{where}: {name}" for where, name, _ in _names(spec) if "partition" in name.lower()]


def test_no_field_or_parameter_is_named_partition():
    spec = _spec()
    assert sum(1 for _ in _names(spec)) > 300, "the scan found too few names to mean anything"
    assert _partition_named(spec) == []


def test_every_slice_is_the_coverage_slice():
    slices = [(where, desc) for where, name, desc in _names(_spec()) if name == "slice"]
    assert {w for w, _ in slices} >= {
        "schema CompareRequest",
        "schema CompareResponse",
        "schema CoverageRequirement",
        "GET /coverage (query)",
    }
    for where, desc in slices:
        assert re.search(r"\bslice\b", desc) and "partition" not in desc, (where, desc)


def test_every_text_saying_partition_means_a_date_partition():
    """A description or route docstring that says "partition" for the slice
    names the concept the CLI retired: it says "slice" instead."""
    stray = [
        (at, text)
        for at, text in _texts(_spec())
        if re.search(r"partition", text, re.I) and not DATE_PARTITION.search(text)
    ]
    assert stray == []


def test_the_gate_catches_a_field_named_partition():
    """The scan itself, staged: CompareRequest's slice under its old name."""
    spec = _spec()
    props = spec["components"]["schemas"]["CompareRequest"]["properties"]
    props["partition"] = props.pop("slice")
    assert _partition_named(spec) == ["schema CompareRequest: partition"]
    # and the old description, which said partition for the slice
    assert not DATE_PARTITION.search(
        'required with source_version; "" for a dataset with no partition dimension'
    )


# --------------------------------------------- the old name is refused (#2261)


def _models_with_a_slice() -> list[type]:
    return [
        obj
        for obj in vars(models).values()
        if isinstance(obj, type)
        and issubclass(obj, models.BaseModel)
        and obj.__module__ == models.__name__
        and obj.model_config.get("extra") == "forbid"
        and "slice" in obj.model_fields
    ]


def test_the_request_models_taking_a_slice_are_the_ones_this_gate_knows():
    assert {m.__name__ for m in _models_with_a_slice()} == {
        "CompareRequest",
        "CoverageRequirement",
    }


@pytest.mark.parametrize("model", _models_with_a_slice(), ids=lambda m: m.__name__)
def test_a_request_model_refuses_partition_naming_slice(model):
    with pytest.raises(ValueError, match="`partition` is now `slice`"):
        model.model_validate({"partition": ""})


@pytest.mark.parametrize(
    ("method", "path", "kw"),
    [
        ("GET", "/coverage", {"params": {"dataset": "t", "partition": "", "source_version": "v"}}),
        (
            "POST",
            "/query",
            {
                "json": {
                    "table": "trades",
                    "require_coverage": {
                        "dataset": "trades",
                        "partition": "",
                        "source_version": "v1",
                        "range_from": "2026-09-13T00:00:00Z",
                        "range_to": "2026-09-16T00:00:00Z",
                    },
                }
            },
        ),
        (
            "POST",
            "/compare",
            {
                "json": {
                    "left": "local",
                    "right": "box",
                    "tables": ["t"],
                    "range_from": "2026-09-13T00:00:00Z",
                    "range_to": "2026-09-16T00:00:00Z",
                    "source_version": "v1",
                    "partition": "",
                }
            },
        ),
    ],
)
def test_each_route_taking_a_slice_refuses_partition_with_a_422_naming_slice(method, path, kw):
    c = TestClient(create_app(gateway=FakeGateway({}), settings=Settings()))
    resp = c.request(method, path, **kw)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "RequestInvalid"
    assert "`partition` is now `slice`" in resp.json()["detail"]
    assert "a partition in this API is only a date partition" in resp.json()["detail"]
