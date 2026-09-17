"""Tests for `scripts/check_q_traps.py` - that each rule FIRES.

A checker nobody has seen fail is a checker that might match nothing. This
repository has hit that failure mode three times: a lint hook scoped to a
path that matched 4 of 47 files, a drift test that skipped 5 of 5 cases, and
a `.replace()` against an anchor that was not there. So every rule gets a
sample it must flag and a sample it must not.

The must-NOT-flag cases are the more important half. Each is real code from
this repository that an earlier, naive version of the rule wrongly flagged -
`cross_ref_price_at` in particular, whose trailing-semicolon projection is
correct and which the first draft of the `@` rule reported as a bug.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not a package, so the checker is loaded by path. It must be
# registered in sys.modules BEFORE exec_module: @dataclass resolves its
# annotations through sys.modules[cls.__module__], which is None for a module
# that is mid-import, and fails with a bare "'NoneType' has no attribute
# '__dict__'" that says nothing about the real cause.
_SPEC = importlib.util.spec_from_file_location(
    "check_q_traps", Path(__file__).resolve().parents[3] / "scripts" / "check_q_traps.py"
)
assert _SPEC and _SPEC.loader
cqt = importlib.util.module_from_spec(_SPEC)
sys.modules["check_q_traps"] = cqt
_SPEC.loader.exec_module(cqt)


def _line_rule(rule, source: str):
    return rule("t.q", source.splitlines())


def _text_rule(rule, source: str):
    return rule("t.q", source)


# ------------------------------------------------------------ bare slash


def test_a_lone_slash_is_flagged():
    found = _line_rule(cqt.rule_bare_slash_comment_block, "a:1\n/\nb:2\n")
    assert len(found) == 1
    assert found[0].line == 2


def test_the_intended_blank_comment_line_is_not_flagged():
    """`/ .` is what this repo uses, and must stay quiet."""
    assert not _line_rule(cqt.rule_bare_slash_comment_block, "/ header\n/ .\n/ more\n")


def test_a_normal_comment_is_not_flagged():
    assert not _line_rule(cqt.rule_bare_slash_comment_block, "/ this is prose\n")


# ------------------------------------------------- reserved parameters


@pytest.mark.parametrize("name", ["desc", "tables", "sv", "load"])
def test_each_name_that_has_bitten_this_repo_is_flagged(name):
    found = _line_rule(cqt.rule_reserved_parameter_names, "f:{[" + name + ";ok] ok}")
    assert len(found) == 1
    assert name in found[0].detail


def test_a_safe_parameter_name_is_not_flagged():
    assert not _line_rule(cqt.rule_reserved_parameter_names, "f:{[label;ok] ok}")


def test_the_repo_s_own_renames_are_not_flagged():
    """timer_desc, sub_tables and label are the fixes; they must stay quiet."""
    src = "f:{[timer_desc;sub_tables;label] 1}"
    assert not _line_rule(cqt.rule_reserved_parameter_names, src)


def test_a_reserved_name_in_prose_is_not_flagged():
    assert not _line_rule(
        cqt.rule_reserved_parameter_names, "/ this mentions {[desc;x] ...} in a comment"
    )


# ------------------------------------------------------------- `. ()`


def test_applying_to_an_empty_list_is_flagged():
    assert _line_rule(cqt.rule_niladic_dot_empty, "r:f . ()")


def test_the_correct_niladic_application_is_not_flagged():
    assert not _line_rule(cqt.rule_niladic_dot_empty, "r:f . enlist(::)")


# ------------------------------------------------- multiparam under @


def test_a_two_parameter_lambda_under_at_is_flagged():
    found = _text_rule(cqt.rule_multiparam_lambda_under_at, "r:@[{[a;b] a+b};x;{0n}]")
    assert len(found) == 1
    assert "effectively 2-argument" in found[0].detail


def test_the_smoke_check_bug_shape_is_flagged():
    """The actual bug: a 2-parameter lambda trapped with a pair."""
    src = "m:@[{[h;t] h(meta;t)};(h;tbl);{[e] (::)}]"
    assert _text_rule(cqt.rule_multiparam_lambda_under_at, src)


def test_a_one_parameter_lambda_under_at_is_not_flagged():
    assert not _text_rule(cqt.rule_multiparam_lambda_under_at, "m:@[{[t] h t};tbl;{(::)}]")


def test_a_trailing_semicolon_projection_is_not_flagged():
    """`cross_ref_price_at`'s real shape, which the first draft flagged.

    Four parameters, three supplied, trailing `;` leaving one open - so the
    projection is unary and the code is correct.
    """
    src = (
        "cross_ref_price_at:{[quotes;sym;ref_size;t]\n"
        "    @[{[quotes;sym;ref_size;t] first cross_book_at[quotes;sym;t]`mid}"
        "[quotes;sym;ref_size;];t;{0n}]};"
    )
    assert not _text_rule(cqt.rule_multiparam_lambda_under_at, src)


def test_a_partially_applied_projection_is_not_flagged():
    """`@[{[spec;w] ...}[spec];w;{x}]` - two params, one supplied, so unary."""
    src = "@[{[spec;w] f[spec;w]}[spec];w;{x}]"
    assert not _text_rule(cqt.rule_multiparam_lambda_under_at, src)


def test_a_lambda_body_containing_braces_is_matched_correctly():
    """Brace-matching, not a regex: a nested lambda must not end the body."""
    src = "@[{[a;b] {x+1} each a}[a];b;{0n}]"
    assert not _text_rule(cqt.rule_multiparam_lambda_under_at, src)


def test_a_named_function_under_at_is_not_flagged():
    """Arity is not local information, so this is deliberately out of scope."""
    assert not _text_rule(cqt.rule_multiparam_lambda_under_at, "@[some.func;x;{0n}]")


def test_dot_apply_with_an_argument_list_is_not_flagged():
    """`.[f;(a;b);h]` genuinely takes an argument list - only `@` is unary."""
    assert not _text_rule(cqt.rule_multiparam_lambda_under_at, ".[{[a;b] a+b};(x;y);{0n}]")


# --------------------------------------------------- reserved locals


def test_the_var_bug_is_flagged():
    """The real bug: `var:credential_var source` in source_contract.q.

    `var` is variance. Assigning it as a lambda local throws `assign at LOAD
    time and aborts the rest of the file, leaving a half-populated namespace -
    and the parameter-name rule cannot see it, because it is not a parameter.
    Sixth reserved-name collision in this repo, first as a local.
    """
    bug = "f:{[source]\n    var:credential_var source;\n    v:getenv `$var;\n    }"
    found = cqt.rule_reserved_local_assignment("t.q", bug)
    assert len(found) == 1
    assert "var" in found[0].detail


