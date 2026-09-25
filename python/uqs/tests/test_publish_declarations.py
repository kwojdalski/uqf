"""Every table a streaming job publishes is one it declares in `publishes`.

`publishes` is what the runner, the registry and the generated diagrams all
read as the job's output. Nothing on the publish path checks it:
`.qstream.wire` accepts any publisher, so a job that publishes into a table
it never declared runs fine, and the registry, the DAG and `uqs summary` all
describe an output set that is not the real one.

This reads the calls back out of each job file and compares. It can only do
that for the one call shape every job uses today,
`.qsub.<job>.publish[`table;...]`, so any other shape - a bare `publish[`
under `\\d`, a table held in a variable, another job's namespace - is
reported as unverifiable rather than passed. A job that needs one of those
is a reason to extend this reader, not to be exempt from it.

The reverse is deliberately NOT checked. A declared table with no literal
publish call is legitimate: a normalizer is published by the framework under
its own name, and `config_change` is published by `.qcfg` on a job's behalf.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqs.model.declarations import Declaration, read_declarations, read_file_text
from uqs.paths import STREAM_DIR, repo_root

# A q string, with its escapes, so prose inside a `note` cannot look like code.
_Q_STRING = re.compile(r'"(?:\\.|[^"\\])*"')

# Every standalone `publish` identifier, with whatever qualifies it. The
# lookbehind stops `publishes` and `.qpipe.publish_x` style names matching.
_ANY_PUBLISH = re.compile(r"(?<![\w.])(?P<name>[\w.]*\bpublish)\b(?!\w)(?P<after>\s*[\[:]?)")

# The one checkable shape: a call in a job's own namespace, table as a literal.
_CHECKABLE = re.compile(r"\.qsub\.(?P<job>\w+)\.publish\[\s*`(?P<table>\w+)")


def _blank_comments(source: str) -> str:
    """Comment lines emptied rather than dropped, so line numbers stay true.

    The same line rule as `declarations.strip_q_comments`, which drops them.
    """
    return "\n".join("" if ln.lstrip().startswith("/") else ln for ln in source.splitlines())


def _blank_string(m: re.Match[str]) -> str:
    return '""' + "\n" * m.group(0).count("\n")


def publish_problems(source: str, declarations: list[Declaration]) -> list[str]:
    """What is wrong with the publish calls in one job file's `source`.

    `declarations` are the jobs that file declares. Returns one message per
    problem, empty when every call names a declared table of its own job.
    """
    code = _Q_STRING.sub(_blank_string, _blank_comments(source))
    declared = {d.name: set(d.publishes) for d in declarations}
    problems: list[str] = []
    for m in _ANY_PUBLISH.finditer(code):
        line = code.count("\n", 0, m.start()) + 1
        name, after = m.group("name"), m.group("after").strip()
        # `publish:.qstream.unwired `job` - the stub the runner replaces.
        if after == ":" and name == "publish":
            continue
        call = _CHECKABLE.match(code, m.start())
        if call is None:
            problems.append(
                f"line {line}: `{name}` is not a `.qsub.<job>.publish[`table;...]` call, "
                "so its table cannot be checked against `publishes`"
            )
            continue
        job, table = call.group("job"), call.group("table")
        if job not in declared:
            problems.append(
                f"line {line}: publishes as .qsub.{job}, which this file does not declare "
                f"(it declares {', '.join(sorted(declared)) or 'nothing'})"
            )
        elif table not in declared[job]:
            problems.append(
                f"line {line}: {job} publishes `{table}` but declares publishes "
                f"{sorted(declared[job]) or '(nothing)'}"
            )
    return problems


def _stream_files(root: Path) -> dict[Path, list[Declaration]]:
    by_file: dict[Path, list[Declaration]] = {}
    for d in read_declarations(root):
        by_file.setdefault(d.path, [])
        by_file[d.path].append(d)
    stream_dir = (root / STREAM_DIR).resolve()
    return {p: ds for p, ds in by_file.items() if (root / p).resolve().parent == stream_dir}


# ------------------------------------------------------------------ the tree


def test_every_streaming_file_is_read():
    """A reader that silently found no files would pass every check below."""
    root = repo_root()
    found = {(root / p).resolve() for p in _stream_files(root)}
    on_disk = {p.resolve() for p in (root / STREAM_DIR).glob("*.q")}
    assert found == on_disk, f"job files with no declaration read: {sorted(on_disk - found)}"


def test_every_published_table_is_declared():
    root = repo_root()
    problems = {
        str(p): ps
        for p, ds in _stream_files(root).items()
        if (ps := publish_problems((root / p).read_text(), ds))
    }
    assert not problems, problems


# ------------------------------------------------------------------ the reader


def _file(source: str) -> list[Declaration]:
    return read_file_text(source, Path("x.q"))


_DECL = (
    ".qstream.define[`j;`procname`subscribe_to`publishes`on_batch!("
    "`j1;enlist `a;enlist `out;.qsub.j.on_batch)];\n"
)


def test_a_declared_table_passes():
    src = "publish:.qstream.unwired `j;\n.qsub.j.publish[`out;rows];\n" + _DECL
    assert publish_problems(src, _file(src)) == []


def test_an_undeclared_table_is_caught():
    src = ".qsub.j.publish[`other;rows];\n" + _DECL
    (problem,) = publish_problems(src, _file(src))
    assert "publishes `other`" in problem and "['out']" in problem


def test_publishing_as_another_job_is_caught():
    src = ".qsub.k.publish[`out;rows];\n" + _DECL
    (problem,) = publish_problems(src, _file(src))
    assert "publishes as .qsub.k" in problem


@pytest.mark.parametrize(
    "call",
    ["publish[`out;rows]", ".qsub.j.publish[tbl;rows]", "f:.qsub.j.publish; f[`out;rows]"],
    ids=["bare-under-d", "table-in-a-variable", "passed-as-a-value"],
)
def test_a_call_that_cannot_be_checked_is_reported_not_passed(call):
    src = call + ";\n" + _DECL
    problems = publish_problems(src, _file(src))
    assert problems and all("cannot be checked" in p for p in problems)


def test_comments_and_strings_are_not_calls():
    src = (
        '/ .qsub.j.publish[`nope;rows]\nmsg:"call .qsub.j.publish[`nope;rows] yourself";\n' + _DECL
    )
    assert publish_problems(src, _file(src)) == []
