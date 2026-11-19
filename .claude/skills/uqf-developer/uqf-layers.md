# The repository map - layers, directories, docs

Read this to orient: before changing code in an area you have not worked in,
before adding a module, and whenever the question is "where does X live" or
"which doc answers this". `uqf-etl-framework.md` has the ETL core file by file
and `uqf-q-rules.md` who owns which authority; this page is the whole-tree map
around them.

## 1. Four layers, one arrow

```
src/                     plain q; never loads TorQ
  foundation pricing portfolio execution market_data examples metadata   the quant library (.qf.*)
  etl/core                                                              the pipeline framework (.qetl.*)
  etl/{sources,transforms,workers,streaming,reactions}                  the declarations (.qpipe.*)
scripts/processes/       .qtorq and the runners - the ONLY q that knows TorQ
python/ web/             uqs CLI + MCP, the frontend, the Airflow provider, the browser app
lib/                     vendored upstream trees, never edited
```

The arrow points one way: `src/` never reaches into `scripts/`, and
`src/etl/core/` never names a declaration (`check.py etl-layering`). The core
reads three TorQ facilities when present, each from one owner file with a
plain-q fallback: `.lg` in `src/etl/core/log.q`, `.servers` in
`src/etl/core/worker_dependencies.q`, `.proc` in `src/etl/core/run.q`
(`scripts/gates/policy/etl_layering.py` `TORQ_OWNERS`). Outbound HTTP is
`src/etl/core/webhook.q` only.

`docs/README.md` opens with the one-page diagram
(`docs/diagrams/repo-overview.svg`) and README's "Components" section lists the
six components: data engineering, the quant library, data processing, the fleet
tooling, scheduling and access, database metadata. The repository is several
components, not one library - frame it that way in anything you write.

## 2. The quant library - `src/` (seven `.qf` domains)

Pure functions only: no state, no I/O, no logging. Names are defined fully
qualified from the root (`.qf.px.fwd_simple:{...}`), never inside `\d .qf.px`; a
function calls a sibling by its full name. Directories are organisational: a
namespace does not follow its path.

  | Domain     | Files (load order in `src/init.q`)                                                                                                                                                                            |
  | ---        | ---                                                                                                                                                                                                           |
  | `.qf.util` | `foundation/schema.q`, `foundation/render.q`, `metadata/metatables.q`, `namespaces.q`                                                                                                                         |
  | `.qf.math` | `foundation/stats.q` (`horner_eval`: the one place polynomials are evaluated)                                                                                                                                 |
  | `.qf.px`   | `foundation/ccy.q`, `daycount.q`, `calendar.q`, `rates.q`; `pricing/forwards.q`, `options.q`                                                                                                                  |
  | `.qf.mkt`  | `market_data/book.q`, `pricing/cross.q`, `pricing/attribution_routes.q`, `market_data/asof.q`, `microstructure.q`, `microstructure_shape.q`, `microstructure_tape.q`, `microstructure_venues.q`, `dqchecks.q` |
  | `.qf.port` | `portfolio/risk.q`, `positions.q`, `desk_positions.q`, `limits.q`                                                                                                                                             |
  | `.qf.exec` | `portfolio/allocation.q`, `execution/execution.q`, `execution/execution_attribution.q`                                                                                                                        |
  | `.qf.demo` | `examples/example_defaults.q` (constants only), plus `src/etl/synthetic_market.q` when the ETL loads                                                                                                          |

- `src/init.q` loads, in order: schema, render, stats, ccy, daycount, calendar,
  rates, book, cross, attribution_routes, forwards, options, risk, positions,
  allocation, desk_positions, limits, asof, execution, execution_attribution,
  microstructure (+ shape, tape, venues), dqchecks, example_defaults,
  metatables, namespaces (`src/init.q:57-85`).
- `src/namespaces.q`: `.qf.util.owned[]` / `.qf.util.functional[]` are the one
  way to enumerate this tree's namespaces; a root-only `key` scan misses
  everything nested under `.qf`, `.qetl` and `.qpipe`.
- A name is defined by ONE file of its domain; the module graph is the reviewed
  edge list `ALLOWED` in `scripts/gates/policy/module_deps.py` (edges are
  between files, from `qlinter analyze graph`); `check.py module-deps` fails a
  new edge, a cycle, a foundation file depending outside foundation, or a second
  definition.
- `src/integrations/data.q` (`.qdata`, camelCase on purpose) is outside the
  library and not loaded by `src/init.q`.
