"""Spins up a real uqf q server for integration tests.

Finds q through python/conftest.py, by the rule scripts/test.py applies: $Q if set, otherwise
~/.kx/bin/q, and nothing else.

This docstring used to say it searched "the same way the pre-commit hook
does", which had stopped being true - the hook still fell back to PeachQ at
./q while this file said ./q was no longer a fallback. Four places each
looked for the interpreter their own way. They now share one rule, and it is
the README's: choosing another interpreter is explicit. A PATH lookup is not,
because whatever `q` happens to be first on PATH is chosen for you.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session")
def q_port(start_q) -> Iterator[int]:
    with start_q("src/init.q") as port:
        yield port
