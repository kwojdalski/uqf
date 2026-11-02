"""Fixtures for the publisher's native registration (#1845): a site process file
with what real ones carry, and the CLI world the attach commands run in."""

from __future__ import annotations

from pathlib import Path

from attach_support import FakeStack, make_bundle
from typer.testing import CliRunner

from uqs import cli
from uqs.attach import config, facts, native_row, plan, publisher, service
from uqs.cli import shared
from uqs.paths import repo_root
from uqs.stack import attached_publisher

SECRET = "s3cr3t-Pw!x-native"
PROC = "pb_ledger1"

#: A site registry as production ones are: CRLF, a site column (taskset_no), a
#: quoted field, a comment-free header in TorQ's order plus the site's.
SITE = (
    "host,port,proctype,procname,U,localtime,g,T,w,load,startwithall,extras,qcmd,taskset_no\r\n"
    "localhost,{KDBBASEPORT}+1,discovery,discovery1,${TORQAPPHOME}/appconfig/passwords/"
    "accesslist.txt,1,0,,,${KDBCODE}/processes/discovery.q,1,,q,0\r\n"
    "localhost,{KDBBASEPORT},segmentedtickerplant,stp1,${TORQAPPHOME}/appconfig/passwords/"
    "accesslist.txt,1,0,,,${KDBCODE}/processes/segmentedtickerplant.q,1,"
    "-schemafile ${TORQAPPHOME}/database.q,q,1:2\r\n"
    'localhost,{KDBBASEPORT}+2,rdb,rdb1,"${TORQAPPHOME}/appconfig/passwords/accesslist.txt",'
    "1,1,180,,${KDBCODE}/processes/rdb.q,1,,q,3\r\n"
)

#: This checkout's row for the publisher, as effective_process_rows composes a job's.
UQF_ROW = {
    "host": "localhost", "port": "{KDBBASEPORT}+99", "proctype": "metrics", "procname": PROC,
    "U": "${TORQAPPHOME}/appconfig/passwords/accesslist.txt", "localtime": "1", "g": "0",
    "T": "", "w": "",
    "load": "${UQF_SCRIPTS}/processes/torq_pipeline.q ${UQF_SCRIPTS}/processes/torq_stream.q",
    "startwithall": "1", "extras": "", "qcmd": "q",
}  # fmt: skip
TREE_ENV = {"UQF_ROOT": "/opt/uqf", "UQF_SCRIPTS": "/opt/uqf/scripts"}


class _ErrorLog:
    """What `log.error` was handed: loguru does not write into CliRunner's output."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def error(self, message: str, *args) -> None:
        self.messages.append(str(message).format(*args))

    def __getattr__(self, _name: str):
        return lambda *a, **kw: None


class World(FakeStack):
    state_dir: Path
    bundle: Path
    app: Path
    registry: Path
    schema: Path


def make_world(monkeypatch, tmp_path) -> World:
    """The fixture each test module declares as `world`."""
    schema = tmp_path / "database.q"
    schema.write_text("/ the stack's schema file\n")
    fake = World(6000, schema_file=str(schema))
    fake.schema = schema
    fake.state_dir = tmp_path / "attachments"
    fake.bundle = make_bundle(tmp_path / "piggybank")
    # the site's TorQ application, as TorQ lays one out: the process file in its
    # appconfig/ (KDBAPPCONFIG), beside the starter pack's access list (#1849)
    fake.app = tmp_path / "site"
    fake.registry = fake.app / "appconfig" / "process.prd.csv"
    access = fake.app / attached_publisher.STARTER_ACCESS_LIST
    access.parent.mkdir(parents=True)
    access.write_text("admin:admin\n")
    fake.registry.write_bytes(SITE.encode())
    monkeypatch.delenv(attached_publisher.KDBSECURE_ENV, raising=False)
    monkeypatch.setenv(config.ATTACH_DIR_ENV, str(fake.state_dir))
    monkeypatch.setattr(service, "ipc_for", lambda att: fake)
    monkeypatch.setattr(
        facts, "_tcp_open", lambda host, port: bool(fake.started) and port not in fake.procs
    )
    monkeypatch.setattr(shared, "configure_logging", lambda **kw: None)
    errors = _ErrorLog()
    monkeypatch.setattr(shared, "log", errors)
    fake.errors = errors.messages
    monkeypatch.setattr(native_row, "uqf_row", lambda paths, procname: dict(UQF_ROW))
    monkeypatch.setattr(native_row, "tree_env", lambda paths: dict(TREE_ENV))
    monkeypatch.setattr(publisher.UqsPublisher, "start", lambda self, att: _started(fake, att))
    monkeypatch.setattr(publisher.UqsPublisher, "running", lambda self, att: bool(fake.started))
    monkeypatch.setattr(publisher.UqsPublisher, "stop", lambda self, att: fake.started.clear())
    return fake


def _started(fake: FakeStack, att) -> None:
    port = plan.publisher_port(att, repo_root()).port
    fake.named("discovery1")["services"].append(
        {"procname": att.publisher.procname, "proctype": "pipeline", "hpup": f":testhost:{port}"}
    )
    fake.started.append(att.publisher.procname)


def new(world: World, *extra: str) -> list[str]:
    """A new attachment, durable (the cli writer), registered in the site's file."""
    return ["pb-uat", "--job", "pb_general_ledger", "--bundle", str(world.bundle),
            "--stack-id", "6000", "--process-file", str(world.registry), *extra]  # fmt: skip


def run(*argv: str):
    return CliRunner().invoke(cli.app, ["job", "attachment", *argv], catch_exceptions=False)


def rows_for(path: Path, procname: str = PROC) -> list[str]:
    return [line for line in path.read_bytes().decode().splitlines() if f",{procname}," in line]


def flat(text: str) -> str:
    return " ".join(text.split())


def said(world: World, result) -> str:
    """Everything a command printed or logged as an error, on one line."""
    return flat(result.output + " " + " ".join(world.errors))
