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


def test_every_rule_is_registered():
    """A rule defined but never called would pass silently - which is this
    checker's own version of the bug it exists to catch.
    """
    defined = {n for n in dir(cqt) if n.startswith("rule_")}
    registered = {r.__name__ for r in cqt.LINE_RULES + cqt.TEXT_RULES}
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


def test_the_real_repo_is_clean():
    """The rules hold on every tracked .q file, so the hook is committable."""
    assert cqt.main() == 0
