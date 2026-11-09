"""The attach lifecycle's reading of a sort rule, held to what q itself accepts (#1868).

`attach/schema.py` (`parse_sorting`, `check_sorting`) and `attach/jobs.py` read a
table's `sorting[...]` declaration as TEXT, with no q, because a bundle is read
before anything is installed and a bad rule should be refused then, not at end of
day. That restates the refusals of `.qetl.plant.sorting` in `src/etl/plant_registry.q`:
an unknown column, a list column, a column named twice, an attribute TorQ does not
take, `p` or `s` anywhere but the first column, and a table declared twice.

q is the authority. This feeds the same set of good and bad rules to both and asserts
they agree on every one - accept or refuse, never the wording - so changing a refusal
in q without changing Python (or the reverse) fails here.

Needs q, so it skips without one - like test_declarations_match_q.py.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from uqs.attach.config import AttachmentError
from uqs.attach.jobs import _sorting
from uqs.attach.schema import SchemaError, check_sorting, parse_sorting, spec_of
from uqs.interpreter import q_interpreter
from uqs.model import plant_metadata

UQF_ROOT = Path(__file__).resolve().parents[3]

#: A plant table with a nested (list per row) column, `bid_prices`, and no declared rule.
TABLE = "mkt_orderbook"

#: (the rule as q text, what q's refusal says - "" for a good rule). Each is written the way
#: a table's declaration writes it: `sorting[`table;<rule>]`.
RULES: tuple[tuple[str, str], ...] = (
    ("`sym`time!`p`", ""),
    ("(enlist `time)!enlist `s", ""),
    ("(enlist `sym)!enlist `g", ""),
    ("`time`sym!`s`g", ""),
    ("`sym`time!`g`g", ""),
    ("`time`sym!``", ""),
    ("`sym`time!`u`u", ""),
    ("`nope`time!`p`", "has no column nope"),
    ("(enlist `securityId)!enlist `p", "has no column securityId"),
    ("(enlist `bid_prices)!enlist `", "bid_prices hold a list per row"),
    ("`sym`ask_prices!`p`", "ask_prices hold a list per row"),
    ("`sym`sym!`p`", "names a column twice"),
    ("`time`time!``", "names a column twice"),
    ("(enlist `sym)!enlist `x", "attribute x is not one TorQ's sort.csv takes"),
    ("`sym`time!`pp`", "attribute pp is not one TorQ's sort.csv takes"),
    ("`sym`time!``p", "p and s hold only on the first sort column"),
    ("`sym`time!``s", "p and s hold only on the first sort column"),
    ("`sym", "must be a dictionary"),
    ("`sym`time!1 2", "must map column symbols to attribute symbols"),
)

#: How q evaluates each rule: `.qetl.plant.sorting` itself, so its own refusals are the
#: ones counted, and the registry emptied again so each rule meets a table with none.
_DUMP = """
\\l src/init.q
\\l src/etl/init.q
rules:@RULES@;
verdict:{[t;text]
    r:.[{[t;text] .qetl.plant.sorting[t;value text]; "accepted"};(t;text);{[e] "refused: ",e}];
    delete from `.qetl.plant.sort_rules where table=t;
    r};
twice:{[t] .qetl.plant.sorting[t;`sym`time!`p`];
    r:@[{[t] .qetl.plant.sorting[t;`sym`time!`p`]; "accepted"};t;{[e] "refused: ",e}];
    delete from `.qetl.plant.sort_rules where table=t;
    r};
emit:{[a;b] -1 "VERDICT|",a,"|",b};
{emit[x;verdict[`@TABLE@;x]]} each rules;
emit["twice";twice`@TABLE@];
exit 0
"""


def _q_verdicts() -> dict:
    q = q_interpreter(os.environ)
    if q is None:
        pytest.skip("no q interpreter - set $QCMD, or put q on PATH")
    quoted = "(" + ";".join('"' + text.replace('"', '\\"') + '"' for text, _ in RULES) + ")"
    env = {**os.environ, "QHOME": os.environ.get("QHOME", str(Path.home() / ".kx"))}
    # A script FILE, not stdin: q continues a multi-line lambda only in a file.
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "verdicts.q"
        script.write_text(_DUMP.replace("@RULES@", quoted).replace("@TABLE@", TABLE))
        result = subprocess.run(
            [str(q), str(script), "-q"],
            cwd=UQF_ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
    lines = [
        ln[len("VERDICT|") :] for ln in result.stdout.splitlines() if ln.startswith("VERDICT|")
    ]
    assert lines, f"q did not load the ETL tree:\n{(result.stdout + result.stderr)[-2000:]}"
    # "rule|verdict": a rule holds no "|", and a verdict may
    found = dict(ln.split("|", 1) for ln in lines)
    twice = found.pop("twice")
    return {"rules": found, "twice": twice}


@pytest.fixture(scope="module")
def q_verdicts() -> dict:
    return _q_verdicts()


def _python_refusal(rule: str) -> str:
    """ "" when the attach lifecycle accepts the rule, else why it refuses."""
    spec = spec_of(plant_metadata.load(UQF_ROOT).own()[TABLE])
    try:
        found = parse_sorting(f"sorting[`{TABLE};{rule}]")
        if found is None:
            return "not a sorting[] line"
        check_sorting(dataclasses.replace(spec, sort=found[1]))
    except SchemaError as exc:
        return str(exc)
    return ""


@pytest.mark.parametrize(("rule", "why"), RULES, ids=[r for r, _ in RULES])
def test_the_attach_lifecycle_and_q_accept_and_refuse_the_same_rules(q_verdicts, rule, why):
    q_refusal = q_verdicts["rules"][rule]
    good = not why
    # the message, not just the verdict: a rule refused for a typo in this test would agree too
    assert (
        (q_refusal == "accepted")
        if good
        else (q_refusal.startswith("refused: sorting: ") and why in q_refusal)
    ), f"the case list is wrong about q: {rule!r} -> {q_refusal!r}"
    ours = _python_refusal(rule)
    assert (ours == "") == (q_refusal == "accepted"), (
        f"{rule}: q {'accepts' if good else 'refuses it: ' + q_refusal}; "
        f"attach/schema.py {'accepts' if not ours else 'refuses it: ' + ours}"
    )


def test_a_table_declared_twice_is_refused_by_both(q_verdicts):
    assert q_verdicts["twice"].startswith("refused: sorting: "), q_verdicts["twice"]
    declared = f"sorting[`{TABLE};`sym`time!`p`]\n"
    assert _sorting(declared, "tables.q") == {TABLE: (("sym", "p"), ("time", ""))}
    with pytest.raises(AttachmentError, match=f"sorting\\[\\] for {TABLE} is declared twice"):
        _sorting(declared * 2, "tables.q")


def test_the_case_list_is_not_vacuous():
    assert any(not why for _, why in RULES) and any(why for _, why in RULES)
