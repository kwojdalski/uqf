# Auditing runtime configuration changes

Someone sets `.qsub.cross_arbitrage.notional` to 5,000,000 over IPC at
14:32. An hour later the edges in `cross_arbitrage` look different. Nothing
records the connection between those two facts.

`.qcfgaudit` records the value side of it, into a `config_change` table.

```q
q) select from config_change where name like "*notional*"
time                          owner           name                            old       new        as_of
2026.09.19D14:02:11.084       cross_arbitrage .qsub.cross_arbitrage.notional  ""        "1000000"  ...
2026.09.19D14:32:47.201       cross_arbitrage .qsub.cross_arbitrage.notional  "1000000" "5000000"  ...
```

The first row, with an empty `old`, is written the first time the process
sees the variable — so the log says what it **started** with. A log that
only records later edits cannot tell you what they were edits from.

## What it cannot do, and why the design follows from that

**q has no hook on global assignment.** There is no `.z` callback for
`x:5`, and a view (`x::expr`) recomputes lazily when *read* rather than
firing when its inputs change. Nothing can observe a change as it happens.

That rules out the obvious design. A logging setter — `.qcfg.set[name;v]`
— would be airtight for anything routed through it and useless for a plain
assignment, and this stack is operated by ad-hoc IPC where a plain
assignment is the common case. It would audit exactly the changes made by
people who did not need auditing.

So the only approach that cannot be bypassed is to look periodically and
compare. The cost is real and worth stating: **a change made and reverted
inside one poll interval is never seen**, and a change's timestamp is when
it was *noticed*, not when it happened. `.qcfgaudit.period` is 5 seconds.

## Who changed it

Not in this table, and deliberately not — because the answer already
exists. TorQ's `logusage.q` writes every incoming IPC command per process,
with the user, the host, the handle and the command text:

```
2026.09.19D14:32:47.198|32781|10045|`ps|`metrics|`crossarb1|"c"|2130706433i|`krzysztofwojdalski|...|".qsub.cross_arbitrage.notional:5000000"|...
```

Join a `config_change` row to `usage_<procname>_<date>.log` at the same
timestamp and you have both halves. Neither can answer the question alone:
the usage log does not know whether a command *changed* anything, and the
audit table does not know who was on the handle.

## Declaring what counts as configuration

Watched, not scanned. `.qsub.cross_arbitrage.books` is state, and large;
snapshotting a whole namespace every five seconds would be both wrong and
expensive. So a job names its own tunables, in its own file, beside their
definitions:

```q
.qcfgaudit.watch[`cross_arbitrage;
    `.qsub.cross_arbitrage.notional`.qsub.cross_arbitrage.max_skew];
```

Which makes "what counts as configuration here" a fact in the tree rather
than a judgement each reader makes again. Names must be fully qualified —
a bare `` `notional `` resolves against whatever namespace is current when
the timer fires, which is not the one the author meant, so it is refused.

Watching today:

| job | variables |
|---|---|
| `superbook` | `max_age` — the expiry window that decides which liquidity counts as live |
| `cross_arbitrage` | `notional`, `max_skew` — the size every edge is quoted at, and how far apart a route's legs may be |

A job that watches anything must also declare `config_change` among its
`publishes`, because the audit publishes through the job's own seam. A test
holds the two together.

## Why it runs in every process rather than one

Configuration lives in each process's own memory. A central poller would
need a handle to every watched process — and connections are the scarce
resource here, sixteen per process on the community licence
([the budget](../architecture/stack.md#what-starts-with-the-stack-and-why-not-all-of-it))
— and it could only ever see what it thought to ask for.

In-process costs no new connection, sees a change whatever caused it, and
needed no new plumbing: every streaming job already has a publish seam and
the runner already installs timers, so this is a second timer publishing
through the seam that was there.

## Adding a variable

Two lines, in the file that owns it:

```q
.qcfgaudit.watch[`my_job;`.qsub.my_job.my_tunable];
```

and `config_change` in that job's `.qstream.register` publishes. Nothing
else — the table already exists, the runner already polls anything
declared, and `tests/q/test_config_audit.q` will fail if the name resolves
to nothing or the publish is not declared.
