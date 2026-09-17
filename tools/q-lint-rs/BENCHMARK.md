# Python and Rust timing comparison

Measured on 2026-09-17 on the local machine. These are wall-clock timings,
not estimates. The Rust CLI was built in release mode.

Environment: `macOS-26.5.1-arm64-arm-64bit-Mach-O`, Python 3.14.5, `rustc 1.98.1 (48a229cea 2026-09-01) (Homebrew)`.

Seven measured fresh-process runs per command, after one untimed warm-up.
Python and Rust runs alternated; both read the same files and emitted JSON.
Filesystem caches were warm. Compilation, `uv run` and `cargo run` overhead are
excluded. No other benchmark or compilation was run by this session concurrently.
Other machine activity was not controlled; ranges show observed variability.
Every timed pair was checked for matching diagnostic identities and locations.

| Builtin workload | Files | Source bytes | Python median (range), ms | Rust median (range), ms | Median speedup |
|---|---:|---:|---:|---:|---:|
| single | 1 | 3,183 | 79.95 (66.97–110.37) | 5.77 (5.37–7.67) | 13.85× |
| quant | 12 | 165,436 | 293.26 (269.55–578.11) | 11.15 (9.89–29.72) | 26.30× |
| source-and-tests | 90 | 960,990 | 1168.52 (1120.14–1225.03) | 30.07 (27.96–33.23) | 38.86× |

`single` is `src/foundation/stats.q`. `quant` contains the 12 modules under
foundation, pricing, portfolio, execution and market_data. `source-and-tests`
contains `src/` and `tests/q/`; it excludes vendored libraries.

Separately, the pure analysis APIs were timed for ten iterations after loading
source text once and warming the implementation. These timings exclude process
startup, imports, file reads, configuration discovery and output serialization:

| Workload | Python median, ms | Rust median, ms |
|---|---:|---:|
| single | 3.745 | 0.073 |
| quant | 236.822 | 3.531 |
| source-and-tests | 1215.405 | 18.516 |

With the external qls backend on the single file, Python took **287.44 ms**
median (191.47–533.81), Rust **199.50 ms**
(141.22–291.71). This includes a fresh installed qls process
and bounded shutdown. Moving the adapter to Rust cannot remove the server's cost.
These results use installed q-lang-server 1.0.1, not the separate 1.0.5 comparison.

Profiling exposed an unrelated adapter delay: Python previously waited for qls
to terminate before closing stdin. This server waits for EOF, so successful
batches spent about a second in the shutdown grace period. The adapter now closes
stdin after the exit notification and before waiting. A fake-server regression
checks that EOF shutdown does not hit that timeout. All numbers above use the fix.

The Python cProfile report identifies repeated `source_views` and `_strip_comments`
scanning as the main hotspot. The API already creates lexical views, but the
legacy rule functions repeat masking on their inputs. Python therefore has room
for algorithmic improvement too; this is a comparison of these implementations,
not an inherent language speed ratio. cProfile timings include instrumentation
and are not used for the speedup table. Rust analysis uses cached regular
expressions and reuses its lexical views across rules.

Validation: 401 inputs match rule identities, severity and positions in both
profiles; all 39 runtime-confirmed mutations are caught with clean baselines;
256 Python/CLI tests and three native tests pass (including 2,000 deterministic
arbitrary-text inputs); the full q suite passes 1,070 tests. Text descriptions
may differ. This finite corpus does not establish equivalence for every q program.

The new scope check also found real outer-local references in nine error-test
wrappers and a metadata error handler. Those sites now pass values explicitly;
a new runtime regression checks the handler's complete error message.

Reproduce:

```sh
cargo build --release --bins --examples --manifest-path tools/q-lint-rs/Cargo.toml
uv run python tools/q-lint-rs/tests/benchmark.py --runs 7 --qls --output /tmp/q-lint-benchmark.json
```

See [raw samples and source hashes](benchmark-results.json),
[the Python profile](python-profile.txt), and [the benchmark script](tests/benchmark.py).