def test_the_rename_is_not_flagged():
    ok = "f:{[source]\n    env_var:credential_var source;\n    }"
    assert not cqt.rule_reserved_local_assignment("t.q", ok)


def test_a_namespace_level_definition_is_not_flagged():
    """Outside a lambda the same name is legal - it defines `.ns.var`."""
    assert not cqt.rule_reserved_local_assignment("t.q", "\\d .qsrc\nvar:1\n")


def test_a_qsql_column_alias_is_not_flagged():
    """`select max:max px` is an alias, not an assignment."""
    assert not cqt.rule_reserved_local_assignment("t.q", "f:{[t] select max:max px from t}")


def test_a_global_assignment_through_a_symbol_is_not_flagged():
    """Backtick-set is absolute and unambiguous, so it is not a local."""
    assert not cqt.rule_reserved_local_assignment("t.q", "f:{[x] `var set x;}")


# ------------------------------------------------ underscore params


def test_an_underscore_parameter_is_flagged():
    """`_` is q's drop operator, so applying such a lambda throws `'match`.

    It parses and projects without complaint and fails only when applied -
    `bounded_worker.q` loaded cleanly, planned its windows, and died on the
    first publish.
    """
    found = cqt.rule_underscore_parameter("t.q", ["f:{[a;_] a+1}"])
    assert len(found) == 1
    assert "_" in found[0].detail


def test_the_conventional_replacement_is_not_flagged():
    assert not cqt.rule_underscore_parameter("t.q", ["f:{[a;unused] a+1}"])


def test_a_snake_case_parameter_is_not_flagged():
    """Names merely containing an underscore are the norm in this tree."""
    assert not cqt.rule_underscore_parameter("t.q", ["f:{[from_ts;to_ts] 1}"])


def test_an_underscore_in_a_comment_is_not_flagged():
    assert not cqt.rule_underscore_parameter("t.q", ["/ mentions {[a;_] x} in prose"])


# -------------------------------------------------- overlong throws


def test_a_long_thrown_message_is_flagged():
    """q truncates a thrown string at 255 bytes, silently."""
    msg = "x" * 260
    found = cqt.rule_overlong_throw("t.q", "f:{[x] '\"" + msg + '"}')
    assert len(found) == 1
    assert "260 chars" in found[0].detail


def test_a_short_thrown_message_is_not_flagged():
    assert not cqt.rule_overlong_throw("t.q", 'f:{[x] \'"too small"}')


