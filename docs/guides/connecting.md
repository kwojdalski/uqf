# Connecting

Run q on a process of the stack, open a session on it, reach a deployed target
through an ssh tunnel, or drive the stack from an MCP client.

## Querying a process

`uqs query` runs a q expression on a running process. Name the process, or give
its `--port`. With no process it asks the gateway, `gateway1`. With no
expression it opens an interactive session.

```bash
uqs query rdb1 "select from trade"             # rdb1 directly, as typed
uqs query "select count i by sym from trade"   # .gw.syncexec[...;`rdb`hdb]
uqs query "select from trade" --servers hdb    # history only
uqs query "tables[]" --no-gateway              # the gateway's own tables[]
uqs query                          # a session on gateway1, every line routed
uqs query rdb1                     # qcon localhost:6052:admin:admin, under rlwrap if installed
```

In a session, `\\`, `exit` or Ctrl-D leaves, and Ctrl-C drops the line being
typed. A session on a process needs `qcon`, which ships with kdb+. Results print
as q's console prints them. `--render kola`, or `UQS_QUERY_RENDER=kola` for
every call, shows a table as a Polars DataFrame and the rest as Python objects.

`uqs query` and `uqs schema` log in as `admin:admin`, the placeholder demo
credential that works on every process. For another login, give `--user` and put
the password in a variable whose name `--password-env` takes. Never put a
password on the command line, where `ps` and shell history show it.

```bash
read -rs Q_PW && export Q_PW
uqs query rdb1 "count trade" --user ops --password-env Q_PW
```

### Through the gateway

The gateway holds no tables of its own, so `uqs` routes what you send it. The
expression runs on the RDB and HDB through TorQ's `.gw.syncexec`, and their
results are joined. `--servers` names other process types. Three things are not
routed:

- A `.gw.*` call of your own and a `\` system command are sent as typed.
- An in-place change is refused, because routed it would change the RDB's live
  data. That is an `update` or `delete` from a table named with a backtick,
  `` `trade upsert ... `` (or `insert`, `set`) and the functional
  `` ![`trade;...] ``. The copy form, `update ... from trade`, is routed. To
  change a table, name its process (`uqs query rdb1 ...`).
- With `--no-gateway`, the gateway gets exactly what you typed.

## A deployed target, from here

`uqs query --tunnel NAME` queries a target declared in `deploy_targets.toml`
from this machine. `uqs` opens an ssh tunnel for the call and closes it
afterwards.

```bash
uqs --runtime peachq-etl query --tunnel dev 'tables[]'               # the gateway, routed
uqs --runtime peachq-etl query --tunnel dev rdb1 'tables[]'          # rdb1, as typed
uqs query --tunnel dev --port 6552 'tables[]'                        # a port on the target
```

The port of PROC (or of the gateway) comes from the target's deployed release,
under the `--runtime` given here. This checkout's ports do not apply.
`uqs --target NAME query ...` is a different command. It runs `uqs query` [on
the server](deploy.md#driving-a-deployed-release-from-your-machine), and giving
both `--target` and `--tunnel` is refused.

### A standing tunnel

For a tunnel that stays open while you work, `uqs target tunnel plan` prints the
processes it forwards and the ssh command that opens it.
`uqs target tunnel check` then tests each forward.

```bash
uqs target tunnel plan prod                        # every rdb, hdb, wdb and gateway
uqs target tunnel plan prod rdb1 hdb1 --local rdb1:7002 --json
uqs target tunnel check prod --password-env PROD_Q_PASSWORD
```

A local port is the remote port plus `--local-offset` (10000) unless
`--local PROC:PORT` names one. The check reports the first layer that fails for
each endpoint: `ssh`, `unreachable` (nothing listens on the remote port),
`auth_refused`, `identity_mismatch` (another process answered), `not_ready` (a
gateway has no active server) or `local_bind` (the local port is taken).

Each endpoint that passes is recorded in `tunnel_checks.json`, beside
`deploy_targets.toml`. The plan then marks each mapping `verified`, `stale`
(checked more than an hour ago, or for other ports) or `unverified`.

## MCP server

`uqs-mcp` exposes the CLI's operations as MCP tools, for an MCP client to drive
the stack. `exec` is not exposed. Point the client's server command at this
(stdio transport):

```bash
uv run uqs-mcp
```

Each tool is one CLI command, named after it: `uqs_start` is `uqs start`,
`uqs_job_list` is `uqs job list`, and `uqs_logs` is `uqs logs --json`. A tool
takes its command's options spelled as identifiers, so `uqs_query` takes
`password_env`, the name of the variable holding the password. `uqs_clean` is a
dry run that returns each entry it would remove. It deletes only with
`confirm=true`. Every tool returns one JSON object, and a failure is
`{"ok": false, "error": "..."}`, marked as an error.
