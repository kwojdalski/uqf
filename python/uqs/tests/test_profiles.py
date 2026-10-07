"""Named start sets: that each one fits the licence, and each one is fed.

The two properties that make a profile better than a hand-written list of
processes, and neither is visible by reading the list:

* it FITS. Past `LICENCE_CONNECTION_LIMIT` the plant resets the extra
  connection rather than refusing it, so the process wedges in its retry loop
  and still reports `up`. A profile that cannot run must fail here, when it is
  declared, not there.
* it is CLOSED. Every member's inputs have a producer that the same profile
  starts, or come from outside the stack. A profile missing a producer starts
  a subscriber that receives nothing - which also has no symptom.

Parametrised over PROFILES, so a profile added tomorrow is covered today.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uqs.interpreter import Q_IMPL_ENV
from uqs.model import dependencies, profiles
from uqs.model import infra as infra_sets
from uqs.model.pipeline import PipelineKind
from uqs.model.registry import PIPELINES
from uqs.paths import UqsError, paths_for_root
from uqs.runtimes import RUNTIMES
from uqs.stack import procs

ROOT = Path(__file__).resolve().parents[3]

PROCNAMES = {pipeline.procname for pipeline in PIPELINES}
NAMES = sorted(profiles.PROFILES)
FITTING = sorted(set(NAMES) - set(profiles.NEEDS_LARGER_LICENCE))


@pytest.fixture(autouse=True)
def _community_licence(monkeypatch):
    """Every test here reads the budget of the licence anyone can get, not of
    whatever licence the machine running them happens to declare - or
    whichever interpreter it runs."""
    monkeypatch.delenv(profiles.LICENCE_CONNECTIONS_ENV, raising=False)
    monkeypatch.delenv(Q_IMPL_ENV, raising=False)


@pytest.mark.parametrize("name", FITTING)
def test_every_profile_fits_the_licence(name):
    """The whole reason profiles exist: the cap, checked at declaration."""
    assert profiles.over_budget([name]) is None, profiles.over_budget([name])


@pytest.mark.parametrize("name", NAMES)
def test_every_profile_is_closed_over_its_inputs(name):
    """No member subscribes to a table this profile does not also produce.

    Externally-fed tables are exempt and that is the point of the exemption:
    `crypto_book` comes from cryptorust's recorder in the normal case, so
    `marketdata1` is fed without `cryptomock1` - which must NOT be started
    alongside the real thing.
    """
    members = set(profiles.resolve([name])) & PROCNAMES
    producers = dependencies.producers_by_table()
    unfed = []
    for procname in sorted(members):
        for table in dependencies.inputs_by_process().get(procname, ()):
            if table in dependencies.EXTERNAL_PRODUCERS:
                continue
            if not producers.get(table, set()) & members:
                unfed.append(f"{procname} <- {table}")
    assert not unfed, f"profile {name} starts a process nothing in it feeds: {unfed}"


@pytest.mark.parametrize("name", NAMES)
def test_every_leaf_is_a_process_that_exists(name):
    """A typo'd leaf would resolve to a smaller set and look like it worked."""
    unknown = [leaf for leaf in profiles.PROFILES[name] if leaf not in PROCNAMES]
    assert not unknown, f"profile {name} names {unknown}, which no pipeline declares"


@pytest.mark.parametrize("name", NAMES)
def test_every_profile_names_at_least_one_leaf(name):
    """Or is an infrastructure-only profile, which says so in PROFILE_INFRA."""
    assert profiles.PROFILES[name] or name in infra_sets.PROFILE_INFRA, f"profile {name} is empty"


def test_default_resolves_to_exactly_what_start_all_runs_today():
    """`default` DESCRIBES the current start set rather than redefining it.

    So the two cannot drift: a job whose declaration gains `start_with_all` and is
    not reachable from `default`'s leaves fails here, which is the question
    worth asking - is it part of the default, or did the flag go on by
    accident.
    """
    today = {pipeline.procname for pipeline in PIPELINES if pipeline.startwithall == "1"}
    resolved = set(profiles.resolve(["default"])) & PROCNAMES
    assert resolved == today


def test_the_core_infrastructure_is_in_every_profile():
    """A profile that started jobs and no plant would start nothing useful.
    All of CORE_INFRA, unless the profile names a smaller set of its own."""
    for name in NAMES:
        resolved = profiles.resolve([name])
        infra = infra_sets.PROFILE_INFRA.get(name, infra_sets.CORE_INFRA)
        assert set(infra) <= set(resolved)
        assert "stp1" in resolved, "the tickerplant is not optional"


def test_a_profile_s_own_infrastructure_reaches_the_start_list():
    """essential adds reporter1 and tpreplay1, which CORE_INFRA does not have.
    `infrastructure` used to keep only CORE_INFRA's names, so a process a
    profile added beyond it was dropped without a word."""
    for name, infra in infra_sets.PROFILE_INFRA.items():
        assert set(infra) <= set(profiles.infrastructure([name])), name


