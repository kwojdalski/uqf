"""Guards on the q programs in :mod:`uqf_frontend.queries`.

These are text constants, so nothing type-checks them. The parameter-name
check exists because a q builtin used as a lambda parameter raises a bare
``'nyi`` when the lambda is *called* - not when it is defined, and whether or
not the body references it. That has already cost this repository three
debugging sessions (``desc`` and ``tables`` in scripts/processes/torq_pipeline.q, and
``sv`` in this package's own COVERAGE lambda).
"""

from __future__ import annotations

import re

import pytest

from uqf_frontend import ops, queries

#: q reserved words plus the `.q` namespace, as reported by
#: `asc distinct .Q.res, key \`.q` on KDB-X. None may be a parameter name.
Q_RESERVED = frozenset(
    {
        "abs",
        "acos",
        "aj",
        "aj0",
        "ajf",
        "ajf0",
        "all",
        "and",
        "any",
        "asc",
        "asin",
        "asof",
        "atan",
        "attr",
        "avg",
        "avgs",
        "bin",
        "binr",
        "by",
        "ceiling",
        "cols",
        "cor",
        "cos",
        "count",
        "cov",
        "cross",
        "csv",
        "cut",
        "delete",
        "deltas",
        "desc",
        "dev",
        "differ",
        "distinct",
        "div",
        "do",
        "dsave",
        "each",
        "ej",
        "ema",
        "enlist",
        "eval",
        "except",
        "exec",
        "exit",
        "exp",
        "fby",
        "fills",
        "first",
        "fkeys",
        "flip",
        "floor",
        "from",
        "get",
        "getenv",
        "group",
        "gtime",
        "hclose",
        "hcount",
        "hdel",
        "hopen",
        "hsym",
        "iasc",
        "idesc",
        "if",
        "ij",
        "ijf",
        "in",
        "insert",
        "inter",
        "inv",
        "key",
        "keys",
        "last",
        "like",
        "lj",
        "ljf",
        "load",
        "log",
        "lower",
        "lsq",
        "ltime",
        "ltrim",
        "mavg",
        "max",
        "maxs",
        "mcount",
        "md5",
        "mdev",
        "med",
        "meta",
        "min",
        "mins",
        "mmax",
        "mmin",
        "mmu",
        "mod",
        "msum",
        "neg",
        "next",
        "not",
        "null",
        "or",
        "over",
        "parse",
        "peach",
        "pj",
        "prd",
        "prds",
        "prev",
        "prior",
        "rand",
        "rank",
        "ratios",
        "raze",
        "read0",
        "read1",
        "reciprocal",
        "reval",
        "reverse",
        "rload",
        "rotate",
        "rsave",
        "rtrim",
        "save",
        "scan",
        "scov",
        "sdev",
        "select",
        "set",
        "setenv",
        "show",
        "signum",
        "sin",
        "sqrt",
        "ss",
        "ssr",
        "string",
        "sublist",
        "sum",
        "sums",
        "sv",
        "svar",
        "system",
        "tables",
        "tan",
        "til",
        "trim",
        "type",
        "uj",
        "ujf",
        "ungroup",
        "union",
        "update",
        "upper",
        "upsert",
        "use",
        "value",
        "var",
        "view",
        "views",
        "vs",
        "wavg",
        "where",
        "while",
        "within",
        "wj",
        "wj1",
        "wsum",
        "ww",
        "xasc",
        "xbar",
        "xcol",
        "xcols",
        "xdesc",
        "xexp",
        "xgroup",
        "xkey",
        "xlog",
        "xprev",
        "xrank",
    }
)

#: Every q program constant in the package, by "module.NAME".
Q_PROGRAMS = {
    f"{mod.__name__.rsplit('.', 1)[-1]}.{name}": getattr(mod, name)
    for mod in (queries, ops)
    for name in dir(mod)
    if name.isupper() and isinstance(getattr(mod, name), str)
}


def _params(program: str) -> list[str]:
    """Parameter names from a q lambda's signature, or [] for an expression."""
    m = re.match(r"\s*\{\s*\[([^\]]*)\]", program)
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(";") if p.strip()]


def test_the_reserved_list_looks_right():
    """Sanity-check the embedded list against names known to be traps."""
    for known in ("sv", "desc", "tables", "count", "select" if "select" in Q_RESERVED else "count"):
        assert known in Q_RESERVED
    assert len(Q_RESERVED) > 150


def test_at_least_one_program_has_parameters():
    """Catch a regex that silently matches nothing - the whole check would
    then pass vacuously, which is the failure mode this repo keeps hitting.
    """
    assert any(_params(p) for p in Q_PROGRAMS.values())


@pytest.mark.parametrize("name", sorted(Q_PROGRAMS))
def test_no_parameter_shadows_a_q_builtin(name):
    offenders = [p for p in _params(Q_PROGRAMS[name]) if p in Q_RESERVED]
    assert not offenders, (
        f"{name} uses q builtin(s) {offenders} as parameter name(s); this raises a bare "
        f"'nyi when the lambda is called, not when it is defined"
    )


@pytest.mark.parametrize("name", sorted(Q_PROGRAMS))
def test_programs_are_balanced(name):
    program = Q_PROGRAMS[name]
    for opener, closer in (("{", "}"), ("[", "]"), ("(", ")")):
        assert program.count(opener) == program.count(closer), (
            f"{name} has unbalanced {opener}{closer}"
        )


@pytest.mark.parametrize("name", sorted(Q_PROGRAMS))
def test_no_program_is_a_bare_niladic_lambda(name):
    """A niladic ``{[] ...}`` sent with no arguments makes q return the
    *function itself*, which kola cannot deserialise ("Not supported k type
    100"). Such a program must be written as a plain expression instead.

    This has bitten twice - queries.PING and ops.IDENTITY - so it is a test
    rather than a comment.
    """
    program = Q_PROGRAMS[name].strip()
    assert not program.startswith("{[]"), (
        f"{name} is a niladic lambda; send it as an expression instead, or q will "
        f"return the function rather than calling it"
    )
