# ml - vendoring notice

Vendored from https://github.com/KxSystems/ml at release `4.1.0`, commit
`5509fa6cfc454c68bf3441672fe1a26cb5a19088` (2026-10-15). Distributed under the
Apache License 2.0; the full text is at `lib/ml/LICENSE-ml` (upstream's
`LICENSE`, renamed to match this tree's other vendored licences).

The release carries three modules: `ml/` (the toolkit, namespace `.ml`),
`nlp/` (`.nlp`) and `automl/` (`.automl`, built on the other two). All three
were copied.

## What was NOT vendored

- `.git/` history.
- Four example models under `ml/examples/models/` that deserialise through
  Python's pickle: `sklearnModel.pkl`, `pythonModel.pkl`, `theanoModel.pkl` and
  `torchModel.pt`. Loading a pickle executes code chosen by whoever wrote it,
  and these are opaque binaries this tree has no way to review. Upstream's
  `ml/registry/tests/registry.t` loads `sklearnModel.pkl`; that test will not
  run from this copy without fetching the file from upstream at the commit
  above.

Everything else is byte-for-byte upstream, including its own `.gitignore`
files; the test data directories those ignore were added to this repository
explicitly, because upstream tracks them.

## Status here

Not loaded by `src/init.q` or anything else in this repository, and not
verified working here. `ml/ml.q` needs a Python bridge, PyKX (`pykx.q`) or
embedPy (`p.q`), plus the Python packages in `requirements.txt`; PeachQ
supports neither. Vendored for reference at the repo owner's request.
