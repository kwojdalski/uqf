# Can cryptorust be discovered by TorQ?

**Yes, but not as a discovery member — and the distinction is the whole
answer.** TorQ already tracks it, in the right place, and nothing surfaces
that. What it must *not* get is a row in `nontorqprocess.csv`, for a reason
worth writing down before someone adds one.

## What cryptorust is, precisely

A Rust binary, started by the Python orchestrator with a PID file, that opens
an **outbound** q IPC connection to `stp1` and publishes market data onto the
tickerplant. Its generated config carries `host`/`port` — those are *stp1's*.

It does not listen on a port of its own.

## Why it cannot be a discovery member

TorQ's discovery is a **connect-to** registry. `.servers.retryrows` calls
`.servers.opencon` against each registered process's `host:port` and asks it
for `.proc.getattributes[]`. A process that does not listen has nothing to
connect to.

TorQ *does* have a first-class mechanism for non-TorQ processes —
`nontorqprocess.csv` (`host,port,proctype,procname`), with
`TRACKNONTORQPROCESS:1b` on by default, and `discovery.q` explicitly excludes
those rows from the connect-back logic. So the mechanism exists and is the
right one for, say, a Java service that listens on a port.

**It is the wrong one here.** Registering cryptorust would require inventing a
`host:port` it does not serve. The row would sit in `.servers.SERVERS`
permanently with a null handle, null `startp` and empty attributes — which a
fleet view renders exactly like a **process that is down**. A dashboard that
shows a healthy recorder as permanently dead is worse than one that does not
show it: the first is a false alarm every day, the second is a known gap.

## Where TorQ already tracks it

`.clients.clients`, in the process it connects *to*. TorQ's
`code/handlers/trackclients.q` maintains one row per connected client with:

```
w  ipa  u  a  pid  port  startp  lastp  hits  errs  sz
```

So `stp1`'s `.clients.clients` carries cryptorust's IP, username, first- and
last-seen timestamps, request count and bytes transferred. That is richer
liveness than discovery would give — discovery records that a process is
*registered*, this records that it is *currently doing work*.

This is not a workaround. A client belongs in the client table; a service
belongs in the server table. Discovery answers "what can I connect to", and
nothing should ever connect to cryptorust.

## The gap, and what closes it

Nothing in this tree reads `.clients.clients`. The frontend's `/ops/connections`
uses `.gw.clients` — the **gateway's** clients — and cryptorust connects to
stp1, not the gateway. So it is tracked and invisible.

Closing it is a per-process client view: fan `.clients.clients` out across the
fleet the way `/ops/usage` already fans out `.usage.usage`,
and cryptorust appears under `stp1` with its real connection state.

Two things make that better than the discovery route rather than merely
different:

- **It reports truth rather than declaration.** A `nontorqprocess.csv` row
  says a process *should* exist. A client row says one *is connected, and last
  sent bytes at 09:41*.
- **It needs no configuration.** Nothing to keep in step with the orchestrator,
  so it cannot drift. A declared row would have to be added and removed
  alongside `start_crypto_recorder`, and the failure mode of forgetting is a
  permanently-dead entry.

## What is NOT recommended

- **Adding cryptorust to `nontorqprocess.csv`.** See above — a permanent
  null-handle row.
- **A q sidecar that registers on its behalf.** It would register the
  *sidecar*, and discovery would then hand callers a handle to a process that
  cannot answer for the recorder. A proxy that answers "yes I am here" on
  behalf of something else is a liveness signal that cannot go false.
- **Making cryptorust listen.** Defensible, and it would make it a genuine
  discovery member — but it is a change in another repository and it adds an
  inbound surface to a recorder that currently has none. Worth doing only if
  something actually needs to *call* it.

## Summary

| Question | Answer |
|---|---|
| Can TorQ discover it? | Not as a service — it does not listen |
| Does TorQ know about it? | **Yes**, in `stp1`'s `.clients.clients` |
| Is that visible today? | No — nothing reads that table |
| Right fix | Surface per-process clients, not a declared server row |
