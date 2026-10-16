// test_torq_querypolicy.q - per-table query policies on the gateway's
// data-access API (.qt.qtorq.querypolicy), scripts/torqcode/gateway/querypolicy.q, and
// the access-list reader in scripts/torqcode/handlers/pmusers.q.
//
// TorQ is not loaded here, so the two steps the policy wraps are stood in
// for before the file loads: checkinputs passes a request through, and
// autojoin razes. That is enough to see the wrapping take effect - the
// checks themselves are pure functions of a policy and a request.

.checkinputs.checkinputs:{[dict] dict}
.dataaccess.autojoin:{[options] raze}
\l scripts/torqcode/gateway/querypolicy.q
\l scripts/torqcode/gateway/browse.q
\l scripts/torqcode/handlers/pmusers.q
\l lib/torq/code/dataaccess/extractqueryparams.q


.qt.qtorq.querypolicy.shipped:.checkinputs.readquerypolicy `:scripts/torqconfig/dataaccess/querypolicy.csv
.qt.qtorq.querypolicy.caps:.checkinputs.policyceiling
.qt.qtorq.querypolicy.none:`symbol$()

/ The shipped policy for a table, as an ordinary caller gets it.
.qt.qtorq.querypolicy.policy:{[t] .checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped;.qt.qtorq.querypolicy.caps;t;.qt.qtorq.querypolicy.none]}

/ A request for the last half hour of EURUSD's book: within every policy.
.qt.qtorq.querypolicy.book:`tablename`starttime`endtime`instruments!(`mkt_orderbook;2026.01.01D00:00;2026.01.01D00:30;`EURUSD)

/ checkrequest's refusal for a request, or `passed.
.qt.qtorq.querypolicy.refusal:{[p;d] @[{.checkinputs.checkrequest[x;`sym;y];`passed}[p];d;{x}]}

.qt.qtorq.querypolicy.test_every_exposed_table_has_a_policy_for_ordinary_callers:{[t]
    .qunit.assertEquals[asc exec tablename from .qt.qtorq.querypolicy.shipped where null role;`crypto_execution_quality`demo_execution_quality`duckdb_deals`last_value`mkt_orderbook;
        "the five tables the gateway exposes, each with a default row - crypto_execution_quality for the rts subscriber's snapshot (#984)"]};

.qt.qtorq.querypolicy.test_a_table_without_a_policy_is_refused_with_how_to_get_one:{[t]
    .qunit.assertThrows[.checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped;.qt.qtorq.querypolicy.caps;;.qt.qtorq.querypolicy.none];`trades;
        "querypolicy: trades has no query policy - it cannot be read through getdata*";
        "no row, no access"]};

.qt.qtorq.querypolicy.test_a_role_exception_widens_the_range_and_the_result:{[t]
    p:.checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped;.qt.qtorq.querypolicy.caps;`mkt_orderbook;enlist`quant];
    .qunit.assertEquals[p`maxrange`maxrows;(1D;1000000);"quant's row, not the default's"]};

.qt.qtorq.querypolicy.test_a_role_exception_never_exceeds_the_ceiling:{[t]
    small:`maxrange`maxrows`maxbytes`timeout!(0D02;1000;1000000;0D00:01);
    p:.checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped;small;`mkt_orderbook;enlist`quant];
    .qunit.assertEquals[p`maxrange`maxrows`maxbytes`timeout;value small;"each field capped"]};

.qt.qtorq.querypolicy.test_a_role_without_an_exception_gets_the_default_row:{[t]
    .qunit.assertEquals[.checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped;.qt.qtorq.querypolicy.caps;`mkt_orderbook;enlist`analyst]`maxrange;0D01;
        "analyst has no mkt_orderbook row of its own"]};

.qt.qtorq.querypolicy.test_a_request_within_the_policy_passes_with_its_timeout_capped:{[t]
    d:.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;.qt.qtorq.querypolicy.book,enlist[`timeout]!enlist 0D01];
    .qunit.assertEquals[d`timeout;0D00:00:30;"the policy's timeout, not the caller's hour"];
    .qunit.assertEquals[d`querypolicy;`tablename`maxrows`maxbytes!(`mkt_orderbook;50000;16000000);
        "the limits the merge is checked against"]};