def test_a_message_containing_an_apostrophe_is_measured_correctly():
    """The case that broke the first two drafts.

    `'"cannot normalize '",s,"' to a pair"` contains the sequence `'"` inside
    its own text, so a naive scan started from there and ran past the end of
    the function - reporting a 150-character message as 5142 once it had
    swallowed the rest of the file.
    """
    src = (
        "normalize:{[s]\n"
        '    if[not ok s; \'"normalize: cannot normalize \'",s,"\' to a 6-letter pair"];\n'
        "    `$s};\n"
    )
    assert not cqt.rule_overlong_throw("t.q", src)


def test_a_dollar_bracket_throw_does_not_swallow_the_next_function():
    """The case that broke the first draft.

    A throw inside `$[...]` closes with `]}`, not `];`, so scanning to the
    next `];` ran into the following function's string literals.
    """
    src = (
        "owner:{[c]\n"
        "    $[c in a; `q;\n"
        '      \'"owner: not an assigned concern, so the split needs amending"]}\n'
        "\n"
        'other:{[x] "' + "y" * 300 + '"}\n'
    )
    assert not cqt.rule_overlong_throw("t.q", src)


def test_a_long_message_inside_a_comment_is_not_flagged():
    """Prose can contain anything, including an apostrophe and a quote."""
    src = "/ this comment mentions '\"" + ("z" * 400) + '"\nf:{[x] x}\n'
    assert not cqt.rule_overlong_throw("t.q", src)


def test_interpolated_values_are_not_counted():
    """Only literal text is counted, so a match is unambiguous.

    The real budget is tighter once values are substituted; the threshold
    sits below 255 to leave room for them.
    """
    src = 'f:{[x] \'"short: ",string[x]," also short"}'
    assert not cqt.rule_overlong_throw("t.q", src)


# ------------------------------------------------------ self comparison


def test_a_self_comparison_in_a_where_clause_is_flagged():
    found = _line_rule(cqt.rule_self_comparison, "select from t where dataset=dataset, ts<x")
    assert len(found) == 1
    assert "dataset=dataset" in found[0].detail


def test_the_repo_s_renamed_parameters_are_not_flagged():
    assert not _line_rule(cqt.rule_self_comparison, "select from t where dataset=ds")


def test_arithmetic_on_the_same_name_is_not_flagged():
    """`x+x` and `x&x` are legitimate; only `=` and `~` are nonsensical."""
    assert not _line_rule(cqt.rule_self_comparison, "select from t where x+x>0")


def test_a_self_comparison_outside_qsql_is_not_flagged():
    """Scoped to qSQL, so a non-query line does not fire."""
    assert not _line_rule(cqt.rule_self_comparison, "flag:a=a")


# ---------------------------------------------------------------- wiring


# ------------------------------------------- interior `like` wildcard


def _like(src: str):
    return cqt.rule_interior_like_wildcard("f.q", src.splitlines())


def test_an_interior_wildcard_is_flagged():
    """q throws a bare 'nyi on this, naming nothing - so it reads as a
    broken tool rather than a broken pattern."""
    assert _like('x:s like "*a*b*";')


def test_a_wildcard_with_text_on_both_sides_is_flagged_without_a_trailing_star():
    assert _like('x:s like "*a*d";')


def test_the_finding_quotes_the_pattern():
    # "an interior wildcard exists somewhere on this line" is not actionable
    # when the line holds two like tests.
    (finding,) = _like('x:s like "*a*b*";')
    assert '"*a*b*"' in finding.detail


def test_a_leading_and_trailing_star_is_fine():
    """The form the whole repository uses. Flagging it would make the rule
    fire on 30-odd correct lines and get the checker switched off."""
    assert not _like('x:s like "*abc*";')


def test_a_leading_star_alone_is_fine():
    assert not _like('x:s like "*abc";')


def test_a_trailing_star_alone_is_fine():
    assert not _like('x:s like "abc*";')


def test_no_wildcard_at_all_is_fine():
    assert not _like('x:s like "abc";')


def test_a_character_class_is_not_a_wildcard():
    """`[abc]` is a class and `?` is a single-character wildcard; q handles
    both. Only `*` has the restriction."""
    assert not _like('x:s like "*[abc]*";')
    assert not _like('x:s like "*a?c*";')


def test_a_pattern_in_a_comment_is_ignored():
    assert not _like('/ s like "*a*b*" would throw')


