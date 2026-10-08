"""Tests for `scripts/portable/flatten_contexts.py` (#856).

Three layers. The rewrite itself, on q text, for every binding rule the issue
names and every refusal, asserting the message as well as the code. The CLI's
promises - dry runs write nothing, a refusal publishes nothing, output is
deterministic and idempotent, inputs are selected as documented. And q itself:
the converted fixtures, and the converted status and interval modules, run
on whichever interpreters this machine has - PeachQ (which, like kdb+ 4.0,
refuses a nested `\\d`), KDB-X, and the 4.0 build `UQF_KDB40` names - and
each run must print its OK marker, not merely exit 0.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "flatten_contexts", REPO / "scripts" / "portable" / "flatten_contexts.py"
)
assert _SPEC and _SPEC.loader
fc = importlib.util.module_from_spec(_SPEC)
sys.modules["flatten_contexts"] = fc
_SPEC.loader.exec_module(fc)

CHECK = REPO / "scripts" / "portable" / "checks" / "status_intervals.q"


def convert(files: dict[str, str], target: str = "4.0", allow: bool = False):
    """Every file through both passes, as main() runs them."""
    conv = fc.Converter(target, allow)
    for path, text in files.items():
        conv.collect(path, text)
    conv.propagate()
    return {path: conv.convert(path, text) for path, text in files.items()}


def one(text: str, **kw) -> fc.FileResult:
    return convert({"f.q": text}, **kw)["f.q"]


def converted(text: str) -> str:
    res = one(text)
    assert not res.refusals, [r.as_dict() for r in res.refusals]
    return res.output


def refusals(text: str, **kw) -> list[tuple[str, int, str]]:
    return [(r.code, r.line, r.reason) for r in one(text, **kw).refusals]


# ------------------------------------------------------------- binding


def test_the_issues_example():
    src = "\\d .example.inner\noffset:2\nadd:{[amount] amount+offset}\n\\d .\n"
    assert converted(src) == (
        "\\d .\n.example.inner.offset:2\n"
        ".example.inner.add:{[amount] amount+.example.inner.offset}\n\\d .\n"
    )


@pytest.mark.parametrize("text", ["", "/ a comment, and nothing else\n", "\n\n"])
def test_a_file_with_no_statement_converts_to_itself(text):
    r = one(text)
    assert (r.refusals, r.output if r.action == "transformed" else text) == ([], text)


def test_globals_and_cross_function_calls_are_qualified():
    src = "\\d .a.b\nf:{x+1}\ng:{f f x}\nh:{[v] g[v]+k}\n"
    assert converted(src) == (
        "\\d .\n.a.b.f:{x+1}\n.a.b.g:{.a.b.f .a.b.f x}\n.a.b.h:{[v] .a.b.g[v]+.a.b.k}\n"
    )


def test_parameters_locals_and_implicit_arguments_stay_local():
    src = (
        "\\d .a.b\n"
        "p:{[offset] offset+1}\n"
        "l:{offset:1; offset+x}\n"
        "i:{x+y+z}\n"
        "e:{[a] a+x}\n"  # with explicit parameters, x is a global
        "u:{r:1; s:r+offset; s}\n"  # a local assigned after use is still local
    )
    assert converted(src).splitlines()[1:] == [
        ".a.b.p:{[offset] offset+1}",
        ".a.b.l:{offset:1; offset+x}",
        ".a.b.i:{x+y+z}",
        ".a.b.e:{[a] a+.a.b.x}",
        ".a.b.u:{r:1; s:r+.a.b.offset; s}",
    ]


def test_a_nested_lambda_does_not_see_the_outer_locals():
    # q has no closures: `a` inside the inner lambda is the context's global.
    assert converted("\\d .a.b\nf:{[a] {a+x} each a}\n").splitlines()[1] == (
        ".a.b.f:{[a] {.a.b.a+x} each a}"
    )


def test_global_mutations_keep_their_target():
    src = (
        "\\d .a.b\n"
        "bump:{n+:1; n}\n"
        "mark:{[k] seen[k]:1b}\n"
        "add:{[e] events,:enlist e}\n"
        "reset:{total::0}\n"
        "own:{c:0; c+:1; c[0]:2; c}\n"  # amending a local stays local
    )
    assert converted(src).splitlines()[1:] == [
        ".a.b.bump:{.a.b.n+:1; .a.b.n}",
        ".a.b.mark:{[k] .a.b.seen[k]:1b}",
        ".a.b.add:{[e] .a.b.events,:enlist e}",
        ".a.b.reset:{.a.b.total::0}",
        ".a.b.own:{c:0; c+:1; c[0]:2; c}",
    ]


def test_context_changes_are_tracked_and_line_numbers_kept():
    src = (
        "\\d .fa\nbase:1\nget1:{base}\n"
        "\\d .fa.b\nbase:2\nget2:{base}\n"
        "\\d .\ntop:4\n"
        "\\d .fa.c\nmore:{base+1}\n"
        "\\d .fa\nback:{base}\n"
    )
    out = converted(src)
    assert out.splitlines() == [
        "\\d .fa",
        "base:1",  # a single-level context is valid on 4.0, and left alone
        "get1:{base}",
        "\\d .",
        ".fa.b.base:2",
        ".fa.b.get2:{.fa.b.base}",
        "\\d .",
        "top:4",
        "\\d .",
        ".fa.c.more:{.fa.c.base+1}",
        "\\d .fa",
        "back:{base}",
    ]
    res = one(src)
    assert [c["context"] for c in res.contexts] == [".fa", ".fa.b", ".", ".fa.c", ".fa"]
    assert res.defined == {".fa.b": ["base", "get2"], ".fa.c": ["more"]}


def test_qsql_columns_stay_columns_and_tables_are_qualified():
    src = (
        "\\d .a.b\n"
        "big:{[t;minpx] select sym, px from t where px>minpx}\n"
        "agg:{select total:sum qty by sym from trades}\n"
        "two:{select from trades where i<2}\n"
        "dbl:{update qty:qty*2 from trades}\n"
        "ex:{exec px from trades where sym=`x}\n"
    )
    assert converted(src).splitlines()[1:] == [
        ".a.b.big:{[t;minpx] select sym, px from t where px>minpx}",
        ".a.b.agg:{select total:sum qty by sym from .a.b.trades}",
        ".a.b.two:{select from .a.b.trades where i<2}",
        ".a.b.dbl:{update qty:qty*2 from .a.b.trades}",
        ".a.b.ex:{exec px from .a.b.trades where sym=`x}",
    ]


def test_literals_comments_and_table_columns_are_untouched():
    src = (
        "\\d .a.b\n"
        "/ offset in a comment\n"
        "/\n"
        "offset in a block comment\n"
        "\\\n"
        'msg:"offset in a string"  / and a trailing comment: offset\n'
        "s:`offset`a.b\n"
        "t:([] offset:1 2; k:3 4)\n"
        "kt:([offset:1 2] v:3 4)\n"
        "d:`x`y!(offset;2)\n"
        'f:{[] (offset; "offset"; `offset)}\n'
        "\\d .\n"
        "\\\n"
        "offset after the exit line is not q\n"
    )
    assert converted(src).splitlines() == [
        "\\d .",
        "/ offset in a comment",
        "/",
        "offset in a block comment",
        "\\",
        '.a.b.msg:"offset in a string"  / and a trailing comment: offset',
        ".a.b.s:`offset`a.b",
        ".a.b.t:([] offset:1 2; k:3 4)",
        ".a.b.kt:([offset:1 2] v:3 4)",
        ".a.b.d:`x`y!(.a.b.offset;2)",
        '.a.b.f:{[] (.a.b.offset; "offset"; `offset)}',
        "\\d .",
        "\\",
        "offset after the exit line is not q",
    ]


def test_builtins_and_absolute_names_are_untouched():
    src = "\\d .a.b\nf:{(count til 3; .z.p; .Q.s1 x; 1_x; x xexp 2)}\n"
    assert converted(src).splitlines()[1] == ".a.b.f:{(count til 3; .z.p; .Q.s1 x; 1_x; x xexp 2)}"


def test_run_time_lookups_inside_functions_are_left_as_written():
    # Resolved against the working context when they run - the root, once
    # loaded, before conversion and after - so the symbols stay as they are.
    src = '\\d .a.b\nput:{`v set x}\npull:{get `v}\nrun:{value "1+1"}\nf:{n set x}\n'
    out = converted(src)
    assert "`v set x" in out and "get `v" in out and 'value "1+1"' in out
    assert ".a.b.f:{.a.b.n set x}" in out


def test_the_5_0_target_keeps_native_nested_contexts():
    src = "\\d .a.b\nf:{g x}\n\\d .\n"
    res = one(src, target="5.0")
    assert (res.output, res.action, res.refusals) == (src, "unchanged", [])


def test_converting_twice_changes_nothing_more():
    src = "\\d .a.b\noffset:2\nadd:{[amount] amount+offset}\n\\d .c\nx:1\n"
    once = converted(src)
    assert converted(once) == once


def test_every_tracked_q_file_lexes_back_to_itself():
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.q"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert files
    for rel in files:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "".join(t.text for t in fc.lex(text)) == text, rel


def test_the_status_and_interval_modules_convert_without_refusal():
    paths = ["src/etl/core/status.q", "src/etl/core/intervals.q"]
    results = convert({p: (REPO / p).read_text(encoding="utf-8") for p in paths})
    for p in paths:
        assert results[p].action == "transformed" and not results[p].refusals, p
    gaps = results["src/etl/core/intervals.q"].output
    assert ".qetl.coverage.require_interval[from_ts;to_ts];" in gaps
    assert "merged:.qetl.coverage.compose covered;" in gaps


# ------------------------------------------------------- back to 5.0


def nested(text: str) -> fc.FileResult:
    res = one(text, target="5.0")
    assert not res.refusals, [r.as_dict() for r in res.refusals]
    return res


def test_the_5_0_target_restores_the_issues_example():
    src = "\\d .example.inner\noffset:2\nadd:{[amount] amount+offset}\n\\d .\n"
    flat = converted(src)
    back = nested(flat)
    assert back.output == src
    assert back.nested == [
        {"line": 1, "context": ".example.inner", "statements": 2, "shortened": 3}
    ]


def test_a_name_is_shortened_only_where_it_binds_the_same():
    flat = (
        "\\d .\n"
        ".a.b.k:1\n"
        ".a.b.p:{[k] k+.a.b.k}\n"  # bare, it would be the parameter
        ".a.b.i:{x+.a.b.x}\n"  # bare, it would be the implicit argument
        ".a.b.s:{.a.b.k:2}\n"  # bare, the assignment would make it local
        ".a.b.q:{select from .a.b.t where px>.a.b.k}\n"  # bare, it could be a column
        ".a.b.g:{.a.b.n::1; .a.b.c+:1; .a.b.k}\n"
        "\\d .\n"
    )
    assert nested(flat).output.splitlines() == [
        "\\d .a.b",
        "k:1",
        "p:{[k] k+.a.b.k}",
        "i:{x+.a.b.x}",
        "s:{.a.b.k:2}",
        "q:{select from t where px>.a.b.k}",
        "g:{n::1; c+:1; k}",
        "\\d .",
    ]


@pytest.mark.parametrize(
    ("flat", "why"),
    [
        ("\\d .\n.a.b.f:{g x}\n\\d .\n", "`g` (line 2) means the root's, and would mean .a.b.g"),
        ("\\d .\n.a.b.f:{x}\ny:1\n\\d .\n", None),
        ("\\d .\n.a.b.f:{x}\n.a.c.g:{x}\n\\d .\n", "it defines in .a.b, .a.c"),
        ("\\d .\n.a.b.v:get `w\n\\d .\n", "get `w (line 2) runs while the file loads"),
        (
            "\\d .\nr:1\n.a.b.f:{select from t where px>r}\n\\d .\n",
            None,
        ),
        ("\\d .\n.a.b.f:{x}\n\\l other.q\n", None),  # ends at \\l, not \\d
        ("\\d .\n.a.b.f:{x}\n", None),  # ends with the file
        ("\\d .\n.a.f:{x}\n\\d .\n", None),  # single-level: never nested
    ],
)
def test_a_block_that_would_change_meaning_stays_flat(flat, why):
    res = nested(flat)
    assert (res.output, res.action, res.nested) == (flat, "unchanged", [])
    if why is not None:
        assert any(why in note for note in res.notes), res.notes


def test_nesting_is_idempotent_and_leaves_native_contexts_alone():
    native = "\\d .a.b\nf:{g x}\n\\d .\n"
    assert nested(native).output == native
    flat = "\\d .\n.a.b.f:{.a.b.g x}\n\\d .\n"
    once = nested(flat).output
    assert once == native and nested(once).output == once


def test_every_binding_survives_the_round_trip_through_5_0():
    """For every q file in the tree, 4.0 -> 5.0 -> 4.0 gives exactly the 4.0
    output - and since the 4.0 output names every binding explicitly, that is
    every binding preserved. The original files through 5.0 likewise."""
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.q"], capture_output=True, text=True, check=True
    ).stdout.split()
    originals = {
        p: (REPO / p).read_text(encoding="utf-8") for p in files if not p.startswith("lib/")
    }
    flat = {p: r.output for p, r in convert(originals, allow=True).items() if not r.refusals}
    assert len(flat) > 200
    renested = convert(flat, target="5.0")
    assert sum(1 for r in renested.values() if r.nested) > 20
    again = convert({p: r.output for p, r in renested.items()}, allow=True)
    assert [p for p in flat if again[p].output != flat[p]] == []
    direct = convert(originals, target="5.0")
    assert [p for p, r in direct.items() if r.output != originals[p]] == []


# ------------------------------------------------------------- refusals


@pytest.mark.parametrize(
    ("src", "code", "line", "says"),
    [
        ("\\d .a.b\n`v set 1\n", "load-time-lookup", 2, "`v set runs while the file loads"),
        (
            "\\d .a.b\npull:{get `v}\nx:pull[]\n",
            "load-time-lookup",
            3,
            "calls .a.b.pull while the file loads, inside .a.b; "
            "it resolves a name at run time (get `v",
        ),
        (
            "\\d .a.b\n{[n] n set 1}[`v]\n",
            "load-time-computed",
            2,
            "pass --allow-computed-names",
        ),
        ("\\d .a.b\n\\l other.q\n", "system-in-context", 2, "runs inside nested context .a.b"),
        ('f:{system "d .a.b"}\n', "context-in-string", 1, "switches to a nested context"),
        ("\\d .a.b\nf:{c.d+1}\n", "relative-dotted", 2, "`c.d` is a dotted name relative"),
        ("\\d .a.b\ncount:1\n", "reserved", 2, "assigns `count`, which q reserves"),
        (
            "\\d .a.b\nf:{[v] v::1}\n",
            "ambiguous-assign",
            2,
            "`v::` in a lambda where `v` is also local",
        ),
        (
            "\\d .a.b\nlim:1\nf:{select from t where px>lim}\n",
            "ambiguous-qsql",
            3,
            "`lim` in a qSQL phrase is a column if the table has one, else .a.b.lim",
        ),
        ("\\d .a.b\nf:{(1;2}\n", "unbalanced", 2, "unmatched `}`"),
        ('\\d .a.b\nf:"open\n', "lex", 2, "a string is never closed"),
        ("\\d a.b\n", "context", 1, "is not an absolute context"),
    ],
)
def test_refusals_name_the_line_and_the_reason(src, code, line, says):
    found = refusals(src)
    assert any(c == code and ln == line and says in why for c, ln, why in found), found


def test_a_refused_file_has_no_output():
    res = one("\\d .a.b\nok:{x}\n`v set 1\n")
    assert res.refusals and res.output == ""


def test_computed_names_can_be_accepted_as_warnings():
    res = one("\\d .a.b\n{[n] n set 1}[`v]\n", allow=True)
    assert not res.refusals
    assert [w.code for w in res.warnings] == ["load-time-computed"]
    assert res.output == "\\d .\n{[n] n set 1}[`v]\n"


def test_a_function_that_is_only_passed_along_runs_nothing_at_load():
    # register[...] receives the lambda; nothing here calls it while loading.
    src = "\\d .a.b\nregister[`x;{get `v}]\nregister:{[k;f] k}\n"
    assert not refusals(src)


def test_a_load_time_lookup_is_found_through_a_chain_of_calls():
    files = {
        "lib.q": "\\d .lib\nraw:{get `v}\nwrap:{raw[]}\n",
        "use.q": "\\d .a.b\nx:.lib.wrap[]\n",
    }
    found = convert(files)["use.q"].refusals
    assert [r.code for r in found] == ["load-time-lookup"]
    assert "calls .lib.wrap" in found[0].reason and "calls .lib.raw" in found[0].reason


# ------------------------------------------------------------------ CLI


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _run(root: Path, *args: str, capsys) -> tuple[int, dict]:
    """main() on paths relative to root, as a caller in root would pass them."""
    flags_with_values = {"--out", "--check", "--q", "--report", "--target", "--root", "--exclude"}
    argv, take = [], False
    for a in args:
        if take or a.startswith("-"):
            argv.append(a)
            take = a in flags_with_values
        else:
            argv.append(str(root / a))
    code = fc.main(["--root", str(root), *argv])
    return code, json.loads(capsys.readouterr().out)


def _digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


GOOD = {
    "src/m.q": "\\d .m.n\nf:{g x}\ng:{x+k}\nk:1\n\\d .\n\\l src/lib.q\n\\l elsewhere.q\n",
    "src/lib.q": "\\d .lib\nh:{x}\n",
}


def test_a_dry_run_writes_nothing(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    before = _digest(src)
    code, report = _run(src, "src", "--out", str(tmp_path / "out"), "--dry-run", capsys=capsys)
    assert (code, report["status"]) == (0, "ok")
    assert not (tmp_path / "out").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".out")]
    assert _digest(src) == before


def test_the_report_describes_the_conversion(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    code, report = _run(src, "src", "--out", str(tmp_path / "out"), capsys=capsys)
    assert code == 0
    assert "nothing else about kdb+ 4.0 compatibility" in report["claim"]
    files = {f["path"]: f for f in report["files"]}
    assert files["src/m.q"]["action"] == "transformed"
    assert files["src/lib.q"]["action"] == "unchanged"
    assert files["src/m.q"]["defined"] == {".m.n": ["f", "g", "k"]}
    assert report["namespace_mappings"] == {"src/m.q": [".m.n"]}
    assert report["dependencies"] == [{"path": "src/m.q", "line": 7, "load": "elsewhere.q"}]
    assert (tmp_path / "out" / "src" / "m.q").read_text() == (
        "\\d .\n.m.n.f:{.m.n.g x}\n.m.n.g:{x+.m.n.k}\n.m.n.k:1\n"
        "\\d .\n\\l src/lib.q\n\\l elsewhere.q\n"
    )


def test_a_refusal_publishes_nothing(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, {**GOOD, "src/bad.q": "\\d .a.b\n`v set 1\n"})
    code, report = _run(src, "src", "--out", str(tmp_path / "out"), capsys=capsys)
    assert (code, report["status"]) == (1, "refused")
    assert [(r["path"], r["line"], r["code"]) for r in report["refusals"]] == [
        ("src/bad.q", 2, "load-time-lookup")
    ]
    assert not (tmp_path / "out").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".out")]


def test_output_is_deterministic_and_idempotent(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    for name in ("a", "b"):
        assert _run(src, "src", "--out", str(tmp_path / name), capsys=capsys)[0] == 0
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")
    # Converting the converted tree again changes nothing.
    again = tmp_path / "again"
    code, report = _run(tmp_path / "a", "src", "--out", str(again), capsys=capsys)
    assert code == 0 and {f["action"] for f in report["files"]} == {"unchanged"}
    assert _digest(again) == _digest(tmp_path / "a")


def test_an_existing_output_is_replaced_only_when_asked(tmp_path, capsys):
    src, out = tmp_path / "repo", tmp_path / "out"
    _tree(src, GOOD)
    _tree(out, {"keep.txt": "mine"})
    code, report = _run(src, "src", "--out", str(out), capsys=capsys)
    assert code == 1 and "pass --force" in report["problems"][0]
    assert (out / "keep.txt").read_text() == "mine"
    assert _run(src, "src", "--out", str(out), "--force", capsys=capsys)[0] == 0
    assert not (out / "keep.txt").exists() and (out / "src" / "m.q").exists()


def test_output_inside_the_inputs_is_refused(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    code, report = _run(src, "src", "--out", str(src / "src" / "out"), capsys=capsys)
    assert code == 1 and "overlaps the root or the inputs" in report["problems"][0]


def test_vendored_code_is_left_out_unless_asked_for(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, {**GOOD, "lib/torq.q": "\\d .t.u\nf:{x}\n"})
    _, report = _run(src, ".", "--out", str(tmp_path / "o1"), "--dry-run", capsys=capsys)
    assert "lib/torq.q" not in {f["path"] for f in report["files"]}
    code, report = _run(
        src, "lib/torq.q", "--out", str(tmp_path / "o2"), "--dry-run", capsys=capsys
    )
    assert code == 1 and "pass --include-vendored" in report["problems"][0]
    code, report = _run(
        src, ".", "--out", str(tmp_path / "o3"), "--dry-run", "--include-vendored", capsys=capsys
    )
    assert code == 0 and "lib/torq.q" in {f["path"] for f in report["files"]}


def test_an_ignored_file_is_converted_only_when_named(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, {**GOOD, ".gitignore": "local/\n", "local/bundle.q": "\\d .b.c\nf:{x}\n"})
    subprocess.run(["git", "init", "-q", str(src)], check=True)
    _, report = _run(src, ".", "--out", str(tmp_path / "o1"), "--dry-run", capsys=capsys)
    assert "local/bundle.q" not in {f["path"] for f in report["files"]}
    named = str(src / "local" / "bundle.q")
    _, report = _run(src, named, "--out", str(tmp_path / "o2"), "--dry-run", capsys=capsys)
    assert [f["path"] for f in report["files"]] == ["local/bundle.q"]


def _fake_q(tmp_path: Path, prints: str) -> str:
    q = tmp_path / "fakeq"
    q.write_text(f"#!/bin/sh\necho '{prints}'\nexit 0\n")
    q.chmod(0o755)
    return str(q)


def test_a_check_must_print_its_marker_to_pass(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    check = tmp_path / "check.q"
    check.write_text("/ stand-in\n")
    silent = _fake_q(tmp_path, "loaded")
    code, report = _run(
        src,
        "src",
        "--out",
        str(tmp_path / "out"),
        "--check",
        str(check),
        "--q",
        silent,
        capsys=capsys,
    )
    assert (code, report["status"]) == (2, "validation-failed")
    assert report["validation"][0]["exit_code"] == 0 and not report["validation"][0]["passed"]
    assert not (tmp_path / "out").exists()
    ok = _fake_q(tmp_path, fc.CHECK_OK)
    code, report = _run(
        src,
        "src",
        "--out",
        str(tmp_path / "out"),
        "--check",
        str(check),
        "--q",
        ok,
        capsys=capsys,
    )
    assert code == 0 and report["validation"][0]["passed"] and (tmp_path / "out").exists()


# ------------------------------------------------------- real interpreters

#: Each converted fixture computes .fixture.got and states .fixture.expected.
FIXTURES = {
    "binding": """\\d .fx.inner
