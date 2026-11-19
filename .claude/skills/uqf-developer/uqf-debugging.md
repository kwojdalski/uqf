# Debugging - a running stack, a job, a q change

Read this when a process is down, up but idle, failing, or publishing nothing;
when a backfill failed; when a gate or test fails in a way you do not
understand; or when a q change "works" but a value downstream is wrong. The
runbooks are `docs/guides/when-it-breaks.md` and `docs/guides/logs.md`; this
page orders them and adds what the tree has learned.

**Starting or restarting a stack is the user's call.** Several sessions share
this machine and its ports; a stack test or `uqs start` occupies them and spends
licence connections. Read state (`uqs status`, `uqs logs`, `uqs query`) freely;
start, stop or restart only when asked.

## 1. Outside in - the ladder

Most problems stop at rung 1 or 2.

1. **Is it running, and wired?** `uqs status` (or `uqs status --json`). Each
   process shows status, PID, port, heartbeat, `Responds` (a handshake within
   `--probe-timeout`), `Batches`, and its declared graph (Depends on, Inputs,
   Outputs). The footers name processes that are up but silent, streaming jobs
   that failed a batch since their last beat, and running processes whose input
   nothing running publishes (**starved**). `up` but idle is usually a missing
   producer: start through a profile (`uqs start --profile NAME`), which pulls
   in its producers. `uqs graph PROC --upstream` shows what feeds it.

2. **What does its log say?** `uqs logs PROC --level ERROR`, `-f` to follow,
   `--stream err`, `--channel coverage`. The last line says where it stopped:

   | Last line                                                                   | Means                                                                                     |
   | ---                                                                         | ---                                                                                       |
   | `qtorq: loading uqf tree from ...` with no `loaded in`                      | a q file failed to load; the error follows, or is in `err_<proc>.log`                     |
   | `waiting for the tickerplant - if this is the last line, it is not running` | stp1 is down                                                                              |
   | no `first batch received` for a table                                       | nothing arrives on it; its producer is down or publishes nothing                          |
   | `first batch received` but no `first rows published`                        | input arrives and the handler publishes nothing                                           |
   | `on_batch failed`                                                           | the handler threw; the line carries the table and the error                               |
   | `no credential - running on the source's fixture`                           | a backfill publishes its fixture; add the `sources.csv` row or `UQF_SOURCE_CRED_<SOURCE>` |
   | `idle - every window in the range is already covered`                       | nothing to do at this `--source-version`                                                  |

   (`scripts/processes/torq_pipeline.q:173-207`,
   `src/etl/core/stream_job.q:562`; the full table is `docs/guides/logs.md`
   "Where a process stopped".) Logs live in `<data>/logs/out_<proc>.log` and
   `err_<proc>.log`.

3. **DEBUG or TRACE for that one process:**
   `uqs query PROC ".qetl.log.debug 1b"`; `uqs query PROC ".qetl.log.trace 1b"`
   (every source query); at start, `uqs start PROC --trace`; for a backfill,
   `uqs --debug job backfill ...` or `--trace`. Never fleet-wide.

