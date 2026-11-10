"""When each old spelling a rename keeps is due to go (#1796).

A rename keeps its old spelling for one release (#1738 rule 6, renames.py):
the stable tag that first ships the rename ships the old spelling too, and the
next stable tag must not. Nothing at a call site says which release that is,
so this table does - one row per old spelling, grouped under the stable tag
that was the newest when the rename landed. The tag after it is the one that
shipped the rename; the tag after THAT is when the old spelling is overdue.

The table is keyed by what an operator types (`uqs start --port`, `uqs up`),
so it needs no `since=` threaded through the ~90 `renamed()`/`alias_command()`
sites: test_cli_rename_expiry.py walks the command tree and fails when an old
spelling has no row here, when a row names a spelling that is gone, and when
CHANGELOG.md's stable tags show a row has outlived its one release.

A new rename adds its old spelling under the newest `## stable/...` heading in
CHANGELOG.md (add that tag's group if it is not here yet). Removing an old
spelling deletes its row along with the alias.
"""

from __future__ import annotations

#: The newest stable tag when the rename landed -> the old spellings it left.
#: The next tag after the key shipped them; the one after that must not.
RENAMED_AFTER: dict[str, frozenset[str]] = {
    "stable/2026-10-29": frozenset(
        {
            # #1845: who writes the schema block is --schema-writer; the old flag refuses,
            # naming it
            "uqs job attachment add --remote-schema-file",
            # #1857: one vocabulary - a name, a type and a unit per concept
            "uqs job new --procname",
            "uqs deploy check --procs",
            "uqs deploy check --deadline",
            "uqs data seed --overwrite",
            "uqs job attachment contract approve --conflict",
            "uqs job attachment contract approve --max-rows",
            "uqs job preview --sample",
            "uqs deploy build --output",
            "uqs deploy component build --output",
            "uqs deploy push --qcmd",
            "uqs deploy push --qhome",
            *(
                f"uqs setup odbc {verb} {old}"
                for verb in ("install", "rollback", "status", "env")
                for old in ("--home", "--qhome")
            ),
            # #1856: one grammar per option across `uqs data`
            "uqs data seed --tables",
            "uqs deploy check --tables",
            "uqs data alter --rename OLD=NEW",
            # #1858: every `uqs list` kind is its noun's; the receiver a noun
            "uqs list",
            "uqs list processes",
            "uqs list profiles",
            "uqs list fields",
            "uqs list env",
            "uqs list dependencies",
            "uqs list backfills",
            "uqs job receive",
            "uqs config set PROC FIELD VALUE",
            "uqs job backfill --mode dry-run",
            # #1855: one meaning per word - the saved attachment is --attachment
            "uqs logs --attach",
            "uqs job backfill --attach",
            "uqs job receive --attach",
            "uqs job runs list --attach",
            "uqs job runs show --attach",
            "uqs job attachment contract approve --attach",
            "uqs job attachment contract list --attach",
            "uqs data check --attach",
            "uqs data alter --attach",
            # #1855: the base port is --base-port; "runtime" is a uqs runtime only
            "uqs job attachment add --stack-id",
            "uqs job attachment add --runtime-only",
            # #1855: "target" is a deploy target; the kdb+ version is --q-version
            "uqs deploy build --q-target",
            # #1855: a bundled q, TorQ or Python is a component, not a runtime
            "uqs deploy build --python-runtime",
            "uqs deploy push --python-runtime",
            "uqs deploy push --python-runtime-sha256",
            "uqs deploy push --q-runtime",
            "uqs deploy push --q-runtime-sha256",
            "uqs deploy push --torq-runtime",
            "uqs deploy push --torq-runtime-sha256",
            # #1855: --release is a release's name; a directory is --release-dir
            "uqs target adopt --release",
            # #1855: --schema is a table's columns; a schema file is --schema-file
            "uqs data replay --schema",
            # #1855: process_overrides.csv is `config list`; `override` is a bypass
            "uqs list overrides",
            # #1890: one name per thing across surfaces
            "uqs data replay --dir",
            "uqs logs --run",
            "uqs deploy build --kafka-home",
            "uqs deploy build --with-kafka-connector",
            "uqs connector start --topic",
            "uqs connector restart --topic",
            "uqs job new --kind external",
            # #1854: a command written with --apply is dry by default; its
            # --dry-run says nothing more
            "uqs data alter --dry-run",
            "uqs data alter -n",
            "uqs data migrate --dry-run",
            "uqs data migrate -n",
            "uqs target adopt --dry-run",
            "uqs target adopt -n",
            # #2235: replay and seed wrote by default; a dry run is the default
            # now, so their --dry-run is refused, naming --apply
            "uqs data replay --dry-run",
            "uqs data replay -n",
            "uqs data seed --dry-run",
            "uqs data seed -n",
            # #1852: a bypass is --override NAME --reason, never a bare flag
            "uqs deploy build --allow-dirty",
            "uqs deploy build --allow-computed-names",
            "uqs deploy push --allow-dirty",
            "uqs deploy push --fix-hdb",
            "uqs deploy push --break-lock",
            "uqs deploy rollback --break-lock",
            "uqs job backfill --any-writer",
            "uqs job remove --even-if-edited",
            # #1853: a secret is never an argument - the variable holding it is
            # #1884: an override name is lower case, words joined by `-`; a finding
            # code's own spelling (TORQ_VERSION:stp1) still parses, warning
            "uqs job attachment add --override CODE:COMPONENT",
            "uqs query --passwd",
            "uqs schema --passwd",
            "uqs connector start --tp-credential",
            "uqs connector restart --tp-credential",
        }
    ),
}
