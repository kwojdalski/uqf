# When it breaks

Two failures happen often enough to walk through end to end: a backfill that
fails partway through its range, and a streaming job that dies. Each section
goes from "something is wrong" to "it is fixed", with the command for every
step. What each log line means is in [`uqs.md`](uqs.md#where-a-process-stopped);
this page is the order to read things in.

## A backfill failed, or was killed, partway through its range

### 1. See that it did not finish

```
uqs run status
```

lists every run that began and never finished. That includes runs whose process
died, which nothing else records. `uqs run list` shows every run with its
outcome: `completed`, `idle` (nothing to do), `partial` (some windows failed) or
`failed`.

The process also told whoever started it:

- the backfill process exits non-zero (`partial` and `failed` both count as
  failures). `uqs backfill` itself returns as soon as torq.sh has started the
  process, so its exit code says only whether the process started. With `--wait`
  it follows the run to its outcome and exits with that: 0 for `completed` or
  `idle`, 1 for `failed` or a process that died. That includes a process that
  died before it recorded anything, for example while loading the tree: once it
  has had 30 seconds to start and is no longer running, the wait ends and points
  at `uqs logs`. Use `--wait` in a script or a scheduler;
- the Airflow sensor fails the task with q's error;
- the browser's **Backfills** view shows **Failure**.

A run killed before it could record an outcome shows as **Abandoned** in the
Backfills view, and the sensor fails it as "its process is gone". Both judge
that only on the host that ran it.

### 2. Find the cause

```
uqs run show <run_id>
```

prints the run: its range, how many windows were planned, completed and failed,
and the facts it recorded. It then prints the two things to do next:

```
logs   uqs logs deals_backfill1 --level ERROR
         .../torqdata/logs/out_deals_backfill1.log
         .../torqdata/logs/err_deals_backfill1.log
re-run uqs backfill demo_deals_backfill --from 2026-09-13T00:00:00 --to 2026-09-15T00:00:00 --version v1
         covered windows are skipped, so this resumes rather than repeats
```

In the log, a failure is a `backfill process failed` line followed by a
`backtrace`. Every line logged during a window carries `worker`, `run` and the
window's `range_from`/`range_to`, so `grep` for the run id finds that run's
lines.

If the error alone doesn't explain it, re-run with more detail:

- `--debug` adds the coverage gaps planned, each fetch's bounds and row counts,
  retries, locks and checkpoints;
- `--trace` adds every query the source was sent.

See [the DEBUG level](uqs.md#where-a-process-stopped).

### 3. Fix the cause, then re-run the same command

Re-running **is** the resume. Coverage records only the windows that published,
so the same command fetches only what is still missing. Any window that did not
publish is fetched again.

- A **wrong credential** is reported by name. The error names the `sources.csv`
  file and row, or the `UQF_SOURCE_CRED_<SOURCE>` variable, that configured it.
  A source with neither runs on its fixture, with a WARNING saying so.
  `uqs config sources` shows which file the stack reads and what each row still
  lacks.
- A **lock left by a killed run** is broken by the next run when its pid is gone
  on the same host. A lock held by a live process, or by another host, is
  refused with the holder's name.
- A **checkpoint** for a different range or version is ignored, and the run
  starts from the beginning of its own range (`checkpoint is for another run`).

You need `uqs remove checkpoint <worker>` only to restart a range from its
beginning on purpose. It refuses while a run of that worker may still be live.

### 4. Confirm

`uqs run show <new_run_id>` should show `completed` with no failed windows, or
`idle` if every window was already covered. Rows in the RDB or HDB are where
[the FAQ](../faq.md#where-do-a-backfills-rows-end-up) says. `uqs data hdb-check`
reports a partition missing a table or column.

## A streaming job died

### 1. See which one

```
uqs summary
```

shows every process's status beside what it subscribes to and publishes. A job
that is `down` is the one to look at. A job that is `up` with an empty output is
the other case, and [the
FAQ](../faq.md#my-process-is-up-but-its-table-stays-empty-why) covers it.

### 2. Find the cause

```
uqs logs <procname> --level ERROR
```

`on_batch failed` names the table and the error. If there is no ERROR line, read
the last few lines without `--level`: [where a process
stopped](uqs.md#where-a-process-stopped) says what each one means, from a q file
that failed to load to a tickerplant that was never up.

### 3. Fix it and start it again

```
uqs start <procname>
```

or `uqs up <procname>` to start it and watch its log in the foreground.

### 4. Decide what to do about the gap

**Rows the job would have published while it was down are not published when it
restarts.** A job declared with `replay 1b` rebuilds its *state* from the day's
tickerplant log at start, but with its publish muted, because republishing a
whole day would duplicate everything already sent. Only what the job's own
`on_replayed` handler sends afterwards is published.

Every streaming job records the sessions it was up and subscribed, so the gap
can be asked for directly:

```
uqs gaps <job> --from <start> --to <end>
```

It lists each stretch of the range when the job was not up. For a job with a
bounded "twin" (a backfill worker filling the table the job publishes), it then
prints the backfill command that refills each gap. For markouts the twin is
`hdb_demo_markouts_backfill` (see [markouts](../services/markouts.md)). A job
with no twin cannot be refilled from here, and `uqs gaps` says so;
`uqs job new NAME --kind backfill --twin-of JOB` scaffolds one (see
[backfills](../scaffolding/backfill.md)).

Two things to know about the answer:

- **It records uptime, not output.** A job that was up but received nothing, or
  published nothing, shows no gap.
- **A gap can be wider than the outage, never narrower.** A session's end is its
  last beat, recorded once a minute (`.qetl.uptime.period`), so a gap can start
  up to a minute before the job really stopped. Refilling a little extra costs
  nothing: covered windows are skipped.

Runs from before uptime was recorded have no sessions, and `uqs gaps` reports
the whole range as down. For those, the log still tells you: the gap runs from
the job's last line before it died to `streaming job wired - running` after the
restart.
