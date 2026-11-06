"""The HTTP API's naming conventions, held so an inconsistency fixed once
cannot come back (#2227, #2230, #2232, #2234, #2236, #2244).

test_api_types.py holds web/src/api.ts to models.py field by field; this file
holds the API to itself: one wire name per request field, one meaning per
word, one shape per kind of route, and one rule for what must be confirmed.
"""

from __future__ import annotations

import pydantic
import pytest

from uqf_frontend import models


def _models() -> dict[str, type[pydantic.BaseModel]]:
    return {
        name: obj
        for name, obj in vars(models).items()
        if isinstance(obj, type)
        and issubclass(obj, pydantic.BaseModel)
        and obj.__module__ == models.__name__
    }


def _request_models() -> dict[str, type[pydantic.BaseModel]]:
    """Every model that refuses a key it does not know - the request models
    (models.STRICT_REQUEST, #1361) and the Settings view's bodies."""
    return {n: m for n, m in _models().items() if m.model_config.get("extra") == "forbid"}


# --------------------------------------------------- one wire name (#2227)


def test_the_scan_finds_the_request_models():
    assert {"ProcessConfigRequest", "WorkerConfigRequest", "LifecycleRequest"} <= set(
        _request_models()
    )


@pytest.mark.parametrize("name", sorted(_request_models()))
def test_a_request_field_has_one_wire_name(name):
    """An alias with populate_by_name accepted two spellings of one field -
    the alias the repository rule forbids (#2227). A request field is sent
    by its own name, and only by it."""
    model = _request_models()[name]
    assert not model.model_config.get("populate_by_name"), name
    aliased = {f: i.alias for f, i in model.model_fields.items() if i.alias not in (None, f)}
    assert aliased == {}, f"{name}: {aliased}"


def test_process_config_refuses_the_old_spelling_by_name():
    with pytest.raises(pydantic.ValidationError, match="`field_name` is `field`"):
        models.ProcessConfigRequest.model_validate({"field_name": "port", "value": "1"})
    assert (
        models.ProcessConfigRequest.model_validate({"field": "port", "value": "1"}).field == "port"
    )
