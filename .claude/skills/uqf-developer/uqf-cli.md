# The `uqs` CLI - commands, conventions, how to add one

Read this before using an unfamiliar `uqs` command, before adding or changing
one, and before touching the MCP server. `uqs <command> --help` (or
`uv run --project python/uqs uqs <command> --help` without the install) is
authoritative for flags; `docs/reference/cli.md` is the convention contract;
`python/uqs/tests/test_cli_conventions.py` enforces it by walking the real
command tree.

## 1. What it is

`python/uqs` ("ultimate q stack"), Python 3.14+, Typer + Rich. Entry points in
`python/uqs/pyproject.toml`: `uqs = "uqs.cli:main"`, `uqs-mcp = "uqs.mcp:main"`;
`python -m uqs` works too. Install with `./install.sh` (puts `uqs` on PATH;
never runs sudo); `./install.sh --dev` adds the workspace and the git hooks.

`main()` (`cli/entry.py`) loads `.env`/`.envrc`, refuses removed or renamed
environment variables by name (`UQF_PEACHQ`, `UQF_Q_IMPL`, ... -
`interpreter.REMOVED_ENV`, `renamed_env.RENAMED_ENV`), forwards `--target NAME`
to a deployed release over ssh before Typer parses anything, routes `--json`
runs so even a usage error is one JSON refusal, then runs the app. An uncaught
`UqsError` is a refusal, not a traceback.

## 2. The command tree

Global options: `--debug` (DEBUG logs; `status` adds load times; `job backfill`
starts its process verbose), `--runtime NAME` (or `UQS_RUNTIME`),
`--target TARGET` (before the command), `--version`. There is no global
`--json`.

  | Command                                         | Subcommands / purpose                                                                                                                                                                                                                                                                                           |
  | ---                                             | ---                                                                                                                                                                                                                                                                                                             |
  | `start`, `restart`, `stop`                      | the stack's lifecycle: every `startwithall=1` process, `PROC...`, or `--profile NAME`; `-f` follows the log; `stop --kill` after the 15 s grace                                                                                                                                                                 |
  | `status`                                        | every process with status, PID, port, heartbeat, `Responds`, `Batches`, and its declared graph (Depends on, Inputs, Outputs); `-i` interactive; `--every`; footers name silent, failing and starved processes                                                                                                   |
  | `graph`                                         | who feeds whom, with the tables on each edge (`--upstream`, `--offline`)                                                                                                                                                                                                                                        |
  | `query [PROC] [EXPR]`                           | a q expression on a process, or a session; no PROC goes through gateway1 (`.gw.syncexec`), which refuses an in-place update; `--no-gateway`, `--port`, `--tunnel TARGET`                                                                                                                                        |
  | `schema [PROC] [TABLE]`                         | live tables and columns (default rdb1; TABLE may be a pattern)                                                                                                                                                                                                                                                  |
  | `logs [PROC...]`                                | `-f`, `-l N`, `--level`, `--multitail`, `--stream out|err|both`, `--channel`, `--subscribe`                                                                                                                                                                                                                     |
  | `exec -- VERB ...`                              | any other torq.sh verb (`exec -- debug rdb1`, `exec -- qcon gateway1 admin:admin`)                                                                                                                                                                                                                              |
  | `clean`                                         | wipe the runtime's data directory (asks; `-n`)                                                                                                                                                                                                                                                                  |
  | `data`                                          | `replay`, `seed`, `migrate`, `check`, `alter`, `compress`, `diff`, `snapshot list|show`                                                                                                                                                                                                                         |
  | `config`                                        | `show`, `set`, `unset`, `list`, `env`, `sources [stub|check]`, `field list`, `housekeeping show`                                                                                                                                                                                                                |
  | `runtime`                                       | `list`, `diff`, `prepare` (never in this checkout)                                                                                                                                                                                                                                                              |
  | `job`                                           | operations: `run JOB --once` (one unit of a job, then exit), `backfill`, `gaps`, `preview`, `check`, `receiver start|stop`, `checkpoint remove`, `runs list|show|audit|upgrade|stop`, `attachment add|remove|show|list|status|contract`; authoring: `new`, `remove`, `install` (never in this checkout), `list` |
  | `connector`                                     | `start`, `stop`, `restart`, `status`, `logs` for `crypto`, `crypto-fills`, `kafka`, and bundle-installed ones                                                                                                                                                                                                   |
  | `setup odbc`                                    | `install`, `rollback`, `status`, `env`                                                                                                                                                                                                                                                                          |
  | `deploy`                                        | `build`, `push`, `check`, `status`, `history`, `rollback`, `prune`, `component build`                                                                                                                                                                                                                           |
  | `target`                                        | `add`, `set`, `unset`, `rename`, `remove`, `edit`, `list`, `check`, `log`, `revert`, `adopt`, `shell`, `tunnel plan|check`, `config push|history|show|diff|restore`                                                                                                                                             |
  | `activity`                                      | the frontend's activity history: `list`, `export`                                                                                                                                                                                                                                                               |
  | `process list`, `profile list`, `override list` | what the stack declares; the named start sets; every bypassable check                                                                                                                                                                                                                                           |

