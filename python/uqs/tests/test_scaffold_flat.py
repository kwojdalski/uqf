"""A scaffolded src/ file is written fully qualified from the root (#1411)."""

from __future__ import annotations

from pathlib import Path

from uqs.scaffold.write import CONVERTER, flat_source

REPO = Path(__file__).resolve().parents[3]


def test_a_scaffolded_declaration_is_written_fully_qualified():
    text = "\\d .qpipe.job.foo\nup:{[t;x] helper x}\nhelper:{x}\n\\d .\n"
    out = flat_source(text, REPO)
    assert "\\d .qpipe" not in out
    assert ".qpipe.job.foo.up:{[t;x] .qpipe.job.foo.helper x}" in out
    assert ".qpipe.job.foo.helper:{x}" in out


def test_text_that_opens_no_context_is_left_as_written():
    text = ".qetl.job.stream.define[`foo;()!()];\n"
    assert flat_source(text, REPO) == text


def test_a_tree_without_the_converter_uses_the_one_uqs_was_installed_from(tmp_path):
    out = flat_source("\\d .a.b\nf:{x}\n\\d .\n", tmp_path)
    assert "\\d .a.b" not in out and ".a.b.f:{x}" in out


def _marked_root(root: Path, marker: str) -> Path:
    """A tree whose converter is the real one, tagging its output with `marker`."""
    converter = root / CONVERTER
    converter.parent.mkdir(parents=True)
    real = (REPO / CONVERTER).read_text()
    converter.write_text(
        real + f"\n\n_convert = Converter.convert\n"
        f"def _tagged(self, path, text):\n"
        f"    result = _convert(self, path, text)\n"
        f"    result.output = result.output + '/ {marker}\\n'\n"
        f"    return result\n"
        f"Converter.convert = _tagged\n"
    )
    return root


def test_each_tree_converts_with_its_own_converter(tmp_path):
    """#1601: a second tree in the same process kept the first one's converter."""
    text = "\\d .a.b\nf:{x}\n\\d .\n"
    first = _marked_root(tmp_path / "first", "first-root")
    second = _marked_root(tmp_path / "second", "second-root")
    assert "/ first-root" in flat_source(text, first)
    out = flat_source(text, second)
    assert "/ second-root" in out and "/ first-root" not in out
