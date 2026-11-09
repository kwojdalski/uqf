# Environment variables

The one operator-facing list. The judgements in it --- "required or optional",
"what happens when it is unset", "who sets it" --- are no generator's to read
off the code, so each variable is one hand-written row of
`docs/reference/environment/read.csv` (or `produced.csv`), and the two variable
tables below are rendered from those rows by
`uv run python scripts/generate/derive.py environment-tables` (#2169).
`derive.py environment-tables --check` fails on a hand edit inside a table or a
row not yet rendered, and `scripts/gates/check.py env-reference` checks the rows
against the code, so the list cannot quietly fall behind. Adding a variable is
adding a row to the CSV, beside related ones, and regenerating.

Read this table as the operator's surface. Two things make it non-obvious
without it:

- **One variable is built mechanically and appears nowhere in the source.**
  `.qetl.cfg.env_name` maps a config key to `UQF_` plus the upper-cased key, so
  `UQF_DRY_RUN` is read by `.qetl.job.bounded.runtime.mode` without the string
  `UQF_DRY_RUN` existing in any production file. `grep` cannot find it. That is
  the single strongest argument for this page existing.

- **Every variable this repository defines is `UQF_` or `UQS_`, with
  underscores** (`UQF_ROOT`, `UQF_STATUS_DIR`, `UQF_DRY_RUN`). `UQS_` is the uqs
  Python package's own - only `python/uqs/` reads one - and anything q, the
  frontend or another package reads is `UQF_`;
  `scripts/gates/check.py env-reference` holds the code to that (#1890).
  `LOG_LEVEL` and `LOG_REGEX` have no prefix: the loggers of every layer read
  them. The unseparated names (`TORQDATA`, `KDBLOG`, `QCMD`) are TorQ's own,
  read by the vendored tree under those names, so they stay as TorQ spells them:
  renaming them would mean editing `lib/torq`, which the vendored-tree rule
  forbids. Both of TorQ's placeholder expanders - `envsubst` in `torq.sh` and
  `removeenvvar` in `torq.q` - accept underscores, so a `${UQF_SCRIPTS}` in
  `process.csv` resolves like any other.

- **A variable that holds a length of time ends in its unit** (`_SECONDS`,
  `_DAYS`, `_MS`, `_MINUTES`, `_HOURS`), and `check.py env-reference` refuses a
  timeout, interval or retention that does not (#2111). The frontend's two
  retention-bounded histories, activity and config pushes, use the same two
  words: `_RETENTION_DAYS` for how long and `_MAX_KEPT` for how many.
  `UQF_FRONTEND_MAX_ROWS` is the per-request query cap and nothing else. A
  renamed variable is refused, naming its new name
  (`uqs.renamed_env.RENAMED_ENV`), by `uqs` and by the frontend at startup:
  `UQF_FRONTEND_TIMEOUT`, `UQF_FRONTEND_CAPTURE_INTERVAL`,
  `UQF_FRONTEND_ACTIVITY_*MAX_ROWS`, `UQF_FRONTEND_CONFIG_HISTORY_KEEP` and
  `_DAYS` are refused this way.

- **`uqs` loads `.env` and `.envrc` into every process it starts.** Before any
  command runs, `uqs` and `uqs-mcp` put both files from the repository root into
  their own environment, which every process they start inherits
  (`uqs.stack.envfiles`). The environment `uqs` was started with wins, then
  `.envrc`, then `.env`. `.envrc` is evaluated by `direnv export json`, never
  sourced, so it loads only once approved with `direnv allow`; without direnv,
  or while the file is blocked, it is skipped with a warning. Only variable
  names are logged, at DEBUG.

- **Outside `uqs`, almost none of this is read from `.env`.** One reader
  consults the file itself: `.qdata.cfg` in `src/integrations/data.q`, for
  `DATABENTO_DATA_DIR`. A process started some other way (q run directly,
  pytest, the browser app) reads the OS environment only. `.env.example`
  therefore lists only what `.qdata.cfg` reads, and sorts the rest by theme
  (secrets, per-run arguments, deployment wiring, developer knobs);
  `check.py env-reference` refuses any other key in it.

## Read by this repository

<!-- panache-ignore-format-start -->
<!-- BEGIN GENERATED environment-read: `uv run python scripts/generate/derive.py environment-tables` from docs/reference/environment/read.csv - edit that file, not this table -->

| Variable | Read by | Required | Unset behaviour |
| --- | --- | --- | --- |
| `UQF_MODE` | `.qetl.job.bounded.runtime.mode` (`src/etl/core/worker_runtime.q`) | no | `run`, unless `UQF_DRY_RUN` is set. One of `validate`, `plan`, `dry_run` or `run` - see the table in `docs/reference/pipeline-declarations.md`. `uqs job backfill --mode` sets it for one run. An unknown value, or `run` with `UQF_DRY_RUN` also set, is refused rather than guessed at |
| `UQF_DRY_RUN` | `.qetl.job.bounded.runtime.mode` (`src/etl/core/worker_runtime.q`) | no | false — the worker publishes for real. Opt-in on purpose: defaulting to true would make a worker silently do nothing and report success |
| `UQF_ON_CONFLICT` | `.qetl.job.bounded.on_conflict` (`src/etl/core/bounded_worker.q`) | no | the worker's own `on_conflict`, `upsert` unless it declares otherwise. One of `upsert`, `replace`, `ignore`, `append`, `fail`: what a backfill's write does with a row whose `row_key` is already there. `uqs job backfill --on-conflict` sets it for one run |
| `UQF_FIXTURE_WRITES` | `.qetl.source.fixture_writes_allowed` (`src/etl/core/source_credentials.q`) | no | false — a backfill whose source has no credential is refused at init rather than writing the source's fixture (a dry run, `plan` and `validate` are not refused: they write nothing). `1` allows it, and its coverage is recorded under the release tagged `~fixture` (`v1~fixture`), so a later live run still fetches the range and replaces the fixture's rows. `uqs job backfill --fixture` sets it for one run |
| `UQF_HDB_RELOAD_SECONDS` | `.qproc.backfill.reload_interval` (`scripts/processes/torq_backfill.q`) | no | 30 — a backfill asks the HDB to reload at most every 30 seconds while it runs, plus once at the end; `0` reloads as each day is finished. Never while a partition is mid-append (`.qetl.io.due`) |
| `UQF_SOURCE_CRED_<SOURCE>` | `.qetl.source.require_credentials` (`src/etl/core/source_credentials.q`) | no; overrides `sources.csv` | wins over the source's `sources.csv` row ([where a source connects](pipeline-declarations.md#where-a-source-connects-sourcescsv)); with neither, `require_credentials` refuses and names it |
| `UQF_CRYPTO_CAPTURE_GLOB` | `.qpipe.source.crypto_market_data.from_expr` (`sidecars/cryptorust/crypto_market_data.q`) | no | unset: the source reads the credential database's `market_data_live` view. Set to an ABSOLUTE glob of the recorder's finalised shards (`$CRYPTORUST_ROOT/data/live*.parquet`) to read them directly, with the credential `DRIVER=DuckDB;Database=:memory:` -- the only arrangement that works while the recorder runs, because it holds `live.duckdb`'s write lock but renames a shard into place only once it is complete. A run covers windows only up to the newest finished shard (`complete_through`); the rest is planned again next run. A relative glob is refused |
| `UQF_KFK_HOME` | `.qpipe.job.kafka_feed.load_library` (`src/etl/streaming/kafka_feed.q`) | for `kafka_feed1` only | unset: `kafka_feed1` refuses to start, naming this variable. The directory holding `kfk.q` and `libkfk.so` built from KxSystems/kafka v1.6.6; nothing else loads it. A release built with `uqs deploy build --kfk-home` sets it in `deploy.env` to its own copy, `.release/kafka` ([packaging](../services/kafka.md#in-a-release)) |
| `UQF_KAFKA_<SETTING>` | `.qpipe.job.kafka_feed.read_env` (`src/etl/streaming/kafka_feed.q`); `BROKERS` and `TOPICS` also `uqs.external.kafka_feed` | `BROKERS` for `kafka_feed1`; the rest no | `BROKERS` (bootstrap servers), `TOPICS` (comma separated; default every declared topic), `GROUP` (default `uqf-kafka-q`), `OFFSET_RESET` (default `earliest`), `SECURITY_PROTOCOL`, `SASL_MECHANISM`, `SASL_USERNAME`, `SSL_CA_LOCATION`, and `SASL_PASSWORD_ENV` -- the NAME of the variable holding the SASL password, which is never itself a setting. `uqs connector start kafka` reads `BROKERS` (unset: `localhost:9092`) and `TOPICS` (unset: every declared topic) after its own options, and never `GROUP`: its group is its own (see [connector configuration](#connector-configuration)) |
| `QCMD` | `uqs.interpreter.q_command`, `uqs.interpreter.q_impl`, the pytest `kdbx` fixture, `.qt.support.q_interpreter` | no | `q` on `PATH`, as `torq.sh` itself defaults it. The one q interpreter: every q lane, the running stack (`build_env` passes it to `torq.sh`), its HDB filler and the backfill launcher. There is no second fallback (no `~/.kx/bin/q`); set it, with `QHOME`, to choose an interpreter. It replaced `Q`, `QBIN` and `UQFQ` (#414). It reaches every process: `torq.sh` prefers a row's `qcmd` to `QCMD`, so the generated `process.csv` writes it into each row left to the default (an empty `qcmd`, or `q`). A custom interpreter in a row, and an override from `uqs config set <proc> qcmd`, even to bare `q`, still win. It is the ONLY variable that chooses an interpreter (#1753): which implementation it is, KDB-X or PeachQ, is asked of the binary itself, once per binary per process, and a binary that answers neither is refused with what it printed. A PeachQ that is not the pinned build is used, and named in one warning with the pin. The `peachq` runtime sets it, for its own stack only, to `$QCMD` when that is PeachQ, else the pinned build |
| `UQF_KDB40` | `scripts/portable/tests/test_flatten_contexts.py` | no | unset, and those tests skip. A kdb+ 4.0 binary - the server-equivalent build - that the tree converted by `scripts/portable/flatten_contexts.py` must run on (#856) |
| `QINIT` | `uqs.stack.env.interpreter_env` | no | unset. q's own: the file q loads before anything else. A PeachQ runtime (`peachq`, `peachq-etl`) sets it, for its own processes only, to `scripts/peachq_init.q`, which defines what TorQ's processes call and PeachQ lacks (`.Q.chk`, which the wdb calls as it starts, #1650). It replaces an operator's `QINIT` for those processes; a KDB-X runtime leaves it alone |
| `UQF_PEACHQ_COMMIT` | `scripts/peachq.py` | no | the pinned commit. When set, a full 40-character hash of another PeachQ commit, built and cached like the pin, logged on every run and recorded in its cache entry's `manifest.json`, so a binary built from it is never taken for the pinned build. The pinned commit itself, or a short hash, is refused |
| `KDBX_ATTESTATION_OVERRIDE` | `scripts/gates/check_kdbx_attestation.py` | no; CI sets it from the PR label | the check refuses an unattested tree. CI sets it to `true` when the PR carries the `kdbx-attestation-override` label; the check then passes, with a warning naming the label and the paths (#1543) |
| `XDG_CACHE_HOME` | `scripts/peachq.py`, `uqs.deploy.fetch` | no | `~/.cache`. Where the pinned PeachQ build is cached, under `uqf/peachq/`, and where `uqs deploy push` downloads an artifact given by tag or URL, under `uqf/releases/` |
| `GH_TOKEN` | `uqs.deploy.fetch` | no | none. A GitHub token `uqs deploy push` sends when it lists a release's assets by tag or downloads one, for a private repository. Sent only to api.github.com and github.com, and never printed |
| `GITHUB_TOKEN` | `uqs.deploy.fetch` | no | as `GH_TOKEN`, read when that is unset |
| `QLINTER` | `scripts/gates/check_q_traps.py` | no | `qlinter` on `PATH`. The [q linter](https://github.com/kwojdalski/q-lint), which the trap hook delegates every correctness rule to, and which must be the version pinned in `.qlinter-version`. Set it to point at a build that is not installed — the same escape hatch `Q` gives for the interpreter. The hook refuses rather than skipping when neither finds it: a hook that reports success over checks that did not run is worse than one that fails |
| `UQF_Q_COVERAGE_RAN` | `tests/qrun/test_q_coverage.py`, set by `scripts/dev/attest_kdbx.py` | no | nothing is written. Names a file the `q-coverage` lane writes `subset` or `all` to: whether the subset it was given decided, or the whole suite did because the subset found a function never entered (#1719). `attest_kdbx.py` records it as `q-coverage=`; not one to set by hand |
| `UQF_Q_COVERAGE_SELECTION` | `scripts/dev/test_impact.py`, set by `scripts/dev/attest_kdbx.py` | no | the `q-coverage` hook selects for itself: q-tests' suites and the suites `tests/q/coverage_map.txt` says enter the functions judged, judging the production files the change reaches, or the whole suite. Set by `attest_kdbx.py` to the selection its trailer records (`ALL` or `-files a.q,b.q -modules src/x.q,...`); not one to set by hand |
| `UQF_REACH_CHILD` | `scripts/dev/reach_child/sitecustomize.py`, set by `scripts/dev/test_reach.py` | no | a Python child of a test records nothing. Set by a pytest run with `--record-reach` (what the `python-tests` hook passes) to `<directory>\|<token>`: every Python started from it then writes what it reads, lists and starts there, charged to the test that started it; not one to set by hand |
| `PYTHONPATH` | `scripts/dev/test_reach.py` | no | Python's own. A pytest run with `--record-reach` puts `scripts/dev/reach_child/` first on it, so each Python child loads the recorder's `sitecustomize.py`, and keeps what was there after it |
| `UQF_HOOKS_FULL` | `scripts/dev/hooks_full.py`, `scripts/dev/python_tests_hook.py`, `scripts/dev/test_impact.py`, via the commit hooks | no | the commit hooks are targeted: `python-tests` runs the Python tests the commit reaches, `q-tests` and `q-coverage` the q suites the branch reaches, and `doc-links` and `q-docs-peachq` do not run (CI runs both on every pull request that can change them). `1` is full mode: every hook as it ran before #1879 - the whole Python suite, the whole q suite and coverage gate (it implies `UQF_Q_TESTS_FULL`), the docs render and the docs' q blocks on PeachQ. `python3 scripts/dev/hooks_full.py --all` runs all of it over the whole tree, without a commit |
| `UQF_Q_TESTS_FULL` | `scripts/dev/test_impact.py`, via the `q-tests` hook and `scripts/dev/attest_kdbx.py` | no | the `q-tests` hook runs only the q suites the change reaches (`origin/master...HEAD` plus what is being committed), or all of them when the selector says ALL or cannot run. `1` runs the whole suite, and an attestation then records `q-tests=all` |
| `UQF_Q_TESTS_SELECTION` | `scripts/dev/test_impact.py`, set by `scripts/dev/attest_kdbx.py` | no | the `q-tests` hook selects for itself. Set by `attest_kdbx.py` to the selection its trailer records (`ALL`, `NONE` or `-files a.q,b.q`), so the hook runs exactly that; not one to set by hand |
| `UQF_Q_UNIT_FROM_COVERAGE` | the `q-tests` hook, set by `scripts/dev/attest_kdbx.py` | no | the hook runs the selected q suites itself. `1`, set by `attest_kdbx.py` after the `q-coverage` run passed with every test passing, skips that run - its verdict is the coverage run's (#1704) - and runs only the documented examples and the worked examples (`tests/qrun/test_worked_examples.py`); not one to set by hand |
| `UQF_ROOT` | `scripts/torq_*.q` | yes | the `\l` of every repository script fails. Set by `build_env`, not by hand |
| `UQF_STATUS_DIR` | `.qetl.status.status_dir` (`src/etl/core/status.q`) | no | falls back to `$TORQDATA/status`. Every bootstrap records the directory it resolved in the stack's `setenv.sh`, and the frontend reads it from there, through `UQF_FRONTEND_STACK_ROOT` - so a frontend started from another shell reads the directory the workers were started with (#2038) |
| `UQF_TEST_FAILURES` | `tests/run_tests.q` and `tests/q/run_coverage.q` (`.qt.support.write_outcomes`); set by `scripts/dev/attest_kdbx.py`, `scripts/portable/full_suite.py` and `tests/qrun/test_q_suite.py` | no | nothing is written. A file to write every test that did not pass to: name, status and why, tab-separated; one empty line when all passed |
| `UQF_TEST_RESULTS` | `tests/run_tests.q` and `tests/q/run_coverage.q` (`.qt.support.write_outcomes`); set by `scripts/dev/attest_kdbx.py` and `tests/qrun/test_q_suite.py` | no | nothing is written. A file to write every test that ran to: suite and name, tab-separated, in run order |
| `DATABENTO_DATA_DIR` | `.qdata.databentoDir` (`src/integrations/data.q`) | for that path only | `.qdata.cfg` also accepts it from a `.env` file, then throws naming the key. Note this is a *data directory*, not a credential — the no-file rule is about secrets |
| `UQF_FRONTEND_GATEWAY_USER` | `uqf_frontend.config.Settings.from_env` | no | empty |
| `UQF_FRONTEND_GATEWAY_PASSWORD` | as above | no | empty |
| `UQF_FRONTEND_DATA_USER` | as above | no | empty |
| `UQF_FRONTEND_DATA_PASSWORD` | as above | no | empty |
| `UQF_FRONTEND_TIMEOUT_SECONDS` | as above | no | `30` seconds, enforced per query by kola |
| `UQF_FRONTEND_ENABLE_WRITES` | as above | no | **false — every `/control/*` route refuses with 403.** Off by default as the security posture, not caution: this deployment has one shared credential and an identity *claimed* through a header anyone can set, which is defensible while every route is a read. Once a route can stop the fleet or rewrite `process.csv`, "anyone who can reach the port" is the whole access control. Accepts true/false/1/0/yes/no/on/off; anything else refuses to start rather than reading as false |
| `UQF_FRONTEND_WRITE_TOKEN` | as above | no | the secret every control action must carry while writes are on, as `Authorization: Bearer <token>`. **Required when `UQF_FRONTEND_ENABLE_WRITES` is on: the server refuses to start without it**, rather than leaving writes open to anyone who can reach the port. Generate one with `openssl rand -hex 32`. The app asks for it in the Control view, under `npm run dev` too (#2182) |
| `UQF_FRONTEND_ALLOWED_HOSTS` | as above | no | comma-separated hosts a request's `Host` header may name while writes are on - default `localhost,127.0.0.1,::1`. Anything else is a 403, which closes DNS rebinding: a page in a browser on this machine that resolves its own name to 127.0.0.1 still sends its own name as `Host`. Add a name here only when the API is reached by it |
| `UQF_FRONTEND_TRUSTED_IDENTITY_HEADER` | as above | no | unset: **no header is ever an identity** - every caller is `anonymous`, which is what the frontend always did. Set, the lower-case name of the header an authenticating proxy (oauth2-proxy, Authelia, Tailscale serve) puts the user it authenticated in, e.g. `x-forwarded-user` (`uqf_frontend/identity.py`, #2045). It is believed only from a peer in `UQF_FRONTEND_TRUSTED_PROXIES`; from any other address, sent twice, or not shaped like a user name it is ignored and the caller is anonymous. Requires `UQF_FRONTEND_TRUSTED_PROXIES`, and the API refuses to start without it. `authorization`, `cookie`, `host` and `x-forwarded-for` are refused. The proxy must REPLACE the header a client sends, never append to it. The claimed `x-uqf-user` header never reaches the policy |
| `UQF_FRONTEND_TRUSTED_GROUPS_HEADER` | as above | no | unset: groups come from the policy file alone. Set, the header the same proxy puts the user's groups in, comma-separated (e.g. `x-forwarded-groups`); believed under the same conditions as the identity header and only alongside a believed user. Requires `UQF_FRONTEND_TRUSTED_IDENTITY_HEADER` |
| `UQF_FRONTEND_TRUSTED_PROXIES` | as above | no | comma-separated IP addresses or CIDR ranges the identity header is believed from - the request's direct TCP peer, never `X-Forwarded-For`. Required with `UQF_FRONTEND_TRUSTED_IDENTITY_HEADER` and refused without it; a malformed entry stops the API at startup. Run uvicorn with `--no-proxy-headers` (or keep `--forwarded-allow-ips` off the proxy's address) so the peer the frontend sees is the proxy's own |
| `UQF_FRONTEND_POLICY_FILE` | as above | no | unset: the packaged default access policy (`uqf_frontend/policy/default_policy.csv`, `p, *, *, *`) - everyone may use every feature, exactly as before #2045, and it cannot be changed from the frontend. Set, this deployment's own Casbin policy of grants (`p, <subject>, <feature>, <action>`) and memberships (`g, <member>, <group>`) over the features in `uqf_frontend/access.py`. Read strictly - an unknown feature, action or line stops the API at startup - and re-read when it changes on disk, without a restart; a change that does not parse is logged and the last good policy stays. A named file that does not exist stops the API rather than falling back to the default. The write token and Host check still stand in front of every write |
| `UQF_FRONTEND_SETTINGS_FILE` | as above | no | unset: the runtime settings (the default activity level, activity retention, config push history) can be read in the Settings view but not changed there. Set, a JSON file the Settings view writes them to, atomically, and the API applies over the environment at startup, so a change made there survives a restart (`uqf_frontend/settings_admin.py`, #2046). `uqs target config restore` reads the config push retention from it too (`uqf_frontend.config.history_bound`, #2072), so a CLI restore prunes to what the Settings view shows. A file that does not parse stops the API. No secret is ever kept in it: the write token and passwords are set only in the environment |
| `UQF_FRONTEND_INSPECTION_MODE` | as above | no | **false.** Set, the whole frontend is in inspection mode (`uqf_frontend/inspection.py`, #2032): every route mapped to a `write` action is a 403 `InspectionMode`, audited, whoever asks and whatever the access policy grants; reads stay available. The write token and Host check still stand in front. One target is put in inspection mode on its own with `inspection = true` in `deploy_targets.toml`, which `uqs` honours too. It does not make ssh or the server read-only. Accepts true/false/1/0/yes/no/on/off; anything else refuses to start |
| `UQF_FRONTEND_INSPECTION_USER` | as above | no | unset: the frontend's own gateway and per-process reads use `UQF_FRONTEND_GATEWAY_USER` in inspection mode too, and `/me/capabilities` says `q_login: "service"`. Set, the restricted q login they use instead while `UQF_FRONTEND_INSPECTION_MODE` is on; the q side makes it read-only (README, "Restricted q logins"). Refused at startup without inspection mode, since nothing would use it |
| `UQF_FRONTEND_INSPECTION_PASSWORD` | as above | no | the password of `UQF_FRONTEND_INSPECTION_USER`; refused without it. Never logged or served |
| `UQF_FRONTEND_STACK_ROOT` | as above | no | unset: the gateway is `localhost:6057` (the default base port +7) and fleet health, the per-process query log, backfill status and `/ui/` report themselves unconfigured. Set, the stack the frontend serves, read rather than restated (`uqf_frontend.stack`): the base port from the `setenv.sh` bootstrap writes (else the runtime's), the gateway's host and port and the fan-out list from the generated `process.csv`, the status directory by `uqs.stack.runs.status_dir`, and `web/dist` once built - each read again whenever the stack's `process.csv` or `setenv.sh` changes, so a stack bootstrapped after the frontend started, or restarted on another base port, is reached where it is (#2027). A start, stop or restart is reported ok only once the processes it named are up, or down, and is refused with 409 while another holds the stack's lifecycle lock (#2028). `UQS_RUNTIME` and `UQS_DATA_ROOT` move it as they move the stack. A path that is not the repository is refused at startup. The `/control/*` routes act on it, or without it on the repository the orchestrator package was installed from |
| `UQF_FRONTEND_CAPTURE_DIR` | as above | no | where usage rows are captured to, as JSON Lines. **Unset means capture does not run**, and that is lossy rather than merely inactive: `.usage.flushtime` is one day in a standard stack, so without it the usage view can only ever show the last day and older history is gone for good |
| `UQF_FRONTEND_CAPTURE_INTERVAL_SECONDS` | `900` | no | seconds between capture sweeps. Keep it well under the flush window — the interval is the whole guarantee, and one longer than the window loses rows silently |
| `UQF_FRONTEND_ACTIVITY_DB` | as above | no | the SQLite file activity history and the action audit are kept in (`uqf_frontend/activity.py`). Unset: `$TORQDATA/frontend/activity.sqlite3` for the stack `UQF_FRONTEND_STACK_ROOT` names, which `uqs clean` removes with the rest of `$TORQDATA`; with no stack root either, history is kept in memory and `GET /activity` reports it as not persistent. A file that cannot be opened stops the API at startup |
| `UQF_FRONTEND_ACTIVITY_RETENTION_DAYS` | `30` | no | days of `actions`-level activity history kept - browser actions and the server's audit of every write; older rows are pruned at startup and every 200 writes |
| `UQF_FRONTEND_ACTIVITY_MAX_KEPT` | `100000` | no | the most `actions`-level browser rows kept, and separately the most server audit rows, newest first, whatever their age (#2019) |
| `UQF_FRONTEND_ACTIVITY_INTERACTIONS_RETENTION_DAYS` | `7` | no | days of `interactions`-level activity kept (#2000); each level has its own bounds, and what they evict is counted on `GET /activity` |
| `UQF_FRONTEND_ACTIVITY_INTERACTIONS_MAX_KEPT` | `50000` | no | the most `interactions`-level rows kept, newest first |
| `UQF_FRONTEND_ACTIVITY_DEBUG_RETENTION_DAYS` | `1` | no | days of `debug`-level activity kept |
| `UQF_FRONTEND_ACTIVITY_DEBUG_MAX_KEPT` | `10000` | no | the most `debug`-level rows kept, newest first |
| `UQF_FRONTEND_ACTIVITY_LEVEL` | `actions` | no | the level a browser tab records its activity at unless its operator picks another in the Activity view: `off`, `actions`, `interactions` or `debug` (#2000). `off` stops browser history only - the server audits every write whatever it says. Any other value stops the API at startup |
| `UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT` | `50` | no | the newest config pushes each target's push history keeps on the server, under `shared/config/.uqf-history/` (#2002). Opening a push prunes the history to this and `UQF_FRONTEND_CONFIG_HISTORY_RETENTION_DAYS` - the tighter wins - and each pruned push is audited as `config.history.prune`. `uqs target config restore` reads it too. 1 to 10000 |
| `UQF_FRONTEND_CONFIG_HISTORY_RETENTION_DAYS` | `90` | no | the days a config push is kept in its target's history (#2002), beside `UQF_FRONTEND_CONFIG_HISTORY_MAX_KEPT`. 1 to 3650 |
| `UQF_FRONTEND_MAX_ROWS` | as above | no | `DEFAULT_MAX_ROWS` — a hard cap, whatever the caller asks for. The per-request cap of `/query` and `/ops/*` only: how many rows a history keeps is its `_MAX_KEPT` (#2111) |
| `UQF_API_ORIGIN` | `web/vite.config.ts` | no | `http://127.0.0.1:8000`. Dev proxy only — it has no effect on a built bundle |
| `LOG_LEVEL` | `uqs.cli.entry` (`_env_log_level`), `uqs.logger.decorators` | no | `INFO`. Sets the level every `uqs` command logs at, and turns on the `logged_function` call trace at `DEBUG`. An unrecognised value falls back to `INFO` rather than aborting — a typo in a log level must not stop the fleet being started or inspected. `uqs --debug` is the same thing per-invocation, and wins over this |
| `LOG_REGEX` | `uqs.logger.core` | no | no name filtering |
| `UQS_LICENCE_CONNECTIONS` | `uqs.model.profiles` (`licence_limit`) | no | `16`, the community licence; no cap on PeachQ (`QCMD` answering as PeachQ). The concurrent connections your q licence allows one process. It sets the budget a start is held to - `--profile`, `uqs profile list`' `fits` column and the warning on a positional `uqs start` - and nothing else: the committed default start is held to 16 in CI whatever this is. A value that is not a whole number, or leaves no slot once 2 are held back, is refused rather than ignored |
| `UQS_RUNTIME` | `uqs.runtimes` (`runtime_from_env`) | no | `uqf`. Which stack uqs builds and runs: `uqf`, the starter pack plus this tree; `torq`, the starter pack as it ships; `peachq`, the same on PeachQ; `crypto` or `fx`, the starter pack plus one profile's pipelines; or `peachq-etl` (experimental). Each has its own `output/uqs-<name>` and base port. `uqs --runtime` sets it, and bootstrap writes it into `setenv.sh`, so child processes agree. An unknown name is refused - see [runtimes](../guides/uqs.md#runtimes) |
| `UQS_RUNTIME_BUNDLES` | `uqs.stack.runtime_bundles` (`declaration_path`) | no | `runtime_bundles.json` at the repository root. The file declaring which sidecar bundles each runtime has; set, one outside the tree. A path that does not exist is refused |
| `TORQHOME` | `uqs.paths.paths_for_root` | no | the vendored `lib/torq`. Set, it names an existing TorQ, and the launcher, `build_env` and the prerequisite checks all use it. A value that is not an absolute directory holding `torq.q` is refused, never ignored. What `uqs deploy push` sets on a server (#773) |
| `TORQAPPHOME` | `uqs.paths.paths_for_root`, `src/etl/plant_registry.q` | no | the vendored `lib/torq-finance-starter-pack`. Set, it names an existing starter pack, which must hold `database.q`; `plant_registry.q` reads the vendored tables from it. Refused when wrong, as `TORQHOME` is |
| `LD_LIBRARY_PATH` | `uqs.stack.odbc_home` (`env_vars`), `scripts/dev/bench_compression.q` | no | kept, after the ODBC home's `lib/`, for the q that `uqs config sources check --odbc-home` starts. A release built with `--kfk-home` puts its `.release/kafka/lib` first in `deploy.env`, for every process it starts. The compression benchmark passes it on to each q it starts, for the snappy, lz4 and zstd libraries |
| `DYLD_LIBRARY_PATH` | `scripts/dev/bench_compression.q` | no | the loader's own. On macOS, where q finds the snappy, lz4 and zstd libraries (`/opt/homebrew/lib` with Homebrew); the compression benchmark sets it again on each q it starts, because macOS drops it from anything `/bin/sh` runs. Without it those settings are listed as unavailable |
| `UQS_TORQ_LAUNCHER` | `uqs.stack.launcher` (`torq_launcher`) | no | `$TORQHOME/torq.sh`. Set, an absolute path to a site's own TorQ launcher, run in its place with TORQHOME still the core and the generated SETENV and TORQPROCESSES. A relative path or anything but an executable file is refused. What `uqs deploy push --torq-launcher` sets on a server |
| `UQS_PROCESS_FIELDS` | `uqs.stack.site_fields` (`declared`) | no | unset: the generated `process.csv` has TorQ's own columns only. Set, comma- or space-separated column names a site launcher reads that TorQ lacks, such as `taskset_no`: each is appended to the header and every row, empty unless `uqs config set` gives that process a value. A name with anything but letters, digits and `_`, or one of TorQ's own columns, is refused. What `uqs deploy push --process-fields` sets on a server |
| `UQS_ODBC_HOME` | `uqs config sources check`, `uqs setup odbc` (`--odbc-home`/`--home`) | no | unset: no private ODBC setup is loaded. Set, the ODBC home whose `current/env.sh` the check loads; `uqs deploy push --odbc-home` writes it into the release's `deploy.env` (see [ODBC sources](../guides/odbc.md)) |
| `UQS_DATA_ROOT` | `uqs.paths.paths_for_root` | no | the checkout's `output/`. Set, each runtime's data directory (`TORQDATA`) is under it instead, so a deployed release keeps its HDB, logs and status outside itself. It must be an existing absolute directory |
| `UQS_ATTACH_DIR` | `uqs.stack.attach_state` (`state_root`) | no | `attachments/` under `UQS_DATA_ROOT`, else the checkout's `output/`. Set, where `uqs job attachment add` keeps each attachment's saved configuration and operation manifest. Not a runtime's data directory: an attachment belongs to a stack, not to a runtime |
| `KDBSECURE` | `uqs.stack.attached_publisher` (`auth`) | no | Unset, an attached publisher starts under the starter pack's authentication, which needs `$TORQAPPHOME/appconfig/passwords/accesslist.txt`; with neither, its start is refused rather than run on TorQ's demo credentials. Set, to the directory a site keeps its encrypted credentials in, the publisher authenticates with the site's own loader and `.pm`: its row names no `-U`, and `UQF_SITE_AUTH` keeps uqf's handlers off them (#1847). A native registration (`--process-file`) judges the same by the site's TorQ application, the one the process file is in, and its launcher exports both (#1849) |
| `UQF_ATTACH_REACH_PASSWORD` | `uqs.attach.backfill_run`, `scripts/dev/attached_backfill.q` | no | set by `uqs job backfill --attachment` for the one runner process it starts: the password of the attachment's user (from its `password_env`, else TorQ's starter-pack default), so the runner can ask the verified HDB readers to reload. Never on a command line, never read from your shell |
| `UQF_ATTACH_WRITER_PASSWORD` | `uqs.attach.backfill_remote`, `scripts/dev/attached_backfill.q` | no | set by `uqs job backfill --attachment --writer` for the extractor it starts (#1650): the password of the attachment's user, which the receiver `uqs job receiver start` started on the stack's machine authenticates. Never on a command line, never read from your shell |
| `UQS_DEPLOY_TARGETS` | `uqs.deploy.targets` | no | `deploy_targets.toml` at the repository root. The file `uqs deploy push\|status\|rollback\|prune TARGET`, `uqs --target` and `uqs target` read their targets from. Set but absent is refused |
| `UQS_DEPLOY_HISTORY` | `uqs.deploy.history` | no | `deploy_history.json` beside the targets file. Where `uqs deploy push` records each deployment, and `uqs deploy history` reads them |
| `UQS_TUNNEL_CHECKS` | `uqs.deploy.tunnel_plan` | no | `tunnel_checks.json` beside the targets file. Where `uqs target tunnel check` records each mapping's last success, and `uqs target tunnel plan` reads it |
| `VISUAL` | `uqs.cli.target_edit` | no | the editor `uqs target edit` opens the copy of `deploy_targets.toml` in, as a command line (`code --wait`). Unset: `EDITOR` |
| `EDITOR` | as above | no | the editor when `VISUAL` is unset; unset too: `vi` |
| `UQF_FX_POSITION_LIMITS` | `.qpipe.job.fx_positions.on_start` (`src/etl/streaming/fx_positions.q`) | no | unset: `fxpositions1` starts with no limits and warns that nothing is policed. A path to a limits CSV loads it at start; a missing or malformed file stops the job from starting (#1112) |
| `UQF_LIVE_SOURCES_REQUIRED` | `.qetl.source.live_required` (`src/etl/core/source_credentials.q`) | no | unset: a source with no credential runs on its fixture. `1` refuses that instead - a bounded worker fails at init and a polling feed publishes nothing - so no synthetic row is published or recorded as covered. `uqs deploy push --live` writes it into the release's `deploy.env` |
| `UQS_QUERY_RENDER` | `uqs.stack.render` (`renderer`) | no | `q`: `uqs query` and its gateway session print results as q's console does. `kola` prints the Python objects kola makes of them - a Polars DataFrame for a table. `--render` wins over it; `--export` always writes the data whichever is set. Any other value is refused rather than ignored |
| `NO_COLOR` | `uqs.logger.core` (`_colorize`) | no | colour on a terminal, plain in a pipe or a file. Set to anything non-empty and `uqs`'s own log lines are never coloured; wins over `FORCE_COLOR` ([no-color.org](https://no-color.org)) |
| `FORCE_COLOR` | as above | no | as above. Set to anything non-empty and `uqs`'s log lines are coloured even when piped - for a pager that renders colour, e.g. `FORCE_COLOR=1 uqs logs \| less -R` |
| `UQF_TP_CREDENTIAL` | `uqs.external.connection` (every `uqs connector start`) | no | the `user:password` every external connector presents to `stp1`, after the variable `--tp-credential-env NAME` names. A connector hands it to the process it starts in this variable, never on its command line. Unset: the starter pack's labelled DEMO login, written in `uqs.external.connection` and never read from a vendored password file. A value that is not `user:password` is refused, naming the variable and never the value. A deployed server can export it from `shared/config/secrets.env` |
| `DATABENTO_API_KEY` | `databento_feed` (the `sidecars/databento` bundle's connector) | for `uqs connector start databento` only | Databento's own variable name, so an existing export works unchanged. The live feed refuses to start without it rather than failing on its first call; the ODBC backfill does not read it |
| `CRYPTORUST_ROOT` | `uqs.external.crypto` | no | the checkout is located by the search path in `cryptorust_root`'s docstring |

<!-- END GENERATED environment-read -->
<!-- panache-ignore-format-end -->
Variables that only a fixture ever sets (`UQF_TEST_CFG_KEY`, `UQF_SHARED` and
the `.qetl.cfg` precedence fixtures) are deliberately absent: they are not an
operator's business, and listing them would bury the ones above that are.

Per-run arguments are flags, not variables: a backfill's worker, version and
range are `uqs job backfill` options, and the live smoke check's sources are
`uv run pytest tests/qrun/test_external_smoke.py -m external --smoke-targets … --smoke-tables … --smoke-timeout-ms …`.

## Produced by the orchestrator

`uqs.stack.env.build_env` computes these from `UqsPaths` and hands them to
`torq.sh`; `process.csv`'s `${VAR}` and `{VAR}+N` placeholders resolve against
the same dict. Setting one in your shell does not override anything ---
`build_env` wins --- but it will make `uqs list` and the running stack disagree
about where data lives, which is a confusing way to spend an afternoon.

`TORQHOME` and `TORQAPPHOME` are the exception. `UqsPaths` itself reads them,
along with `UQS_DATA_ROOT`, so they move the trees everything else is built from
(see the table above).

<!-- panache-ignore-format-start -->
<!-- BEGIN GENERATED environment-produced: `uv run python scripts/generate/derive.py environment-tables` from docs/reference/environment/produced.csv - edit that file, not this table -->

| Variable | Value |
| --- | --- |
| `TORQHOME` | the vendored TorQ tree |
| `TORQAPPHOME` | the starter-pack app tree |
| `TORQDATA` | data root; `UQF_STATUS_DIR` falls back inside it |
| `TORQPROCESSES` | the generated `process.csv` |
| `UQF_DEPLOYMENT_FILE` | `$TORQDATA/deployment.txt`: the processes TorQ discovers after a profile start (#1207) |
| `UQF_SITE_AUTH` | `1` for an attached publisher started under `KDBSECURE` (#1847): `scripts/torqcode/handlers/loadpassword.q` and `pmusers.q` then leave the site's credential loader and `.pm` users as they are; otherwise unset |
| `UQF_SCRIPTS` | `scripts/` |
| `KDBCONFIG` | `$TORQHOME/config` |
| `KDBCODE` | `$TORQHOME/code` |
| `KDBAPPCONFIG` | `$TORQAPPHOME/appconfig` |
| `KDBAPPCODE` | `$TORQAPPHOME/code` |
| `KDBSERVCONFIG` | `scripts/torqconfig`: names TorQ's log levels as `.qetl.log` does. Not set in the `torq` runtime |
| `KDBSERVCODE` | `scripts/torqcode`: TorQ's password loader, fixed for that layer, and the deployment-set filter; not set in the `torq` runtime |
| `KDBLIB` | `$TORQHOME/lib` |
| `KDBTESTS` | `$TORQHOME/tests` |
| `KDBLOG` | `$TORQDATA/logs` |
| `KDBHDB` | `$TORQDATA/hdb` |
| `KDBWDB` | `$TORQDATA/wdbhdb` |
| `KDBTPLOG` | `$TORQDATA/tplogs` |
| `KDBDQCDB` | the DQC database |
| `KDBDQEDB` | the DQE database |
| `KDBBASEPORT` | the port block base |
| `RLWRAP` | `rlwrap` |
| `QCON` | `qcon` |
| `QCMD` | `$QCMD`, else `q` --- see above |

<!-- END GENERATED environment-produced -->
<!-- panache-ignore-format-end -->

`RLWRAP` and `QCON` are set so that a differently named or located binary is a
variable to change rather than a patch to `torq.sh`.

## Where to put them

What a source connects to can live in the gitignored application-layer
`sources.csv` ([where a source
connects](pipeline-declarations.md#where-a-source-connects-sourcescsv)), but a
secret never does: it reaches the process through its environment, and a
`sources.csv` row only names the variable. A gitignored `.envrc` is the tidiest
way to do that: direnv exports it in your shell, and `uqs` exports it into every
process it starts even from a shell without direnv --- through direnv in both
cases, so only after `direnv allow`.

Two reasons to prefer it over typing the `export`:

- It **unloads when you leave the directory**. A stale credential cannot outlive
  the shell that set it. Re-running an older `export` line and having it
  silently win, while the corrected value sits unused in a file, is a real way
  to lose an afternoon to a failure that looks like a bug in the source.

- `.envrc` is **executed**, so a path can be derived rather than pinned.
  `CRYPTORUST_ROOT` is enough to build the whole `crypto_market_data`
  credential, including the `file_search_path=` that `data/live.duckdb`'s
  relative `read_parquet` view needs to resolve from this repository's working
  directory:

  ```bash
  export CRYPTORUST_ROOT="${CRYPTORUST_ROOT:-$(cd "$PWD/../cryptorust" && pwd)}"
  export UQF_SOURCE_CRED_CRYPTO_MARKET_DATA="DRIVER=DuckDB;Database=$CRYPTORUST_ROOT/data/live.duckdb;access_mode=READ_ONLY;file_search_path=$CRYPTORUST_ROOT"
  ```

  That credential is for a **stopped** recorder: a running one holds
  `live.duckdb`'s write lock and refuses every reader, read-only included. To
  backfill while it runs, read its parquet shards instead --- they are written
  as hidden `.live*.tmp.*` files and renamed into place when complete, so
  `live*.parquet` never matches a torn one (#1079):

  ```bash
  export UQF_CRYPTO_CAPTURE_GLOB="$CRYPTORUST_ROOT/data/live*.parquet"
  export UQF_SOURCE_CRED_CRYPTO_MARKET_DATA="DRIVER=DuckDB;Database=:memory:"
  ```

`.env` also reaches every process `uqs` starts, but it is inert data rather than
shell, so nothing in it can be derived, and outside `uqs` only `.qdata.cfg`
reads it (for `DATABENTO_DATA_DIR`). See [`.env.example`](../../.env.example).

## Connector configuration

The external connectors (`uqs connector start NAME`) share one tickerplant
connection and keep everything else their own (`uqs.external.connection`,
#1786). Every setting an option declares a variable for resolves in one order:

1. **Given explicitly** --- the option to `uqs connector start` (`--brokers`,
   `--topic`), or the argument a Python caller passes. A secret is never given
   inline (#1853): `--tp-credential-env NAME` and `--api-key-env NAME` name the
   variable holding it, so it reaches neither `ps`, shell history, nor the
   connector's pid file. The inline `--tp-credential` still parses for one
   release, hidden, and warns.
2. **The environment** --- `UQF_TP_CREDENTIAL`, `UQF_KAFKA_BROKERS`,
   `UQF_KAFKA_TOPICS`, `DATABENTO_API_KEY`. On a deployed server, put a secret
   in `shared/config/secrets.env`, which `deploy.env` sources for every process.
3. **The declared default** --- for the credential, the starter pack's sample
   `feed` login, labelled DEMO wherever it appears. It works against an
   unchanged stack only because the vendored sample access list accepts it; no
   connector reads a vendored password file, and a start that falls back to it
   logs that it did.

A dry run (`--dry-run`) lists each setting that is not a default with where it
came from, and prints a credential or key as `***`; a refusal names the option
or variable, never the value. What is shared and what is each connector's own:

  | Setting                                         | Kind                           | Where it is set                                                                                       |
  | ---                                             | ---                            | ---                                                                                                   |
  | stp1's host                                     | shared                         | `TP_HOST`, `uqs.external.connection`                                                                  |
  | stp1's port                                     | shared                         | stp1's `process.csv` row (`connection.tickerplant_port`; crypto takes `--base-port`)                  |
  | stp1's credential                               | shared                         | `--tp-credential-env NAME` > `UQF_TP_CREDENTIAL` > the demo login                                     |
  | Kafka brokers                                   | shared by both Kafka consumers | `--brokers` > `UQF_KAFKA_BROKERS` > `localhost:9092` (`kafka_feed1`: the variable, required)          |
  | Kafka topics                                    | q/Python binding               | `--topic` > `UQF_KAFKA_TOPICS` > every topic a decoder declares; an undeclared one is refused by both |
  | Kafka consumer group                            | each consumer's own            | `uqf-kafka-flow` (Python, `--group`); `uqf-kafka-q` (`kafka_feed1`, `UQF_KAFKA_GROUP`)                |
  | the raw table published                         | q/Python binding               | the connector's `produces`; refused before a start unless `plant_tables.q` defines it                 |
  | venues, symbols, dataset, intervals, OMS socket | each connector's own           | that connector's options                                                                              |

## PeachQ

`QCMD` naming its binary is the way to run on it, and the only one: nothing
declares the implementation beside the binary. `uqs` and the pytest conftest ask
the binary which q it is (`uqs.interpreter.q_impl`), once per binary per
process, so a green run says which interpreter made it. Nothing falls back to
PeachQ: a `q` on `PATH` is PeachQ only when someone put a PeachQ there.

What runs on PeachQ whatever `QCMD` is (the tests that take the `peachq` fixture -
the whole q suite and the docs' q blocks under `-m peachq` among them) finds it
one way, `uqs.interpreter.peachq_binary`:

1. `--peachq PATH`, given to pytest. It must be PeachQ: a missing or KDB-X
   binary fails, with no fallback. This is how KDB-X work runs those tests on a
   PeachQ build of its own.
2. `QCMD`, when it is PeachQ.
3. The pinned build, otherwise.

You need not install PeachQ for that. The pinned build is the commit in
[`scripts/peachq.json`](../../scripts/peachq.json), fetched on first use,
checked to be that commit, built and cached. That needs `git`, `make`, a C
compiler and the network once; later runs reuse the cache offline.

- **Cache:** `${XDG_CACHE_HOME:-~/.cache}/uqf/peachq/`, one entry per commit,
  platform and build options. Delete the directory to force a rebuild.
- **Platforms:** Linux x86-64 and macOS arm64. Anywhere else the lane fails,
  naming the platform.
- **Another commit:** `UQF_PEACHQ_COMMIT=<40-hex>` builds and caches that commit
  instead of the pin, logged on every run and recorded in its cache entry's
  `manifest.json`. The platform must still be supported.
- **Not the pin:** a PeachQ in `QCMD` or `--peachq` that is not the pinned build
  is used, and named in one warning giving the binary and the pin. The pinned
  build is known by the manifest beside it: the cache entry's `manifest.json`,
  or a deployed q runtime's `RUNTIME_MANIFEST.json`.
- **Directly:** `python3 scripts/peachq.py` prints the pinned binary's path,
  building it if needed.

PeachQ is never put on `PATH`; only the lanes that run on it are given its path.

The variables that chose an interpreter before #1753 are refused, by `uqs`,
pytest and deploy's preflight, each named with what replaces it
(`QCMD=<binary>`, or `--peachq PATH`). A `deploy.env` written before then sets
them, so such a release needs a redeploy; see the
[changelog](../../CHANGELOG.md).

It has no connection licence, so the community edition's 16-connection cap does
not apply: on PeachQ a start is held to no connection budget, `uqs profile list`
shows `no cap`, and monitor1 watches every process. `UQS_LICENCE_CONNECTIONS`,
if set, still wins.

### PeachQ and file compression

What HDB compression (#2146-#2152) needs from q, measured on the pinned build
and KDB-X 4.x on macOS arm64 (#2153). `tests/q/test_compression_primitives.q`
pins each row. Its PeachQ failures are in `tests/q/peachq_known_gaps.txt`, so
compression tests run on KDB-X only.

  | Primitive                                                  | KDB-X                                                    | Pinned PeachQ                                                      |
  | ---------------------------------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------ |
  | `-19!` compress a file                                     | algorithms 0-5; 3-5 need libsnappy, liblz4 or libzstd    | `'nyi` for every algorithm, block size and level                   |
  | `(file;blocksize;algo;level) set v`                        | algorithms 0-5                                           | algorithms 0 and 2 (gzip) only. 1, 3, 4 and 5 throw `'nyi`         |
  | `.z.zd:(blocksize;algo;level)` as the default for `set`    | yes, for a vector, a splayed table and a partition       | yes, for gzip only                                                 |
  | `.z.zd` as a column dictionary                             | yes                                                      | ignored: every column is written plain                             |
  | malformed `.z.zd`                                          | `set` is refused                                         | ignored: the file is written plain                                 |
  | `-21!` inspect a file                                      | yes; an empty dictionary for a plain file                | yes, for its own files                                             |
  | read a compressed column with `get` or a splayed table     | yes; it is mapped (`.Q.w[]` counts it as `mmap`)         | yes, but read onto the heap, not mapped                            |
  | `select` on a compressed partitioned HDB                   | yes                                                      | no: `\l` of any partitioned database defines no table              |
  | `s#` on a compressed column                                | kept                                                     | kept                                                               |
  | `p#` on a compressed column                                | kept                                                     | dropped, as on a plain one                                         |
  | a bad algorithm, level or block size                       | refused, naming the value and the file                   | bare `'nyi` (algorithm) or `'domain` (level, block size)           |
  | read the other's compressed file                           | one block: yes. More: `'bad logicalBlockSize`            | one block: yes. More: `'corrupt`                                   |

Two facts hold on both. The smallest block size is the page size: 2^12 on Linux
x86-64, but 2^14 on macOS arm64, where 12 and 13 are refused. And the
enumeration domain that `.Q.en` writes stays plain under `.z.zd`.

## Prerequisites, not configuration

`QHOME` must point at a real q installation (`~/.kx` for KDB-X, the preferred
and only verified interpreter --- see the [README](../../README.md#requirements)
for what else may work). `$QCMD`, or `q` on `PATH` when it is unset, must be
that interpreter: nothing else is tried. To run PeachQ instead, point `QCMD` at
its binary. `HOME` is read only to locate `~/.kx`.

## How this page is kept honest

`scripts/gates/check.py env-reference` runs in CI and fails the build when the
code and this page disagree, in either direction:

- a variable read by `src/`, `scripts/`, a package's `src/`, `web/` or the smoke
  script and **not** listed here;
- a variable listed here that nothing reads any more, which is how a reference
  page ends up describing a previous version of the system.

Pattern rows (`UQF_SOURCE_CRED_<SOURCE>`) are exempt from the second direction
only --- a mechanically built name has no literal to find.
