/ Exact eFX fixtures for metatable collection and replacement.
\d .metatest

setUp:{[t]
    source::([]date:2026.09.01 2026.09.01 2026.09.01 2026.09.02;
        sym:`EURUSD`EURUSD`GBPUSD`EURUSD;venue:`EBS`EBS`REUTERS`EBS;
        time:2026.09.01D01:00:00.000000000 2026.09.01D02:00:00.000000000 2026.09.01D03:00:00.000000000 2026.09.02D01:00:00.000000000;
        size:10 20 30 40f)};

spec:{[groups;metrics] .qmeta.definition[`.metatest.source;`date;groups;metrics]};
strip:{[result] delete meta_observed_at from result};

test_totals_include_empty_slices_and_deduplicate_requests:{[t]
    result:.qmeta.collect[spec[`symbol$();()!()];2026.09.01 2026.09.03 2026.09.01];
    .qunit.assertEquals[strip result;([]date:2026.09.01 2026.09.03;rows:3 0j);"explicit totals, including an empty slice"];
    .qunit.assertTrue[all not null result`meta_observed_at;"UTC observations populated"]};

test_efx_grouping_and_custom_aggregates:{[t]
    metrics:`rows`notional`first_time`last_time!((count;`i);(sum;`size);(min;`time);(max;`time));
    result:.qmeta.collect[spec[`sym`venue;metrics];enlist 2026.09.01];
    .qunit.assertEquals[result`rows;2 1j;"counts per pair and venue"];
    .qunit.assertEquals[result`notional;30 30f;"custom aggregate"];
    .qunit.assertEquals[result`first_time;source[`time]0 2;"first timestamp per group"];
    .qunit.assertEquals[result`last_time;source[`time]1 2;"last timestamp per group"]};

test_restatement_removes_disappeared_groups_and_preserves_other_partitions:{[t]
    definition:spec[enlist`sym;()!()];
    stored:.qmeta.collect[definition;2026.09.01 2026.09.02];
    source::delete from source where sym=`GBPUSD;
    refreshed:.qmeta.refresh[stored;definition;enlist 2026.09.01];
    .qunit.assertEquals[strip refreshed;([]date:2026.09.02 2026.09.01;sym:`EURUSD`EURUSD;rows:1 2j);"replace whole slice, not upsert obsolete groups"];
    .qunit.assertEquals[first refreshed`meta_observed_at;last stored`meta_observed_at;"other slice keeps observation"];
    .qunit.assertEquals[count stored;3j;"caller owns prior immutable value"]};

test_refresh_to_empty_removes_all_groups:{[t]
    definition:spec[enlist`sym;()!()];
    stored:.qmeta.collect[definition;enlist 2026.09.01];
    source::0#source;
    .qunit.assertEquals[count .qmeta.refresh[stored;definition;enlist 2026.09.01];0j;"empty recomputation clears stale groups"]};

test_validation_rejects_unbounded_and_wrong_type_requests:{[t]
    definition:spec[`symbol$();()!()];
    .qunit.assertError[.qmeta.collect[definition;];`date$();"empty request is not all partitions"];
    .qunit.assertError[.qmeta.collect[definition;];enlist 1j;"wrong partition type"];
    .qunit.assertError[.qmeta.collect[definition;];enlist 0Nd;"null partition"];
    .qunit.assertError[.qmeta.collect[spec[enlist`missing;()!()];];enlist 2026.09.01;"unknown grouping column"]};

test_invalid_definitions_fail:{[t]
    .qunit.assertError[spec[;()!()];enlist`date;"partition cannot also be a group"];
    .qunit.assertError[spec[;()!()];`sym`sym;"duplicate grouping"];
    .qunit.assertError[spec[`symbol$();];enlist[`date]!enlist(count;`i);"aggregate cannot overwrite partition"];
    .qunit.assertError[spec[`symbol$();];enlist[`meta_observed_at]!enlist(count;`i);"reserved provenance"]};

test_failed_refresh_leaves_prior_table_unchanged:{[t]
    definition:spec[`symbol$();()!()];
    stored:.qmeta.collect[definition;enlist 2026.09.01];
    before:stored;
    broken:spec[`symbol$();enlist[`rows]!enlist(sum;`missing)];
    .qunit.assertError[.qmeta.refresh[stored;broken;];enlist 2026.09.01;"query errors propagate"];
    .qunit.assertEquals[stored;before;"no partial mutation"];
    changed:spec[`symbol$();enlist[`other]!enlist(count;`i)];
    .qunit.assertError[.qmeta.refresh[stored;changed;];enlist 2026.09.01;"schema change needs a rebuild"]};

