# kx-dbmaint - vendoring notice

Vendored from https://github.com/KxSystems/kdb at commit
`1daebf73349199b1318649fc2c5656cab2b4a7f2` (the repository's `master` head on
2026-10-16), paths `utils/dbmaint.q` and its documentation `utils/dbmaint.md`.
`dbmaint.q` was last changed upstream at `02c11e98e0c00fde3119c5383994e8063ee64de5`
(2018-07-30, "change to enum function to prevent compression of sym file when
.z.zd is set"), so the file is identical at both; `dbmaint.md` was last changed
at `063db9eb9311460e646aa2d4af6ae0d148e82821` (2023-04-05), which is why the
whole copy is pinned to the head rather than to the older commit. Distributed
under the Apache License 2.0; the full text is at `lib/kx-dbmaint/LICENSE-kx-kdb`
(upstream's `LICENSE` at the same commit, renamed to match this tree's other
vendored licences). Upstream carries no separate NOTICE file; this one records
the provenance. Nothing else in the repository was copied.

## What was copied

| File               | Upstream                | git blob                                   |
| ---                | ---                     | ---                                        |
| `dbmaint.q`        | `utils/dbmaint.q`       | `d00d97cf4d47a5e4e2e743568d699deb2da342fa` |
| `dbmaint.md`       | `utils/dbmaint.md`      | `6dede35348ab0131ff287cf5d36e7f8937e879c2` |
| `LICENSE-kx-kdb`   | `LICENSE`               | `8dada3edaf50dbc082c9a125058f25def75e625a` |

All three are byte-for-byte upstream (`git hash-object` reproduces the blob
ids above). Nothing was edited, reformatted or renamed other than the licence.

## Why

`scripts/gates/fill_hdb_partitions.q` repairs a historical HDB whose partitions
predate columns or tables the schema now declares (`uqs data hdb-check --fix`,
`uqs deploy ... --fix-hdb`). It used to carry its own copy of what dbmaint's
`add1col` does. It now decides what is missing from the declared schema and
calls dbmaint's `allpaths` and `add1col` to write it (#1423). The script's
header lists what it uses and what stays its own, and why.

## Distribution

Apache 2.0 asks that its licence travel with redistributed copies. Release
artifacts (`uqs deploy build`) normally exclude everything under `lib/`; this
directory is the one exception (`uqs.deploy.payload`), so a release carries
`dbmaint.q`, `dbmaint.md`, `LICENSE-kx-kdb` and this notice, and the repair
script loads offline from it.

## Not used, and not reachable from automatic repair

dbmaint also defines destructive and rewriting operations (`deletecol`,
`renamecol`, `castcol`, `fncol`, `rentable`, `addtable`). They load with the
file and nothing in this tree calls them. `addtable` in particular writes an
empty table into every partition without checking for an existing one.

## Not the TorQ copy

`lib/torq/tests/dataaccess/queryorder/hdb/dbmaint.q` is an unrelated fixture
inside the vendored TorQ tree: unmodified, and loaded by nothing here.

## Portability

`dbmaint.q` is plain q with one level of `\d` (`\d .os`). `add1col` and
`allpaths` run on KDB-X. On PeachQ the file loads, but `allcols` (which
`add1col` calls) reads `get tabledir,`.d`, a symbol-vector path PeachQ refuses
('io), and `add1col` appends to `.d` with an amend PeachQ does not implement
('nyi): column repair is KDB-X-only there, as it was before. The `.os` helpers
(`cpy`, `del`, `ren`) shell out to `cp`/`rm`/`mv` through `system` and back
only `copycol`, `deletecol`, `renamecol` and `rentable`.
