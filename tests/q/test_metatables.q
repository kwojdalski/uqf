/ Exact eFX fixtures for metatable collection and replacement.

.qt.qf.util.metatables.setUp:{[t]
    .qt.qf.util.metatables.source::([]date:2026.09.01 2026.09.01 2026.09.01 2026.09.02;
        sym:`EURUSD`EURUSD`GBPUSD`EURUSD;venue:`EBS`EBS`REUTERS`EBS;
        time:2026.09.01D01:00:00.000000000 2026.09.01D02:00:00.000000000 2026.09.01D03:00:00.000000000 2026.09.02D01:00:00.000000000;
        size:10 20 30 40f)};

.qt.qf.util.metatables.spec:{[groups;metrics] .qf.util.definition[`.qt.qf.util.metatables.source;`date;groups;metrics]};
.qt.qf.util.metatables.strip:{[result] delete meta_observed_at, meta_definition from result};

.qt.qf.util.metatables.test_totals_include_empty_slices_and_deduplicate_requests:{[t]
    result:.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();()!()];2026.09.01 2026.09.03 2026.09.01];
    .qunit.assertEquals[.qt.qf.util.metatables.strip[result];([]date:2026.09.01 2026.09.03;rows:3 0j);"explicit totals, including an empty slice"];
    .qunit.assertTrue[all not null result`meta_observed_at;"UTC observations populated"]};

.qt.qf.util.metatables.test_efx_grouping_and_custom_aggregates:{[t]
    metrics:`rows`notional`first_time`last_time!((count;`i);(sum;`size);(min;`time);(max;`time));
    result:.qf.util.collect[.qt.qf.util.metatables.spec[`sym`venue;metrics];enlist 2026.09.01];
    .qunit.assertEquals[result`rows;2 1j;"counts per pair and venue"];
    .qunit.assertEquals[result`notional;30 30f;"custom aggregate"];
    .qunit.assertEquals[result`first_time;.qt.qf.util.metatables.source[`time]0 2;"first timestamp per group"];
    .qunit.assertEquals[result`last_time;.qt.qf.util.metatables.source[`time]1 2;"last timestamp per group"]};

.qt.qf.util.metatables.test_restatement_removes_disappeared_groups_and_preserves_other_partitions:{[t]
    definition:.qt.qf.util.metatables.spec[enlist`sym;()!()];
    stored:.qf.util.collect[definition;2026.09.01 2026.09.02];
    .qt.qf.util.metatables.source::delete from .qt.qf.util.metatables.source where sym=`GBPUSD;
    refreshed:.qf.util.refresh[stored;definition;enlist 2026.09.01];
    .qunit.assertEquals[.qt.qf.util.metatables.strip[refreshed];([]date:2026.09.02 2026.09.01;sym:`EURUSD`EURUSD;rows:1 2j);"replace whole slice, not upsert obsolete groups"];
    .qunit.assertEquals[first refreshed`meta_observed_at;last stored`meta_observed_at;"other slice keeps observation"];
    .qunit.assertEquals[count stored;3j;"caller owns prior immutable value"]};

.qt.qf.util.metatables.test_refresh_to_empty_removes_all_groups:{[t]
    definition:.qt.qf.util.metatables.spec[enlist`sym;()!()];
    stored:.qf.util.collect[definition;enlist 2026.09.01];
    .qt.qf.util.metatables.source::0#.qt.qf.util.metatables.source;
    .qunit.assertEquals[count .qf.util.refresh[stored;definition;enlist 2026.09.01];0j;"empty recomputation clears stale groups"]};

