# Notebooks: uqf in three sittings

Three q notebooks that explain this framework by running it. Each is prose, code
and charts in one file, takes about fifteen minutes, and needs nothing but one
scratch q process - no stack, no tickerplant, no database.

  | Notebook                                                           | What it shows                                                                                                                                                                                                                   |
  | ---                                                                | ---                                                                                                                                                                                                                             |
  | [`01_quant_library.qnb`](01_quant_library.qnb)                     | The seven `.qf` namespaces, by following one pair through a desk's day: conventions and dates, a forward from interest parity, an option and its greeks, a position, its exposure and its VaR.                                  |
  | [`02_books_and_execution.qnb`](02_books_and_execution.qnb)         | The observed market: the one quotes shape the library reads, what a size costs to sweep, a cross built from pairs that are quoted, the as-of rule, markouts and their sign convention, and data-quality checks.                 |
  | [`03_pipeline_in_one_process.qnb`](03_pipeline_in_one_process.qnb) | The pipeline framework with no stack: a job as a declaration, windows and the coverage ledger, a re-run that does nothing, a window that fails its check and is claimed nowhere, the repair, the streaming twin, the job graph. |

Read them in order. The first two are the library; the third is why the
framework around it is shaped as it is, and its idea - *a claim is true, or
absent* - first appears in the second.

## Opening one

They are `.qnb` files, the notebook format of the
[q-lint](https://github.com/kwojdalski/q-lint) VS Code extension. They need
version 0.28.0 of it or later: the values shown inside the prose (`{{q: ...}}`)
and the shell runner below arrived then, and an older one shows them as text.

1. Start a scratch q **from the repository root** - the notebooks load
   `src/init.q` by a relative path, and refuse to run from anywhere else:

   ```
   q -p 5010
   ```

2. In VS Code, add that process to q-lint (**Add Process**, then **Connect to
   Process**) and open a notebook.

3. **Run All.**

Use a process of your own, never one that belongs to a running stack: the cells
define names in the root namespace, and notebook 3 runs real workers. It points
them at a new, empty temporary status directory before it loads anything, and
refuses to run inside a TorQ process.

Without VS Code, q-lint's runner executes a notebook from a shell and writes the
result as a page:

```
node ~/.vscode/extensions/wojdalski.q-lint-<version>/out/qnbrun.js \
    scripts/examples/notebooks/01_quant_library.qnb --process localhost:5010 -o 01.html
```

## Changing or adding one

Every notebook here is run on each change, by the test that runs the worked
examples beside them:

```
uv run pytest tests/qrun/test_worked_examples.py -k notebook
```

It runs each notebook's code cells, and the live values (`{{q: ...}}`) its prose
shows, as one script on KDB-X. So:

- **The last code cell checks the claims the prose made**, and throws unless
  they all hold. A notebook that only *ran* would still pass with every sentence
  in it false; this is what makes a stale explanation a failing test. A new
  claim in the prose gets a line in that cell.
- **Commit without outputs.** Clear All Outputs, then save. Outputs are a run's,
  not the notebook's: committed, they go stale with the next change to the
  library, and every Run All would show as a diff. The test refuses a notebook
  that carries any.
- **A cell runs as a script does.** An indented line continues the statement
  above it, and a line holding only `/` opens a block comment.
- **No name after a q builtin.** `cross`, `fills` and `cov` each cost this
  folder a run; `qlinter` reports them (QF001-QF004) on the script that **Export
  Notebook as q Script** writes.
- **Run All twice.** A notebook that works once and fails the second time in the
  same process teaches the wrong thing about the framework. Notebook 3's first
  cell empties what its last run left in memory for this reason.
