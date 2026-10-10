// test_torq_querypolicy.q - per-table query policies on the gateway's
// data-access API (.qpoltest), scripts/torqcode/gateway/querypolicy.q, and
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

\d .qpoltest

shipped:.checkinputs.readquerypolicy `:scripts/torqconfig/dataaccess/querypolicy.csv
caps:.checkinputs.policyceiling
none:`symbol$()

/ The shipped policy for a table, as an ordinary caller gets it.
policy:{[t] .checkinputs.resolvepolicy[shipped;caps;t;none]}

/ A request for the last half hour of EURUSD's book: within every policy.
book:`tablename`starttime`endtime`instruments!(`mkt_orderbook;2026.01.01D00:00;2026.01.01D00:30;`EURUSD)

/ checkrequest's refusal for a request, or `passed.
refusal:{[p;d] @[{.checkinputs.checkrequest[x;`sym;y];`passed}[p];d;{x}]}

test_every_exposed_table_has_a_policy_for_ordinary_callers:{[t]
    .qunit.assertEquals[asc exec tablename from shipped where null role;`crypto_execution_quality`demo_execution_quality`duckdb_deals`last_value`mkt_orderbook;
        "the five tables the gateway exposes, each with a default row - crypto_execution_quality for the rts subscriber's snapshot (#984)"]};

test_a_table_without_a_policy_is_refused_with_how_to_get_one:{[t]
    .qunit.assertThrows[.checkinputs.resolvepolicy[shipped;caps;;none];`trades;
        "querypolicy: trades has no query policy - it cannot be read through getdata*";
        "no row, no access"]};

test_a_role_exception_widens_the_range_and_the_result:{[t]
    p:.checkinputs.resolvepolicy[shipped;caps;`mkt_orderbook;enlist`quant];
    .qunit.assertEquals[p`maxrange`maxrows;(1D;1000000);"quant's row, not the default's"]};

test_a_role_exception_never_exceeds_the_ceiling:{[t]
    small:`maxrange`maxrows`maxbytes`timeout!(0D02;1000;1000000;0D00:01);
    p:.checkinputs.resolvepolicy[shipped;small;`mkt_orderbook;enlist`quant];
    .qunit.assertEquals[p`maxrange`maxrows`maxbytes`timeout;value small;"each field capped"]};

test_a_role_without_an_exception_gets_the_default_row:{[t]
    .qunit.assertEquals[.checkinputs.resolvepolicy[shipped;caps;`mkt_orderbook;enlist`analyst]`maxrange;0D01;
        "analyst has no mkt_orderbook row of its own"]};

test_a_request_within_the_policy_passes_with_its_timeout_capped:{[t]
    d:.checkinputs.checkrequest[policy`mkt_orderbook;`sym;book,enlist[`timeout]!enlist 0D01];
    .qunit.assertEquals[d`timeout;0D00:00:30;"the policy's timeout, not the caller's hour"];
    .qunit.assertEquals[d`querypolicy;`tablename`maxrows`maxbytes!(`mkt_orderbook;50000;16000000);
        "the limits the merge is checked against"]};