.qt.qtorq.querypolicy.test_a_range_past_the_policy_is_refused_with_how_far:{[t]
    .qunit.assertThrows[.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;];@[.qt.qtorq.querypolicy.book;`endtime;:;2026.01.01D02:00];
        "querypolicy: mkt_orderbook allows at most 0D01:00:00.000000000 per request and this one covers 0D02:00:00.000000000*";
        "two hours against one"]};

.qt.qtorq.querypolicy.test_a_date_end_counts_the_whole_day:{[t]
    .qunit.assertEquals[.checkinputs.requestspan[2026.01.01;2026.01.01];1D;"one date is one day, not none"];
    .qunit.assertThrows[.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;];.qt.qtorq.querypolicy.book,`starttime`endtime!2#2026.01.01;
        "querypolicy: mkt_orderbook allows at most 0D01:00:00.000000000 per request and this one covers 1D00:00:00.000000000*";
        "a day of book is past the hour"]};

.qt.qtorq.querypolicy.test_a_missing_required_filter_is_refused_naming_it:{[t]
    .qunit.assertThrows[.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;];`instruments _ .qt.qtorq.querypolicy.book;
        "querypolicy: mkt_orderbook needs a filter on sym - pass instruments*";"no sym, no rows"]};

.qt.qtorq.querypolicy.test_instruments_or_a_filter_satisfies_the_required_filter:{[t]
    viafilter:(`instruments _ .qt.qtorq.querypolicy.book),enlist[`filters]!enlist enlist[`sym]!enlist(=;`EURUSD);
    .qunit.assertEquals[.qt.qtorq.querypolicy.refusal[.qt.qtorq.querypolicy.policy`mkt_orderbook] each (.qt.qtorq.querypolicy.book;viafilter);`passed`passed;"either names sym"]};

/ #1254: a required filter keeps a request to the values it names, so only =
/ or in satisfies it - as .uqf.browse spells them (browse_pair) and as a
/ getdata caller sends one bare pair.
.qt.qtorq.querypolicy.symfilter:{[pairs] (`instruments _ .qt.qtorq.querypolicy.book),enlist[`filters]!enlist enlist[`sym]!enlist pairs}

.qt.qtorq.querypolicy.test_eq_and_in_on_a_required_column_pass:{[t]
    r:.qt.qtorq.querypolicy.refusal[.qt.qtorq.querypolicy.policy`mkt_orderbook] each .qt.qtorq.querypolicy.symfilter each (
        enlist .uqf.browse_pair[`eq;`EURUSD];enlist .uqf.browse_pair[`in;`EURUSD`GBPUSD];(in;`EURUSD`GBPUSD));
    .qunit.assertEquals[r;`passed`passed`passed;"each keeps sym to the pairs it names"]};

.qt.qtorq.querypolicy.test_ne_gt_and_not_in_on_a_required_column_are_refused_naming_it:{[t]
    f:.qt.qtorq.querypolicy.symfilter each (enlist .uqf.browse_pair[`ne;`ZZZ];enlist .uqf.browse_pair[`gt;`];enlist (not;in;`ZZZ`YYY));
    {[d] .qunit.assertThrows[.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;];d;
        "querypolicy: mkt_orderbook needs sym narrowed with = or in - a not, <, >, within or like condition*";
        "names sym and still reads every pair"]} each f};

.qt.qtorq.querypolicy.test_ne_beside_eq_on_a_required_column_passes:{[t]
    .qunit.assertEquals[.qt.qtorq.querypolicy.refusal[.qt.qtorq.querypolicy.policy`mkt_orderbook] .qt.qtorq.querypolicy.symfilter .uqf.browse_pair'[`eq`ne;`EURUSD`GBPUSD];`passed;
        "the eq already narrows it"]};

/ #1329: the frontend sends a timespan as a one-item timespan vector, the
/ only form kola gives nanoseconds; browse_pair compares with the atom, or
/ with the vector for in, so 1234 ns is 1234 ns.
.qt.qtorq.querypolicy.test_a_timespan_value_is_compared_to_the_nanosecond:{[t]
    s:"n"$1000 1234 1500;
    one:.uqf.browse_pair[`eq;enlist 0D00:00:00.000001234];
    many:.uqf.browse_pair[`in;(enlist 0D00:00:00.000001234;enlist 0D00:00:00.0000015)];
    .qunit.assertEquals[(one[0][s;one 1];many[0][s;many 1]);(010b;011b);"an atom for eq, a vector for in"]};