offset:2
add:{[amount] amount+offset}
twice:{add add x}
shadow:{[offset] offset+1}
local:{offset:100; offset+x}
explicit:{[a] a+x}
x:10
nested:{[a] {[b] b+offset} each a}
curried:{[a;b] a+b+offset}[1]
\\d .
.fixture.got:(.fx.inner.add 3;.fx.inner.twice 1;.fx.inner.shadow 5;.fx.inner.local 1;
  .fx.inner.explicit 1;.fx.inner.nested 1 2;.fx.inner.curried 10)
.fixture.expected:(5;5;6;101;11;3 4;13)
""",
    "mutation": """\\d .fx.state
n:0
seen:(`$())!`boolean$()
events:()
bump:{n+:1; n}
mark:{[k] seen[k]:1b; count seen}
record:{[e] events,:enlist e; count events}
reset:{total::42}
\\d .
.fx.state.bump[];
.fixture.b:.fx.state.bump[];
.fx.state.mark`a; .fixture.m:.fx.state.mark`b;
.fx.state.record`x; .fixture.r:.fx.state.record`y;
.fx.state.reset[];
.fixture.got:(.fixture.b;.fixture.m;.fixture.r;.fx.state.total;.fx.state.n)
.fixture.expected:(2;2;2;42;2)
""",
    "contexts": """\\d .fa
base:1
getbase:{base}
\\d .fa.b
base:2
getbase:{base}
\\d .fa.c
base:3
getbase:{base}
\\d .
top:4
\\d .fa.b
more:{base+10}
\\d .
.fixture.got:(.fa.getbase[];.fa.b.getbase[];.fa.c.getbase[];top;.fa.b.more[])
.fixture.expected:(1;2;3;4;12)
""",
    "qsql": """\\d .fx.q
trades:([] sym:`a`b`a; px:1 2 3; qty:10 20 30)
big:{[t;minpx] select sym, px from t where px>minpx}
agg:{select total:sum qty by sym from trades}
first2:{select from trades where i<2}
dbl:{update qty:qty*2 from trades}
pxa:{exec px from trades where sym=`a}
\\d .
.fixture.got:(.fx.q.big[.fx.q.trades;1];.fx.q.agg[];.fx.q.first2[];.fx.q.dbl[];.fx.q.pxa[])
.fixture.expected:(([] sym:`b`a; px:2 3);([sym:`a`b] total:40 20);
  ([] sym:`a`b; px:1 2; qty:10 20);([] sym:`a`b`a; px:1 2 3; qty:20 40 60);1 3)
""",
    "literals": """\\d .fx.lit
offset:5
msg:"offset"
s:`offset
t:([] offset:1 2)
f:{[] (offset;"offset";`offset)}
put:{`fxval set x}
\\d .
.fx.lit.put 7;
.fixture.got:(.fx.lit.msg;.fx.lit.s;.fx.lit.t;.fx.lit.f[];fxval)
.fixture.expected:("offset";`offset;([] offset:1 2);(5;"offset";`offset);7)
""",
}

RUNNER = """r:@[system;"l fixture.q";{-2 "FIXTURE_LOAD_FAILED: ",x; exit 1}];
$[.fixture.got~.fixture.expected;-1 "FIXTURE_OK";
  [-2 "FIXTURE_MISMATCH: ",.Q.s1 .fixture.got; exit 1]];
exit 0
"""


def _identify(q: str) -> str:
    spec = importlib.util.spec_from_file_location("peachq", REPO / "scripts" / "peachq.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.identify(q)


def _peachq() -> str | None:
    """A PeachQ already on this machine; never built here."""
    candidates = [os.environ.get("UQF_PEACHQ", "")]
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uqf" / "peachq"
    candidates += sorted(str(p) for p in cache.glob("*/q"))
    return next(
        (c for c in candidates if c and Path(c).is_file() and _identify(c) == "peachq"), None
    )


def _kdbx() -> str | None:
    q = shutil.which(os.environ.get("QCMD") or "q")
    return q if q and _identify(q) == "kdbx" else None


INTERPRETERS = {
    # PeachQ refuses a nested `\d`, as kdb+ 4.0 does: the converted tree
    # must run on it, the original cannot.
    "peachq": (_peachq, False),
    # KDB-X runs both, and they must agree.
    "kdbx": (_kdbx, True),
    # The server-equivalent 4.0 build, where one is installed.
    "kdb40": (lambda: os.environ.get("UQF_KDB40") or None, False),
}


def _q_for(name: str) -> tuple[str, bool]:
    find, nested_ok = INTERPRETERS[name]
    q = find()
    if not q:
        pytest.skip(f"no {name} interpreter on this machine")
    return q, nested_ok


def _run_q(q: str, cwd: Path, script: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [q, str(script), "-q"],
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize("interp", sorted(INTERPRETERS))
@pytest.mark.parametrize("fixture", sorted(FIXTURES))
def test_converted_fixtures_compute_what_the_originals_do(interp, fixture, tmp_path):
    q, nested_ok = _q_for(interp)
    trees = {"converted": converted(FIXTURES[fixture])}
    if nested_ok:
        trees["original"] = FIXTURES[fixture]
        trees["renested"] = nested(trees["converted"]).output
    for name, text in trees.items():
        d = tmp_path / name
        d.mkdir()
        (d / "fixture.q").write_text(text)
        (d / "run.q").write_text(RUNNER)
        r = _run_q(q, d, d / "run.q")
        assert "FIXTURE_OK" in r.stdout, (name, r.stdout, r.stderr)


@pytest.mark.parametrize("interp", sorted(INTERPRETERS))
def test_the_converted_status_and_interval_modules_work(interp, tmp_path, capsys):
    q, nested_ok = _q_for(interp)
    out = tmp_path / "portable"
    code, report = _run(
        REPO,
        "src/etl/core/status.q",
        "src/etl/core/intervals.q",
        "--out",
        str(out),
        "--check",
        str(CHECK),
        "--q",
        q,
        capsys=capsys,
    )
    assert (code, report["status"]) == (0, "ok"), report["validation"]
    assert report["validation"][0]["output"][-1] == "PORTABLE_CHECK_OK status_intervals"
    original = _run_q(q, REPO, CHECK)
    if nested_ok:
        assert "PORTABLE_CHECK_OK" in original.stdout, original.stderr
    else:
        assert "PORTABLE_CHECK_OK" not in original.stdout


def _our_globals(path: str, toks: list, st, lam) -> tuple[set[str], set[str]]:
    """The names the converter qualifies in one lambda, and the qSQL column
    names it leaves alone there."""
    ours, cols = set(), set()
    for u in st.uses:
        name = toks[u.idx].text
        if u.lam is not lam or u.role in ("assign", "column") or not fc._relative(name):
            continue
        if name in fc.BUILTINS or name in fc.QSQL_START or lam.is_local(name):
            continue
        (cols if u.column_ctx else ours).add(name)
    return ours, cols


@pytest.mark.parametrize("interp", sorted(INTERPRETERS))
def test_bindings_agree_with_qs_own_parser(interp, tmp_path):
    """For every lambda in a nested context in the tree, the names the
    converter qualifies are exactly the globals q itself lists for it -
    `(value f) 3` - less builtins and qSQL columns (which q may list too)."""
    q, _ = _q_for(interp)
    files = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.q"], capture_output=True, text=True, check=True
    ).stdout.split()
    cases = []
    for rel in files:
        if rel.startswith("lib/"):
            continue
        toks = fc.lex((REPO / rel).read_text(encoding="utf-8"))
        for lo, hi, ctx in fc._contexts(toks):
            if toks[lo].kind in ("sys", "tail") or not fc._nested(ctx):
                continue
            st = fc.Statement(toks, lo, hi, rel)
            for lam in st.lambdas:
                a, b = lam.span
                text = "".join(t.text for t in toks[a : b + 1])
                cases.append((rel, toks[a].line, text, *_our_globals(rel, toks, st, lam)))
    assert len(cases) > 500
    body = [".o.r:()!()"]
    body += [
        f'.o.r[{i}]:@[{{" " sv string (value x) 3}};{text};{{"ERR ",x}}]'
        for i, (_, _, text, _, _) in enumerate(cases)
    ]
    body += ["-1 .j.j .o.r;", "exit 0"]
    script = tmp_path / "globals.q"
    script.write_text("\n".join(body) + "\n")
    r = _run_q(q, tmp_path, script)
    answers = json.loads(r.stdout.strip().splitlines()[-1])
    disagree = []
    for i, (rel, line, _, ours, cols) in enumerate(cases):
        said = answers[str(i)]
        assert not said.startswith("ERR"), (rel, line, said)
        theirs = {n for n in said.split() if not n.startswith(".") and n not in fc.BUILTINS}
        if ours - theirs or theirs - ours - cols:
            disagree.append((rel, line, sorted(ours - theirs), sorted(theirs - ours - cols)))
    assert not disagree


def test_the_5_0_report_names_the_rebuilt_contexts(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, {"src/m.q": "\\d .\n.m.n.f:{.m.n.g x}\n.m.n.g:{x}\n\\d .\n"})
    code, report = _run(
        src, "src", "--out", str(tmp_path / "out"), "--target", "5.0", capsys=capsys
    )
    assert code == 0 and "nested again" in report["claim"]
    assert report["files"][0]["nested"] == [
        {"line": 1, "context": ".m.n", "statements": 2, "shortened": 3}
    ]
    assert report["namespace_mappings"] == {"src/m.q": [".m.n"]}
    assert (tmp_path / "out" / "src" / "m.q").read_text() == "\\d .m.n\nf:{g x}\ng:{x}\n\\d .\n"


def test_exclude_leaves_folders_and_globs_out(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(
        src,
        {
            **GOOD,
            "tests/t.q": "\\d .a.b\n`v set 1\n",  # would be refused
            "src/gen/x.q": "\\d .g.h\nf:{x}\n",
            "src/test_y.q": "\\d .y.z\nf:{x}\n",
        },
    )
    code, report = _run(
        src,
        ".",
        "--out",
        str(tmp_path / "out"),
        "--exclude",
        "tests",
        "--exclude",
        "src/gen/",
        "--exclude",
        "*/test_*.q",
        capsys=capsys,
    )
    assert (code, report["status"]) == (0, "ok"), report["refusals"]
    assert report["excluded"] == ["src/gen/x.q", "src/test_y.q", "tests/t.q"]
    assert {f["path"] for f in report["files"]} == {"src/m.q", "src/lib.q"}
    assert not (tmp_path / "out" / "tests").exists()
    assert not (tmp_path / "out" / "src" / "gen").exists()


def test_an_excluded_file_still_informs_the_others(tmp_path, capsys):
    # `lim` is the context's global in the excluded file, so the qSQL phrase
    # naming it in the converted one is still ambiguous - and still refused.
    src = tmp_path / "repo"
    _tree(
        src,
        {
            "src/a.q": "\\d .a.b\nf:{select from t where px>lim}\n",
            "src/held.q": "\\d .a.b\nlim:1\n",
        },
    )
    code, report = _run(
        src, "src", "--out", str(tmp_path / "out"), "--exclude", "src/held.q", capsys=capsys
    )
    assert code == 1 and [r["code"] for r in report["refusals"]] == ["ambiguous-qsql"]


def test_an_explicitly_named_file_is_still_excluded(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    code, report = _run(
        src,
        "src/m.q",
        "--out",
        str(tmp_path / "out"),
        "--exclude",
        "src/m.q",
        "--dry-run",
        capsys=capsys,
    )
    assert code == 0 and report["files"] == [] and report["excluded"] == ["src/m.q"]


def test_diff_debug_and_the_summary_go_to_stderr(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    code = fc.main(
        [
            "--root",
            str(src),
            str(src / "src"),
            "--out",
            str(tmp_path / "out"),
            "--dry-run",
            "--diff",
            "--debug",
        ]
    )
    cap = capsys.readouterr()
    assert code == 0 and json.loads(cap.out)["status"] == "ok"
    assert "--- a/src/m.q\n+++ b/src/m.q\n" in cap.err
    assert "\n-f:{g x}\n" in cap.err and "\n+.m.n.f:{.m.n.g x}\n" in cap.err
    assert "src/m.q:3:6 k -> .m.n.k (refers to a global of .m.n)" in cap.err
    assert "1 transformed, 1 unchanged, 0 excluded" in cap.err and "dry run" in cap.err
    report = json.loads(cap.out)
    m = next(f for f in report["files"] if f["path"] == "src/m.q")
    assert {
        "line": 1,
        "column": 1,
        "before": "\\d .m.n",
        "after": "\\d .",
        "why": "4.0 has no nested contexts",
    } in m["decisions"]


def test_quiet_prints_nothing_but_the_report(tmp_path, capsys):
    src = tmp_path / "repo"
    _tree(src, GOOD)
    fc.main(
        ["--root", str(src), str(src / "src"), "--out", str(tmp_path / "o"), "--dry-run", "--quiet"]
    )
    cap = capsys.readouterr()
    assert cap.err == "" and json.loads(cap.out)["status"] == "ok"
