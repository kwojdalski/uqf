---
name: wire-external-kdb
description: Connect uqf to a real external kdb+/KDB-X database over the qmcp
  MCP server, discover its actual live table/column schema, and wire it into
  uqf's quotes-table shape. Never analyzes from static files alone (MCP
  connection is mandatory), and never targets a local test database or the
  vendored uqs sample stack. Use when the user wants uqf connected to a
  production, staging, or other real external kdb+ source rather than local
  example data.
---

# Wire External kdb+ Database

uqf's `src/*.q` functions (forwards.q, microstructure.q, execution.q) consume a
`quotes` table of a specific shape. Locally that shape is only ever seen via
synthetic test fixtures (`tests/`) or a local test instance (`servers.default`
in the qmcp config, `localhost:5001`). This skill wires a *real* external
kdb+/KDB-X database into that same shape - and every fact it relies on about
that database must come from actually querying it, not from assumption.

## Hard rules

1. **MCP is mandatory, not optional.** If `mcp__qmcp__connect_to_q` and friends
   aren't available, stop and tell the user the qmcp MCP server needs to be
   enabled first. Do not fall back to reading local `.q` files, sample fixtures,
   or documentation and calling that "the schema" - none of that is the actual
   external database. Every schema/type claim in this workflow must be backed by
   a live `describe_table`/`meta`/`query_q` call made during this session, not
   recalled from a prior run.
2. **Never target the example databases.**
   - `servers.default` in the qmcp config is the local test instance
     `tests/run_tests.q` exercises - not a legitimate target for this skill.
   - `lib/torq-finance-starter-pack`'s vendored sample HDB (managed via
     `python/uqs`/`uqs`, see `docs/guides/uqs.md`) is also local example data,
     not "an actual" database.
   - If the user hasn't named a real external host/port or an existing named
     server in the qmcp config other than `default`, ask before connecting - do
     not guess, and do not silently fall back to `default`.
3. **Credentials never land in the repo.** They live only in the qmcp config
   file (outside the repo - get its path via `MultiTool[get_config_file_path]`
   before assuming a location, it varies by OS). Generated q/py wiring reads the
   connection from that config or from an env var; it never contains a literal
   host/user/password string. Adding or editing a `[servers.<name>]` block in
   that file is a real, hard-to-reverse-ish edit to a file outside this repo -
   show the user exactly what block you're about to write and get explicit
   confirmation first, the same way you would before any action with effects
   outside the local working tree.

## Steps

### 1. Identify the target

Ask the user (if not already given) for one of: - an existing named server in
the qmcp config (anything other than `default`), or - host, port, and
credentials for a new one.

Read the qmcp config file (path from `MultiTool[get_config_file_path]`) to see
what's already there and confirm the name isn't `default`. If this is a new
server, draft the `[servers.<name>]` block, show it to the user, and get
confirmation before writing it. After writing, run
`MultiTool[action: "reload_config"]`.

### 2. Connect live and discover, don't assume

- `connect_to_q[host: "<server-name-or-conn-string>"]`
- `MultiTool[action: "list_tables"]` - the real table list on the real server.
- `MultiTool[action: "describe_table"; parameter: "<table>"]` for every
  candidate table - actual column names and actual types, not the names this
  repo's own examples happen to use.
- Fill in anything `describe_table` doesn't cover with `query_q`: `meta t`,
  `.Q.pt` (is it date-partitioned?), `count t`, a small sample (`5#t` /
  `select[5] from t`), `exec distinct sym from t`, and an explicit sortedness
  check (`` t~`sym`ts xasc t `` or whatever its actual key columns are called) -
  never assume a real feed is sorted the way a local fixture is.
- Do this for every table that could feed uqf: quote/book tables at minimum,
  trade tables too if execution.q-family analytics are in scope.

### 3. Diff the live schema against what uqf expects

uqf's quote consumers want a `quotes` table shaped
`` `ts`sym`bid_prices`bid_sizes`ask_prices`ask_sizes ``, each level column a
level-0-first vector per row, sorted `` `sym`ts xasc `` (see forwards.q's
`require_quotes_cols`/`leg_book_as_of`, microstructure.q's `quotes_for_sym`).
Compare that, column by column, against what step 2 actually returned. Common
real-world mismatches to check for explicitly, and the existing uqf tooling that
already handles each one (reuse it, don't re-derive it): - **Wide per-level
columns** (`bid0..bid9`, or Databento-style `bid_px_00..bid_px_09`) instead of
one vector column per side -> `book.q`'s `derive_level_groups` +
`fold_level_columns` / `book_from_wide_levels`. - **String identifier columns**
instead of symbols -> `book.q`'s `symbolize_columns` /
`candidate_symbol_columns`. - **A different pair-symbol convention** (lowercase,
`EUR/USD`, underscore-separated) -> `ccy.q`'s `normalize_ccy_pair`. - **Not
actually sorted `` `sym`ts xasc ``** - confirmed live, not assumed; if unsorted,
the wiring must sort defensively at its own boundary, since every downstream uqf
function throws on unsorted input by design rather than silently as-of-joining
the wrong row. - **Type mismatches** - real vs float prices/sizes, or a
timestamp/timespan storage type that doesn't line up with what `ts` needs.

### 4. Generate the wiring

Based on what step 3 actually found (not a generic template): - A q bridge
script under `scripts/`, mirroring the shape of the existing
`scripts/examples/reshape_wide_order_book_example.q` /
`src/etl/streaming/quotes_feed.q`, that opens the IPC handle to the named server
(connection details read from the qmcp config or an env var - never hardcoded),
pulls or subscribes to the real table(s), and reshapes into the `quotes` shape
using the `book.q` helpers identified in step 3 - or explicitly notes no reshape
was needed if the live table already matches. - The script must be loadable by
consumers without editing anything under `lib/` (vendored trees are never edited
in place, matching this repo's existing convention for `book.q`/`torq_*`
scripts).

### 5. Verify against the live database, not a fixture

- Re-connect and run the new bridge script for real.
- Confirm the resulting table passes uqf's own checks: right columns,
  `` ~`sym`ts xasc `` sortedness, non-empty `bid_prices`/`ask_prices` vectors.
- Feed one real row through an actual uqf function live via `query_q` (e.g.
  `.qfwd.cross_book_at` or `.qmicro.mid_price`) and sanity-check the result
  against what the raw live quote implies - a real number from the real
  database, not a canned example.
- Report what was actually found (live schema, any reshape applied, any mismatch
  that couldn't be resolved cleanly) and exactly what was wired, so the user can
  review before anything downstream depends on it.

## Non-goals

- The local test instance (`servers.default`, port 5001) used by
  `tests/run_tests.q` - explicitly excluded, it's the example db this skill
  exists to go beyond.
- `lib/torq-finance-starter-pack`'s vendored sample stack driven by
  `python/uqs`/`uqs` - also local example data (see `docs/guides/uqs.md`), a
  separate concern from this skill.
- `src/*.q` pricing/analytics logic itself is never modified here - only the
  data-wiring boundary that feeds it with real quotes.