4. **Look inside it:** `uqs query PROC` opens a session (`uqs query PROC "expr"`
   for one expression; no PROC goes through gateway1). Useful there:
   `` .qetl.job.stream.def `job `` (its declaration), `.qpipe.job.<job>` (its
   state), `.qtorq.received` / `.qtorq.published` (rows per table),
   `.qetl.log.channels`, `.qetl.cfg.explain`, TorQ's `.servers.SERVERS`,
   `.timer.timer` (an `active` 0b row is a timer TorQ disabled after a throw),
   `.usage.usage`. `uqs schema PROC [TABLE]` shows live columns and types.

5. **Reproduce without the stack.** In plain q from the repo root: load
   `src/init.q` and `src/etl/init.q` (set `.qetl.load.only` first to load just
   the job), `.qetl.job.stream.wire[job;recorder]`, then call
   `.qpipe.job.<job>.on_batch[t;x]` with a batch you build. Or run it on stock
   kdb+: `q scripts/jobs/run_stream.q -job JOB -feed FEED`.

### Missing data - work from the data inward

When a table stays empty and nothing errors, don't start by reading framework
source. Check the receiving process's table and the publisher's table live; call
the receiver's root `upd` or the job's `on_batch` directly with a representative
row; step the suspect body line by line in a session with real values; only then
look at the plumbing (`.u.w`/`.stpps` on the tickerplant, `.servers.SERVERS`,
recent errors). Two silent drops to rule out first:

- the table is not in `src/etl/plant_tables.q` (`.u.upd` discards rows for an
  undefined table; `.qtorq.assert_publishable` should have refused to start);
- the publisher sent a `time` column (stp1's **INFO** log says
  `Bad message received, error: length`).

## 2. A backfill failed

1. `uqs job runs list --unfinished` - outcomes completed, idle, partial,
   stopped, failed. `job backfill --wait` exits 0 (completed or idle), 1
   (failed, partial, refused, died), 4 (stopped), 3 (attachment not reloaded).
2. `uqs job runs show RUN` - the facts, log paths and the command that resumes.
   `uqs job runs audit DATASET --from T --to T` - every fact recorded about one
   window, from every run that published it.
3. Fix the cause, then **re-run the same command**: covered windows are skipped,
   a stale lock whose pid is gone is broken, a checkpoint for another range is
   ignored. `uqs job checkpoint remove JOB` only to restart a range on purpose.
4. Confirm with `uqs job runs show`, rows with `uqs data check`.

A source that will not connect or has the wrong shape:
`uqs config sources check SOURCE` (credential -> tls -> connect -> schema ->
read -> output, each with its own diagnostic).

## 3. A streaming job died, or left gaps

`uqs status` -> `uqs logs PROC --level ERROR` -> `uqs start PROC [-f]`. Rows it
missed while down are not republished. `uqs job gaps JOB --from T --to T` lists
the stretches it was down or failing and, for a job with a backfill twin, prints
the refill commands (`uqs job new NAME --kind backfill --twin-of JOB` makes a
twin). Gaps record uptime and failed batches, not output; a gap can be wider
than the outage (beats are a minute apart); today cannot be refilled before end
of day.

## 4. `uqs start` refused, or a process died at once

Before launching, `stack/start_policy.py` refuses (and nothing starts): an
unknown name; a q older than a job or the tree needs (override
`q-version-check`); ports held by another stack or a foreign pid
(`stack/occupancy.py` - start elsewhere with `--base-port`); a profile over the
licence budget (override `profile-budget-check`); an unverified attachment
publisher. Bootstrap refuses a missing TorQ/starter pack or a missing `envsubst`
(macOS: `brew install gettext`). Advisory warnings name inputs nothing running
publishes and a start past the connection cap.

After launching, `stack/start_check.py` waits `--grace` (3 s) and fails naming
each process that died or does not own its port, with a 10-line log tail.
`uqs exec -- debug PROC` runs one process in the foreground.

  | Symptom                                          | Usual cause                                                                                                                                                             |
  | ---                                              | ---                                                                                                                                                                     |
  | a job is `up`, its table empty                   | its producer is not running - start through a profile                                                                                                                   |
  | every process shows up but some never receive    | past the 16-connection licence the plant resets the extra handles (`uqs profile list`)                                                                                  |
  | a process exits right after start                | a load error: `err_<proc>.log`; or a port taken                                                                                                                         |
  | `'access` connecting                             | the proctype has no password file, or the user is not in the target's access list; a backfill connects as the `metrics` identity unless `passwords/backfill.txt` exists |
  | HDB `./<date>/<table>. OS reports: No such file` | a partition lacks a table: `uqs data check`, then `--apply`                                                                                                             |
  | an HDB that has not picked up a backfill         | `hdb reload: could not open a handle...` - the rows are on disk and covered; reload or restart the HDB                                                                  |
  | `uqs` exits 1 before doing anything              | a refused environment variable such as `UQF_PEACHQ`: unset it                                                                                                           |

## 5. A q change that "works" but is wrong

1. Run `qlinter` on the file first (`CLAUDE.md`); the `qlint-reviewer` agent
   rules on its findings.

