"""What a bounded run may do, and which of those modes execute (#1839).

The modes (`.qetl.job.bounded.runtime.modes`) and which of them are read-only
(`.qetl.job.bounded.runtime.read_only_modes`) are q's, read into
uqs.generated.q_facts; this module only spells them as the CLI takes them and
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

#: The mode a run takes when none is given: q's `.qetl.job.bounded.runtime.mode`
#: resolves an unset mode to `run`, the last - the one that does all of it.
DEFAULT_MODE = MODES[-1]

#: What each mode does, for `--mode`'s help; tests/test_cli_conventions.py
#: holds its keys to MODES, so a mode q adds is described or fails there.
NOTES = {
    "validate": "check the worker and range, open nothing",
    "plan": "also list the windows a run would fetch, from the ledgers, read-only",
    "dry-run": "also fetch and transform, and write nothing",
    "run": "all of it",
}

#: The values `--mode` offers: every mode but `dry-run`, which is spelled
#: `--dry-run` (#1858) - `--mode dry-run` is refused, naming it.
MODE_OPTION_MODES = tuple(mode for mode in MODES if mode != "dry-run")

#: The modes that open no source, write nothing and leave no status file -
#: so there is no outcome to wait for and no capability to require. q's own
#: declaration, .qetl.job.bounded.runtime.read_only_modes.
READ_ONLY_MODES = tuple(mode.replace("_", "-") for mode in q_facts.READ_ONLY_RUN_MODES)

#: The modes that start the runner: every other one - `dry-run` opens the
#: source, `run` writes too.
EXECUTION_MODES = tuple(mode for mode in MODES if mode not in READ_ONLY_MODES)

# A mode q adds executes unless q lists it read-only; tests/test_run_modes.py
# holds the two halves to q's whole list, since --wait, the capability check
# and the attached-backfill dispatch each branch on this split.

#: The modes that rehearse a run rather than make it - every one but `run`.
#: Their promise is to write nothing, so `uqs job backfill` starts them
#: without bootstrapping the data directory first (stack/backfill.py, #1902).
REHEARSAL_MODES = tuple(mode for mode in MODES if mode != "run")