/ #1371: a timestamp crosses kola the same way, for the same reason - a
/ Python datetime stops at the microsecond - so a `.z.p` stamp is compared
/ to the nanosecond.
.qt.qtorq.querypolicy.test_a_timestamp_value_is_compared_to_the_nanosecond:{[t]
    p:2026.10.14D10:00:00.000001+0 1234 1999;
    one:.uqf.browse_pair[`eq;enlist 2026.10.14D10:00:00.000002234];
    many:.uqf.browse_pair[`in;(enlist 2026.10.14D10:00:00.000002234;enlist 2026.10.14D10:00:00.000002999)];
    .qunit.assertEquals[(one[0][p;one 1];many[0][p;many 1]);(010b;011b);"an atom for eq, a vector for in"]};

.qt.qtorq.querypolicy.test_a_timestamp_bound_sent_as_a_vector_sets_an_atom_window:{[t]
    w:.uqf.browse_window[`time`time;`ge`lt;(enlist 2026.10.14D10:00:00.000001234;enlist 2026.10.14D10:00:00.000001999);1D;2026.10.15D00:00];
    .qunit.assertEquals[w;2026.10.14D10:00:00.000001234 2026.10.14D10:00:00.000001999;"the bounds to the nanosecond, as atoms"]};

/ #1330: kola makes 0W and -0W null, so a column holding one goes as floats,
/ where 0w and -0w survive; every other column is left exactly as it was.
.qt.qtorq.querypolicy.test_an_integral_infinity_goes_as_a_float_one:{[t]
    r:.uqf.browse_infinite ([] l:1 0W -0W 0N; i:"i"$1 0W 2 3; j:1 2 3 4; f:1.5 0w 2 3);
    .qunit.assertEquals[(r`l;r`i;r`j;r`f);(1 0w -0w 0n;1 0w 2 3f;1 2 3 4;1.5 0w 2 3);"0W as 0w, a column without one untouched"]};

.qt.qtorq.querypolicy.test_instruments_satisfies_a_required_column_whatever_else_filters_it:{[t]
    d:.qt.qtorq.querypolicy.book,enlist[`filters]!enlist enlist[`sym]!enlist enlist .uqf.browse_pair[`ne;`ZZZ];
    .qunit.assertEquals[.qt.qtorq.querypolicy.refusal[.qt.qtorq.querypolicy.policy`mkt_orderbook] d;`passed;"instruments names the pair"]};

.qt.qtorq.querypolicy.test_a_comparison_on_a_column_no_policy_requires_is_unaffected:{[t]
    d:.qt.qtorq.querypolicy.book,enlist[`filters]!enlist `sym`bid!(enlist .uqf.browse_pair[`eq;`EURUSD];enlist .uqf.browse_pair[`gt;1.08]);
    p:.qt.qtorq.querypolicy.policy`demo_execution_quality;
    e:`tablename`starttime`endtime`filters!(`demo_execution_quality;2026.01.01D00:00;2026.01.01D01:00;
        enlist[`sym]!enlist enlist .uqf.browse_pair[`ne;`ZZZ]);
    .qunit.assertEquals[(.qt.qtorq.querypolicy.refusal[.qt.qtorq.querypolicy.policy`mkt_orderbook] d;.qt.qtorq.querypolicy.refusal[p] e);`passed`passed;"only required columns must narrow"]};

