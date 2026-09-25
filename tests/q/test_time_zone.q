// test_time_zone.q - tests for the time and timezone decisions recorded on
// issue #80: the z->p cast bug class, what a window's "day" means, DST in
// windowed backfills, and who converts.
//
// Two of those questions are about a WRONG ANSWER rather than an error, so
// most of what follows asserts that something did NOT happen: that a
// datetime column never reaches a target, that a zoned source with no zone
// table fetches nothing rather than guessing an offset, that an over-fetched
// window does not publish its neighbours' rows, and that windows stay
// exactly one day wide across a DST transition.
//
// The cast tests deliberately CONSTRUCT q datetimes, which
// scripts/gates/check_q_traps.py forbids in src/. That is the one legitimate place
// for the type: a test that proves the trap exists has to build the value
// the trap needs. The checker is scoped to non-test files for exactly this.
//
// Load src/etl/core/status.q, src/etl/core/materialisation.q,
// src/etl/core/worker_runtime.q, src/etl/core/source_contract.q,
// src/etl/sources/demo_deals.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .tztest

/ The zone table TorQ vendors (tzdata-derived: 631 zones, 70381
/ transitions). src/ deliberately does not reach into lib/, so the path is
/ supplied here rather than hard-coded in the contract.
zone_path:"lib/torq/config/tzinfo"

london:`$"Europe/London"

/ Europe/London's 2026 transitions, from the vendored table:
/   spring forward - 2026.03.29, 01:00 UTC. Local 01:00-01:59 never happens.
/   autumn back    - 2026.10.25, 01:00 UTC. Local 01:00-01:59 happens twice.
spring:2026.03.29D01:00:00.000000000
autumn:2026.10.25D01:00:00.000000000

beforeNamespace_zones:{[]
    .qsrc.load_zone_table .tztest.zone_path;
    }

/ Register this suite's own sources, and remove ONLY those. Wiping
/ .qsrc.sources would take the demo source with it and break every test in
/ .ddbftest - the cross-suite leak test_source_contract.q already records.
setUp_sources:{[]
    .tztest.drop_sources[];
    .qsrc.define[`tz_london;
        `source`tablename`target`timecolumn`row_key`columns`types`query`fixture`tz!
        (`tz_london;`ext;`loc;`ts;`ts;`ts`px;"pf";
         {[h;a;b] ()};
         {([] ts:2026.10.25D00:30:00.000000000+0D00:30*til 8; px:8#1.5)};
         .tztest.london)];
    .qsrc.define[`tz_summer;
        `source`tablename`target`timecolumn`row_key`columns`types`query`fixture`tz!
        (`tz_summer;`ext;`loc;`ts;`ts;`ts`px;"pf";
         {[h;a;b] ()};
         {([] ts:enlist 2026.07.15D09:00:00.000000000; px:enlist 1.5)};
         .tztest.london)];
    }

drop_sources:{[]
    mine:`tz_london`tz_summer where `tz_london`tz_summer in key .qsrc.sources;
    if[count mine; .qsrc.sources:mine _ .qsrc.sources];
    }

/ --- the z->p cast bug class -------------------------------------

/ q's datetime (type 15h, "z") is a FLOAT count of days; a timestamp (12h,
/ "p") is a long count of nanoseconds. So z->p is a float-to-long rounding,
/ and this is the measurement that makes it a bug class rather than a
/ curiosity: nearly every real timestamp is changed by it.
/ .
/ `sum` of a boolean vector is an INT, not a long - 999i and 999 do not
/ match under `~`, which is its own silent trap.
test_almost_no_timestamp_survives_a_round_trip_through_a_datetime:{[t]
    ts:2026.09.15D00:00:00.000000000+123456789*til 1000;
    .qunit.assertEquals[sum ts<>"p"$"z"$ts;999i;"999 of 1000 nanosecond-spaced instants come back as a different instant"]};

/ And this is why it survives review: every value anyone would type by hand
/ round-trips perfectly. A full day of whole seconds is unchanged, so a
/ hand-check, a demo and a fixture built from round numbers all agree - and
/ only production timestamps disagree.
test_whole_seconds_do_survive_which_is_why_the_bug_passes_review:{[t]
    ts:2026.09.15D00:00:00.000000000+1000000000*til 86400;
    .qunit.assertEquals[sum ts<>"p"$"z"$ts;0i;"a whole day of whole seconds round-trips exactly, so round-number checks never catch this"]};

test_the_round_trip_error_can_move_an_instant_backwards:{[t]
    ts:2026.09.15D00:00:00.000000000+123456789*til 1000;
    .qunit.assertEquals[0<max ts-"p"$"z"$ts;1b;"the rounding is not one-directional, so a row can move to an EARLIER instant and cross a window boundary"]};

/ `=` and `~` disagree about the same instant held as the two types. Any
/ guard written with one operator therefore reports the opposite of a guard
/ written with the other, and both look correct in review.
test_equals_and_match_disagree_across_the_two_types:{[t]
    p:2026.09.15D10:00:00.000000000;
    .qunit.assertEquals[(("z"$p)=p;("z"$p)~p);(1b;0b);"`=` says same instant, `~` says no match - the two guards cannot both be right"]};

/ The consequence for a retry-safe pipeline: dedupe stops working. The
/ guarantee is retry-safe publication, and a row that no longer matches
/ itself is published again on every retry.
test_a_value_and_its_own_round_trip_are_two_distinct_values:{[t]
    v:2026.09.15D10:00:00.123456789;
    .qunit.assertEquals[count distinct (v;"p"$"z"$v);2;"a dedupe on (key;time) stops recognising the row it already published"]};

/ Filtering a datetime column with timestamp bounds does not error - q
/ promotes and the window comes back plausible. So the type mismatch is not
/ caught by the query; it has to be caught by the contract, which is what
/ the next test asserts.
test_a_datetime_column_filters_without_complaint:{[t]
    tbl:([] ts:"z"$2026.09.11D09:00:00.000000000+1D*til 5; px:5#1.5);
    got:?[tbl;((>=;`ts;2026.09.12D00:00:00.000000000);(<;`ts;2026.09.14D00:00:00.000000000));0b;()];
    .qunit.assertEquals[count got;2;"the filter runs and returns a believable answer, so nothing upstream of the contract will notice the type"]};

/ Guard 1 of 3 against recurrence (register's timecolumn type check is 2,
/ check_q_traps.py is 3): a source whose time column arrives as a datetime
/ fails the contract, on the same code path the fixture goes through.
test_a_datetime_time_column_is_refused_by_the_contract:{[t]
    bad:([] ts:enlist "z"$2026.09.11D09:00:00.000000000; px:enlist 1.5);
    .qunit.assertError[{.qsrc.validate[`tz_summer;x]};bad;"a datetime where a timestamp was declared is a contract breach, not a coercion"]};