test_a_range_past_the_policy_is_refused_with_how_far:{[t]
    .qunit.assertThrows[.checkinputs.checkrequest[policy`mkt_orderbook;`sym;];@[book;`endtime;:;2026.01.01D02:00];
        "querypolicy: mkt_orderbook allows at most 0D01:00:00.000000000 per request and this one covers 0D02:00:00.000000000*";
        "two hours against one"]};

test_a_date_end_counts_the_whole_day:{[t]
    .qunit.assertEquals[.checkinputs.requestspan[2026.01.01;2026.01.01];1D;"one date is one day, not none"];
    .qunit.assertThrows[.checkinputs.checkrequest[policy`mkt_orderbook;`sym;];book,`starttime`endtime!2#2026.01.01;
        "querypolicy: mkt_orderbook allows at most 0D01:00:00.000000000 per request and this one covers 1D00:00:00.000000000*";
        "a day of book is past the hour"]};

test_a_missing_required_filter_is_refused_naming_it:{[t]
    .qunit.assertThrows[.checkinputs.checkrequest[policy`mkt_orderbook;`sym;];`instruments _ book;
        "querypolicy: mkt_orderbook needs a filter on sym - pass instruments*";"no sym, no rows"]};

test_instruments_or_a_filter_satisfies_the_required_filter:{[t]
    viafilter:(`instruments _ book),enlist[`filters]!enlist enlist[`sym]!enlist(=;`EURUSD);
    .qunit.assertEquals[refusal[policy`mkt_orderbook] each (book;viafilter);`passed`passed;"either names sym"]};

test_an_empty_instruments_list_does_not_count_as_a_filter:{[t]
    .qunit.assertThrows[.checkinputs.checkrequest[policy`mkt_orderbook;`sym;];@[book;`instruments;:;none];
        "querypolicy: mkt_orderbook needs a filter on sym*";"an empty list filters nothing"]};

test_an_aggregate_on_a_raw_only_table_is_refused:{[t]
    agg:book,enlist[`aggregations]!enlist enlist[`count]!enlist`sym;
    .qunit.assertThrows[.checkinputs.checkrequest[policy`mkt_orderbook;`sym;];agg;
        "querypolicy: mkt_orderbook allows raw and this request is aggregate - drop aggregations*";"raw only"]};

test_a_raw_read_on_an_aggregate_only_table_is_refused:{[t]
    p:@[policy`demo_execution_quality;`operations;:;enlist`aggregate];
    d:`tablename`starttime`endtime!(`demo_execution_quality;2026.01.01D00:00;2026.01.01D01:00);
    .qunit.assertThrows[.checkinputs.checkrequest[p;`sym;];d;
        "querypolicy: demo_execution_quality allows aggregate and this request is raw - send aggregations*";"aggregates only"]};

test_only_approved_aggregation_functions_pass_where_a_policy_lists_them:{[t]
    p:@[policy`demo_execution_quality;`functions;:;`avg`count];
    d:`tablename`starttime`endtime`aggregations!(`demo_execution_quality;2026.01.01D00:00;2026.01.01D01:00;`avg`max!`markout_pips`markout_pips);
    .qunit.assertThrows[.checkinputs.checkrequest[p;`sym;];d;
        "querypolicy: demo_execution_quality allows the aggregations avg, count - not max";"max is not on the list"];
    .qunit.assertEquals[refusal[p;@[d;`aggregations;:;enlist[`avg]!enlist`markout_pips]];`passed;"avg is"]};

test_a_parameter_the_policy_cannot_see_into_is_refused:{[t]
    .qunit.assertThrows[.checkinputs.checkprohibited;book,enlist[`postprocessing]!enlist{x};
        "querypolicy: postprocessing not allowed for your role - a postprocessing lambda runs arbitrary q*";"a lambda"];
    .qunit.assertThrows[.checkinputs.checkprohibited;enlist[`sqlquery]!enlist"select from trade";
        "querypolicy: sqlquery not allowed for your role*";"SQL skips the checks"]};

test_a_result_over_the_row_limit_is_refused:{[t]
    .qunit.assertThrows[.dataaccess.checkresponsesize[`tablename`maxrows`maxbytes!(`mkt_orderbook;2;1000000)];([]a:til 3);
        "querypolicy: mkt_orderbook returns at most 2 rows and this result has 3*";"three rows against two"]};

test_a_result_over_the_byte_limit_is_refused:{[t]
    .qunit.assertThrows[.dataaccess.checkresponsesize[`tablename`maxrows`maxbytes!(`mkt_orderbook;1000;100)];([]a:til 100);
        "querypolicy: mkt_orderbook returns at most 100 bytes and this result is*";"800 bytes of longs"]};

test_a_result_within_the_limits_is_returned_unchanged:{[t]
    r:([]a:til 3);
    .qunit.assertEquals[.dataaccess.checkresponsesize[`tablename`maxrows`maxbytes!(`x;3;1000000);r];r;"as merged"]};

test_a_policy_file_allowing_an_unknown_operation_is_refused:{[t]
    f:hsym `$"/tmp/qpoltest_policy_",string[.z.i],".csv";
    f 0:("tablename,role,maxrange,requiredfilters,operations,functions,maxrows,maxbytes,timeout,basis";
        "trades,,0D01:00:00,,raw|everything,,10,10,0D00:00:30,x");
    .qunit.assertThrows[.checkinputs.readquerypolicy;f;
        "querypolicy: trades - operations must be one or more of raw|aggregate";"a typo must not widen access"]};

test_a_policy_file_with_a_missing_limit_is_refused:{[t]
    f:hsym `$"/tmp/qpoltest_policy_",string[.z.i],".csv";
    f 0:("tablename,role,maxrange,requiredfilters,operations,functions,maxrows,maxbytes,timeout,basis";
        "trades,,0D01:00:00,,raw,,,10,0D00:00:30,x");
    .qunit.assertThrows[.checkinputs.readquerypolicy;f;
        "querypolicy: trades - maxrange, maxrows, maxbytes and timeout must all be set and positive";
        "a blank limit is not an unlimited one"]};

test_the_wrapped_checkinputs_holds_an_ordinary_caller_to_the_policy:{[t]
    was:.checkinputs.callerroles;held:.checkinputs.querypolicies;
    .checkinputs.callerroles:{[] `symbol$()};.checkinputs.querypolicies:shipped;
    got:@[.checkinputs.checkinputs;@[book;`endtime;:;2026.01.01D02:00];{x}];
    .checkinputs.callerroles:was;.checkinputs.querypolicies:held;
    .qunit.assertTrue[got like "querypolicy: mkt_orderbook allows at most*";"refused through TorQ's own entry point"]};

test_the_wrapped_checkinputs_lets_a_trusted_role_through:{[t]
    was:.checkinputs.callerroles;held:.checkinputs.querypolicies;
    .checkinputs.callerroles:{[] enlist`admin};.checkinputs.querypolicies:shipped;
    got:.checkinputs.checkinputs @[book;`endtime;:;2026.01.01D02:00];
    .checkinputs.callerroles:was;.checkinputs.querypolicies:held;
    .qunit.assertEquals[got`endtime;2026.01.01D02:00;"admin can run raw q anyway"]};

test_the_wrapped_autojoin_checks_the_merged_result:{[t]
    j:.dataaccess.autojoin enlist[`querypolicy]!enlist`tablename`maxrows`maxbytes!(`x;2;1000000);
    .qunit.assertThrows[j;(([]a:til 2);([]a:til 2));"querypolicy: x returns at most 2 rows and this result has 4*";
        "two backends' rows, merged, then counted"]};

test_access_list_logins_are_read_as_user_and_password:{[t]
    logins:.pm.accesslogins hsym`$"lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt";
    .qunit.assertTrue[any logins~\:(`admin;"admin");"admin's own password, which .pm checks against"]};

test_the_ordinary_users_hold_only_ordinary_roles:{[t]
    u:.pm.policylogins `:scripts/torqconfig/permissions/gateway_users.csv;
    .qunit.assertEquals[exec role from u;`analyst`quant`browser`analyst;"never admin or administrator - rts, the real-time subscriber (#984), is an analyst"]};


/ --- the browser (#889) ----------------------------------------------------

/ A catalog with one unbounded table and one that has a policy row too.
unbounded:`config_change`mkt_orderbook!("a few rows a day";"never used: it has a row")

/ Run f with `name` set to v, then put back whatever was there - or nothing.
with_global:{[name;v;f]
    had:@[{(1b;get x)};name;{[e] (0b;::)}];
    name set v;
    r:@[f;::;{[e] (`failed;e)}];
    $[had 0; name set had 1; ![` sv -1_` vs name;();0b;enlist last ` vs name]];
    if[(2=count r) and `failed~first r; 'last r];
    r}

test_an_unbounded_table_gets_a_row_cap_for_the_browser_alone:{[t]
    u:0!with_global[`.qcat.unbounded;unbounded;{[x] .checkinputs.unboundedpolicies .qpoltest.shipped}];
    .qunit.assertEquals[exec tablename from u;enlist `config_change;"mkt_orderbook keeps its real row"];
    p:.checkinputs.resolvepolicy[shipped,2!u;caps;`config_change;enlist`browser];
    .qunit.assertEquals[p`maxrows`requiredfilters;(.checkinputs.browsermaxrows;`symbol$());
        "a row cap, no required filter"];
    .qunit.assertThrows[.checkinputs.resolvepolicy[shipped,2!u;caps;`config_change;];enlist`analyst;
        "querypolicy: config_change has no query policy for your role*";"another role still has none"]};

test_a_table_with_a_policy_holds_the_browser_to_it:{[t]
    p:with_global[`.qcat.unbounded;unbounded;{[x]
        ps:.qpoltest.shipped,.checkinputs.unboundedpolicies .qpoltest.shipped;
        .checkinputs.resolvepolicy[ps;.qpoltest.caps;`mkt_orderbook;enlist`browser]}];
    .qunit.assertEquals[p`maxrange`requiredfilters;(0D01;enlist`sym);"the file's row, an hour and a sym filter"]};

/ What .uqf.browse asked getdata for.
asked:()

browse_with:{[pol;f]
    `.qpoltest.asked set ();
    with_global[`.checkinputs.querypolicyfor;{[p;t] p}[pol];{[f;x]
        with_global[`.dataaccess.getdata;{[d] `.qpoltest.asked set d; ([] n:enlist 1)};{[f;x] f[]}[f]]}[f]]}

test_browse_turns_the_browsers_filters_into_getdata_filters:{[t]
    browse_with[.checkinputs.policyceiling;
        {[] .uqf.browse[`mkt_orderbook;`sym`px`sym;`eq`gt`ne;(`EURUSD;1.08;`GBPUSD);500;`rdb`hdb]}];
    f:asked`filters;
    .qunit.assertEquals[key f;`sym`px;"one entry per column"];
    .qunit.assertEquals[f`px;enlist (>;1.08);"an operator name, as getdata's function"];
    .qunit.assertEquals[f`sym;((=;`EURUSD);(not;in;enlist `GBPUSD));"ne is not in"];
    .qunit.assertEquals[asked`sublist`procs;(500;`rdb`hdb);"the row cap and the tiers"]};

test_browse_never_asks_for_more_rows_than_the_policy_allows:{[t]
    / #928: a frontend whose max_rows is above the policy's cap is truncated
    / to the cap, not refused by checkresponsesize after the read.
    browse_with[`maxrange`maxrows!(1D;10);{[] .uqf.browse[`config_change;`symbol$();`symbol$();();50000;enlist `rdb]}];
    .qunit.assertEquals[asked`sublist;10;"the policy's maxrows, not the 50000 asked for"]};

test_browse_reads_the_latest_window_the_policy_allows:{[t]
    browse_with[`maxrange`maxrows!(0D01;10);{[] .uqf.browse[`mkt_orderbook;enlist `sym;enlist `eq;enlist `EURUSD;10;enlist `rdb]}];
    .qunit.assertEquals[(asked`endtime)-asked`starttime;0D01;"an hour, ending now"]};

test_browse_takes_its_window_from_the_browsers_time_bounds:{[t]
    w:.uqf.browse_window[`time`time;`ge`lt;(2026.01.01D10:00;2026.01.01D11:00);1D;2026.01.02D00:00];
    .qunit.assertEquals[w;2026.01.01D10:00 2026.01.01D11:00;"the bounds given, not the policy's span"]};

test_browse_refuses_an_unknown_operator:{[t]
    .qunit.assertThrows[{.uqf.browse[`t;enlist `sym;enlist `like;enlist "E*";10;enlist `rdb]};::;
        "uqf.browse: unknown operator like";"named, not run"]};
\d .