.qt.qtorq.querypolicy.test_an_empty_instruments_list_does_not_count_as_a_filter:{[t]
    .qunit.assertThrows[.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;];@[.qt.qtorq.querypolicy.book;`instruments;:;.qt.qtorq.querypolicy.none];
        "querypolicy: mkt_orderbook needs a filter on sym*";"an empty list filters nothing"]};

.qt.qtorq.querypolicy.test_an_aggregate_on_a_raw_only_table_is_refused:{[t]
    agg:.qt.qtorq.querypolicy.book,enlist[`aggregations]!enlist enlist[`count]!enlist`sym;
    .qunit.assertThrows[.checkinputs.checkrequest[.qt.qtorq.querypolicy.policy`mkt_orderbook;`sym;];agg;
        "querypolicy: mkt_orderbook allows raw and this request is aggregate - drop aggregations*";"raw only"]};

.qt.qtorq.querypolicy.test_a_raw_read_on_an_aggregate_only_table_is_refused:{[t]
    p:@[.qt.qtorq.querypolicy.policy`demo_execution_quality;`operations;:;enlist`aggregate];
    d:`tablename`starttime`endtime!(`demo_execution_quality;2026.01.01D00:00;2026.01.01D01:00);
    .qunit.assertThrows[.checkinputs.checkrequest[p;`sym;];d;
        "querypolicy: demo_execution_quality allows aggregate and this request is raw - send aggregations*";"aggregates only"]};

.qt.qtorq.querypolicy.test_only_approved_aggregation_functions_pass_where_a_policy_lists_them:{[t]
    p:@[.qt.qtorq.querypolicy.policy`demo_execution_quality;`functions;:;`avg`count];
    d:`tablename`starttime`endtime`aggregations!(`demo_execution_quality;2026.01.01D00:00;2026.01.01D01:00;`avg`max!`markout_pips`markout_pips);
    .qunit.assertThrows[.checkinputs.checkrequest[p;`sym;];d;
        "querypolicy: demo_execution_quality allows the aggregations avg, count - not max";"max is not on the list"];
    .qunit.assertEquals[.qt.qtorq.querypolicy.refusal[p;@[d;`aggregations;:;enlist[`avg]!enlist`markout_pips]];`passed;"avg is"]};

.qt.qtorq.querypolicy.test_a_parameter_the_policy_cannot_see_into_is_refused:{[t]
    .qunit.assertThrows[.checkinputs.checkprohibited;.qt.qtorq.querypolicy.book,enlist[`postprocessing]!enlist{x};
        "querypolicy: postprocessing not allowed for your role - a postprocessing lambda runs arbitrary q*";"a lambda"];
    .qunit.assertThrows[.checkinputs.checkprohibited;enlist[`sqlquery]!enlist"select from trade";
        "querypolicy: sqlquery not allowed for your role*";"SQL skips the checks"]};

.qt.qtorq.querypolicy.test_a_result_over_the_row_limit_is_refused:{[t]
    .qunit.assertThrows[.dataaccess.checkresponsesize[`tablename`maxrows`maxbytes!(`mkt_orderbook;2;1000000)];([]a:til 3);
        "querypolicy: mkt_orderbook returns at most 2 rows and this result has 3*";"three rows against two"]};

.qt.qtorq.querypolicy.test_a_result_over_the_byte_limit_is_refused:{[t]
    .qunit.assertThrows[.dataaccess.checkresponsesize[`tablename`maxrows`maxbytes!(`mkt_orderbook;1000;100)];([]a:til 100);
        "querypolicy: mkt_orderbook returns at most 100 bytes and this result is*";"800 bytes of longs"]};

.qt.qtorq.querypolicy.test_a_result_within_the_limits_is_returned_unchanged:{[t]
    r:([]a:til 3);
    .qunit.assertEquals[.dataaccess.checkresponsesize[`tablename`maxrows`maxbytes!(`x;3;1000000);r];r;"as merged"]};

.qt.qtorq.querypolicy.test_a_policy_file_allowing_an_unknown_operation_is_refused:{[t]
    f:hsym `$"/tmp/qpoltest_policy_",string[.z.i],".csv";
    f 0:("tablename,role,maxrange,requiredfilters,operations,functions,maxrows,maxbytes,timeout,basis";
        "trades,,0D01:00:00,,raw|everything,,10,10,0D00:00:30,x");
    .qunit.assertThrows[.checkinputs.readquerypolicy;f;
        "querypolicy: trades - operations must be one or more of raw|aggregate";"a typo must not widen access"]};

.qt.qtorq.querypolicy.test_a_policy_file_with_a_missing_limit_is_refused:{[t]
    f:hsym `$"/tmp/qpoltest_policy_",string[.z.i],".csv";
    f 0:("tablename,role,maxrange,requiredfilters,operations,functions,maxrows,maxbytes,timeout,basis";
        "trades,,0D01:00:00,,raw,,,10,0D00:00:30,x");
    .qunit.assertThrows[.checkinputs.readquerypolicy;f;
        "querypolicy: trades - maxrange, maxrows, maxbytes and timeout must all be set and positive";
        "a blank limit is not an unlimited one"]};