def test_the_real_repository_has_none():
    """Asserted against the tree rather than a sample: the rule was written
    after the trap was hit, so it has to be true of the code that exists."""
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    tracked = subprocess.run(
        ["git", "ls-files", "*.q"], capture_output=True, text=True, check=True, cwd=repo
    ).stdout.split()
    for name in tracked:
        path = repo / name
        if not path.is_file():
            continue
        found = cqt.rule_interior_like_wildcard(name, path.read_text(errors="replace").splitlines())
        assert not found, f"{name}: {found}"


def test_every_rule_is_registered():
    """A rule defined but never called would pass silently - which is this
    checker's own version of the bug it exists to catch.
    """
    defined = {n for n in dir(cqt) if n.startswith("rule_")}
    registered = {r.__name__ for r in cqt.LINE_RULES + cqt.TEXT_RULES + cqt.PYTHON_RULES}
    assert defined == registered, f"unregistered rule(s): {defined - registered}"


def test_the_checker_finds_the_repo_s_q_files():
    """Guards against the excluded-prefix logic matching everything - the
    checker would then report 0 files clean and exit 0.
    """
    files = cqt._tracked_q_files()
    rel = [str(f.relative_to(cqt.REPO)) for f in files]
    assert len(rel) > 50
    # The exclusion is on the PREFIX `lib/` - the vendored TorQ tree. It is
    # deliberately not a substring match: `tests/lib/` holds this repo's own
    # qUnit harness and the ETL doubles, which should be checked.
    assert not any(p.startswith("lib/") for p in rel)
    assert "tests/lib/etl_test_doubles.q" in rel


def test_the_type_gate_is_registered_in_the_scope_checker():
    """`ty` must be in the scope checker's gate list, not just in the config.

    A hook present in .pre-commit-config.yaml but absent from
    check_hook_scopes.py is unenforced: it can narrow to a stale path and
    nothing complains - which is exactly how 43 of 47 files went unlinted.
    """
    scope_checker = Path(__file__).resolve().parents[3] / "scripts" / "check_hook_scopes.py"
    text = scope_checker.read_text()
    assert 'TYPE_HOOKS = ("ty",)' in text
    assert '("type", TYPE_HOOKS)' in text


def test_the_real_repo_is_clean():
    """The rules hold on every tracked .q file, so the hook is committable."""
    assert cqt.main() == 0


# ------------------------------------------- q embedded in Python strings


def _py_rule(source: str):
    return cqt.rule_reserved_name_in_embedded_q("t.py", source)


#: The offending column name, CONCATENATED rather than written whole.
#:
#: This file is itself scanned by the rule under test, so a fixture
#: containing the literal `([] cols:...` would make the checker report its
#: own tests - the same self-reference the rule already dodges for
#: docstrings. Building the string at runtime means no `ast.Constant` here
#: holds the pattern, so no exemption list is needed and the rule stays
#: honest about what it finds.
_BAD_COL = "col" + "s"


def test_a_builtin_as_a_column_name_in_an_embedded_literal_is_flagged():
    # The exact shape that shipped in `uqf-stack schema` and threw 'assign
    # against a live process. `cols` was already in RISKY_PARAM_NAMES; no
    # rule was looking in Python.
    source = 'expr = "([] name:string tables `; ' + _BAD_COL + ':count each x)"\n'
    found = _py_rule(source)
    assert len(found) == 1
    assert _BAD_COL in found[0].detail


def test_a_builtin_READ_inside_an_expression_is_not_flagged():
    # `count each cols each tables` uses the builtins as the functions they
    # are, which is correct and common. Flagging it would make the rule
    # useless, so only the column-name position counts.
    assert _py_rule('expr = "([] n:count each cols each tables `)"\n') == []


def test_a_docstring_is_not_scanned():
    # A docstring is prose ABOUT code, not code that reaches q - and the
    # rule's own docstring quotes the bad expression, so without this the
    # checker reports itself.
    source = '"""Example: ([] ' + _BAD_COL + ':1 2 3) is wrong."""\n'
    assert _py_rule(source) == []


def test_a_safe_column_name_passes():
    safe = 'expr = "([] name:string tables `; ncols:count each cols each tables `)"\n'
    assert _py_rule(safe) == []


def test_a_string_without_a_table_literal_is_ignored():
    # Deliberately conservative: only `([]` marks a string as embedded q. A
    # broader heuristic would start firing on English prose, and a gate that
    # cries wolf gets switched off rather than fixed.
    assert _py_rule('msg = "the value: something, count: 3"\n') == []


def test_unparseable_python_is_not_this_rule_s_problem():
    # ruff already reports a syntax error; this rule returning findings for
    # one would be noise attached to the wrong tool.
    assert _py_rule("def (\n") == []
