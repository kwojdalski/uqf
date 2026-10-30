"""q's type characters, type names and attributes: one authority (#1785).

uqs.generated.q_facts carries them, exported by q (scripts/generate/
export_q_facts.q). The attachment check, the schema view, the plant metadata
and the scaffold derive their lookups from it and add only what is theirs: a
presentation label, the subset of types they support, the scaffold's `list`.
These tests hold the authority to a running q, every consumer to the
authority, and each consumer's refusals of what it does not support.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from uqs.attach import schema as attach
from uqs.checks.schema_view import ATTR_NAMES, TYPE_NAMES, type_name
from uqs.generated.q_facts import Q_ATTRIBUTES, Q_TYPE_CHARS, Q_TYPE_NAMES
from uqs.interpreter import q_interpreter
from uqs.model import plant_metadata
from uqs.paths import UqsError
from uqs.scaffold.columns import TYPES, parse_columns, type_char

# --------------------------------------------------------------- the authority


def test_the_type_vocabulary_is_what_q_casts_and_reports():
    """Each name casts, in a running q, to the type whose `.Q.t` character it
    is paired with; every character `.Q.t` has is named; `attr` reports the
    attributes in the order q_facts carries them."""
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    names = "`" + "`".join(Q_TYPE_NAMES.values())
    script = (
        f"n:{names};\n"
        "-1 .j.j `cast`chars`attrs!("
        "n!{[x] enlist .Q.t type x$()} each n;"
        'asc distinct .Q.t except " ";'
        "string attr each (`s#0 1;`u#0 1;`p#0 0 1;`g#0 1));\n"
        "exit 0\n"
    )
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    out = subprocess.run(
        [str(q), "-q"],
        input=script,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    lines = out.stdout.strip().splitlines()
    assert lines and lines[-1].startswith("{"), (out.stdout, out.stderr)
    found = json.loads(lines[-1])
    assert found["cast"] == Q_TYPE_CHARS
    assert sorted(found["chars"]) == sorted(Q_TYPE_NAMES)
    assert tuple(found["attrs"]) == Q_ATTRIBUTES


def test_the_two_directions_are_one_map():
    assert {n: c for c, n in Q_TYPE_NAMES.items()} == Q_TYPE_CHARS
    assert len(Q_TYPE_NAMES) == len(Q_TYPE_CHARS) == 18
    assert all(c.islower() and len(c) == 1 for c in Q_TYPE_NAMES)
    assert " " not in Q_TYPE_NAMES, "a general list is each consumer's own overlay"


# ------------------------------------------------- consumers: derived lookups


@pytest.mark.parametrize(("char", "name"), sorted(Q_TYPE_NAMES.items()))
def test_every_q_type_round_trips_through_every_full_consumer(char, name):
    """name -> char (attach), char -> name (plant metadata, schema view), and
    the q a schema file holds back to the same char."""
    assert attach.TYPE_CHARS[name] == char
    assert plant_metadata.Q_TYPES[char] == name
    assert type_name(char) == name
    assert type_name(char.upper()) == f"{name} vector"
    spec = attach.parse_definition(f"t:([] time:`timestamp$(); x:`{name}$())")
    assert spec.columns[1] == ("x", char)
    assert attach.parse_definition(spec.definition()) == spec
    column = plant_metadata.Column("x", char, "")
    table = plant_metadata.Table("t", False, (column,), {}, ())
    assert table.definition() == f"t:([]x:`{name}$())"


def test_the_overlays_are_only_the_general_column():
    assert {c: n for c, n in TYPE_NAMES.items() if c.strip()} == Q_TYPE_NAMES
    assert {c: n for c, n in TYPE_NAMES.items() if not c.strip()} == {
        " ": "general",
        "": "general",
    }
    assert attach.NESTED_CHARS == {c.upper() for c in Q_TYPE_NAMES}


def test_the_attribute_sets_are_q_s():
    assert set(ATTR_NAMES) == set(Q_ATTRIBUTES), "a label for every attribute, and no other"
    assert plant_metadata.ATTRIBUTES == ("", *Q_ATTRIBUTES)
    # TorQ's sort.csv rule is the attachment's own; today it takes every attribute.
    assert set(attach.SORT_ATTRIBUTES) == {"", *Q_ATTRIBUTES}


def test_the_scaffold_offers_a_subset_of_q_s_types_with_q_s_chars():
    for t in TYPES:
        if t.name == "list":
            assert (t.literal, t.char) == ("()", " ")
            continue
        assert Q_TYPE_CHARS[t.name] == t.char, t.name
        assert t.literal == f"`{t.name}$()"
        assert type_char(t.literal) == t.char
        assert type_char(f"`g#{t.literal}") == t.char


# ------------------------------------------------------ unsupported types


def test_an_unknown_q_type_is_refused_by_the_attachment():
    with pytest.raises(attach.SchemaError, match="unknown q type 'bogus'"):
        attach.parse_definition("t:([] time:`timestamp$(); x:`bogus$())")


def test_a_nested_element_type_q_does_not_have_is_refused():
    with pytest.raises(attach.SchemaError, match="not a meta type character"):
        attach.parse_definition("t:([] time:`timestamp$(); x:())", {"x": "Q"})


@pytest.mark.parametrize("name", sorted(set(Q_TYPE_CHARS) - {t.name for t in TYPES}))
def test_a_q_type_the_scaffold_does_not_offer_is_refused_by_name(name):
    with pytest.raises(UqsError, match=f"unknown type '{name}'"):
        parse_columns(f"x:{name}")
    with pytest.raises(UqsError, match="not one a scaffold knows"):
        type_char(f"`{name}$()")


def test_a_character_q_does_not_have_is_shown_as_it_came():
    assert type_name("Q") == "Q"
    assert "Q" not in Q_TYPE_NAMES
