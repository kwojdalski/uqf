"""Detach of an attachment with more than one table (#1471, #1472).

The manifest holds one `stp_register` op, and one `consumer_attach` op per
consumer, for EACH table. Every check detach makes about a table must read that
table's own ops - not the first table's, and not a sum over all of them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from attach_support import LEDGER_COLUMNS, LEDGER_Q, FakeStack, make_bundle

from uqs.attach import apply as applying
from uqs.attach import config, facts, service
from uqs.attach import detach as detaching
from uqs.attach.config import Storage
from uqs.attach.preflight import Host
from uqs.paths import repo_root

ROOT = repo_root()
LEDGER, JOURNAL = "piggybank_general_ledger", "piggybank_journal"
LOCAL = Host(lambda h, p: False, ("localhost",))


class Publisher:
    def running(self, att) -> bool:
        return False

    def start(self, att) -> None:
        pass

    def stop(self, att) -> None:
        pass


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    monkeypatch.setattr(facts, "_tcp_open", lambda host, port: False)


def two_tables(tmp_path: Path, order: tuple[str, str]):
    """A FakeStack and an attachment whose job publishes both tables, in `order`."""
    bundle = make_bundle(tmp_path / "piggybank")
    tables = {LEDGER: LEDGER_Q, JOURNAL: LEDGER_Q.replace(LEDGER, JOURNAL)}
    (bundle / "tables.q").write_text("\n".join(tables[t] for t in order) + "\n")
    (bundle / "pb_general_ledger.q").write_text(
        ".qetl.job.stream.define[`pb_general_ledger;"
        "`procname`subscribe_to`publishes`note!(`pb_ledger1;`symbol$();"
        f'`{"`".join(order)};"publishes both")];\n'
    )
    (bundle / "catalog.q").write_text("".join(f'.qcat.describe[`{t}]:"x";\n' for t in order))
    for name in ("tplogs", "wdb", "hdb"):
        (tmp_path / name).mkdir()
    schema = tmp_path / "database.q"
    schema.write_text("trade:([] time:`timestamp$())\n")
    fake = FakeStack(6000, schema_file=str(schema))
    fake.named("stp1")["tplogdir"] = str(tmp_path / "tplogs")
    fake.named("wdb1")["savedir"] = f":{tmp_path / 'wdb'}"
    fake.named("wdb1")["hdbdir"] = f":{tmp_path / 'hdb'}"
    storage = Storage(
        str(tmp_path / "tplogs"), str(schema), str(tmp_path / "wdb"), str(tmp_path / "hdb")
    )
    state = tmp_path / "attachments"
    att = service.create(
        "pb-uat", "pb_general_ledger", bundle, 6000, ROOT, runtime_only=False, storage=storage
    )
    config.save(att, state)
    return fake, att, state


def detach_(att, fake, state):
    return detaching.detach(att, fake, Publisher(), host=LOCAL, state_dir=state)


def test_each_table_is_unregistered_expecting_only_its_own_consumers(tmp_path):
    """#1471: two consumers subscribed to each table, so each table's
    `stp_unregister` expects 2 subscribers - not the 4 of both tables together."""
    fake, att, state = two_tables(tmp_path, (LEDGER, JOURNAL))
    applying.apply(att, fake, ROOT, state_dir=state)
    fake.calls.clear()
    assert detach_(att, fake, state).outcome == "rolled_back"
    asked = {
        (r["table"], r["expect_subscribers"]) for _, op, r in fake.calls if op == "stp_unregister"
    }
    assert asked == {(LEDGER, 2), (JOURNAL, 2)}


def test_a_foreign_subscriber_on_one_of_two_tables_refuses_the_reversal(tmp_path):
    """#1471: the extra subscriber on the ledger is not one this attachment
    attached; it must not be hidden by the journal's consumers."""
    fake, att, state = two_tables(tmp_path, (LEDGER, JOURNAL))
    applying.apply(att, fake, ROOT, state_dir=state)
    fake.named("stp1")["subscribers"][LEDGER] += 1
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    assert "subscribers other than the attached consumers" in [b.reason for b in result.blockers]
    assert {LEDGER, JOURNAL} <= set(fake.named("stp1")["schemas"]), "nothing was removed"


@pytest.mark.parametrize("order", [(LEDGER, JOURNAL), (JOURNAL, LEDGER)])
def test_each_consumer_table_is_checked_against_its_own_tickerplant_table(tmp_path, order):
    """#1472: the journal was already on the tickerplant, the ledger was created
    here. The journal's consumer tables are refused, whichever table comes
    first; the ledger's are not."""
    fake, att, state = two_tables(tmp_path, order)
    fake.named("stp1")["schemas"][JOURNAL] = LEDGER_COLUMNS
    fake.named("stp1")["published"].append(JOURNAL)
    applying.apply(att, fake, ROOT, state_dir=state)
    result = detach_(att, fake, state)
    assert result.outcome == "refused"
    plant = [
        b for b in result.blockers if "tickerplant table this attachment does not own" in b.reason
    ]
    assert sorted(b.component for b in plant) == ["rdb1", "wdb1"]
    assert all(b.reason.startswith(f"{JOURNAL} ") for b in plant)
    assert JOURNAL in fake.named("rdb1")["schemas"] and JOURNAL in fake.named("wdb1")["schemas"]