.qt.qf.util.metatables.test_validation_rejects_unbounded_and_wrong_type_requests:{[t]
    definition:.qt.qf.util.metatables.spec[`symbol$();()!()];
    .qunit.assertError[.qf.util.collect[definition;];`date$();"empty request is not all partitions"];
    .qunit.assertError[.qf.util.collect[definition;];enlist 1j;"wrong partition type"];
    .qunit.assertError[.qf.util.collect[definition;];enlist 0Nd;"null partition"];
    .qunit.assertError[.qf.util.collect[.qt.qf.util.metatables.spec[enlist`missing;()!()];];enlist 2026.09.01;"unknown grouping column"]};

.qt.qf.util.metatables.test_invalid_definitions_fail:{[t]
    .qunit.assertError[.qt.qf.util.metatables.spec[;()!()];enlist`date;"partition cannot also be a group"];
    .qunit.assertError[.qt.qf.util.metatables.spec[;()!()];`sym`sym;"duplicate grouping"];
    .qunit.assertError[.qt.qf.util.metatables.spec[`symbol$();];enlist[`date]!enlist(count;`i);"aggregate cannot overwrite partition"];
    .qunit.assertError[.qt.qf.util.metatables.spec[`symbol$();];enlist[`meta_observed_at]!enlist(count;`i);"reserved provenance"];
    .qunit.assertThrows[.qt.qf.util.metatables.spec[`symbol$();];enlist[`meta_definition]!enlist(count;`i);"*meta_definition are reserved";"reserved fingerprint"]};

/ --- definition fingerprint ---------------------------------------

.qt.qf.util.metatables.test_collected_rows_carry_the_definition_fingerprint:{[t]
    definition:.qt.qf.util.metatables.spec[enlist`sym;()!()];
    result:.qf.util.collect[definition;2026.09.01 2026.09.02];
    .qunit.assertEquals[distinct result`meta_definition;enlist .qf.util.fingerprint definition;"every row names its definition"];
    .qunit.assertEquals[.qf.util.fingerprint definition;.qf.util.fingerprint .qt.qf.util.metatables.spec[enlist`sym;()!()];"equal definitions, equal fingerprint"];
    .qunit.assertTrue[(.qf.util.fingerprint definition)<>.qf.util.fingerprint .qt.qf.util.metatables.spec[`sym`venue;()!()];"different grouping, different fingerprint"]};

/ The case the schema check cannot see: same column names and types, different meaning.
.qt.qf.util.metatables.test_refresh_refuses_a_redefined_aggregate_with_the_same_name:{[t]
    stored:.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();enlist[`notional]!enlist(sum;`size)];enlist 2026.09.01];
    redefined:.qt.qf.util.metatables.spec[`symbol$();enlist[`notional]!enlist(max;`size)];
    .qunit.assertEquals[0#.qt.qf.util.metatables.strip[stored];0#.qt.qf.util.metatables.strip[.qf.util.collect[redefined;enlist 2026.09.01]];"schemas really are identical"];
    .qunit.assertThrows[.qf.util.refresh[stored;redefined;];enlist 2026.09.01;"*different definition*";"semantic change needs a rebuild"]};

.qt.qf.util.metatables.test_refresh_refuses_a_table_without_fingerprints:{[t]
    definition:.qt.qf.util.metatables.spec[`symbol$();()!()];
    legacy:delete meta_definition from .qf.util.collect[definition;enlist 2026.09.01];
    .qunit.assertThrows[.qf.util.refresh[legacy;definition;];enlist 2026.09.01;"*no meta_definition*";"pre-fingerprint table must be rebuilt"]};

/ --- reconciliation with the coverage ledger ----------------------

/ An in-memory ledger: these tests read claims, they never persist one.
.qt.qf.util.metatables.ledger:{[rows]
    ![`.;();0b;enlist`etl_coverage];
    .qetl.coverage.init_ledger[];
    if[count rows;`etl_coverage insert flip .qetl.coverage.schema!flip rows];};

.qt.qf.util.metatables.claim:{[from_ts;to_ts;published;superseded]
    (`trade;`;`v1;from_ts;to_ts;published;2026.09.03D00:00:00.000000000;superseded;0Ng)};

.qt.qf.util.metatables.tearDown:{[t] ![`.;();0b;enlist`etl_coverage]};

