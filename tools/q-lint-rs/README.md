# qlinter — native q linter

Rust implementation of the standalone [q-lint](../q-lint/README.md), including
both built-in checks and a native stdio/LSP adapter for the optional qls server.
The built-in backend requires neither Python, q, VS Code, nor an LSP server.
It never executes input code.

Install and run through the uv workspace from the repository root:

```sh
uv sync
uv run qlinter src/
uv run qlinter --rules
```

The root `pyproject.toml` includes this package as a workspace dependency.
The distribution name remains `q-lint-rs`; the installed command is `qlinter`.
[Maturin's binary packaging](https://www.maturin.rs/bindings.html#bin) installs
an actual release-mode executable into the environment, without a Python launcher.
Local source installs require Rust/Cargo. Changes to Rust sources, embedded rule
data or Cargo manifests invalidate uv's cached build.

You can also build directly with Cargo:

```sh
cargo build --release --manifest-path tools/q-lint-rs/Cargo.toml
tools/q-lint-rs/target/release/qlinter src/
tools/q-lint-rs/target/release/qlinter src/ --format json
tools/q-lint-rs/target/release/qlinter --rules
tools/q-lint-rs/target/release/qlinter --explain QF005
```

To install a `qlinter` command into Cargo's bin directory:

```sh
cargo install --path tools/q-lint-rs --locked
qlinter src/
```

The executable is independently deployable once built. Compilation uses the
checked-in Cargo lockfile; this version was tested with Rust 1.98.1 on macOS ARM64.

The CLI supports the same `--profile general|uqf`, `--format text|json`,
`--exclude`, `--config`, stdin (`-`, `--stdin-filename`) and
`--backend builtin|qls|all` options. The default is builtin.
`--qls-executable` chooses the separately installed server;
`--qls-timeout` bounds a batch, plus at most one second for cleanup.
The qls server may itself require Python; the Rust builtin backend does not.

The same `[tool.q-lint]` exclusion configuration in `pyproject.toml` applies:

```toml
[tool.q-lint]
exclude = ["torq/*", "generated/"]
```

This repository excludes `lib/torq/` and `.claude/worktrees/`. Exclusions apply before reading a file
or sending it to qls. Diagnostics include the same [codes and categories](../q-lint/RULES.md).
Exit codes are 0 (no error/warning), 1 (findings), and 2 (input/server failure).
Messages may differ between implementations; rule codes, categories, severities
and locations are compared by the parity harness. Both retain the existing
heuristic limitations; a successful lint is not a correctness proof.
Column-zero `p)`/`k)` blocks and their indented continuations are opaque to
built-in q checks; Python/K syntax is not validated. Normal q analysis resumes
on the next nonblank, unindented q line, and `q)` bodies are checked normally.
The Python-only repository hook's embedded-q-in-Python check is outside the
standalone q analyzers' scope.

## Editor support: the language server

`qlinter --lsp` speaks the Language Server Protocol on stdin/stdout, so any
editor that speaks LSP gets diagnostics with no editor-specific code beyond
"launch this binary".

```sh
qlinter --lsp --profile uqf
```

It implements `initialize`/`initialized`, `didOpen`/`didChange`/`didSave`/
`didClose`, `shutdown`/`exit`, and pushes `textDocument/publishDiagnostics`.
That is the set an editor needs to show squiggles.

**The reason it is a server rather than an editor plugin shelling out to the
CLI**: `didChange` carries the buffer, so what gets linted is what is on
screen, including a file that has never been saved. A CLI over paths cannot do
that, and `--stdin-filename` only gets you there one editor at a time.

Deliberately not implemented: completion, hover, go-to-definition, formatting.
Those need a resolver and a symbol table this crate does not have — it reads
source without executing it, which is what makes it safe to run on every
keystroke. Announcing a capability and answering emptily is worse than not
announcing it, because an editor told that a server provides completion stops
offering its own word-based fallback.

### VS Code

The extension in `editors/vscode` is about thirty lines: it launches the
server and lets the protocol do the rest.

```sh
cargo build --release                      # produces target/release/qlinter
cd editors/vscode && npm install && npm run compile
```

Then either press <kbd>F5</kbd> in that directory to open an Extension
Development Host, or package and install it:

```sh
npx vsce package                           # produces q-lint-0.1.0.vsix
code --install-extension q-lint-0.1.0.vsix
```

Two settings: `q-lint.serverPath` (default `qlinter`, looked up on `PATH` —
point it at `target/release/qlinter` if you have not installed it) and
`q-lint.profile` (`general` or `uqf`).

### Other editors

Neovim, with `nvim-lspconfig`:

```lua
vim.filetype.add({ extension = { q = "q" } })
require("lspconfig.configs").qlint = {
  default_config = {
    cmd = { "qlinter", "--lsp", "--profile", "uqf" },
    filetypes = { "q" },
    root_dir = require("lspconfig.util").root_pattern("pyproject.toml", ".git"),
  },
}
require("lspconfig").qlint.setup({})
```

Helix, in `languages.toml`:

```toml
[language-server.qlint]
command = "qlinter"
args = ["--lsp", "--profile", "uqf"]

[[language]]
name = "q"
file-types = ["q"]
language-servers = ["qlint"]
```

### What the server does not read

`--config` and `--exclude` are not consulted in server mode. An editor asking
for diagnostics on a file it has open has already decided the file is
interesting, and honouring an exclude list there would show a file with no
findings and no explanation for their absence. Exclusions remain a
batch-linting concern, where the question is which files to visit.

## Shipping prebuilt binaries

Build a wheel, standalone executable, archive and SHA-256 checksums for the
current platform:

```sh
uv run python tools/q-lint-rs/scripts/build_release.py
```

Artifacts go to `dist/q-lint-rs/` (ignored by Git). The wheel contains the compiled
executable; the standalone archive contains that same executable. Neither needs
Rust/Cargo to run. Installing the wheel needs a Python package installer;
running the standalone binary needs neither Python nor an installer. Optional
`--backend qls` still requires a separately installed qls server.

For example, install a wheel with `uv tool install /path/to/q_lint_rs-....whl`
or `uv pip install /path/to/q_lint_rs-....whl`, then run `qlinter src/`.
Alternatively, unpack the standalone archive and run `./qlinter src/`.

The verified artifacts in this checkout target **macOS 11+ on Apple Silicon**.
Build on each target OS/architecture to distribute its matching binary; this
command does not cross-compile. It builds artifacts locally and does not publish
them to PyPI or a release service.

## Validation and profiling

```sh
cargo test --manifest-path tools/q-lint-rs/Cargo.toml
cargo clippy --all-targets --manifest-path tools/q-lint-rs/Cargo.toml -- -D warnings
uv run python tools/q-lint-rs/tests/parity.py
Q_LINT_TEST_QLS=qls uv run pytest tools/q-lint-rs/tests
cargo build --release --example profile --manifest-path tools/q-lint-rs/Cargo.toml
uv run python tools/q-lint-rs/tests/benchmark.py --runs 7 --qls --output /tmp/q-lint-benchmark.json
```

Build the release CLI before running the Python harnesses. The parity harness
checks both profiles against mutation pairs, boundary snippets, reserved names,
and actual source/test files. Native tests include deterministic arbitrary-text
inputs to catch parser crashes. Fake LSP subprocesses exercise diagnostics,
configuration requests, malformed messages, crashes, timeouts and hung shutdown.

The benchmark alternates fresh Python/Rust processes after one warm-up each,
checks matching diagnostics, records all samples and source hashes, and measures
the preloaded analysis APIs separately. It also saves a Python cProfile report.
Compilation, `uv` startup and `cargo run` overhead are excluded. File caches are
warm; results are specific to the machine and corpus. See [measured timings](BENCHMARK.md).

`src/taxonomy.json` and `src/reserved.json` mirror the Python catalogue and name
set. The parity checks enforce the catalogue and exercise every reserved name;
update both implementations together when adding a rule.
