# tap1

A diagnostic subscriber, started on demand, that logs every batch the
tickerplant publishes. Starting, stopping and inspecting the stack as a whole is
in [running the uqf stack](../guides/uqs.md).

`tap1` (`torq_tap.q`) is a generic debug tap: it subscribes to some (or, by
default, every) table on the tickerplant and logs each incoming batch unmodified
through the stack's `logs` command - the table name lands in the log line's `id`
field, so `uqs logs -f tap1` shows you literally everything being written to
kdb+, and `uqs logs -f tap1 | grep quotes`-style filtering works even without
narrowing the subscription itself. `startwithall=0` (debug utility, not part of
the standing stack):

```
uqs start tap1
uqs logs -f tap1
```

Restrict it to specific tables via the same `extras`-as-CLI-flags mechanism
`sctp1`/others already use - no orchestrator code needed for the filtering
itself:

```
uqs config-set -- tap1 extras "-tables quote wide_book"
uqs restart tap1
```

(the leading `--` is needed so the CLI doesn't try to parse `-tables` as one of
its own options). Set `extras` back to `""` to return to "every table".