- `docs/reference/qf-domains.md` (the domains, the `px`/`mkt` boundary, the
  retired `.q<abbrev>` names) and `docs/reference/quant-modules.md`.
- The library is strictly eFX: FX forwards/swaps, Garman-Kohlhagen options, FX
  position risk, execution quality, LOB microstructure, data-quality checks. The
  `uqf-quant-developer` agent owns it; `kdb-q-conventions` holds its style
  (precedence, sign conventions, `pip_factor` always caller-supplied).

Live counts (KDB-X, `\l src/init.q`): lambdas per domain px 67, mkt 126, exec
60, port 30, math 4, util 22, demo 0.

## 3. `src/etl/`

  | Path                                                              | Holds                                                                   |
  | ---                                                               | ---                                                                     |
  | `core/`                                                           | the framework shells and contracts (`.qetl.*`) - `uqf-etl-framework.md` |
  | `plant_registry.q`, `plant_tables.q`                              | the `.qetl.plant` API, and every table the plant carries                |
  | `sources/`, `transforms/`, `workers/`, `streaming/`, `reactions/` | one declaration per file                                                |
  | `generated/`                                                      | `load_plan.q`, `pipeline_dag.q`, `plant_schema.json` - never edit       |
  | `compression_policy.q`, `synthetic_market.q`                      | the declared compression policy; demo market data (`.qf.demo`)          |
  | `init.q`                                                          | the loader                                                              |

## 4. `scripts/`

  | Dir                        | Holds                                                                                                                                                                                                                                        |
  | ---                        | ---                                                                                                                                                                                                                                          |
  | `processes/`               | `.qtorq` (`torq_pipeline.q`), the runners (`torq_stream.q`, `torq_backfill.q`, `torq_tap.q`), compression and metatables adapters, `uqs_catalog.q` (`.qcat`), the `uqs_*_config.q` loaders, `uqs_row_ids.q`, `process_ports.csv` (generated) |
  | `jobs/`                    | TorQ-free runners (`run_stream.q`, `run_backfill.q`) and the attached-stack backfill scripts (`attached_*.q`)                                                                                                                                |
  | `torqconfig/`, `torqcode/` | the KDBSERVCONFIG / KDBSERVCODE overlays (`uqf-on-torq.md` section 3)                                                                                                                                                                        |
  | `generate/`                | `derive.py` and its generators and q exporters (`export_*.q`); `q_api_docs.py`                                                                                                                                                               |
  | `gates/`                   | `check.py` and `policy/*.py`; `check_doc_links.py`, `qlint_graph.py`, `stack_smoke.py`; their tests                                                                                                                                          |
  | `dev/`                     | developer tools: `kdbx_attestation.py`, `ship.sh`, `bundle_suite.py`, `revendor.py`, the hook selectors, coverage, `odbc_rosetta.sh` (`uqf-testing-and-gates.md` section 10)                                                                 |
  | `peachq/`                  | `resolve.py`, `pin.json`, `full_suite.py`, `pre_push.py`                                                                                                                                                                                     |
  | `portable/`                | `flatten_contexts.py` (kdb+ 4.0 releases)                                                                                                                                                                                                    |
  | `hdb/`                     | HDB maintenance q behind `uqs data`: `alter.q`, `fill.q`, `layout.q`, `lock.q`, `seed.q`, `sort_lib.q`, `sort_order.q`, `stage_migration.q`, `column_types.q`, `compression_report.q`                                                        |
  | `deploy/`                  | `install_release.py`, `smoke.q` (what `uqs deploy push` runs)                                                                                                                                                                                |
  | `examples/`                | worked examples `*_example.q` (run by `tests/qrun/test_worked_examples.py`), `lib/example_books.q`                                                                                                                                           |
  | `bench/`                   | batching and compression benchmarks                                                                                                                                                                                                          |

## 5. `python/`, `web/`, `sidecars/`, `lib/`, `tests/`

- `python/uqs` - the CLI and MCP server (`uqf-cli.md`).
- `python/uqf_frontend` - the backend-for-frontend; `web/` - the browser app;
  `python/uqf_airflow_provider` - Airflow sensors over `.qetl.status` files. The
  workspace members are in the root `pyproject.toml`; run Python through
  `uv run` from the repository root.
- `sidecars/` - optional bundles (`uqf-on-torq.md` section 10).
- `lib/` - vendored trees, `lib/VENDORED.toml` (`uqf-on-torq.md` section 1).
- `tests/` - `run_tests.q`, `lib/` (qUnit, support, discovery, doubles), `q/`
  (every suite), `qrun/` (pytest drivers), `remote/` (Docker)
  (`uqf-testing-and-gates.md`).
