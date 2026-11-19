# q and TorQ traps this tree has paid for

The expensive bugs here aren't the ones that error. They are the ones that
**return a plausible wrong value, or silently do nothing**. Every reproduction
below was run on KDB-X; the right-hand column is what q printed. When a q change
appears to work but a downstream value is wrong, check this list before adding
instrumentation. Run `qlinter` on the file first: the codes in brackets are the
rules that catch each trap.

## The language

  | #   | Trap                                                                                                       | Reproduction                                                                      | Write instead                                    |
  | --- | ---                                                                                                        | ---                                                                               | ---                                              |
  | 1   | Name, space, negative literal **applies** the name (QB010)                                                 | `parse ".z.p -0D00:01"` → `` (`.z.p;-0D00:01:00.000000000) ``                     | `.z.p - 0D00:01` or `.z.p-0D00:01`               |
  | 2   | `.[f;();h]` fires `h` even when a niladic `f` succeeds                                                     | `.[{[] 1+1};();{`caught}]``` → `` ```caught `` | `@[{[] 1+1};::;{`caught}]` → `2` |                                                  |
  | 3   | A builtin as a parameter fails at **call** time, with an unrelated error (QF001)                           | `{[fills] fills+1}[1]` → `'match`                                                 | name it apart: `fill_rows`                       |
  | 4   | `like` with an interior `*` beside another `*` is `'nyi`                                                   | `"ab cd" like "a* c*"` → `'nyi`                                                   | `"*b*"`, `"prefix*"`, or `ss`                    |
  | 5   | `where col=col` (a parameter named like the column) matches every row (QB001)                              | `count select from ([] a:1 2 3) where a=a` → `3`                                  | name parameters apart: `from_ts`, `version`      |
  | 6   | `n#str` **wraps**, rather than truncating                                                                  | `4#"ab"` → `"abab"`                                                               | `str like "ab*"`                                 |
  | 7   | `sum` over booleans is an int, so `~` with a long fails                                                    | `type sum 101b` → `-6h`                                                           | `sum "j"$x`                                      |
  | 8   | `sum` over an empty `each` is a general empty list, and a comparison on it throws later                    | `type sum {x} each ()` → `0h`                                                     | `sum "j"$x`                                      |
  | 9   | One character is an atom, not a string                                                                     | `"1"~enlist "1"` → `0b`                                                           | compare `enlist`ed, or use `like`                |
  | 10  | A line holding only `/` opens a block comment to the next `\` line (QP001)                                 | a file whose header has a bare `/` loads with no names, and `\l` reports success  | `/ .` for a blank comment line                   |
  | 11  | A fully-applied projection runs now: `f[a;b]` is a call, so `@[f[a;b];::;h]` throws before `@` can trap it | -                                                                                 | trap a projection with an argument still missing |
  | 12  | `@` is unary: `@[f;(x;y);h]` passes the pair as one argument                                               | -                                                                                 | `.[f;(x;y);h]` for several arguments             |
  | 13  | Right to left, with no precedence                                                                          | `2*3+4` → `14`                                                                    | parenthesise; see the `kdb-q-conventions` skill  |
  | 14  | q lambdas don't close over enclosing locals                                                                | -                                                                                 | pass the value in, or use a global               |

## The nine TorQ invariants

These are from the header of `scripts/processes/torq_pipeline.q`. Each one cost
a live debugging session. `.qtorq` enforces most of them, so a job that goes
through `publish` and its runner doesn't meet them. Code that talks to TorQ
directly does.

1. **`.u.upd` stamps its own `time`.** Sending one makes every message one
   column too wide: `'length`. → `.qtorq.publish` drops `time`.
2. **Keyed tables (99h) are rejected by the tickerplant.** → `.qtorq.publish`
   unkeys them. Keep keyed state private and publish a flat snapshot.
3. **Every column must be a vector,** even for one row. → `.qtorq.publish`
   enlists a dict of atoms.
4. **A timer function that throws is silently deactivated,** and the `.` trap
   misfires on niladics (trap 2 above). → `.qtorq.safe_timer` uses `@[f;::;h]`.
5. **A function in a non-root namespace doesn't reliably resolve a root global
   by its bare name.** → `upd` lives at root, state references are fully
   qualified, and `.qtorq.publish` takes the handle as an argument.
6. **`src/init.q`'s `\l` lines are relative to the repository root,** and
   `torq.sh` doesn't start there. → `.qtorq.load_uqf` changes directory and
   always restores it.
7. **A real `.sub.subscribe` needs an access-listed handle from
   `.servers.startup[]`.** → `.qtorq.subscribe_etl` borrows the credentialed
   `metrics` proctype.
8. **`.u.upd` onto a table the plant doesn't define discards the rows,** with
   nothing reported anywhere. → `.qtorq.assert_publishable` refuses to start the
   process; define the table in `src/etl/plant_tables.q`.
9. **The plant sends `endofperiod` and `endofday` to every subscriber at root.**
   A subscriber missing either logs an error once per period. →
   `.qtorq.install_period_handlers`.

## Tests

- **Assert the message:** `.qunit.assertThrows[f;arg;"*text*";"why"]`.
  `assertError` also passes on a typo in the test (`'value`).
- **Prove the negative:** that an action was *not* taken (pass a function that
  throws; assert a ledger is still empty), and not only what a call returns.
- **Restore what you replace:** globals, `.qetl.load.only`, stubs and
  environment variables. Suites share one process, and a leftover makes another
  suite's result depend on order.
- **Wire before driving:** a streaming job's `publish` starts as
  `.qetl.job.stream.unwired`, which throws. A test that produces no output
  passes without wiring, then fails under another suite order
  (`tests/q/test_cross_arbitrage.q`).
