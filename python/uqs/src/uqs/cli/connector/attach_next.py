"""What to run next on an attachment: follow its publisher, query its rows (#2696).

`uqs job attachment add` (once it started the publisher and is not following
it) and `uqs job attachment show NAME` end with two ready-to-run commands,
filled in from the saved attachment, in the words `uqs job backfill` uses
("follow it with `uqs logs ...`", cli/job/backfill_attached.py):

    follow it with `uqs logs <publisher> -f`
    query its rows with `uqs query --host H --port P 'select from T where i >= count[i]-10'`

No new option or command: `uqs query --host/--port/--user/--password-env`
reaches any process. The port is the stack's rdb's, as discovery listed it to
`add`'s preflight - it is not saved with the attachment, so `show`, which opens
no connection to the stack, names discovery's address and says the rdb's host
and port come from there. A port is never guessed. No credential is printed:
`--password-env` takes the variable's NAME, and `--user`/`--password-env` are
left out where they are `uqs query`'s own defaults.
"""

from __future__ import annotations

from uqs.attach.config import DEFAULT_USER, Attachment
from uqs.attach.remote import Endpoint
from uqs.attach.topology import Observation

#: the rows `its rows` shows: the last ten the rdb holds
ROWS_Q = "select from {table} where i >= count[i]-10"


def rdb_of(observation: Observation | None) -> Endpoint | None:
    """The first of the attachment's consumers that is an rdb, as discovery listed it."""
    if observation is None:
        return None
    return next((c.endpoint for c in observation.consumers if c.proctype == "rdb"), None)


def commands(att: Attachment, rdb: Endpoint | None) -> dict:
    """{"follow", "rows", "port_from", "tables"}: the two commands, where the
    query's port comes from, and every table the attachment publishes (the
    query shows the first)."""
    tables = [t.name for t in att.tables]
    login = ""
    if att.stack.user != DEFAULT_USER:
        login += f" --user {att.stack.user}"
    if att.stack.password_env:
        login += f" --password-env {att.stack.password_env}"
    query = ROWS_Q.format(table=tables[0] if tables else "<table>")
    discovery = f"{att.stack.discovery_host}:{att.stack.discovery_port}"
    if rdb is None:
        where = "--host <rdb-host> --port <rdb-port>"
        port_from = (
            f"the rdb's host and port come from the stack's discovery at {discovery}: "
            "the attachment does not save them"
        )
    else:
        where = f"--host {rdb.host} --port {rdb.port}"
        port_from = (
            f"the port is {rdb.procname}'s, as the stack's discovery at {discovery} lists it"
        )
    return {
        "follow": f"uqs logs {att.publisher.procname} -f",
        "rows": f"uqs query {where}{login} '{query}'",
        "port_from": port_from,
        "tables": tables,
    }


#: The states in which the attachment's table is registered and its publisher
#: may run: the only ones with a log to follow and rows to query.
PUBLISHING = ("applied", "verified")
#: What moves the attachment on from a state that publishes nothing (#2943):
#: `add` applies one never applied, or whose apply stopped part-way; `remove`
#: finishes a detach or a rollback that stopped part-way. A removed one
#: (detached, rolled_back) needs nothing, and an unreadable one is the list's.
MOVES_ON = {
    "planned": ("add", "apply it"),
    "applying": ("add", "finish applying it"),
    "partial": ("add", "finish applying it"),
    "detaching": ("remove", "finish removing it"),
    "rollback_partial": ("remove", "finish removing it"),
}


def for_state(att: Attachment, state: str) -> dict:
    """What `show` ends with in `state`: `commands` when it publishes, the one
    command that moves it on ({"command", "why"}) when one does, else {}."""
    if state in PUBLISHING:
        return commands(att, None)
    if state in MOVES_ON:
        verb, why = MOVES_ON[state]
        return {"command": f"uqs job attachment {verb} {att.name}", "why": why}
    return {}


def lines(nxt: dict, *, follow: bool = True) -> list[str]:
    """`commands` as the lines a person reads; without `follow`, the query alone.
    A `for_state` answer that is not `commands` is its one line, or none."""
    if "rows" not in nxt:
        return [f"{nxt['why']} with `{nxt['command']}`"] if nxt else []
    out = [f"follow it with `{nxt['follow']}`"] if follow else []
    out.append(f"query its rows with `{nxt['rows']}`")
    out.append(f"  {nxt['port_from']}")
    if len(nxt["tables"]) > 1:
        out.append(f"  it also publishes {', '.join(nxt['tables'][1:])}")
    return out
