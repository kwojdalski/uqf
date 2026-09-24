# Reference

The contracts, and the pages CI holds the code to. That is what separates
this directory from [`architecture/`](../architecture/): an architecture page
argues why, and a reference page states what — precisely enough that a gate
can fail when the code and the page disagree.

| Page | The contract | Held by |
|---|---|---|
| [`environment.md`](environment.md) | every environment variable the tree reads | `scripts/gates/check_env_reference.py` |
| [`etl-framework-requirements.md`](etl-framework-requirements.md) | ETL-nn — what the pipeline framework must do | prose; cited from the code it constrains |
| [`frontend-requirements.md`](frontend-requirements.md) | FE-nn — what the desk application must do | cited from `python/uqf_frontend` and its tests |
| [`quant-modules.md`](quant-modules.md) | each `src/` pricing module, its namespace and its tests | — |
| [`surfaces/uqf-local/`](surfaces/uqf-local/) | this tree's exported contract surface: every public function, table, process and variable | `contract_surface.py check` in CI, and `check_doc_references.py` reads it as its list of functions that exist |

**`surfaces/` is generated.** Edit the source and regenerate; a hand-edit
fails the build. It lived under `docs/migrations/` while it existed to be
diffed against a canonical upstream — that upstream is frozen, so what
remains is a contract reference, and it sits here now.

Function-level documentation is not here: every `src/**/*.q` function carries
a qDoc block, collected into [`../man.q`](../man.q) by
`generate_man_registry.py`.