/ --- what a window's "day" is ------------------------------------

/ WHAT THE CANONICAL CODE MEANT BY A TRADING DAY IS NOT KNOWABLE HERE, and
/ nothing in this tree has a business-date notion: .qwrt.windows cuts
/ elapsed time, .qmatz composes half-open intervals, and .qdcf counts actual
/ calendar days. These two tests pin the assumption that is therefore in
/ force, so that whoever adds a venue calendar later has to change a failing
/ test rather than a comment.
test_a_window_is_elapsed_time_not_a_calendar_day:{[t]
    w:.qwrt.windows[2026.09.11D17:00:00.000000000;2026.09.14D17:00:00.000000000;1D];
    .qunit.assertEquals[first exec range_from from w;2026.09.11D17:00:00.000000000;"a 1D window starts where the request did - windows are never snapped to a calendar or venue boundary"]};

test_five_days_means_five_windows_not_five_business_days:{[t]
    w:.qwrt.windows[2026.09.11D00:00:00.000000000;2026.09.16D00:00:00.000000000;1D];
    .qunit.assertEquals[count w;5;"the weekend is not skipped: 'the previous five days' is five 24h windows, and any business-day reading would have to be built on top"]};

/ --- DST in windowed backfills -----------------------------------

/ The deliberate pick: windows are cut in UTC, so a daily window is always
/ exactly 24h of elapsed time - never the 23h or 25h a local-calendar day
/ becomes on a transition. Short or long windows would be worse than wrong:
/ coverage would still tile and still report the range complete.
test_windows_across_a_spring_forward_are_all_exactly_one_day:{[t]
    w:.qwrt.windows[2026.03.28D00:00:00.000000000;2026.03.31D00:00:00.000000000;1D];
    .qunit.assertEquals[distinct exec range_to-range_from from w;enlist 1D;"the 23-hour local day does not shorten any window"]};

test_windows_across_an_autumn_transition_still_tile_exactly:{[t]
    w:.qwrt.windows[2026.10.24D00:00:00.000000000;2026.10.27D00:00:00.000000000;1D];
    .qunit.assertEquals[(-1_exec range_to from w)~1_exec range_from from w;1b;"each window ends where the next begins, so the transition introduces neither a gap nor an overlap"]};

