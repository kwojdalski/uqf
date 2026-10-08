"""Python's use of q's vocabularies, held to q (#609, #818).

uqs must work without starting q. Where it needs a q VALUE - the conflict
strategies, the run modes, the log levels, the ledgers' columns - it reads
uqs.generated.q_facts, which scripts/generate/q_facts.py writes from the
loaded tree and a hook --checks (#818). These tests hold what uqs builds ON
those values: a mapping covers every level, a column shown exists. Where uqs
re-implements a q RULE (the status directory, the checkpoint path), the rule
is behaviour, not a value, and its test reads q's definition. That happened
once already: `uqs logs` did not know two of q's levels and showed them as
INFO (#598).
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from uqs.cli import runs as cli_runs
from uqs.generated import q_facts
from uqs.stack import backfill, logs, runs
from uqs.stack.trace_render import CODE_FIELDS

UQF_ROOT = Path(__file__).resolve().parents[3]
CORE = UQF_ROOT / "src" / "etl" / "core"


def q_levels() -> list[str]:
    return list(q_facts.LOG_LEVELS)


def test_every_level_q_logs_is_one_uqs_logs_maps():
    """An unmapped level falls through to INFO: labelled INFO, and kept by
    `--level INFO` however quiet it was meant to be."""
    levels = q_levels()
    assert levels, "log.q's `levels:` is no longer spelled the way this test reads it"
    unmapped = [lv for lv in levels if lv not in logs._LOGURU_LEVEL]
    assert not unmapped, f"stack/logs.py _LOGURU_LEVEL has no entry for q's {unmapped}"


def test_q_writes_exactly_the_five_names_uqs_accepts():
    """One vocabulary: what log.q writes is what `--level` takes, in order."""
    assert q_levels() == list(logs.LEVEL_CHOICES)


def test_torqs_renamed_levels_are_among_the_five():
    """scripts/torqconfig/settings/default.q renames TorQ's INF, WARN and ERR
    in every TorQ process; each must land on a name `--level` takes."""
    settings = UQF_ROOT / "scripts" / "torqconfig" / "settings" / "default.q"
    line = next(ln for ln in settings.read_text().splitlines() if ln.startswith("uqf_names:"))
    torq, ours = line.split(":", 1)[1].split("!")
    assert re.findall(r"`(\w+)", torq) == ["INF", "WARN", "ERR"]
    assert set(re.findall(r"`(\w+)", ours)) <= set(logs.LEVEL_CHOICES)


def test_every_mapped_level_has_a_place_in_the_order():
    """`--level` filters by _LEVEL_ORDER; a mapped level missing from it ranks
    0 and is shown under every filter."""
    missing = set(logs._LOGURU_LEVEL.values()) - set(logs._LEVEL_ORDER)
    assert not missing, f"_LEVEL_ORDER has no rank for {missing}"


def test_q_levels_keep_their_order_once_mapped():
    """q lists its levels least to most severe; mapped, they must still rank
    that way, or `--level DEBUG` would keep what q considers quieter."""
    ranks = [logs._LEVEL_ORDER[logs._LOGURU_LEVEL[lv]] for lv in q_levels()]
    assert ranks == sorted(ranks), f"q's {q_levels()} rank {ranks} once mapped"


def test_the_level_choices_are_derived_from_the_order():
    """The `--level` completion and help on `logs` and `up` read these, so no
    command restates the list."""
    assert logs.LEVEL_CHOICES == tuple(sorted(logs._LEVEL_ORDER, key=logs._LEVEL_ORDER.__getitem__))
    assert all(choice in logs.LEVEL_HELP for choice in logs.LEVEL_CHOICES)


def test_every_traced_code_field_is_one_q_writes():
    """trace_render shows a trace event's code as a block, by event text and
    field name. Renaming either in q alone would stop the block rendering, and
    nothing else would notice."""
    sources = [
        p.read_text() for p in (UQF_ROOT / "src").rglob("*.q") if ".qetl.log.trc" in p.read_text()
    ]
    for event, field in CODE_FIELDS.items():
        call = re.compile(rf'"{re.escape(event)}";\s*\(?\s*\(?enlist\[`{re.escape(field)}\]', re.S)
        assert any(call.search(src) for src in sources), (
            f'no q .qetl.log.trc call logs "{event}" with a `{field} field - '
            "trace_render.CODE_FIELDS would never render it"
        )


def q_definition(file: str, name: str) -> str:
    """The text of `name:`'s definition in src/etl/core/`file`, from its first
    line to the next top-level line - enough to read a list or a body."""
    lines = (CORE / file).read_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(f"{name}:")), None)
    assert start is not None, f"{file} no longer defines `{name}:` the way this test reads it"
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace()),
        len(lines),
    )
    return "\n".join(lines[start:end]).rstrip()


def test_on_conflict_and_modes_are_read_from_q_not_copied():
    """`--on-conflict` and `--mode` offer q's own values (#818): the CLI
    spelling of a mode swaps q's underscore for a hyphen, and nothing else."""
    assert backfill.ON_CONFLICT == q_facts.IO_STRATEGIES
    assert backfill.MODES == tuple(m.replace("_", "-") for m in q_facts.RUN_MODES)


def test_status_dir_is_qs_rule(tmp_path):
    """`uqs run` and the lock and checkpoint paths read the directory q writes
    status files to: $UQF_STATUS_DIR, else $TORQDATA/status. Both halves of
    q's rule are read from status.q, then checked against Python's."""
    body = q_definition("status.q", "status_dir")
    assert "getenv`UQF_STATUS_DIR" in body
    assert '(getenv[`TORQDATA]),"/status"' in body
    paths: Any = SimpleNamespace(torqdata=tmp_path / "data")  # all status_dir reads
    assert runs.status_dir(paths, {}) == tmp_path / "data" / "status"
    assert runs.status_dir(paths, {"UQF_STATUS_DIR": "/elsewhere"}) == Path("/elsewhere")


def test_checkpoint_path_is_qs_rule(tmp_path, monkeypatch):
    """`uqs remove checkpoint` deletes the file q's checkpoint_path names:
    `<worker>.checkpoint` in lock_dir, which is the status directory."""
    assert q_definition("backfill_state.q", "lock_dir") == "lock_dir:{[] .qetl.status.status_dir[]}"
    body = q_definition("backfill_state.q", "checkpoint_path")
    assert '(lock_dir[]),"/",string[worker],".checkpoint"' in body
    monkeypatch.setenv("UQF_STATUS_DIR", str(tmp_path))
    paths: Any = SimpleNamespace(torqdata=tmp_path / "unused")
    assert backfill.checkpoint_path(paths, "w") == tmp_path / "w.checkpoint"


def test_every_column_uqs_run_shows_is_one_q_records():
    """`uqs run` prints a choice of each ledger's columns; one renamed in q
    alone would print empty rather than fail. _FACT_COLUMNS had no check at
    all beside _RUN_COLUMNS' (#818)."""
    for shown, ledger, name in (
        (cli_runs._RUN_COLUMNS, q_facts.ETL_RUNS_COLUMNS, "_RUN_COLUMNS"),
        (cli_runs._FACT_COLUMNS, q_facts.ETL_RUN_META_COLUMNS, "_FACT_COLUMNS"),
    ):
        missing = [c for c in shown if c not in ledger]
        assert not missing, f"cli/runs.py {name} names {missing}, which the ledger lacks"
