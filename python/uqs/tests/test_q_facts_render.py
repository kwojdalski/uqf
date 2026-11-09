"""scripts/generate/q_facts.py's rendering of a q list (#2636): an empty one
was `(,)`, which ruff format refuses, and a word was written unescaped."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "generate" / "q_facts.py"
_spec = importlib.util.spec_from_file_location("uqf_q_facts_script", SCRIPT)
assert _spec and _spec.loader
q_facts_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q_facts_script)


def test_an_empty_or_quoting_list_renders_as_python_that_says_the_same(monkeypatch, tmp_path):
    out = tmp_path / "facts.py"
    monkeypatch.setitem(q_facts_script.MODULES, out, ("uqs", ["EMPTY", "QUOTED"]))
    monkeypatch.setitem(q_facts_script.FACTS, "EMPTY", "an empty list")
    monkeypatch.setitem(q_facts_script.FACTS, "QUOTED", "words that need escaping")
    words = ['say "hi"', "back\\slash", "plain"]
    text = q_facts_script.render({"EMPTY": [], "QUOTED": words}, out)
    found = {
        node.target.id: ast.literal_eval(node.value)
        for node in ast.parse(text).body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert found == {"EMPTY": (), "QUOTED": tuple(words)}