.qt.qf.util.metatables.reconciled:{[dates]
    observed:.qf.util.collect[.qt.qf.util.metatables.spec[enlist`sym;()!()];dates];
    `date xkey .qf.util.reconcile[observed;dates;`trade;`;`v1]};

.qt.qf.util.metatables.test_reconcile_matches_mismatches_and_unrecorded_dates:{[t]
    current:.qetl.coverage.still_current;
    .qt.qf.util.metatables.ledger[(.qt.qf.util.metatables.claim[2026.09.01D00:00:00.000000000;2026.09.01D12:00:00.000000000;2;current];
        .qt.qf.util.metatables.claim[2026.09.01D12:00:00.000000000;2026.09.02D00:00:00.000000000;1;current];
        .qt.qf.util.metatables.claim[2026.09.02D00:00:00.000000000;2026.09.03D00:00:00.000000000;5;current];
        .qt.qf.util.metatables.claim[2026.09.03D00:00:00.000000000;2026.09.04D00:00:00.000000000;0;current])];
    result:.qt.qf.util.metatables.reconciled[2026.09.01 2026.09.02 2026.09.03 2026.09.04];
    .qunit.assertEquals[exec status from result;`match`mismatch`match`unrecorded;"status per date"];
    .qunit.assertEquals[exec observed from result;3 1 0 0j;"observed rows summed over groups"];
    .qunit.assertEquals[exec published from result;3 5 0 0j;"published rows summed over the day's windows"];
    .qunit.assertEquals[exec windows from result;2 1 1 0j;"windows inside each day"];
    .qunit.assertEquals[exec fully_covered from result;1110b;"coverage of the whole day"]};

.qt.qf.util.metatables.test_reconcile_ignores_superseded_claims:{[t]
    current:.qetl.coverage.still_current;
    .qt.qf.util.metatables.ledger[(.qt.qf.util.metatables.claim[2026.09.01D00:00:00.000000000;2026.09.02D00:00:00.000000000;9;2026.09.03D01:00:00.000000000];
        .qt.qf.util.metatables.claim[2026.09.01D00:00:00.000000000;2026.09.02D00:00:00.000000000;3;current])];
    .qunit.assertEquals[(.qt.qf.util.metatables.reconciled[enlist 2026.09.01])[2026.09.01;`status];`match;"only the current claim counts"]};

.qt.qf.util.metatables.test_reconcile_flags_lost_rows_on_a_date_with_no_observations:{[t]
    .qt.qf.util.metatables.ledger enlist .qt.qf.util.metatables.claim[2026.09.05D00:00:00.000000000;2026.09.06D00:00:00.000000000;7;.qetl.coverage.still_current];
    row:(.qt.qf.util.metatables.reconciled[enlist 2026.09.05])2026.09.05;
    .qunit.assertEquals[row`observed`published;0 7j;"published rows that are gone"];
    .qunit.assertEquals[row`status;`mismatch;"absent date is not skipped"]};

.qt.qf.util.metatables.test_reconcile_refuses_to_attribute_crossing_or_overlapping_windows:{[t]
    current:.qetl.coverage.still_current;
    .qt.qf.util.metatables.ledger[(.qt.qf.util.metatables.claim[2026.09.01D18:00:00.000000000;2026.09.02D06:00:00.000000000;4;current];
        .qt.qf.util.metatables.claim[2026.09.03D00:00:00.000000000;2026.09.03D12:00:00.000000000;2;current];
        .qt.qf.util.metatables.claim[2026.09.03D06:00:00.000000000;2026.09.04D00:00:00.000000000;2;current])];
    result:.qt.qf.util.metatables.reconciled[2026.09.01 2026.09.02 2026.09.03];
    .qunit.assertEquals[exec status from result;3#`ambiguous;"midnight crossing and overlap cannot be split"]};

.qt.qf.util.metatables.test_reconcile_rejects_malformed_requests:{[t]
    .qt.qf.util.metatables.ledger ();
    observed:.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();()!()];enlist 2026.09.01];
    .qunit.assertThrows[.qf.util.reconcile[observed;;`trade;`;`v1];`date$();"*nonempty dates*";"no implicit dates"];
    .qunit.assertThrows[.qf.util.reconcile[observed;;`trade;`;`v1];enlist 0Nd;"*null dates*";"null date"];
    .qunit.assertThrows[.qf.util.reconcile[observed;;`trade;`;`v1];enlist 1j;"*date vector*";"wrong type"];
    .qunit.assertThrows[.qf.util.reconcile[;enlist 2026.09.01;`trade;`;`v1];delete rows from observed;"*rows count*";"rows column required"]};

.qt.qf.util.metatables.test_failed_refresh_leaves_prior_table_unchanged:{[t]
    definition:.qt.qf.util.metatables.spec[`symbol$();()!()];
    stored:.qf.util.collect[definition;enlist 2026.09.01];
    before:stored;
    broken:.qt.qf.util.metatables.spec[`symbol$();enlist[`rows]!enlist(sum;`missing)];
    .qunit.assertError[.qf.util.refresh[stored;broken;];enlist 2026.09.01;"query errors propagate"];
    .qunit.assertEquals[stored;before;"no partial mutation"];
    changed:.qt.qf.util.metatables.spec[`symbol$();enlist[`other]!enlist(count;`i)];
    .qunit.assertError[.qf.util.refresh[stored;changed;];enlist 2026.09.01;"schema change needs a rebuild"]};

.qt.qf.util.metatables.test_raw_columns_are_not_scalar_aggregates:{[t]
    raw:enlist[`sizes]!enlist`size;
    .qunit.assertError[.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();raw];];enlist 2026.09.01;"ungrouped raw column refused"];
    .qunit.assertError[.qf.util.collect[.qt.qf.util.metatables.spec[enlist`sym;raw];];enlist 2026.09.01;"nested group refused"]};

.qt.qf.util.metatables.test_symbol_partitions_are_literal_values:{[t]
    definition:.qf.util.definition[`.qt.qf.util.metatables.source;`venue;`symbol$();()!()];
    result:.qf.util.collect[definition;`EBS`REUTERS];
    .qunit.assertEquals[.qt.qf.util.metatables.strip[result];([]venue:`EBS`REUTERS;rows:3 1j);"logical partition keys are not evaluated as variables"]};

.qt.qf.util.metatables.test_dqe_adapter_preserves_requested_partition_in_payload:{[t]
    result:.dqe.uqf_metatable[`meta_fx_counts;`.qt.qf.util.metatables.source;`date;enlist 2026.09.01;`sym`venue;()!()];
    .qunit.assertEquals[key result;enlist`meta_fx_counts;"DQE resultkeys identifier"];
    .qunit.assertEquals[(result`meta_fx_counts)`date;2#2026.09.01;"source date independent of DQE observation partition"]};

.qt.qf.util.metatables.test_a_metatable_name_must_carry_the_prefix:{[t]
    .qunit.assertEquals[.qf.util.require_name`meta_fx_counts;`meta_fx_counts;"a prefixed name passes unchanged"];
    / Two patterns, not "*...*...": KDB-X's like throws 'nyi past two wildcards.
    e:.qunit.assertThrows[.qf.util.require_name;`fx_counts;"*must start with meta_*";"unprefixed name refused"];
    .qunit.assertTrue[e like "*e.g. `meta_fx_counts";"with the name it should be"];
    .qunit.assertThrows[.qf.util.require_name;`meta_;"*must start with meta_ and name something*";"the prefix alone names nothing"];
    .qunit.assertThrows[.qf.util.require_name;`;"*must start with meta_*";"empty name refused"];
    .qunit.assertThrows[.qf.util.require_name;"meta_x";"*must be a symbol atom*";"a string is not a name"]};

.qt.qf.util.metatables.test_dqe_adapter_refuses_an_unprefixed_name_before_querying:{[t]
    .qunit.assertThrows[.dqe.uqf_metatable[;`.qt.qf.util.metatables.source;`date;enlist 2026.09.01;`sym`venue;()!()];`fx_counts;"*must start with meta_*";"adapter holds DQE results to the convention"]};

.qt.qf.util.metatables.test_profile_counts_ranges_nulls_and_violations:{[t]
    .qt.qf.util.metatables.source[`size]:10 0n -5 40f;
    .qt.qf.util.metatables.source::update time:0Np from .qt.qf.util.metatables.source where i=1;
    metrics:.qf.util.profile[enlist`time;`time`size;enlist[`negative_size]!enlist(<;`size;0f)];
    result:.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();metrics];2026.09.01 2026.09.03];
    .qunit.assertEquals[result`rows;3 0j;"profile denominator"];
    .qunit.assertEquals[result`null_size;1 0j;"null counts"];
    .qunit.assertEquals[result`null_time;1 0j;"timestamp null counts"];
    .qunit.assertEquals[first result`min_time;first .qt.qf.util.metatables.source`time;"minimum skips nulls"];
    .qunit.assertEquals[first result`max_time;.qt.qf.util.metatables.source[`time]2;"maximum time"];
    .qunit.assertTrue[all null (last result`min_time;last result`max_time);"empty range is null, not infinity"];
    / q comparisons include numeric nulls: the configured rule controls that policy.
    .qunit.assertEquals[result`bad_negative_size;2 0j;"q null is below zero, explicitly counted by this rule"]};

.qt.qf.util.metatables.test_profile_grouped_all_null_bounds:{[t]
    .qt.qf.util.metatables.source[`time]:4#0Np;
    metrics:.qf.util.profile[enlist`time;enlist`time;()!()];
    result:.qf.util.collect[.qt.qf.util.metatables.spec[enlist`sym;metrics];enlist 2026.09.01];
    .qunit.assertEquals[result`null_time;2 1j;"per-pair null counts"];
    .qunit.assertTrue[all null result`min_time;"all-null minimum"];
    .qunit.assertTrue[all null result`max_time;"all-null maximum"]};

.qt.qf.util.metatables.test_profile_rejects_wrong_types_and_rules:{[t]
    .qunit.assertError[.qf.util.profile[;`symbol$();()!()];`time`time;"duplicate temporal columns"];
    metrics:.qf.util.profile[enlist`size;`symbol$();()!()];
    .qunit.assertError[.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();metrics];];enlist 2026.09.01;"range must be temporal"];
    metrics:.qf.util.profile[`symbol$();`symbol$();enlist[`broken]!enlist`size];
    .qunit.assertError[.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();metrics];];enlist 2026.09.01;"rule must be boolean"];
    metrics:.qf.util.profile[`symbol$();`symbol$();enlist[`short]!enlist(enlist;1b)];
    .qunit.assertError[.qf.util.collect[.qt.qf.util.metatables.spec[`symbol$();metrics];];enlist 2026.09.01;"one boolean per row required"]};
