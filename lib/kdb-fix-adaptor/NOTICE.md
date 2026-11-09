# kdb-fix-adaptor - vendoring notice

Vendored from https://github.com/DataIntellectTech/kdb-fix-adaptor (originally
AquaQ Analytics') at commit `11941454f0224d377cb17bd32e8d1a666f63294c`
(2023-03-06) (#2292), with the local changes listed below. MIT licensed:
`LICENSE-kdb-fix-adaptor` is upstream's `LICENSE.md`, renamed. Recorded in
`lib/VENDORED.toml`; the changed files are frozen in
`lib/.vendored/kdb-fix-adaptor.drift.tsv`, and the vendored gate fails on any
other byte that is not upstream's. Do not edit the tree casually: a further
patch changes that drift file, its `drift_digest` and this notice together, and
`python3 scripts/dev/revendor.py kdb-fix-adaptor` restores upstream's bytes,
undoing every change below.

## What was changed here, and why

As upstream ships it, the tree does not build: CMake refuses to configure it,
and it does not compile against current QuickFIX. These are the only edits
(#2448), each the least that removes one failure; upstream's behaviour is
otherwise untouched.

`CMakeLists.txt`:

- `cmake_minimum_required(VERSION 3.2)` -> `3.10`. CMake 4 refuses a minimum
  below 3.5 and warns below 3.10.
- `configure_file` reads `LICENSE-kdb-fix-adaptor` instead of `LICENSE.md`,
  which this tree renamed (as it renames every vendored licence). The copy in
  the build directory is still named `LICENSE.md`, so `build_package` is
  unchanged.
- `-std=c++11` -> `-std=c++17`. QuickFIX master requires C++17.
- `-Wno-deprecated-declarations` added to the flags. macOS's SDK marks
  `sprintf` deprecated; the bundled pugixml 1.7 calls it, and `-Werror` made
  that fatal. `-Werror` stays on for everything else.
- On `APPLE` only, the library links with `-undefined dynamic_lookup`. The
  `k.h` functions it calls are provided by the q process that loads it; Linux
  allows that in a shared object by default, Apple's linker does not.

`src/main.cxx`:

- The six `throw (FIX::...)` specifications on `fromAdmin`, `toApp` and
  `fromApp` (declarations and definitions) are now QuickFIX's `EXCEPT(...)`
  macro, which is how its `FIX::Application` interface declares them. C++17
  has no dynamic exception specifications; `EXCEPT` expands to nothing there,
  and to the same `throw (...)` as before under older standards.
- `Tm::to_time_point`'s default clock and the `to_time_t` call in
  `strtotemporal` use `std::chrono::system_clock` instead of
  `high_resolution_clock`. The code converts a calendar time to Unix time, a
  wall-clock operation; libc++ makes `high_resolution_clock` an alias of
  `steady_clock`, which has no `from_time_t`/`to_time_t`, so it did not compile
  on macOS. On libstdc++ the alias was `system_clock` already, so Linux
  behaviour is the same.

Upstream's `README.md` is unchanged, so two of its lines are now stale: "CMake
3.2+" (line 15) and the note that the dynamic exception specifications are
"left in" (line 49).

## What it is

A C++ shared library, loaded with `2:`, that gives q a FIX engine built on
QuickFIX: `.fix.create[`acceptor|`initiator; `:sessions/x.ini]`,
`.fix.send[msg]` (synchronous) and the `.fix.onrecv[msg]` callback, a message
being a dict of tag (long) -> value (string). QuickFIX owns the session layer
(logon, heartbeats, sequence numbers, resend, the message store, validation
against the bundled FIX 4.0-5.0SP2/FIXT1.1 dictionaries in
`src/config/spec/`); inbound messages reach q's main thread through a
socketpair registered with `sd1`, not by calling q from QuickFIX's thread.

## What it is not, here

- **Not loaded by anything.** Nothing in `src/` or `scripts/` loads it, and no
  FIX <-> plant mapping exists yet.
- **Compiled, not verified.** With the changes above it configures, compiles
  and links against QuickFIX master (libquickfix 17) with CMake 4.4 and Apple
  clang 21 on macOS arm64 (2026-11-09). That is all: it has not been loaded
  into q or run against a FIX counterparty. Upstream calls it alpha - "has not
  been used in a production system to date" - written for kdb+ 3.x against the
  `k.h` it ships in `third_party/kx/include/kx/`, not KDB-X's, and its runtime
  defects are not fixed here. `CMakeLists.txt` does not look for QuickFIX; for
  one installed outside the compiler's default paths, pass
  `-DCMAKE_CXX_FLAGS=-I<prefix>/include` and
  `-DCMAKE_SHARED_LINKER_FLAGS=-L<prefix>/lib`. Build in a directory outside
  this repository: `build_package` (and `package.sh`, which runs it) writes
  its `.tar.gz` into the source directory, which is this tree.
- **Not loadable on PeachQ**, which has no `2:` - like ODBC (#1865), it needs
  KDB-X.

## What was NOT vendored

- `.git/` history.
- `src/a.out`, `third_party/kx/include/kx/test`,
  `third_party/kx/include/kx/test.o` and
  `third_party/pugixml-1.7/docs/samples/a.out`: compiled x86-64 ELF
  executables upstream committed, build output rather than source.
- `.gitignore`: inside this repository it would hide files in our own tree.

## Third-party code inside it

`third_party/pugixml-1.7/` (MIT, Arseny Kapoulkine) and KX's `k.h` C API
header, as upstream ships them; QuickFIX itself is not included (it is a build
dependency, licensed under the QuickFIX Software License).
