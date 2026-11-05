"""The config-push API names one thing one way (#2108).

The push surface (#1951, #2002) had grown two names for one concept and one
name for two types, sometimes in the same response: the upload named its push
`push` where every other call says `push_id`; a restore's source was
`source_push_id` beside the manifest's `restores`; `summary.reported` was a
count and `push.reported` the list it counted; `created` was a timestamp in one
model and a list of directories in another; and each per-file result had its
own words for what happened, `uncertain` beside `unknown`.

`restores` and `created` (the timestamp) are keys in manifests already stored
on servers, so the API moved towards them. A manifest written before this
reports `uncertain`; it is read as `unknown`.
"""

from __future__ import annotations

import inspect
import re
import types
import typing
from collections import defaultdict
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from uqf_frontend import models, remote_helper
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway

API_TS = Path(__file__).resolve().parents[3] / "web" / "src" / "api.ts"

#: Field names that already have two JSON kinds in models.py, outside the
#: config-push surface this test was written for. Fix one and delete its
#: line; nothing may be added.
_KNOWN_TWO_KINDS = frozenset(
    {
        "access",
        "available",
        "declared",
        "detail",
        "dropped",
        "groups",
        "inspection",
        "key",
        "release",
    }
)


def _models() -> list[type[BaseModel]]:
    return [
        c
        for c in vars(models).values()
        if inspect.isclass(c) and issubclass(c, BaseModel) and c.__module__ == models.__name__
    ]


def _json_kinds(annotation: object) -> frozenset[str]:
    """The JSON kinds a field annotation can produce, null left out."""
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _json_kinds(typing.get_args(annotation)[0])
    if origin in (typing.Union, types.UnionType):
        return frozenset().union(*(_json_kinds(a) for a in typing.get_args(annotation)))
    if annotation is type(None):
        return frozenset()
    if origin is typing.Literal:
        return frozenset(
            "boolean" if isinstance(v, bool) else "number" if isinstance(v, int | float)
            else "string"
            for v in typing.get_args(annotation)
            if v is not None
        )  # fmt: skip
    if origin in (list, tuple, set, frozenset) or annotation in (list, tuple):
        return frozenset({"array"})
    if origin is dict or annotation is dict:
        return frozenset({"object"})
    if annotation is bool:
        return frozenset({"boolean"})
    if annotation in (int, float):
        return frozenset({"number"})
    if annotation is str:
        return frozenset({"string"})
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return frozenset({"object"})
    return frozenset({"any"})


def test_no_field_name_has_two_json_kinds():
    """`created` was a timestamp in one model and a directory list in
    another, `reported` a count beside the list it counted - in one
    response. A client reading a field by name must get one kind."""
    seen: dict[str, dict[frozenset[str], list[str]]] = defaultdict(lambda: defaultdict(list))
    for model in _models():
        for name, info in model.model_fields.items():
            kinds = _json_kinds(info.annotation)
            if "any" not in kinds:
                seen[info.alias or name][kinds].append(model.__name__)
    clashes = {name: dict(by) for name, by in seen.items() if len(by) > 1}
    new = {name: by for name, by in clashes.items() if name not in _KNOWN_TWO_KINDS}
    assert not new, f"one field name, two JSON kinds: {new}"


def test_every_per_file_result_says_its_outcome_in_one_vocabulary():
    """A delete said `deleted: bool`, a restore `succeeded|failed`, a
    reported file `uncertain|...`. Every result for one path says `outcome`,
    in FileOutcome's words."""
    for model in _models():
        fields = model.model_fields
        assert "deleted" not in fields, f"{model.__name__}.deleted: say `outcome`"
        if "path" in fields and "outcome" in fields:
            assert fields["outcome"].annotation is models.FileOutcome, model.__name__


def test_unknown_is_the_word_for_may_have_happened():
    words = set(typing.get_args(models.FileOutcome))
    assert "unknown" in words and "uncertain" not in words
    assert set(typing.get_args(models.ActivityOutcome)) >= words - {"cancelled", "invalid"}


def test_a_restore_names_the_push_it_undoes_as_the_manifest_does():
    fields = models.ConfigRestoreResponse.model_fields
    assert "restores" in fields and "source_push_id" not in fields
    assert "restores" in models.ConfigPushOut.model_fields


def test_a_reported_uncertain_is_refused_with_its_new_word():
    with pytest.raises(ValidationError, match="`uncertain` is now `unknown`"):
        models.ConfigPushReported(path="shared/config/a", outcome="uncertain")  # type: ignore[arg-type]


def test_a_reported_file_cannot_have_succeeded():
    """The server never recorded it, so the browser cannot know it landed."""
    with pytest.raises(ValidationError):
        models.ConfigPushReported(path="shared/config/a", outcome="succeeded")


def test_a_manifest_written_before_the_rename_reads_uncertain_as_unknown():
    manifest = {"reported": [{"path": "a", "outcome": "uncertain"},
                             {"path": "b", "outcome": "cancelled"}]}  # fmt: skip
    assert remote_helper.read_reported(manifest) == [
        {"path": "a", "outcome": "unknown"},
        {"path": "b", "outcome": "cancelled"},
    ]
    assert set(remote_helper.REPORTED) <= set(typing.get_args(models.FileOutcome))


def _routes() -> list[APIRoute]:
    app = create_app(gateway=FakeGateway(), settings=Settings())
    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_push_is_never_a_bare_id_parameter():
    """The upload alone named its push `push`; a client building its URL
    from the open's `push_id` had to rename it."""
    for route in _routes():
        dep = route.dependant
        names = {p.name for p in dep.query_params + dep.path_params}
        assert "push" not in names, f"{route.path} takes `push`: name it `push_id`"


def test_the_browser_never_names_a_push_id_push():
    """The same rule in api.ts: a push's id is `pushId` wherever it is a
    parameter, and the upload's query says `push_id`."""
    text = API_TS.read_text()
    assert not re.findall(r"\bpush: string\b", text)
    assert "push_id: pushId" in text


def test_the_old_upload_parameter_is_refused_with_a_migration_message():
    client = TestClient(
        create_app(
            gateway=FakeGateway(),
            settings=Settings(enable_writes=True, write_token="t" * 64),
        ),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {'t' * 64}"},
    )
    resp = client.put(
        "/services/box/files/content",
        params={"path": "shared/config/x", "push": "20261104T000000000Z-00000000"},
        content=b"x",
    )
    assert resp.status_code == 422
    assert "`push` is now `push_id`" in resp.json()["detail"]