2. Check `uqf-framework/uqf-q-traps.md` - the language traps that return a
   plausible wrong value or silently do nothing, with reproductions. Measured on
   KDB-X 5 (2026.07.23):

   | Expression                                                                     | Result                                                                       |
   | ---                                                                            | ---                                                                          |
   | `parse ".z.p -0D00:01"`                                                        | `` (`.z.p;-0D00:01:00.000000000) `` - applies `.z.p`; write `.z.p - 0D00:01` |
   | `` .[{[] 1+1};();{`caught}] ``                                                 | `` `caught `` even though `f` succeeded - use `@[f;::;h]`                    |
   | `{[fills] fills+1}[1]`                                                         | `'match` - a builtin as a parameter fails at call time                       |
   | `"ab cd" like "a* c*"`, `"x" like "*a*b*"`, `"..." like "load_cursor: *.bak*"` | `'nyi` - keep to `"prefix*"` or `"*middle*"`                                 |
   | `{[a] count select from ([] a:1 2 3) where a=a}[2]`                            | `3` - the column wins; name parameters apart                                 |
   | `4#"ab"`                                                                       | `"abab"` - take wraps                                                        |
   | `type sum 101b`, `type sum {x} each ()`                                        | `-6h`, `0h` - write `sum "j"$x`                                              |
   | `"1"~enlist "1"`                                                               | `0b` - one character is an atom                                              |
   | `2*3+4`                                                                        | `14` - right to left, no precedence                                          |

   Also: a line holding only `/` opens a block comment to the next `\` line and
   the file still "loads" (use `/ .`); a fully-applied projection runs at once;
   `@` is unary (`.[f;(x;y);h]` for several arguments); lambdas don't close over
   enclosing locals; a `parse` error at load aborts the rest of the file with no
   line number; `-9!-8!` of a splayed table on KDB-X is not a copy and drops the
   sym enumeration (`src/etl/core/io_hdb.q:300`: copy by indexing every column).

3. Reproduce with real values in a session; compare against a reference value or
   identity, not "it didn't throw".

## 6. A hook, gate or test fails

- Read the gate's own message: each names the file, the rule and usually the
  fix. Run the gate alone (`python3 scripts/gates/check.py NAME`,
  `uv run python scripts/generate/derive.py TARGET --check`) and read its exit
  code, never through a pipe.
- `operational-docs` rewrote a file and failed: `git add` it and commit again.
- A kdbx-only generator is stale (`contract-surface`, `q-facts`, `plant-schema`,
  `compression-config`): run it without `--check`, review the diff, commit it.
- `q-traps` says the qlinter version differs: install the tag in
  `.qlinter-version`; check which binary is first on PATH.
- `doc-references` names a function your prose cites: it does not exist (or
  takes fewer arguments); fix the prose, or the name.
- A test passes alone and fails in the suite (or the reverse): something leaked
  state - a global, `.qetl.load.only`, a stub, an environment variable.
  `UQF_TEST_ORDER=reverse` or `shuffle` reproduces order dependence.
- A PeachQ-only failure: is it in `tests/q/peachq_known_gaps.txt`? Off CI's
  Linux platform the comparison is "not comparable".
- Never `--no-verify`, never `SKIP=` on your own initiative: stop and report the
  failing hook.

## 7. Facts about this machine that look like bugs

- Hand-run q test subscribers have received no async pushes from a TorQ
  tickerplant here; test a subscriber with kxkdb/kola, or query the TP's
  `.stpps.subrequestall` / `.stplg.msgcount`.
- `until ! pgrep -f X` matches its own shell and never ends; wait on a PID, and
  run q from a file under `timeout`.
- `cp` and `rm` are interactive aliases in zsh; use `command cp`,
  `command rm -f` in scripts.
- A moved worktree's `.venv` keeps the old path's interpreter:
  `rm -rf .venv && uv sync`.
- `git push` over https can block on an osxkeychain prompt; push with
  `git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push`
  under `timeout`.
- ODBC from q on Apple silicon only works through `scripts/dev/odbc_rosetta.sh`
  (x86_64 under Rosetta).
