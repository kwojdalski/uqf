"""Scaffolding a reaction: work that starts when a dataset is published.

The fourth way work starts in src/etl/, beside a streaming job, a normalizer
and a bounded worker - and the only one without a process. A reaction is a
handler `.qetl.reaction` calls with the range a bounded worker has just
published, inside that worker's own process. So this writes no table, no
profile entry and no port: a reaction file, and a test that fails.

WHICH DATASETS CAN TRIGGER ONE. Only a bounded worker's window announces
itself (`.qetl.job.bounded.do_window`); a streaming job's publication does
not. A reaction on a table only streaming jobs write would load, register,
and never run - so `dataset` must be one a bounded worker fills, and anything
else is refused before a file is written.
"""

from __future__ import annotations

from uqs.paths import REACTION_DIR, TEST_DIR, UqsError
from uqs.scaffold.jobs import _check_name, _nslist_action, test_namespace
from uqs.scaffold.plan import FileAction, ScaffoldPlan
from uqs.scaffold.templates import reaction_body, test_stub


def reaction(
    name: str,
    dataset: str,
    writes: list[str],
    *,
    producers: dict[str, list[str]],
    taken: set[str],
    streaming_tables: set[str] | frozenset[str] = frozenset(),
) -> ScaffoldPlan:
    """The plan for reaction `name`, fired when `dataset` is published.

    `producers` maps each dataset a bounded worker fills to the processes that
    fill it - the processes the reaction will run in. `taken` is every job
    name already in the tree, since the reaction's namespace, `.qpipe.job.<name>`,
    is shared with them. `streaming_tables` only sharpens the refusal for a
    dataset that is published, but never by a bounded worker.
    """
    _check_name(name, "reaction name")
    _check_name(dataset, "--triggered-by")
    for table in writes:
        _check_name(table, "--writes")
    if name in taken:
        raise UqsError(
            f"a job called {name!r} already exists, and a reaction shares its namespace "
            f"(.qpipe.job.{name}) - pick another name"
        )
    if dataset not in producers:
        if dataset in streaming_tables:
            raise UqsError(
                f"{dataset} is published by streaming jobs only, and only a bounded worker's "
                "published window fires a reaction (.qetl.job.bounded.do_window) - a reaction "
                "on it would load and never run"
            )
        known = ", ".join(sorted(producers)) or "none"
        raise UqsError(
            f"no bounded worker fills {dataset!r}, so nothing would ever fire this reaction. "
            f"Datasets that can: {known}"
        )
    if dataset in writes:
        raise UqsError(
            f"--writes {dataset} is the dataset this reacts to: the job graph refuses that "
            "cycle when the file loads, so the tree would stop loading"
        )

    procs = producers[dataset]
    ns = test_namespace(name, reaction=True)
    actions = [
        FileAction(REACTION_DIR / f"{name}.q", reaction_body(name, dataset, writes, procs)),
        FileAction(
            TEST_DIR / f"test_{name}.q",
            test_stub(name, ns, f"the {name} reaction to {dataset}"),
        ),
        _nslist_action(ns),
    ]
    notes = [
        f"implement .qpipe.job.{name}.handler, then replace the scaffolded test",
        f"it runs inside {', '.join(procs)} - the process(es) that fill {dataset}",
    ]
    if writes:
        notes.append(
            f"--writes {', '.join(writes)} is asserted, not derived: the handler must write "
            "exactly that, and nothing checks it does"
        )
    return ScaffoldPlan(name=name, actions=actions, notes=notes)
