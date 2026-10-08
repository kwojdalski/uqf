"""A `local` source's declared table_name is the table its query reads (#842).

`.qetl.source.validate_live` vets `table_name`'s columns before a run. hdb_transfer
declared its own target, trades_copy, while its query read `trades` - so the
check passed against a table the source never reads, and a live run then failed
at its first window. Read from the q text, so this runs without q.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uqs.paths import SOURCE_DIR, repo_root

ROOT = repo_root()

#: (source_name;`table_name;... - the define call's second value.
_DECLARED = re.compile(r"\(source_name;`([a-z_][a-z0-9_]*);")
#: read[`trades;...] or read[(`trades;`time`sym);...] inside a local query.
_READS = re.compile(r"\bread\[\(?`([a-z_][a-z0-9_]*)")


def _local_sources() -> list[Path]:
    return [
        path
        for path in sorted((ROOT / SOURCE_DIR).glob("*.q"))
        if re.search(r"^transport:`local\b", path.read_text(), re.MULTILINE)
    ]


def test_there_is_a_local_source_to_check() -> None:
    assert _local_sources(), "no `local` source left - this test guards nothing"


@pytest.mark.parametrize("path", _local_sources(), ids=lambda p: p.stem)
def test_table_name_is_the_table_the_query_reads(path: Path) -> None:
    text = path.read_text()
    declared = _DECLARED.findall(text)
    assert len(declared) == 1, f"{path.name}: expected one .qetl.source.define call"
    query = text[text.index("query:") : text.index("fixture:")]
    read = set(_READS.findall(query))
    assert read == {declared[0]}, (
        f"{path.name} declares table_name `{declared[0]} but its query reads "
        f"{', '.join('`' + t for t in sorted(read)) or 'nothing'} - validate_live "
        "would check a table the source does not read"
    )