/ The reading in the skipped hour denotes no instant at all. Converting it
/ anyway would invent one.
test_a_nonexistent_local_time_is_refused:{[t]
    .qunit.assertError[{.qsrc.local_to_utc[.tztest.london;x]};.tztest.spring+0D00:30;"a local time in the spring-forward gap has no UTC instant, so any answer would be invented"]};

/ The reading in the repeated hour denotes two instants an hour apart.
/ Picking either silently moves the row into a neighbouring backfill window,
/ which the ledger would still record as complete.
test_an_ambiguous_local_time_is_refused:{[t]
    .qunit.assertError[{.qsrc.local_to_utc[.tztest.london;x]};.tztest.autumn+0D00:30;"an hour that happens twice cannot be resolved from the reading alone"]};

test_the_ambiguity_error_names_both_candidate_instants:{[t]
    err:@[{.qsrc.local_to_utc[.tztest.london;x]; ""};.tztest.autumn+0D00:30;{x}];
    .qunit.assertEquals[all err like/: ("*2026.10.25D00:30*";"*2026.10.25D01:30*");1b;"the operator is told which two instants it could be, not merely that it is ambiguous"]};

/ An ambiguity is a DATA failure, so .qwrt must not retry it: the next
/ attempt reads the same unresolvable row and buries the real error under N
/ identical ones.
test_an_ambiguity_is_a_terminal_failure_not_a_retryable_one:{[t]
    err:@[{.qsrc.local_to_utc[.tztest.london;x]; ""};.tztest.autumn+0D00:30;{x}];
    .qunit.assertEquals[(.qwrt.classify err;.qwrt.retryable err);(`data;0b);"retrying an unresolvable local time produces the same unresolvable local time, more slowly"]};

/ The trap that makes bound conversion unsafe, measured rather than
/ asserted: converting each UTC bound with the offset in effect at that
/ bound is NOT monotonic. This window's two bounds collapse onto the same
/ local instant, so the obvious implementation queries an empty range, gets
/ no rows, and records an hour of missing trades as a complete window.
test_naive_bound_conversion_collapses_a_window_to_nothing:{[t]
    lo:.qsrc.utc_to_local[.tztest.london;.tztest.autumn-0D00:30];
    hi:.qsrc.utc_to_local[.tztest.london;.tztest.autumn+0D00:30];
    .qunit.assertEquals[lo~hi;1b;"[00:30;01:30) UTC becomes [01:30;01:30) local - an empty range that returns no rows and never errors"]};

/ And the framework does not do that: it refuses. Zero rows is the answer
/ that would have been silent.
test_the_framework_refuses_that_window_rather_than_returning_no_rows:{[t]
    .qunit.assertError[{.qsrc.fetch_window[`tz_london;0Ni;x 0;x 1]};
        (.tztest.autumn-0D00:30;.tztest.autumn+0D00:30);
        "a window whose rows cannot be placed fails loudly instead of publishing nothing"]};

