"""When each old spelling a rename keeps is due to go (#1796).

A rename kept its old spelling for one release (#1738 rule 6): the stable tag
that first shipped the rename shipped the old spelling too, and the next
stable tag must not. This table says which release that is - one row per old
spelling, grouped under the stable tag that was the newest when the rename
landed. The tag after it is the one that shipped the rename; the tag after
THAT is when the old spelling is overdue.

It is empty: the last old spellings went in #2752, and a rename since #2237
keeps none - its old spelling is refused, naming the new one (renames.py's
`refused()`). test_cli_rename_expiry.py still walks the command tree and fails
on any hidden option or command with no row here, when a row names a spelling
that is gone, and when CHANGELOG.md's stable tags show a row has outlived its
one release.
"""

from __future__ import annotations

#: The newest stable tag when the rename landed -> the old spellings it left.
#: The next tag after the key shipped them; the one after that must not.
RENAMED_AFTER: dict[str, frozenset[str]] = {}
