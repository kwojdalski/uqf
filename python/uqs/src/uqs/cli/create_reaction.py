"""`uqs job new NAME --triggered-by DATASET`: scaffolding a reaction.

Apart from cli/create.py, which reached the module budget: a reaction is the
one `job new` shape with no process of its own, so it shares no options with
the others and is refused all of theirs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from uqs.cli import completion
from uqs.cli.create_bundle import planning_root, write_bundle_plan
from uqs.cli.regenerate import write_plan
from uqs.cli.shared import _die, _paths
from uqs.model.declarations import read_declarations
from uqs.model.jobs import bounded_producers, job_rows
from uqs.paths import UqsError, UqsUsageError
from uqs.scaffold import reaction

#: `job new`'s option of a reaction's own (its outputs are the shared `--outputs`).
TriggeredByOpt = Annotated[
    str | None,
    typer.Option(
        "--triggered-by",
        metavar="DATASET",
        help="Scaffold a reaction instead: run when a bounded worker publishes this dataset",
        autocompletion=completion.bounded_datasets,
    ),
]


def scaffold_reaction(
    name: str,
    triggered_by: str,
    outputs: str | None,
    others: dict[str, bool],
    *,
    bundle: Path | None = None,
    dry_run: bool,
) -> None:
    """`uqs job new NAME --triggered-by DATASET [--outputs T]`: a reaction.

    Every option that shapes a process is refused rather than ignored, because
    a reaction has no process: it runs inside the one that publishes DATASET.
    """
    for option in (o for o, used in others.items() if used):
        _die(
            UqsUsageError(
                f"{option} does not apply to a reaction (--triggered-by): it has no process, "
                "table or schedule of its own"
            )
        )
    repo_root = _paths().repo_root
    try:
        with planning_root(repo_root, bundle) as root:
            plan = reaction.reaction(
                name,
                triggered_by.strip(),
                [w.strip() for w in (outputs or "").split(",") if w.strip()],
                producers=bounded_producers(root),
                taken={row["job"] for row in job_rows(root)},
                streaming_tables={t for d in read_declarations(root) for t in d.publishes},
            )
    except UqsError as exc:
        _die(exc)
    if bundle is not None:
        write_bundle_plan(plan, bundle, dry_run=dry_run)
        return
    write_plan(plan, repo_root, dry_run=dry_run)
