"""q facts uqf_airflow_provider reads, generated from the q tree - DO NOT EDIT.

Written by scripts/generate/q_facts.py, which loads src/ under KDB-X and
reads each value (#818). A hook runs it with --check, so a change to any of
these in q fails the commit until this file is regenerated.
"""

#: every state a worker's status file may record - .qetl.status.status_states
STATUS_STATES: tuple[str, ...] = (
    "starting",
    "running",
    "idle",
    "completed",
    "failed",
)

#: the states that end a run - .qetl.status.terminal_states
STATUS_TERMINAL_STATES: tuple[str, ...] = (
    "idle",
    "completed",
    "failed",
)

#: the states a worker's process exits 0 for - .qetl.job.bounded.exit_code
STATUS_SUCCESS_STATES: tuple[str, ...] = (
    "idle",
    "completed",
)

#: a status file's name before the instance id - .qetl.status.file_prefix
STATUS_FILE_PREFIX: str = "airflow_status_"

#: a status file's name after the instance id - .qetl.status.file_suffix
STATUS_FILE_SUFFIX: str = ".txt"
