"""What a bounded run may do, and which of those modes execute (#1839).

The modes themselves are q's (`.qetl.job.bounded.runtime.modes`, read into
uqs.generated.q_facts); this module only spells them as the CLI takes them and
splits them in two. It imports nothing of uqs but the generated facts, so any
module in `stack/` - capabilities included, which stack.backfill imports - and
every layer above can read the split from here rather than restate it.
"""

from __future__ import annotations

from uqs.generated import q_facts

#: What a run may do - .qetl.job.bounded.runtime.modes, read from q and
#: spelled as the CLI takes them (dry_run is dry-run). Each opens and writes
#: strictly more than the one before: validate reads only configuration and
#: code, plan adds the local ledgers read-only, dry-run adds the source, run
#: adds every write.
MODES = tuple(mode.replace("_", "-") for mode in q_facts.RUN_MODES)

#: The modes that open no source, write nothing and leave no status file -
#: so there is no outcome to wait for and no capability to require.
READ_ONLY_MODES = tuple(mode for mode in MODES if mode in {"validate", "plan"})

#: The modes that start the runner: `dry-run` opens the source, `run` writes too.
EXECUTION_MODES = tuple(mode for mode in MODES if mode in {"dry-run", "run"})

# A mode q adds is in MODES and in neither half until it is classified here;
# tests/test_run_modes.py fails until then, since --wait, the capability check
# and the attached-backfill dispatch each branch on this split.