`uqs override list` names every guardrail a command can bypass (19 today:
`q-version-check`, `profile-budget-check`, `eod-fence`, `even-if-edited`,
deploy's `accept-*`/`allow-*`, attachment findings, ...).

## 3. The conventions (`docs/reference/cli.md`)

- **A group is a noun; a leaf is a verb** from one vocabulary (`status`,
  `check`, `list`, `show`, `set`/`unset`, `add`/`remove`, `start`/`stop`/
  `restart`/`logs`, `clean`, `exec`, `query`/`graph`/`schema`, `new`/ `install`,
  `backfill`/`preview`/`gaps`/`audit`/`receive`, `replay`/`seed`/ `migrate`,
  `alter`, `compress`, `diff`/`prepare`, `build`/`push`/`rollback`/
  `prune`/`history`, ...). The verb table must equal `vocabulary.VERBS`. Only
  the stack's lifecycle and the readers are top-level leaves.
- **The thing acted on is positional; a filter is an option.** Positionals use
  one upper-case vocabulary: `PROC`, `JOB`, `TARGET`, `TABLE`, `DATASET`,
  `SOURCE`, `ATTACHMENT`, `CONNECTOR`, `RUNTIME`, `RUN`, `SNAPSHOT_ID`.
  `WORKER`, `PROCNAME`, `PROCS` and `NAME` are retired.
- **One option name, one meaning, one type.** `--port` is one process's port,
  `--base-port` the stack's; `-n` is `--dry-run`; `target` is a deploy target,
  `runtime` a uqs runtime.
- **Shared flags:** `--json` on every command that reports; `--dry-run`/`-n` on
  every command that changes state, ending with the fixed line
  `dry run: nothing was changed. Run it again without --dry-run.`; `--yes`/`-y`
  on every command that asks; `--override NAME --reason TEXT` (or a generated
  `--skip-<id>`) is the only bypass. Commands that rewrite data in place
  (`data check|alter|compress|migrate|replay|seed`, `target adopt`) are dry by
  default and write only with `--apply`. Irreversible deletions ask (`[y/N]`; no
  TTY means refuse and say "add --yes").
- **Exit codes:** 0 done (or nothing to do), 1 refused or declined, 2 usage, 3
  done in part, 4 stopped on request (`q_facts.STOP_EXIT_CODE`), 5 unknown
  outcome, 6 `--once` found nothing to do (`q_facts.ONCE_EXIT_NO_WORK`), 130
  Ctrl-C on a followed attached backfill.
- **Pipes:** output into a pipe is never cut or wrapped; logs go to stderr
  (except `uqs logs` and `start -f`, where the log lines are the output).
- **Time ranges:** `--from/--to` is `[A, B)`, UTC by default; a command taking
  one of `--from`/`--to`/`--date`/`--last` takes all four; whole-partition
  commands take whole days only.
- **`--json`:** one object on stdout (`{<kind>: [...]}`, never a bare list),
  snake_case keys, `ok` a bool false exactly when the exit is non-zero, ISO-8601
  times with an offset, `procname` respelled `process`; a refusal is
  `{"ok": false, "error": ...}`. `json_out.emit()` is the only printer
  (`json.dumps(` is banned in `cli/` and `deploy/`).
- **No backward compatibility.** A renamed option or command keeps no old
  spelling, not even for a release: the old one is a usage error naming the new
  (`cli/common/renames.py`), `RENAMED_AFTER` is empty, and no command is hidden
  (`test_cli_rename_expiry.py`). Environment variables follow the same rule.
  (The docstring of `test_cli_conventions.py` still describes the older
  hidden-alias policy; `renames.py` is the authority.)
- **Help text:** the first paragraph at most 110 characters; no issue numbers
  (`#123`) in help; option help starts with a capital, a backtick, a quote or
  `$`; never "fleet" (say "stack").

## 4. Adding or changing a command

1. Write it in a module under its group's subpackage of
   `python/uqs/src/uqs/cli/` on the shared `app` (`cli/common/shared.py`) or a
   group Typer, with its effect mark: `@app.command(cls=ChangesState)`,
   `ReadOnly` or `ConfirmsOnServer` (`cli/common/safety.py`; traits
   `own_output`, `no_dry_run`, `dry_run_starts`, `acts_when` via `marked(...)`).
   `uqs --target` reads the mark to decide whether to ask first.
2. Import the module in `cli/entry.py` at the right place: **the import order is
   the `--help` order**.
3. Use the shared option types instead of a new spelling: `safety.DryRunOpt`,
   `YesOpt`, `ApplyOpt`, `OverrideOpt`, `ReasonOpt`; `json_out.json_opt`;
   `choices.ChoiceOpt` (help generated from the authoritative tuple);
   `concepts.py` (`job_arg`, `target_arg`, `table_arg`, `profile_opt`, ...);
   `vocabulary.duration`; `shared.BasePortOpt`, `ProcArg`, `ProcsArg`.
4. Prompt only through `safety.ask`/`confirm`/`choose`; `--json` never prompts
   and refuses without `--yes`.
5. A bypass is a `Guardrail` (`python/uqs/src/uqs/guardrails.py`) registered in
   `safety.registry()`; never a bespoke `--allow-*`/`--force` flag.
6. Add the cases the tests demand: `test_json_stdout.py` `CASES` (every `--json`
   command runs for real and prints exactly one document);
   `test_dry_run_tree.py` `CASES` (every `--dry-run` runs in a copied repo and
   must touch no file); `test_cli_conventions.py` `PROMPTING_MODULES` /
   `JSON_WITHOUT_YES` where they apply; an MCP tool if it should be exposed.
7. Update `docs/reference/cli.md` only for a convention change, and the guide
   that describes the task (via the `docs-maintainer` agent for an existing
   page). Never copy `--help` into a doc.

A command merged without its case fails
`test_json_stdout.py::test_every_json_command_has_a_case` or
`test_dry_run_tree.py::test_every_dry_run_is_walked` on every branch cut from
that commit, and the pre-push hook then blocks unrelated work. Add the case in
the PR that adds the command.

## 5. MCP - `uqs-mcp`

`uv run uqs-mcp` starts a FastMCP stdio server (`python/uqs/src/uqs/mcp.py`,
`mcp_server.py`). Each tool mirrors one CLI command (`@tool("config show")` ->
`uqs_config_show`) and fails at import if the CLI lacks it. Tools include
`uqs_start`, `uqs_stop`, `uqs_restart`, `uqs_status`, `uqs_clean` (dry unless
`confirm=true`), `uqs_query`, `uqs_config_show`, `uqs_config_set`, one list tool
per noun, `uqs_logs`, `uqs_connector_start|stop|status`. Every failure is
`{"ok": false, "error"}`. Retired tool and parameter names are refused, not
aliased. The CLI, MCP and the frontend's `/control` share
`stack/start_policy.py`'s refusals.

## 6. Where the code lives

  | Package                                                                         | Holds                                                                                                                                                                                                                                                                                                                                                                                                                                 |
  | ---                                                                             | ---                                                                                                                                                                                                                                                                                                                                                                                                                                   |
  | `uqs/cli/`                                                                      | one subpackage per command group (`fleet`, `summary`, `config`, `data`, `job`, `connector`, `deploy`, `target`) plus `common/`: `shared.py` (the app, exit codes), `safety.py`, `json_out.py`, `choices.py`, `concepts.py`, `vocabulary.py`, `renames.py`; `entry.py` stays at the top                                                                                                                                                |
  | `uqs/stack/`                                                                    | running the stack: `runtime.py` (`bootstrap`, torq.sh), `procs.py` (process.csv), `env.py`, `generated.py`, `start_policy.py`, `start_check.py`, `occupancy.py`, `stopping.py`, `alive.py`, `listing.py` (status columns), `logs.py`, `replay.py`, `hdb_*.py`, `backfill.py`, `bundles.py`, `qtree.py`, ...                                                                                                                           |
  | `uqs/model/`                                                                    | the registry derived from the q declarations (`uqf-etl-framework.md` section 8)                                                                                                                                                                                                                                                                                                                                                       |
  | `uqs/scaffold/`                                                                 | `uqs job new`/`remove` templates and writers                                                                                                                                                                                                                                                                                                                                                                                          |
  | `uqs/attach/`                                                                   | attachments (`uqf-on-torq.md` section 8)                                                                                                                                                                                                                                                                                                                                                                                              |
  | `uqs/deploy/`                                                                   | build, push, targets, `--target` control                                                                                                                                                                                                                                                                                                                                                                                              |
  | `uqs/external/`                                                                 | connectors: processes this tree starts but does not own                                                                                                                                                                                                                                                                                                                                                                               |
  | `uqs/checks/`                                                                   | live schema views, stack smoke, traced queries                                                                                                                                                                                                                                                                                                                                                                                        |
  | `uqs/logger/`                                                                   | loguru setup; logs to stderr                                                                                                                                                                                                                                                                                                                                                                                                          |
  | `uqs/generated/q_facts.py`                                                      | facts exported from q (`derive.py q-facts`); never edit                                                                                                                                                                                                                                                                                                                                                                               |
  | `uqs/interpreter.py`                                                            | which q: `QCMD`, else `q` on PATH; identifies KDB-X vs PeachQ by asking the binary                                                                                                                                                                                                                                                                                                                                                    |
  | `uqs/guardrails.py`, `liveness.py`, `renamed_env.py`, `runtimes.py`, `paths.py` | guardrail records; "is the writer of this file gone" (same host, case-insensitive); refused env names; runtimes; every path                                                                                                                                                                                                                                                                                                           |

Other Python packages in the workspace (root `pyproject.toml`):

- `python/uqf_frontend` - FastAPI backend-for-frontend over the TorQ gateway. q
  programs are constants (`queries.py`), identifiers are whitelisted
  (`catalog.py`), values go as typed IPC arguments - client input never reaches
  q as text. Run:
  `uv run uvicorn --factory uqf_frontend.app:create_app --port 8000`.
- `web/` - React 19 + TypeScript + Vite;
  `npm --prefix web ci && npm --prefix web run dev`, tests with `vitest`,
  formatting owned by prettier (`npm run check`).
- `python/uqf_airflow_provider` - maps `.qetl.status` files to Airflow sensor
  states (`QWorkerStatusSensor`); no scheduler lives in this tree.