def test_core_processes_come_first_and_a_profile_s_extras_after():
    started = profiles.infrastructure(["essential"])
    core = tuple(p for p in started if p in infra_sets.CORE_INFRA)
    assert started[: len(core)] == core
    assert started[len(core) :] == ("reporter1", "tpreplay1")


def test_essential_is_the_torq_stack_and_nothing_else():
    resolved = profiles.resolve(["essential"])
    assert set(resolved) == {
        "discovery1",
        "stp1",
        "rdb1",
        "hdb1",
        "wdb1",
        "sort1",
        "sortworker1",
        "gateway1",
        "monitor1",
        "housekeeping1",
        "sctp1",
        "metrics1",
        "reporter1",
        "tpreplay1",
    }
    assert not set(resolved) & PROCNAMES, "no uqf job"


def test_essential_holds_only_the_slots_of_what_it_starts():
    """rdb1, wdb1, sctp1 and metrics1 subscribe; the sort processes, reporter1
    and tpreplay1 do not."""
    assert profiles.plant_slots(profiles.resolve(["essential"])) == 4


def test_composing_essential_with_a_job_profile_is_the_union():
    """`fx` needs all of CORE_INFRA, so `essential,fx` is fx's set plus what
    essential adds beyond the core - hdb2 and sortworker2 come back with fx."""
    combined = profiles.resolve(["essential", "fx"])
    assert set(combined) == set(profiles.resolve(["fx"])) | {"reporter1", "tpreplay1"}


def test_core_infrastructure_leads_the_resolved_list():
    """Order changes nothing for torq.sh; it makes the printed list readable."""
    resolved = profiles.resolve(["fx"])
    assert resolved[: len(infra_sets.CORE_INFRA)] == infra_sets.CORE_INFRA


# ------------------------------------------------- the closure's own behaviour


def test_a_closure_stops_at_an_externally_fed_table():
    """cryptomock1 publishes onto the same tables as cryptorust's recorder,
    and its own declaration says to start it INSTEAD, never as well. A
    mechanical walk over posbook1's inputs reaches it; this one must not."""
    assert "cryptomock1" not in profiles.closure(["posbook1"])
    assert "marketdata1" in profiles.closure(["posbook1"])


def test_the_mock_is_reachable_by_naming_it():
    """Which is the documented workflow, and why `crypto` is its own profile."""
    assert "cryptomock1" in profiles.closure(["cryptomock1"])


def test_a_closure_reaches_through_a_chain():
    """crossarb1 -> superbook1 -> marketdata1 -> the feeds under it."""
    reached = profiles.closure(["crossarb1"])
    assert {"crossarb1", "superbook1", "marketdata1"} <= reached


def test_a_backfill_holds_no_plant_slot():
    """It is bounded: registers with discovery, runs its window, exits."""
    backfills = [
        pipeline.procname for pipeline in PIPELINES if pipeline.kind is PipelineKind.BACKFILL
    ]
    assert backfills, "this test is vacuous without a backfill in the registry"
    assert profiles.plant_slots(backfills) == 0, "a backfill was counted as a plant client"


def test_only_the_listed_vendored_processes_hold_a_slot():
    """Most of CORE_INFRA opens no plant handle - the gateway queries the
    databases, discovery is registered WITH, and stp1 IS the plant. Counting
    them all put a five-slot profile over a fourteen-slot budget."""
    assert profiles.plant_slots(infra_sets.CORE_INFRA) == len(profiles.VENDORED_PLANT_CLIENTS)


# ----------------------------------------------------------------- composition


def test_two_profiles_that_each_fit_can_together_not_fit():
    """The case the refusal exists for, and it is not hypothetical: fx is 13
    of 14 and arbitrage is 10, so either runs and neither runs with the
    other."""
    assert profiles.over_budget(["fx"]) is None
    assert profiles.over_budget(["arbitrage"]) is None
    message = profiles.over_budget(["fx", "arbitrage"])
    assert message is not None
    assert "16" in message and "14" in message


def test_composing_counts_the_union_not_the_sum():
    """fx and arbitrage share fxfeed1, fxorderbookfeed1 and marketdata1, so
    13 + 10 is not 23."""
    assert profiles.plant_slots(profiles.resolve(["fx", "arbitrage"])) < 13 + 10


def test_all_is_every_other_profiles_leaves_but_the_exempt():
    """Derived, so a leaf added to any profile is in `all` without a second edit."""
    expected = {
        leaf
        for name, leaves in profiles.PROFILES.items()
        if name != "all" and name not in profiles.NOT_IN_ALL
        for leaf in leaves
    }
    assert set(profiles.PROFILES["all"]) == expected


def test_all_leaves_the_crypto_mock_to_be_asked_for():
    """cryptomock1 replaces the real recorders; `all` must never start it
    alongside them by default."""
    assert "cryptomock1" not in profiles.resolve(["all"])
    assert "cryptomock1" in profiles.resolve(["all", "crypto"])