/ The deliberately wide bounds (bound_padding) pull in up to a day of a
/ neighbour's rows. This is the pair of assertions that the over-fetch costs
/ nothing: a clean window is not failed by an ambiguous row it does not
/ want, and the surplus rows are not published.
test_a_clean_window_is_not_failed_by_a_neighbours_ambiguous_row:{[t]
    got:last .qsrc.fetch_window[`tz_london;0Ni;.tztest.autumn+0D01:00;.tztest.autumn+0D03:00];
    .qunit.assertEquals[count got;4;"the over-fetch reaches the repeated hour, but only rows that could land in THIS window are judged"]};

test_the_over_fetch_does_not_publish_neighbouring_rows:{[t]
    lo:.tztest.autumn+0D01:00;
    hi:.tztest.autumn+0D03:00;
    got:last .qsrc.fetch_window[`tz_london;0Ni;lo;hi];
    .qunit.assertEquals[all (got[`ts]>=lo) and got[`ts]<hi;1b;"every returned row is inside the requested half-open range, in UTC"]};

/ --- who converts -------------------------------------------------

/ The zone is a per-source declaration, because only the source knows it.
test_the_demo_source_declares_its_zone:{[t]
    .qunit.assertEquals[(.qsrc.declaration `demo_deals)`tz;`UTC;"the zone is a claim the source makes, which validate_live can be run against"]};

/ A UTC source takes the identity path: no table, no lookup, no conversion.
/ That matters because it is the path every source in this tree takes, so a
/ regression there would be invisible in a demo.
test_a_utc_source_is_returned_unconverted:{[t]
    got:last .qsrc.fetch_window[`demo_deals;0Ni;2026.09.11D00:00:00.000000000;2026.09.12D00:00:00.000000000];
    .qunit.assertEquals[got`deal_time;enlist 2026.09.11D09:00:00.000000000;"a UTC source's timestamps pass through untouched"]};

/ The framework converts, once, at fetch - not the worker and not the
/ consumer. Local 09:00 on 2026.07.15 is British Summer Time, so it is
/ 08:00 UTC, and that is what a caller asking in UTC must get back.
test_a_zoned_source_is_converted_to_utc_at_fetch:{[t]
    got:last .qsrc.fetch_window[`tz_summer;0Ni;2026.07.15D00:00:00.000000000;2026.07.16D00:00:00.000000000];
    .qunit.assertEquals[got`ts;enlist 2026.07.15D08:00:00.000000000;"local 09:00 BST is 08:00 UTC, and the conversion happens in one place"]};

test_the_window_is_applied_to_the_converted_utc_value:{[t]
    got:last .qsrc.fetch_window[`tz_summer;0Ni;2026.07.15D09:00:00.000000000;2026.07.16D00:00:00.000000000];
    .qunit.assertEquals[count got;0;"a window starting at 09:00 UTC excludes an 08:00 UTC row, even though the source stored it as 09:00"]};

/ No table, no guess. A fixed offset would be right for part of the year and
/ an hour wrong for the rest, and would never error.
test_a_zoned_source_without_a_zone_table_fetches_nothing:{[t]
    saved:.qsrc.zone_table;
    saved_names:.qsrc.zone_names;
    .qsrc.zone_table:0#saved;
    .qsrc.zone_names:`symbol$();
    err:@[{.qsrc.fetch_window[`tz_summer;0Ni;x 0;x 1]};
        (2026.07.15D00:00:00.000000000;2026.07.16D00:00:00.000000000);{(`err;x)}];
    .qsrc.zone_table:saved;
    .qsrc.zone_names:saved_names;
    .qunit.assertEquals[first err;`err;"with no zone table the fetch fails rather than falling back to a fixed offset"]};

test_the_missing_zone_table_error_names_the_loader:{[t]
    saved:.qsrc.zone_table;
    saved_names:.qsrc.zone_names;
    .qsrc.zone_table:0#saved;
    .qsrc.zone_names:`symbol$();
    err:@[{.qsrc.require_zone_table x; ""};.tztest.london;{x}];
    .qsrc.zone_table:saved;
    .qsrc.zone_names:saved_names;
    .qunit.assertEquals[err like "*load_zone_table*";1b;"the error says what to call, which is all the operator needs"]};

test_an_unknown_zone_is_refused_rather_than_treated_as_utc:{[t]
    .qunit.assertError[{.qsrc.require_zone_table x};`$"Mars/Olympus";"a misspelt zone must not silently behave like UTC"]};

/ The conversion is a bijection away from the transitions, and this is the
/ property that makes the two directions trustworthy at all.
test_utc_and_local_round_trip_away_from_a_transition:{[t]
    ts:2026.01.15D12:00:00.000000000 2026.07.15D12:00:00.000000000;
    .qunit.assertEquals[.qsrc.local_to_utc[.tztest.london;.qsrc.utc_to_local[.tztest.london;ts]];ts;"winter and summer instants survive both directions"]};

test_the_offset_differs_between_winter_and_summer:{[t]
    w:2026.01.15D12:00:00.000000000;
    s:2026.07.15D12:00:00.000000000;
    offsets:(.qsrc.utc_to_local[.tztest.london;w]-w;.qsrc.utc_to_local[.tztest.london;s]-s);
    .qunit.assertEquals[offsets;(0D00:00:00.000000000;0D01:00:00.000000000);"a single fixed offset cannot be right for both, which is why there is a table"]};

/ An instant the table does not cover would otherwise produce a null
/ timestamp, which reads downstream as "no data" rather than "the lookup
/ missed".
test_an_uncovered_instant_is_refused_rather_than_nulled:{[t]
    .qunit.assertError[{.qsrc.utc_to_local[.tztest.london;x]};1850.01.01D00:00:00.000000000;"a missed lookup must not come back as a null timestamp"]};

test_a_utc_source_needs_no_zone_table_at_all:{[t]
    saved:.qsrc.zone_table;
    saved_names:.qsrc.zone_names;
    .qsrc.zone_table:0#saved;
    .qsrc.zone_names:`symbol$();
    got:@[{last .qsrc.fetch_window[`demo_deals;0Ni;x 0;x 1]};
        (2026.09.11D00:00:00.000000000;2026.09.12D00:00:00.000000000);{(`err;x)}];
    .qsrc.zone_table:saved;
    .qsrc.zone_names:saved_names;
    .qunit.assertEquals[count got;1;"the UTC path is table-free, so declaring UTC costs nothing operationally"]};

\d .
