# kdb-parquet - vendoring notice

Vendored from https://github.com/DataIntellectTech/kdb-parquet at commit
`e5cd641c321e52c0631fb3ef5ea1ec1e56062ef0` (2026-08-21).

**No LICENSE file exists in the upstream repository** (checked the full git
tree, not just GitHub's license auto-detection). Unlike `lib/log4q.q`
(Apache-2.0) and `tests/lib/qunit.q` (CC BY-NC-SA, both documented in this
repo's top-level README under Licensing), there is no explicit grant of
rights to copy, modify, or redistribute this code - default copyright
applies. It was vendored here anyway at the repo owner's explicit request;
this file exists so that fact isn't silently lost. If upstream ever adds a
LICENSE, replace this notice with it.

## What was NOT vendored

- `.git/` history
- `.gitmodules` / `rawlibs/arrow` - upstream builds on a full Apache Arrow
  C++ submodule checkout (`git submodule init && git submodule update`,
  then a separate Arrow CMake build) before `libPQ` itself can be built.
  That submodule wasn't pulled in - it's the Arrow project's own source
  tree, not part of kdb-parquet.

## Platform note

`libPQ.so` as checked into upstream (and copied here unmodified) is a
**Linux x86-64 ELF binary** (`file libPQ.so` confirms
`ELF 64-bit LSB shared object, x86-64 ... GNU/Linux`). It will not load on
this repo's primary dev machine (macOS/Darwin) - `2:` (native library
loading) would need a from-source rebuild targeting macOS/arm64, following
upstream's `installation.md`/README build instructions (clone the Arrow
submodule, build Arrow with `-DARROW_PARQUET=ON -DARROW_BUILD_SHARED=ON`,
then `cmake . && make` in this directory).

Separately, this repo's PeachQ interpreter (`./q` at the repo root) has no
`2:` support at all (see `.claude/skills/kdb-q-conventions`'s companion
memory on interpreter gaps) - loading `libPQ` requires real KDB-X
regardless of platform.

Real KDB-X also ships its own official parquet module at
`~/.kx/mod/kx/pq/` (bundled with the KX personal-edition install used
elsewhere in this repo's setup) - worth checking as a licensed,
ready-to-use alternative before investing in building this one from
source.