def test_a_profile_needing_a_larger_licence_is_refused_on_this_one_and_says_how():
    message = profiles.over_budget(["all"])
    assert message is not None
    assert "19" in message and "14" in message
    assert profiles.LICENCE_CONNECTIONS_ENV in message, "the refusal names the way out"


def test_declaring_a_larger_licence_lets_it_start(monkeypatch):
    monkeypatch.setenv(profiles.LICENCE_CONNECTIONS_ENV, "24")
    assert profiles.allowance() == 22
    assert profiles.over_budget(["all"]) is None


def test_peachq_has_no_connection_cap(monkeypatch):
    """PeachQ carries no connection licence, so nothing is held to sixteen."""
    monkeypatch.setenv(Q_IMPL_ENV, "peachq")
    monkeypatch.setenv("QCMD", "/opt/peachq/q")
    assert profiles.licence_limit() is None
    assert profiles.allowance() is None
    assert profiles.over_budget(["all"]) is None, "the profile KDB-X community refuses"


def test_a_declared_licence_still_wins_on_peachq(monkeypatch):
    monkeypatch.setenv(Q_IMPL_ENV, "peachq")
    monkeypatch.setenv("QCMD", "/opt/peachq/q")
    monkeypatch.setenv(profiles.LICENCE_CONNECTIONS_ENV, "18")
    assert profiles.allowance() == 16


@pytest.mark.parametrize("value", ["lots", "2", "-1"])
def test_a_licence_setting_that_cannot_be_a_budget_is_refused(monkeypatch, value):
    """Refused rather than ignored: falling back to 16 would be the wrong
    budget with no sign of it."""
    monkeypatch.setenv(profiles.LICENCE_CONNECTIONS_ENV, value)
    with pytest.raises(UqsError, match=profiles.LICENCE_CONNECTIONS_ENV):
        profiles.allowance()


def test_no_larger_licence_exemption_is_stale():
    """An exemption for a profile that now fits, or no longer exists, is a
    reason nobody will re-read."""
    for name, reason in profiles.NEEDS_LARGER_LICENCE.items():
        assert name in profiles.PROFILES, f"{name} is exempted and is not a profile"
        assert profiles.over_budget([name]) is not None, f"{name} is exempted and fits"
        assert reason.strip(), f"{name} is exempted with no reason"


def test_an_unknown_profile_is_refused_by_name():
    with pytest.raises(UqsError, match="unknown profile"):
        profiles.resolve(["nope"])


def test_the_refusal_lists_what_is_available():
    with pytest.raises(UqsError, match="arbitrage"):
        profiles.resolve(["nope"])


# ---------------------------------------------------- coverage of the registry


def test_every_standing_process_is_reachable_or_exempt_with_a_reason():
    """A job scaffolded today is startable by name and by no profile, which
    nothing fails to tell you: `start all` and `start <name>` both keep
    working. So the set is closed here instead - a process in neither a
    profile nor UNPROFILED fails, and the exemption carries its reason.

    Backfills are excluded: bounded, no plant connection, no standing set.
    """
    standing = {
        pipeline.procname for pipeline in PIPELINES if pipeline.kind is not PipelineKind.BACKFILL
    }
    reached: set[str] = set()
    for name in NAMES:
        reached |= set(profiles.resolve([name]))
    orphans = standing - reached - set(profiles.UNPROFILED)
    assert not orphans, (
        f"{sorted(orphans)} are in no profile. Add each to one in "
        f"profiles.PROFILES, or to UNPROFILED with the reason it belongs to no "
        f"standing start set."
    )


def test_no_exemption_is_stale():
    """An UNPROFILED entry for a process a profile now reaches, or for one the
    registry no longer declares, is a reason nobody will re-read."""
    procnames = {pipeline.procname for pipeline in PIPELINES}
    reached: set[str] = set()
    for name in NAMES:
        reached |= set(profiles.resolve([name]))
    for procname, reason in profiles.UNPROFILED.items():
        assert procname in procnames, f"{procname} is exempted and does not exist"
        assert procname not in reached, f"{procname} is exempted and a profile reaches it"
        assert reason.strip(), f"{procname} is exempted with no reason"


@pytest.mark.parametrize(
    ("runtime", "profile"),
    [(r.name, p) for r in RUNTIMES.values() for p in r.profiles],
)
def test_every_declared_profile_starts_only_processes_its_runtime_has(runtime, profile):
    """#765: checked when declared, not filtered when listed - a profile that
    names a process its runtime lacks fails here, not an operator's start."""
    have = set(procs.list_process_names(paths_for_root(ROOT, runtime)))
    missing = set(profiles.resolve([profile])) - have
    assert not missing, f"{profile} on {runtime} needs {sorted(missing)}"


def test_every_profile_is_declared_by_some_runtime():
    declared = {p for r in RUNTIMES.values() for p in r.profiles}
    assert set(profiles.PROFILES) <= declared


def test_the_torq_feed_profile_is_essential_with_the_starter_packs_feed():
    assert profiles.resolve(["feed"]) == (*profiles.resolve(["essential"]), "feed1")