- Repo root: `install.sh` (`--dev`, `--web`, `--odbc`, `--check`),
  `conftest.py`, `pyproject.toml`, `.pre-commit-config.yaml`, `.qlinter.toml`,
  `.qlinter-version`, `.panache.toml`, `.gitattributes`, `CHANGELOG.md` (daily
  `stable/YYYY-MM-DD` snapshots from the `snapshot` skill, never rewritten),
  `LICENSING.md`, `CONTRIBUTING.md` (only `kwojdalski`'s PRs merge).
- `output/` - runtime data (gitignored); a test under `python/` that writes
  there fails (`scripts/dev/checkout_guard.py`).

## 6. The docs map - `docs/README.md`

Five directories, one question each; a page goes where its **purpose** puts it:

  | Directory            | Answers                       | Key pages                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
  | ---                  | ---                           | ---                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
  | `docs/guides/`       | how do I do this?             | `uqs.md`, `runtimes-and-profiles.md`, `logs.md`, `connecting.md`, `hdb.md`, `when-it-breaks.md`, `new-pipeline.md`, `config-audit.md`, `metatables.md`, `deploy.md`, `odbc.md`, `attach-existing-torq.md`, `sidecar-bundles.md`, `real-time-subscribers.md`, `cryptorust-kafka-books.md`, `kafka-crypto-example.md`                                                                                                                                                                                                                                  |
  | `docs/scaffolding/`  | how do I create one of these? | one per `uqs job new` shape: `feed.md`, `etl.md`, `normalizer.md`, `horizon.md`, `backfill.md`, `reaction.md`, `external.md`, `hdb-transfer.md`                                                                                                                                                                                                                                                                                                                                                                                                      |
  | `docs/services/`     | what does each process do?    | one card per process: `synthetic-feeds.md`, `superbook.md`, `cross-arbitrage.md`, `fx-positions.md`, `markouts.md`, `fx-markout.md`, `databento.md`, `kafka.md`, `crypto-recorder.md`, `tap.md`                                                                                                                                                                                                                                                                                                                                                      |
  | `docs/architecture/` | why is it shaped this way?    | `pipeline-philosophy.md` (the ten positions: a claim is true or absent; the weakest true guarantee; nothing exists that does nothing; derive, do not restate; refuse at the boundary; required for choices, ambient for facts; history accumulates; authority is split; declarations live apart, arrive together; two frameworks, one adapter), `stack.md`, `query-policies.md`, `event-tape.md`, `markout-contract.md`, `markout-memory.md`, `bundle-overlay.md`, `zero-downtime.md`, `cryptorust-discovery.md`, `pipeline-architecture-example.md` |
  | `docs/reference/`    | what is the contract?         | `cli.md`, `pipeline-declarations.md` (gated), `environment.md` (generated tables, gated), `processes.md` (generated), `qf-domains.md`, `quant-modules.md`, `book-liquidity.md`, `compression-policy.md`, `surfaces/current/` (generated), `q-api/` (rendered, gitignored)                                                                                                                                                                                                                                                                            |

Also: `docs/faq.md` (short answers, starting with how uqf differs from plain
TorQ), `docs/man.q` (the function registry - reads the qDoc blocks at load, not
generated), `docs/diagrams/` (`.d2` sources and rendered `.svg`; edit the `.d2`
and run `derive.py diagrams`), `docs/presentation/uqf.qmd`. The site is Quarto
(`docs/_quarto.yml`; pre-render runs `scripts/generate/q_api_docs.py`), and its
render doubles as the link gate in CI.

Documentation rules for an agent:

- A NEW page: the `docs-writer` skill. An EXISTING page: the `docs-maintainer`
  agent, which carries `.claude/docs-preferences.md` (style and scope
  preferences; never facts).
- Every `.qetl.*`/`.qf.*` name in prose must exist (`check.py doc-references`
  covers `docs/` and `.claude/`); a q block marked
  `<!-- q-example: run|transcript -->` is executed (`q-docs` hook); every `@eg`
  in a qDoc block runs (`tests/q/run_examples.q`, `.qt.man.examples`).
- Never edit a generated page (`processes.md`, `surfaces/current/`, the
  environment tables, the SVGs); never edit `CHANGELOG.md` history or the
  vendored trees' docs.
- No meta-commentary about the docs process; say how to do a thing without
  copying `--help`; run every command before writing it down.