.qt.qtorq.querypolicy.test_the_wrapped_checkinputs_holds_an_ordinary_caller_to_the_policy:{[t]
    was:.checkinputs.callerroles;held:.checkinputs.querypolicies;
    .checkinputs.callerroles:{[] `symbol$()};.checkinputs.querypolicies:.qt.qtorq.querypolicy.shipped;
    got:@[.checkinputs.checkinputs;@[.qt.qtorq.querypolicy.book;`endtime;:;2026.01.01D02:00];{x}];
    .checkinputs.callerroles:was;.checkinputs.querypolicies:held;
    .qunit.assertTrue[got like "querypolicy: mkt_orderbook allows at most*";"refused through TorQ's own entry point"]};

.qt.qtorq.querypolicy.test_the_wrapped_checkinputs_lets_a_trusted_role_through:{[t]
    was:.checkinputs.callerroles;held:.checkinputs.querypolicies;
    .checkinputs.callerroles:{[] enlist`admin};.checkinputs.querypolicies:.qt.qtorq.querypolicy.shipped;
    got:.checkinputs.checkinputs @[.qt.qtorq.querypolicy.book;`endtime;:;2026.01.01D02:00];
    .checkinputs.callerroles:was;.checkinputs.querypolicies:held;
    .qunit.assertEquals[got`endtime;2026.01.01D02:00;"admin can run raw q anyway"]};

.qt.qtorq.querypolicy.test_the_wrapped_autojoin_checks_the_merged_result:{[t]
    j:.dataaccess.autojoin enlist[`querypolicy]!enlist`tablename`maxrows`maxbytes!(`x;2;1000000);
    .qunit.assertThrows[j;(([]a:til 2);([]a:til 2));"querypolicy: x returns at most 2 rows and this result has 4*";
        "two backends' rows, merged, then counted"]};

.qt.qtorq.querypolicy.test_access_list_logins_are_read_as_user_and_password:{[t]
    logins:.pm.accesslogins hsym`$"lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt";
    .qunit.assertTrue[any logins~\:(`admin;"admin");"admin's own password, which .pm checks against"]};

