# kdb-fix-adaptor - vendoring notice

Vendored from https://github.com/DataIntellectTech/kdb-fix-adaptor (originally
AquaQ Analytics') at commit `11941454f0224d377cb17bd32e8d1a666f63294c`
(2023-03-06), unchanged (#2292). MIT licensed: `LICENSE-kdb-fix-adaptor` is
upstream's `LICENSE.md`. Recorded in `lib/VENDORED.toml`; never edit it here -
`python3 scripts/dev/revendor.py kdb-fix-adaptor` is the one way it changes.

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
- **Not built or verified here.** Upstream calls it alpha - "has not been used
  in a production system to date" - written for kdb+ 3.x against the `k.h`
  it ships in `third_party/kx/include/kx/`. It needs QuickFIX C++ built first
  (see `README.md`, `package.sh`, `CMakeLists.txt`); whether it compiles
  against current QuickFIX and KDB-X's `k.h` is untested.
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
