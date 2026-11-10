"""One aware instant and one window spelling across the frontend API (#2104, #2105).

The parser (`uqf_frontend.instants`), the four routes that take an instant
(/coverage, /control/backfill, /compare, /activity), and two gates: every
model field that holds an instant is typed `AwareInstant`, and no model spells
a time window two ways.
"""

from __future__ import annotations

import inspect
import secrets
import types
import typing

import pytest
from api_support import model_modules
from fastapi.testclient import TestClient
from pydantic import BaseModel

from uqf_frontend import charts, instants
from uqf_frontend.app import create_app
from uqf_frontend.config import Settings
from uqf_frontend.gateway import FakeGateway
from uqs.deploy import diff_endpoints as uqs_diff

TOKEN = secrets.token_hex(32)

# --- the parser ------------------------------------------------------------


def test_an_instant_is_read_to_the_nanosecond():
    ns = instants.nanoseconds("2026-10-14T10:00:00.000001234Z")
    assert ns % 10**9 == 1234
    assert instants.text(ns) == "2026-10-14T10:00:00.000001234Z"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20260101T100000.5Z", "2026-01-01T10:00:00.500000000Z"),
        ("2026-01-01T100000,25Z", "2026-01-01T10:00:00.250000000Z"),
        ("20260101T100000.999999999Z", "2026-01-01T10:00:00.999999999Z"),
        ("20260101T100000Z", "2026-01-01T10:00:00.000000000Z"),
    ],
)
def test_a_basic_format_fraction_is_read_not_dropped(raw, expected):
    """#2528: the fraction was matched on extended HH:MM:SS only, so a basic
    format instant reached fromisoformat whole and was floored to the second."""
    assert instants.canonical(raw) == expected


def test_a_basic_format_fraction_finer_than_a_nanosecond_is_refused():
    with pytest.raises(instants.InstantRefused, match="finer than a nanosecond"):
        instants.canonical("20260101T100000.123456789012Z")


def test_an_offset_is_converted_not_dropped():
    assert instants.canonical("2026-10-14T12:00:00+02:00") == "2026-10-14T10:00:00.000000000Z"


@pytest.mark.parametrize(
    ("raw", "said"),
    [
        ("2026-10-14T10:00:00", "needs an explicit timezone offset"),
        ("2026-10-14", "needs an explicit timezone offset"),
        ("yesterday", "is not an ISO-8601 instant"),
        ("2026-10-14T10:00:00.0000000001Z", "finer than a nanosecond"),
    ],
)
def test_what_is_not_an_aware_instant_is_refused(raw, said):
    with pytest.raises(instants.InstantRefused, match=said):
        instants.nanoseconds(raw)


def test_a_datetime_is_exact_or_refused_never_truncated():
    """#2105: `AwareDatetime` and `fromisoformat` kept six digits of nine."""
    assert instants.to_datetime("2026-10-14T10:00:00.000001Z").microsecond == 1
    with pytest.raises(instants.InstantRefused, match="finer than a microsecond"):
        instants.to_datetime("2026-10-14T10:00:00.000001234Z")


def test_q_output_without_a_zone_is_utc():
    """`.j.j` writes a timestamp with no zone; a q timestamp is UTC."""
    assert instants.from_q("2026-09-13T00:00:00.000000000") == "2026-09-13T00:00:00.000000000Z"
    assert instants.from_q("2026-09-13T02:00:00+02:00") == "2026-09-13T00:00:00.000000000Z"


# --- the four routes -------------------------------------------------------


