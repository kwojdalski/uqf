"""Which q interpreters a job runs on: its declaration's `q` key (#2747).

The tree runs on kdb+ 4.0, KDB-X 5 and PeachQ, but one job may use something
only some of them have - an ODBC source, a KDB-X module, a primitive PeachQ
lacks - and a sidecar bundle's job above all is written somewhere else, against
some q. Such a job says so, as one q string of space-separated terms:

    kdbx         any kdb+ or KDB-X
    kdbx>=4.1    a minimum version (.z.K)
    kdbx=4       a version prefix: 4 is every 4.x, 4.1 every 4.1.x
    peachq       the PeachQ interpreter

Absent, the job runs on every q. q checks the form at load
(src/etl/core/declared.q, .qetl.declared.require_q) and this the meaning,
where the interpreter is known: `uqs start` (stack/start_policy.
refuse_unsupported, which refuse_start applies) and `uqs deploy push`,
against the server's q (deploy/overrides.for_job_q).

kdb+ 4.0 prints its .z.K as "4": a version is padded with zeros before it is
compared, as deploy/artifact.meets does (#2577).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from uqs.interpreter import KDBX, PEACHQ

#: One term: an interpreter, and for KDB-X an optional version comparison.
_TERM = re.compile(r"(kdbx)(>=|=)([0-9]+(?:\.[0-9]+)*)|(kdbx|peachq)")

#: How an interpreter is named in a message.
NAMES = {KDBX: "KDB-X", PEACHQ: "PeachQ"}


@dataclass(frozen=True)
class Term:
    """One interpreter a job runs on: `impl`, and for KDB-X an optional
    comparison `op` (">=" or "=") with `version`."""

    impl: str
    op: str = ""
    version: tuple[int, ...] = ()

    def __str__(self) -> str:
        return self.impl + (self.op + ".".join(map(str, self.version)) if self.op else "")

    def admits(self, impl: str, version: str) -> bool:
        """Whether a q of implementation `impl` reporting .z.K `version` is
        this term. A version that says nothing comparable meets no version
        comparison: it cannot be confirmed."""
        if impl != self.impl:
            return False
        if not self.op:
            return True
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", version.strip()):
            return False
        got = tuple(int(p) for p in version.strip().split("."))
        if self.op == "=":
            got += (0,) * (len(self.version) - len(got))
            return got[: len(self.version)] == self.version
        width = max(len(got), len(self.version))
        return got + (0,) * (width - len(got)) >= self.version + (0,) * (width - len(self.version))


def parse(text: str, where: str = "q") -> tuple[Term, ...]:
    """The terms of a `q` key's string, or ValueError naming each bad one -
    the refusals .qetl.declared.require_q makes at load."""
    words = text.split()
    if not words:
        raise ValueError(f"{where} names no interpreter - drop the key to run on every q")
    bad = [w for w in words if not _TERM.fullmatch(w)]
    if bad:
        raise ValueError(
            f"{where} holds {', '.join(bad)}, which is not kdbx, kdbx>=VERSION, "
            "kdbx=VERSION or peachq"
        )
    terms = []
    for word in words:
        found = _TERM.fullmatch(word)
        assert found is not None  # every word matched above
        if found.group(4):
            terms.append(Term(found.group(4)))
        else:
            terms.append(Term(KDBX, found.group(2), tuple(map(int, found.group(3).split(".")))))
    return tuple(terms)


def text(terms: Iterable[Term]) -> str:
    """`terms` as a `q` key spells them."""
    return " ".join(map(str, terms))


def supports(terms: Iterable[Term], impl: str, version: str) -> bool:
    """Whether a job declaring `terms` runs on a q of `impl` reporting
    `version`; True for a job that declares none."""
    terms = tuple(terms)
    return not terms or any(t.admits(impl, version) for t in terms)


def described(impl: str, version: str) -> str:
    """A q as a message names it: `KDB-X 4.1`, `PeachQ`, `KDB-X of an unknown
    version`."""
    name = NAMES.get(impl, impl)
    if impl == PEACHQ:
        return name
    return f"{name} {version}" if version.strip() else f"{name} of an unknown version"


def unsupported(jobs: Mapping[str, tuple[Term, ...]], impl: str, version: str) -> list[str]:
    """Each of `jobs` (name -> its terms) that does not run on this q, as
    `name (q "kdbx>=5")`, in name order."""
    return [
        f'{name} (q "{text(terms)}")'
        for name, terms in sorted(jobs.items())
        if not supports(terms, impl, version)
    ]


def problem(found: list[str], impl: str, version: str, remedy: str) -> str:
    """The refusal for `found` (unsupported's answer) on this q."""
    word = "job declares" if len(found) == 1 else "jobs declare"
    return (
        f"the q is {described(impl, version)}, which the {word} no support for: "
        f"{', '.join(found)} - {remedy}"
    )