.qt.qtorq.querypolicy.test_the_ordinary_users_hold_only_ordinary_roles:{[t]
    u:.pm.policylogins `:scripts/torqconfig/permissions/gateway_users.csv;
    .qunit.assertEquals[exec role from u;`analyst`quant`browser`analyst;"never admin or administrator - rts, the real-time subscriber (#984), is an analyst"]};


/ --- the browser (#889) ----------------------------------------------------

/ A catalog with one unbounded table and one that has a policy row too.
.qt.qtorq.querypolicy.unbounded:`config_change`mkt_orderbook!("a few rows a day";"never used: it has a row")

/ Run f with `name` set to v, then put back whatever was there - or nothing.
.qt.qtorq.querypolicy.with_global:{[name;v;f]
    had:@[{(1b;get x)};name;{[e] (0b;::)}];
    name set v;
    r:@[f;::;{[e] (`failed;e)}];
    $[had 0; name set had 1; ![` sv -1_` vs name;();0b;enlist last ` vs name]];
    if[(2=count r) and `failed~first r; 'last r];
    r}

.qt.qtorq.querypolicy.test_an_unbounded_table_gets_a_row_cap_for_the_browser_alone:{[t]
    u:0!.qt.qtorq.querypolicy.with_global[`.qcat.unbounded;.qt.qtorq.querypolicy.unbounded;{[x] .checkinputs.unboundedpolicies .qt.qtorq.querypolicy.shipped}];
    .qunit.assertEquals[exec tablename from u;enlist `config_change;"mkt_orderbook keeps its real row"];
    p:.checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped,2!u;.qt.qtorq.querypolicy.caps;`config_change;enlist`browser];
    .qunit.assertEquals[p`maxrows`requiredfilters;(.checkinputs.browsermaxrows;`symbol$());
        "a row cap, no required filter"];
    .qunit.assertThrows[.checkinputs.resolvepolicy[.qt.qtorq.querypolicy.shipped,2!u;.qt.qtorq.querypolicy.caps;`config_change;];enlist`analyst;
        "querypolicy: config_change has no query policy for your role*";"another role still has none"]};

.qt.qtorq.querypolicy.test_a_table_with_a_policy_holds_the_browser_to_it:{[t]
    p:.qt.qtorq.querypolicy.with_global[`.qcat.unbounded;.qt.qtorq.querypolicy.unbounded;{[x]
        ps:.qt.qtorq.querypolicy.shipped,.checkinputs.unboundedpolicies .qt.qtorq.querypolicy.shipped;
        .checkinputs.resolvepolicy[ps;.qt.qtorq.querypolicy.caps;`mkt_orderbook;enlist`browser]}];
    .qunit.assertEquals[p`maxrange`requiredfilters;(0D01;enlist`sym);"the file's row, an hour and a sym filter"]};

/ What .uqf.browse asked getdata for.
.qt.qtorq.querypolicy.asked:()

.qt.qtorq.querypolicy.browse_with:{[pol;f]
    `.qt.qtorq.querypolicy.asked set ();
    .qt.qtorq.querypolicy.with_global[`.checkinputs.querypolicyfor;{[p;t] p}[pol];{[f;x]
        .qt.qtorq.querypolicy.with_global[`.dataaccess.getdata;{[d] `.qt.qtorq.querypolicy.asked set d; ([] n:enlist 1)};{[f;x] f[]}[f]]}[f]]}

.qt.qtorq.querypolicy.test_browse_turns_the_browsers_filters_into_getdata_filters:{[t]
    .qt.qtorq.querypolicy.browse_with[.checkinputs.policyceiling;
        {[] .uqf.browse[`mkt_orderbook;`sym`px`sym;`eq`gt`ne;(`EURUSD;1.08;`GBPUSD);500;`rdb`hdb]}];
    f:.qt.qtorq.querypolicy.asked`filters;
    .qunit.assertEquals[key f;`sym`px;"one entry per column"];
    .qunit.assertEquals[f`px;enlist (>;1.08);"an operator name, as getdata's function"];
    .qunit.assertEquals[f`sym;((=;`EURUSD);(not;in;enlist `GBPUSD));"ne is not in"];
    .qunit.assertEquals[.qt.qtorq.querypolicy.asked`sublist`procs;(500;`rdb`hdb);"the row cap and the tiers"]};

.qt.qtorq.querypolicy.test_browse_never_asks_for_more_rows_than_the_policy_allows:{[t]
    / #928: a frontend whose max_rows is above the policy's cap is truncated
    / to the cap, not refused by checkresponsesize after the read.
    .qt.qtorq.querypolicy.browse_with[`maxrange`maxrows!(1D;10);{[] .uqf.browse[`config_change;`symbol$();`symbol$();();50000;enlist `rdb]}];
    .qunit.assertEquals[.qt.qtorq.querypolicy.asked`sublist;10;"the policy's maxrows, not the 50000 asked for"]};

.qt.qtorq.querypolicy.test_browse_returns_the_rows_and_the_cap_it_read_under:{[t]
    / #1255: the caller compares what came back against this cap, so a cut
    / the policy made is not mistaken for a complete result.
    r:.qt.qtorq.querypolicy.browse_with[`maxrange`maxrows!(1D;10);{[] .uqf.browse[`config_change;`symbol$();`symbol$();();50000;enlist `rdb]}];
    .qunit.assertEquals[key r;`rows`cap;"rows and cap"];
    .qunit.assertEquals[r`cap;10;"the policy's maxrows, not the 50000 asked for"]};

.qt.qtorq.querypolicy.test_browse_reads_the_latest_window_the_policy_allows:{[t]
    .qt.qtorq.querypolicy.browse_with[`maxrange`maxrows!(0D01;10);{[] .uqf.browse[`mkt_orderbook;enlist `sym;enlist `eq;enlist `EURUSD;10;enlist `rdb]}];
    .qunit.assertEquals[(.qt.qtorq.querypolicy.asked`endtime)-.qt.qtorq.querypolicy.asked`starttime;0D01;"an hour, ending now"]};

