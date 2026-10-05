# uqs

The stack's operator CLI and MCP server: it starts, inspects and stops the TorQ
fleet built from `lib/torq/` and `lib/torq-finance-starter-pack/`, without
writing into either. **Using it:**
[docs/guides/uqs.md](../../docs/guides/uqs.md). This page is for working on it.

## Layout

```
src/uqs/
  paths.py     where every file in the tree lives; repo_root() searches upward
  model/       what the stack DECLARES: pipelines, their edges, table schemas,
               the process graph. Starts nothing, opens nothing
  stack/       the RUNNING fleet: start/stop, environment, queries, logs, the
               licence's connection budget
  cli/         the `uqs` command, one module per command family on one Typer app;
               entry.py is the entry point
  scaffold/    `uqs job new`: the plan, the templates, applying them
  external/    processes this tree starts but does not own (crypto recorder,
               Databento feed)
  checks/      read-only diagnostics: smoke test, HDB shape, live plant schema
  logger/      the CLI's own loguru logging
  mcp.py       the `uqs-mcp` server: the same operations as MCP tools
tests/         flat, one module per source module
```

The folders are layers, and imports only point down:

```
logger/ paths.py           depend on nothing in this package
model/            -> paths, logger
stack/            -> model, paths, logger
external/ checks/ -> stack, model, paths, logger
scaffold/ cli/    -> whatever they need below them
```

`model/` importing from `stack/` would mean the stack's declared shape could not
be read without the code that starts processes.
`test_module_split.py::test_the_folders_are_layers` fails on any import that
points back up.

## Testing

```
uv run --project python/uqs pytest
```

Most tests need no q; the ones that do skip without it.