test_raw_columns_are_not_scalar_aggregates:{[t]
    raw:enlist[`sizes]!enlist`size;
    .qunit.assertError[.qmeta.collect[spec[`symbol$();raw];];enlist 2026.09.01;"ungrouped raw column refused"];
    .qunit.assertError[.qmeta.collect[spec[enlist`sym;raw];];enlist 2026.09.01;"nested group refused"]};

test_symbol_partitions_are_literal_values:{[t]
    definition:.qmeta.definition[`.metatest.source;`venue;`symbol$();()!()];
    result:.qmeta.collect[definition;`EBS`REUTERS];
    .qunit.assertEquals[strip result;([]venue:`EBS`REUTERS;rows:3 1j);"logical partition keys are not evaluated as variables"]};

test_dqe_adapter_preserves_requested_partition_in_payload:{[t]
    result:.dqe.uqf_metatable[`fx_counts;`.metatest.source;`date;enlist 2026.09.01;`sym`venue;()!()];
    .qunit.assertEquals[key result;enlist`fx_counts;"DQE resultkeys identifier"];
    .qunit.assertEquals[(result`fx_counts)`date;2#2026.09.01;"source date independent of DQE observation partition"]};

test_profile_counts_ranges_nulls_and_violations:{[t]
    source[`size]:10 0n -5 40f;
    source::update time:0Np from source where i=1;
    metrics:.qmeta.profile[enlist`time;`time`size;enlist[`negative_size]!enlist(<;`size;0f)];
    result:.qmeta.collect[spec[`symbol$();metrics];2026.09.01 2026.09.03];
    .qunit.assertEquals[result`rows;3 0j;"profile denominator"];
    .qunit.assertEquals[result`null_size;1 0j;"null counts"];
    .qunit.assertEquals[result`null_time;1 0j;"timestamp null counts"];
    .qunit.assertEquals[first result`min_time;first source`time;"minimum skips nulls"];
    .qunit.assertEquals[first result`max_time;source[`time]2;"maximum time"];
    .qunit.assertTrue[all null (last result`min_time;last result`max_time);"empty range is null, not infinity"];
    / q comparisons include numeric nulls: the configured rule controls that policy.
    .qunit.assertEquals[result`bad_negative_size;2 0j;"q null is below zero, explicitly counted by this rule"]};

test_profile_grouped_all_null_bounds:{[t]
    source[`time]:4#0Np;
    metrics:.qmeta.profile[enlist`time;enlist`time;()!()];
    result:.qmeta.collect[spec[enlist`sym;metrics];enlist 2026.09.01];
    .qunit.assertEquals[result`null_time;2 1j;"per-pair null counts"];
    .qunit.assertTrue[all null result`min_time;"all-null minimum"];
    .qunit.assertTrue[all null result`max_time;"all-null maximum"]};

test_profile_rejects_wrong_types_and_rules:{[t]
    .qunit.assertError[.qmeta.profile[;`symbol$();()!()];`time`time;"duplicate temporal columns"];
    metrics:.qmeta.profile[enlist`size;`symbol$();()!()];
    .qunit.assertError[.qmeta.collect[spec[`symbol$();metrics];];enlist 2026.09.01;"range must be temporal"];
    metrics:.qmeta.profile[`symbol$();`symbol$();enlist[`broken]!enlist`size];
    .qunit.assertError[.qmeta.collect[spec[`symbol$();metrics];];enlist 2026.09.01;"rule must be boolean"];
    metrics:.qmeta.profile[`symbol$();`symbol$();enlist[`short]!enlist(enlist;1b)];
    .qunit.assertError[.qmeta.collect[spec[`symbol$();metrics];];enlist 2026.09.01;"one boolean per row required"]};

\d .
