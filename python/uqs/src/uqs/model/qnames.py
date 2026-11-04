"""Names q computes from a key, and the names q keeps for itself, spelled
once for Python (#1368).

q owns each rule (`.qetl.cfg.env_name`, `.qetl.source.credential_var`); the
prefixes they build on are generated into `uqs.generated.q_facts`, so a q
change to one fails the q-facts hook until Python follows. The `upper` that
completes each rule is held to q's own output by `test_qnames.py`.
"""

from __future__ import annotations

from uqs.generated.q_facts import CFG_ENV_PREFIX, SOURCE_CREDENTIAL_PREFIX


def credential_var(source: str) -> str:
    """The environment variable a live run reads the source's credential from -
    `.qetl.source.credential_var`."""
    return SOURCE_CREDENTIAL_PREFIX + source.upper()


def config_env_var(key: str) -> str:
    """The environment variable that outranks a worker-config key -
    `.qetl.cfg.env_name`."""
    return CFG_ENV_PREFIX + key.upper()


#: q's own words: `.Q.res` plus every name in the `.q` context (KDB-X 4.x/5.x). A root
#: global with one of these names shadows the keyword for the whole process, and a
#: column named with one cannot be written in a table literal - `([] value:...)` is
#: refused with `assign` (#1588). `test_qnames.py` holds the list to a running q: every
#: word the interpreter keeps must be here (KDB-X keeps a few PeachQ does not; both are).
Q_WORDS = frozenset(
    (
        "abs acos asin atan avg bin binr cor cos cov delete dev div do enlist exec exit exp "
        "getenv hopen if in insert last like log max min prd select setenv sin sqrt ss sum tan "
        "update var wavg while within wsum xexp from by neg not null string reciprocal floor "
        "ceiling signum mod xbar xlog and or each scan over prior mmu lsq inv md5 ltime gtime "
        "count first svar sdev scov med all any rand sums prds mins maxs fills deltas ratios "
        "avgs differ prev next rank reverse iasc idesc asc desc msum mcount mavg mdev xrank "
        "mmin mmax xprev rotate ema distinct group where flip type key til get value attr cut "
        "set upsert raze union inter except cross sv vs sublist read0 read1 hclose hdel hsym "
        "hcount peach system ltrim rtrim trim lower upper ssr view tables views cols xcols "
        "keys xkey xcol xasc xdesc fkeys meta lj ljf aj aj0 ajf ajf0 ij ijf pj asof uj ujf ww "
        "wj wj1 fby xgroup ungroup ej save load rsave rload dsave show csv parse eval reval use "
        # PeachQ's one word KDB-X does not have: the PeachQ lane loads the same tree
        "rlike"
    ).split()
)

#: Globals every TorQ process of the supported version defines at its root (read with
#: `system"f"` / `system"v"` on an isolated 5.2.12 stack) - the entry points the plant calls
#: by name, the views it keeps, and `sym`, the enumeration domain the wdb and hdb share.
#: A table of one of these names would overwrite it (#1450). The names are static so
#: scaffolds and bundles can refuse them without a stack; attachment preflight also
#: reads the running stack's own names from its probe (NAME_RESERVED).
_TORQ_GLOBALS = frozenset(
    "upd tablelist init setup subdetails loadschemas generateschemas createlogs tptype "
    "loghandles endofday endofperiod reload countbysym hloc sym".split()
)
#: The hdb's partition domain: `date` for the default `.Q.pf`, and the other partition
#: types. A table with one of these names in a partition breaks every date-filtered
#: query of every table (`'splay`).
_PARTITION_NAMES = frozenset("date month year int".split())


def reserved_reason(name: str) -> str | None:
    """Why `name` cannot be a table name on a TorQ stack, or None when nothing
    static forbids it (the running processes may still hold the name, which
    attachment preflight reads from the probe)."""
    if name in Q_WORDS:
        return "a q keyword"
    if name in _TORQ_GLOBALS:
        return "a global every TorQ process defines (a table would overwrite it)"
    if name in _PARTITION_NAMES:
        return "a partition name of the hdb (it would break date-filtered queries of every table)"
    return None