.qt.qtorq.querypolicy.test_browse_takes_its_window_from_the_browsers_time_bounds:{[t]
    w:.uqf.browse_window[`time`time;`ge`lt;(2026.01.01D10:00;2026.01.01D11:00);1D;2026.01.02D00:00];
    .qunit.assertEquals[w;2026.01.01D10:00 2026.01.01D11:00;"the bounds given, not the policy's span"]};

.qt.qtorq.querypolicy.test_browse_refuses_an_unknown_operator:{[t]
    .qunit.assertThrows[{.uqf.browse[`t;enlist `sym;enlist `like;enlist "E*";10;enlist `rdb]};::;
        "uqf.browse: unknown operator like";"named, not run"]};

/ #1042: the frontend sends a guid as a char vector (kola cannot encode a
/ Python uuid, and a str would be a symbol). The filter getdata gets must hold
/ a guid, or the read fails with 'type. Applied here to an etl_coverage-shaped
/ table through TorQ's own extractfilters, the step getdata takes.
.qt.qtorq.querypolicy.coverage:([] run_id:"G"$("8c6b8b64-6815-6084-0a3e-178401251b68";"0a3e1784-0125-1b68-8c6b-8b6468156084";"11111111-2222-3333-4444-555555555555"); dataset:`a`b`c)

.qt.qtorq.querypolicy.filtered:{[f] ?[.qt.qtorq.querypolicy.coverage;.eqp.extractfilters[enlist[`filters]!enlist f;.eqp.queryparams]`filters;0b;()]}

.qt.qtorq.querypolicy.test_browse_filters_etl_coverage_on_a_guid_run_id:{[t]
    .qt.qtorq.querypolicy.browse_with[.checkinputs.policyceiling;
        {[] .uqf.browse[`etl_coverage;enlist `run_id;enlist `eq;enlist "8c6b8b64-6815-6084-0a3e-178401251b68";10;enlist `rdb]}];
    .qunit.assertEquals[type last first .qt.qtorq.querypolicy.asked[`filters]`run_id;-2h;"a guid, not the chars it was sent as"];
    r:.qt.qtorq.querypolicy.filtered .qt.qtorq.querypolicy.asked`filters; .qunit.assertEquals[r`dataset;enlist `a;"the one run asked for"]};

.qt.qtorq.querypolicy.test_browse_filters_etl_coverage_on_a_list_of_guid_run_ids:{[t]
    .qt.qtorq.querypolicy.browse_with[.checkinputs.policyceiling;{[] ids:("8c6b8b64-6815-6084-0a3e-178401251b68";"11111111-2222-3333-4444-555555555555");
        .uqf.browse[`etl_coverage;`run_id`run_id;`in`ne;(ids;last ids);10;enlist `rdb]}];
    r:.qt.qtorq.querypolicy.filtered .qt.qtorq.querypolicy.asked`filters; .qunit.assertEquals[r`dataset;enlist `a;"in, then ne, both on guids"]};

.qt.qtorq.querypolicy.test_a_symbol_value_on_a_guid_column_is_the_type_error_1042_fixed:{[t]
    .qunit.assertThrows[{.qt.qtorq.querypolicy.filtered enlist[`run_id]!enlist enlist (=;`$"8c6b8b64-6815-6084-0a3e-178401251b68")};::;
        "type";"why the value must be parsed"]};

/ #1391: the frontend sends an `in` list for a short column as a short
/ vector - KDB-X refuses `1 2h in 1 2j` with 'type, so the longs it sent
/ before failed every such read. browse must hand the vector to getdata as
/ it came, for the filter to match the column by its own type.
.qt.qtorq.querypolicy.lots:([] lots:1 2 3h; dataset:`a`b`c)

.qt.qtorq.querypolicy.test_browse_filters_a_short_column_on_a_short_in_list:{[t]
    .qt.qtorq.querypolicy.browse_with[.checkinputs.policyceiling;{[] .uqf.browse[`lots;`lots`lots;`in`ne;(1 3h;3j);10;enlist `rdb]}];
    f:.qt.qtorq.querypolicy.asked`filters;
    .qunit.assertEquals[first f`lots;(in;1 3h);"the short vector, not widened"];
    r:?[.qt.qtorq.querypolicy.lots;.eqp.extractfilters[enlist[`filters]!enlist f;.eqp.queryparams]`filters;0b;()];
    .qunit.assertEquals[r`dataset;enlist `a;"in on shorts, then ne with a long atom"]};