def _client(**kw) -> TestClient:
    settings = Settings(
        enable_writes=True,
        write_token=TOKEN,
        allowed_hosts=("localhost", "testserver"),
        **kw,
    )
    return TestClient(
        create_app(gateway=FakeGateway(), settings=settings),
        base_url="http://localhost",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


NAIVE = "2026-09-11T00:00:00"
COMPARE = {
    "left": "local",
    "right": "box",
    "tables": ["trades"],
    "range_to": "2026-09-16T00:00:00Z",
}


def _refusals(client: TestClient) -> dict[str, typing.Any]:
    """Each route's answer to a range_from (or since) with no offset."""
    return {
        "coverage": client.get(
            "/coverage",
            params={
                "dataset": "fx_markout",
                "slice": "",
                "source_version": "v1",
                "range_from": NAIVE,
                "range_to": "2026-09-16T00:00:00Z",
            },
        ),  # fmt: skip
        "backfill": client.post(
            "/control/backfill",
            json={
                "worker": "w",
                "source_version": "v1",
                "range_from": NAIVE,
                "range_to": "2026-09-16T00:00:00Z",
            },
        ),  # fmt: skip
        "compare": client.post("/compare", json={**COMPARE, "range_from": NAIVE}),
        "activity": client.get("/activity", params={"since": NAIVE}),
    }


def _shape(body: typing.Any) -> typing.Any:
    """A body's structure without its values: keys, and the types under them."""
    if isinstance(body, dict):
        return {k: _shape(v) for k, v in body.items() if k not in ("input", "loc", "url")}
    if isinstance(body, list):
        return [_shape(v) for v in body[:1]]
    return type(body).__name__


def test_a_missing_offset_is_refused_the_same_way_on_every_route(tmp_path):
    """#2105: /compare answered Pydantic's list, /activity the FrontendError
    shape. Now one validator refuses on all four, so one shape and one reason."""
    answers = _refusals(_client(activity_db=tmp_path / "a.sqlite3"))
    assert {name: r.status_code for name, r in answers.items()} == dict.fromkeys(answers, 422)
    shapes = {name: _shape(r.json()) for name, r in answers.items()}
    assert len({repr(s) for s in shapes.values()}) == 1, shapes
    for name, r in answers.items():
        assert "needs an explicit timezone offset" in r.text, (name, r.text)


def test_compare_refuses_a_bound_finer_than_a_microsecond(monkeypatch):
    """#2105: it used to compare a window up to 999 ns short of the one sent,
    where `uqs data diff --from` refuses the same value. Refused here too - and
    uqs is never asked."""
    asked = []
    monkeypatch.setattr(uqs_diff, "run", lambda *a, **kw: asked.append(a))
    r = _client().post("/compare", json={**COMPARE, "range_from": "2026-09-11T00:00:00.000001234Z"})
    assert r.status_code == 422, r.text
    assert "finer than a microsecond" in r.text
    assert asked == []


# --- gates -----------------------------------------------------------------

#: Field names that hold an instant wherever they appear in a model.
INSTANT_NAMES = {
    "range_from", "range_to", "cursor", "since", "until", "time", "at",
    "mtime", "created", "closed",
}  # fmt: skip

#: A field with an instant's name that does not hold one, and what it holds.
NOT_INSTANTS = {
    ("RemoteUploadResponse", "created"): "the directories the upload created",
}

#: The ways a time window has been spelled (#2104). Only the first is a
#: window in a model; `since`/`until` are /activity's independent filter
#: bounds, which are query parameters and never a model's.
WINDOW_SPELLINGS = {
    "range_from/range_to": {"range_from", "range_to"},
    "from/to": {"from", "to"},
    "since/until": {"since", "until"},
}


def _all_models() -> list[type[BaseModel]]:
    found = []
    for module in (*model_modules(), charts):
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == module.__name__:
                found.append(cls)
    return found


def _holds_instant(name: str) -> bool:
    return name in INSTANT_NAMES or name.endswith("_at")


def _is_aware_instant(annotation: typing.Any) -> bool:
    shared = (instants.AwareInstant, instants.AwareInstantOrBlank)
    if annotation in shared:
        return True
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return len(args) == 1 and args[0] in shared
    return False


def _annotation(cls: type[BaseModel], name: str) -> typing.Any:
    return typing.get_type_hints(cls, include_extras=True)[name]


def test_every_model_field_that_holds_an_instant_is_an_aware_instant():
    """The gate for #2105: an instant field typed `str`, `float`, `datetime` or
    `AwareDatetime` is a fifth parser, or none."""
    assert len(_all_models()) > 50, "the models were found"
    wrong = [
        f"{cls.__name__}.{name}: {_annotation(cls, name)}"
        for cls in _all_models()
        for name in cls.model_fields
        if _holds_instant(name)
        and (cls.__name__, name) not in NOT_INSTANTS
        and not _is_aware_instant(_annotation(cls, name))
    ]
    assert wrong == [], "type these instants.AwareInstant:\n" + "\n".join(wrong)


def test_no_model_types_a_field_as_a_datetime_or_an_epoch():
    """Every instant on the wire is text with a zone (#2104): `mtime` was the
    one float of epoch seconds."""
    from datetime import datetime

    from pydantic import AwareDatetime

    wrong = [
        f"{cls.__name__}.{name}"
        for cls in _all_models()
        for name in cls.model_fields
        if any(t in (datetime, AwareDatetime) for t in _leaves(_annotation(cls, name)))
    ]
    assert wrong == []


def _leaves(annotation: typing.Any) -> list[typing.Any]:
    args = typing.get_args(annotation)
    if not args:
        return [annotation]
    return [leaf for a in args for leaf in _leaves(a)]


def _wire_names(cls: type[BaseModel], seen: set[type] | None = None) -> set[str]:
    """Every key `cls` puts on the wire, its nested models' included."""
    seen = set() if seen is None else seen
    if cls in seen:
        return set()
    seen.add(cls)
    names = set()
    for name, f in cls.model_fields.items():
        names.add(f.alias or name)
        for leaf in _leaves(_annotation(cls, name)):
            if inspect.isclass(leaf) and issubclass(leaf, BaseModel):
                names |= _wire_names(leaf, seen)
    return names


def test_no_model_spells_a_time_window_two_ways():
    """#2104: one /compare document said `from`/`to` at the top and
    `range_from`/`range_to` in its intervals, and a client could not send back
    the window it was given."""
    mixed = {}
    for cls in _all_models():
        names = _wire_names(cls)
        used = sorted(s for s, keys in WINDOW_SPELLINGS.items() if keys & names)
        if len(used) > 1:
            mixed[cls.__name__] = used
    assert mixed == {}


def test_a_window_in_a_model_is_range_from_range_to():
    """`range_from`/`range_to` won (7 models against 1, #2104); neither other
    spelling names a model field, aliased or not."""
    others = WINDOW_SPELLINGS["from/to"] | WINDOW_SPELLINGS["since/until"]
    found = [
        f"{cls.__name__}.{name}"
        for cls in _all_models()
        for name, f in cls.model_fields.items()
        if {name, f.alias} & others
    ]
    assert found == []
