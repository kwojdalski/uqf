/ test_reference_policy.q - the markout reference chosen per instant from a rule table
/ (src/etl/core/reference_policy.q, .qetl.reference.policy, #2996).
/ .
/ What is held here, against the issue's acceptance list:
/ .
/   selection                 the highest-priority matching rule, by UTC time of day, weekday and instrument
/   boundaries                active_from inclusive, active_to exclusive: an instant on an edge selects one rule
/   overlap                   resolved by priority; a tie at the top is refused when the policy is declared
/   coverage                  a gap is refused at declaration, proved over the rules' boundaries; an instrument
/                             no rule names is refused at run time as unmatched_reference_rule
/   source filtering          a source outside the selected rule never sets the price
/   clocks, staleness         the rule's books are matched on the target's clock, within the rule's max_age
/   t0 / t1                   one execution's two instants select their rules independently
/   legacy                    a policy of variant #1 alone prices as variant #1 does, to the bit
/   configuration, lineage    a policy is its own config_id, and a scored row's rules re-derive from it
/ .
/ The books are four generic liquidity providers quoting EURUSD at prices exact in binary, so every
/ expected price below is worked by hand:
/ .
/   LP_A 1.0   / 2.0      LP_B 1.25 / 1.75      LP_C 1.5 / 1.625 (the best bid and ask)      LP_D 1.125 / 1.875

.qt.qetl.reference.policy.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-reference-policy"];
    system"mkdir -p build/test-status-reference-policy";
    }

.qt.qetl.reference.policy.setUp_fresh:{[] .qt.support.reset_snapshots[];}

/ The issue's example policy, over flat-top variants: Asia on LP_A and LP_B (best), London on LP_A and LP_C
/ (median), the US on LP_B and LP_D (best), and the fallback of variant #1 over every source.
.qt.qetl.reference.policy.rules:{[]
    ([] rule_id:`asia_open`london_open`us_open`fallback; priority:10 20 30 0;
        active_from:00:00 08:00 12:00 00:00; active_to:08:00 12:00 21:00 24:00;
        weekdays:(`weekdays;`weekdays;`weekdays;`all);
        instrument_selector:(`EURUSD;`EURUSD;`EURUSD;`all);
        sources:(`LP_A`LP_B;`LP_A`LP_C;`LP_B`LP_D;`all);
        reference_variant:`best_top`median_top`best_top`best_top;
        max_age:4#0Nn;
        parameters:4#enlist ())}

/ One rule row, for a table built a row at a time.
.qt.qetl.reference.policy.rule:{[rid;prio;from;to;days;ins;src;variant]
    ([] rule_id:enlist rid; priority:enlist prio; active_from:enlist from; active_to:enlist to; weekdays:enlist days;
        instrument_selector:enlist ins; sources:enlist src; reference_variant:enlist variant; max_age:enlist 0Nn; parameters:enlist ())}

/ Take test policies (and variants) back out of the registries.
.qt.qetl.reference.policy.forget:{[policies;variants]
    `.qetl.reference.policies set policies _ .qetl.reference.policies;
    `.qetl.reference.variants set variants _ .qetl.reference.variants;}

/ What a declaration refuses with, or what it returns when it does not refuse.
.qt.qetl.reference.policy.refusal:{[name;rules] @[.qetl.reference.policy.register[name;];rules;{[e] e}]}

/ The four providers' EURUSD books `age` before each of `tms`, on `clk` (one clock per source, in LP order).
.qt.qetl.reference.policy.books:{[tms;age;clks]
    one:{[age;clks;tm] ([] sym:4#`EURUSD; source:`LP_A`LP_B`LP_C`LP_D; source_time:4#tm-age;
        bid:1.0 1.25 1.5 1.125; ask:2.0 1.75 1.625 1.875; clock:clks)}[age;clks];
    .qetl.mapped.arrange raze one each tms}

/ The price under a policy of EURUSD targets on the venue clock.
.qt.qetl.reference.policy.priced:{[name;tob;max_age;tms]
    n:count tms;
    sy:n#`EURUSD;
    .qetl.reference.policy.at[name;.qetl.reference.policy.select[name;sy;tms];tob;max_age;sy;tms;n#`venue]}

/ --- selection -----------------------------------------------------------------------------------------

.qt.qetl.reference.policy.test_the_highest_priority_matching_rule_is_selected_by_time_weekday_and_instrument:{[t]
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    / 2026.09.17 is a Thursday, 2026.09.19 a Saturday
    tm:2026.09.17D03:00:00 2026.09.17D09:00:00 2026.09.17D13:00:00 2026.09.17D22:00:00 2026.09.19D09:00:00 2026.09.17D09:00:00;
    r:.qetl.reference.policy.select[p;`EURUSD`EURUSD`EURUSD`EURUSD`EURUSD`GBPUSD;tm];
    none:.qetl.reference.policy.select[p;``EURUSD;0N 2026.09.17D03:00:00];
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertEquals[r;`asia_open`london_open`us_open`fallback`fallback`fallback;
        "Thursday's three sessions, the evening, a Saturday morning, and another instrument at London's time"];
    .qunit.assertEquals[none;``asia_open;"a target with no instrument has no rule, and is not an error"]};

.qt.qetl.reference.policy.test_an_instant_on_a_window_edge_selects_exactly_one_rule:{[t]
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    tm:2026.09.17D07:59:59.999999999 2026.09.17D08:00:00 2026.09.17D11:59:59.999999999 2026.09.17D12:00:00,
        2026.09.17D20:59:59.999999999 2026.09.17D21:00:00 2026.09.20D23:59:59.999999999 2026.09.21D00:00:00;
    r:.qetl.reference.policy.select[p;(count tm)#`EURUSD;tm];
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertEquals[r;`asia_open`london_open`london_open`us_open`us_open`fallback`fallback`asia_open;
        "active_from is inclusive and active_to exclusive, across 08:00, 12:00, 21:00 and Sunday into Monday"]};

.qt.qetl.reference.policy.test_an_overlap_is_resolved_by_priority:{[t]
    fix:.qt.qetl.reference.policy.rule[`london_fix;25;10:00;10:05;`weekdays;`EURUSD;enlist `LP_A;`best_top];
    p:.qetl.reference.policy.register[`with_fix;.qt.qetl.reference.policy.rules[],fix];
    r:.qetl.reference.policy.select[p;3#`EURUSD;2026.09.17D09:59:59 2026.09.17D10:02:00 2026.09.17D10:05:00];
    .qt.qetl.reference.policy.forget[enlist `with_fix;`symbol$()];
    .qunit.assertEquals[r;`london_open`london_fix`london_open;"the fix (25) wins inside its five minutes over London (20) and the fallback (0)"]};

.qt.qetl.reference.policy.test_selection_is_pure_and_the_declared_order_of_rules_changes_nothing:{[t]
    a:.qetl.reference.policy.canonical[`x;.qt.qetl.reference.policy.rules[]];
    b:.qetl.reference.policy.canonical[`x;reverse .qt.qetl.reference.policy.rules[]];
    .qunit.assertEquals[a;b;"the canonical rules do not depend on the order they were declared in"];
    .qunit.assertEquals[a`rule_id;`asia_open`fallback`london_open`us_open;"they are sorted by rule_id"];
    .qunit.assertEquals[first a`weekdays;`mon`tue`wed`thu`fri;"`weekdays is Monday to Friday"];
    tm:2026.09.17D03:00:00 2026.09.19D03:00:00;
    .qunit.assertEquals[.qetl.reference.policy.match[`x;a;`EURUSD`EURUSD;tm];`asia_open`fallback;"match reads the rules it is given, and no registry"]};

/ --- refused at declaration --------------------------------------------------------------------------------

.qt.qetl.reference.policy.test_two_matching_rules_at_the_top_priority_are_refused_as_ambiguous:{[t]
    extra:.qt.qetl.reference.policy.rule[`extra;20;11:00;13:00;`weekdays;`EURUSD;`all;`best_top];
    e:.qt.qetl.reference.policy.refusal[`tie;.qt.qetl.reference.policy.rules[],extra];
    .qunit.assertEquals[e;"reference_policy: tie: rules extra, london_open tie at priority 20 for EURUSD on mon 11:00-12:00";
        "the first tied cell is named; from 12:00 the US (30) outranks both, so that hour is not a tie"];
    .qunit.assertFalse[.qetl.reference.policy.is `tie;"and nothing is registered"]};

.qt.qetl.reference.policy.test_an_instant_no_rule_covers_is_refused_at_declaration:{[t]
    nofb:.qt.qetl.reference.policy.refusal[`nofb;-1_.qt.qetl.reference.policy.rules[]];
    .qunit.assertEquals[nofb;"reference_policy: nofb: no rule covers EURUSD on sat 00:00-08:00";"without the fallback, the weekend is a gap"];
    eur:.qt.qetl.reference.policy.rule[`eur;5;00:00;24:00;`all;`EURUSD;`all;`best_top];
    wk:.qt.qetl.reference.policy.rule[`others;0;00:00;24:00;`weekdays;`all;`all;`best_top];
    other:.qt.qetl.reference.policy.refusal[`other;eur,wk];
    .qunit.assertEquals[other;"reference_policy: other: no rule covers any other instrument on sat 00:00-24:00";
        "a rule for `all instruments speaks for every instrument, so its gap is one too"];
    late:.qt.qetl.reference.policy.rule[`late;0;00:00;23:59:59;`all;`all;`all;`best_top];
    sec:.qt.qetl.reference.policy.refusal[`sec;late];
    .qunit.assertEquals[sec;"reference_policy: sec: no rule covers any other instrument on sat 23:59:59-24:00";
        "the last second is found: the proof walks the declared boundaries, it does not sample"];
    .qunit.assertFalse[any .qetl.reference.policy.is each `nofb`other`sec;"and nothing is registered"]};

.qt.qetl.reference.policy.test_a_malformed_rule_is_refused_by_name:{[t]
    rs:.qt.qetl.reference.policy.rules[];
    bad:.qt.qetl.reference.policy.refusal[`bad;];
    .qunit.assertEquals[bad update weekdays:(`weekdays;`weekdays;`funday;`all) from rs;
        "reference_policy: bad.us_open: unknown weekday funday (mon tue wed thu fri sat sun, `weekdays or `all)";"an unknown weekday"];
    .qunit.assertTrue[(bad update reference_variant:`nope from rs where rule_id=`us_open) like "reference_policy: bad.us_open: reference_variant nope is not a registered variant*";
        "an unknown variant"];
    .qunit.assertEquals[bad update active_from:12:00 from rs where rule_id=`london_open;
        "reference_policy: bad.london_open: active_from 12:00 must be before active_to 12:00 (a window crossing midnight is two rules)";"from not before to"];
    .qunit.assertEquals[bad update active_to:25:00 from rs where rule_id=`fallback;
        "reference_policy: bad.fallback: active_to must lie between 00:00 and 24:00";"a time past the end of the day"];
    .qunit.assertEquals[bad update rule_id:`asia_open from rs where rule_id=`london_open;"reference_policy: bad: duplicate rule_id asia_open";"a duplicate rule_id"];
    .qunit.assertEquals[bad update parameters:(();();();enlist[`qty]!enlist 1f) from rs;
        "reference: bad.fallback's transform top has no parameter qty";"a parameter the variant's transform lacks, refused by the variant registry's own rule"];
    .qunit.assertEquals[bad update sources:(`all`LP_A;`LP_A`LP_C;`LP_B`LP_D;`all) from rs;
        "reference_policy: bad.asia_open: sources names `all inside a list: write the atom `all to mean every one";"`all is the atom, intentionally"];
    .qunit.assertEquals[bad update max_age:-0D00:00:01 from rs where rule_id=`us_open;
        "reference_policy: bad.us_open: max_age must be a positive timespan, or 0Nn for the variant's own staleness";"a negative max_age"];
    .qunit.assertFalse[.qetl.reference.policy.is `bad;"and nothing is registered"]};

.qt.qetl.reference.policy.test_a_policy_that_is_not_a_rule_table_or_clashes_with_its_variants_is_refused:{[t]
    rs:.qt.qetl.reference.policy.rules[];
    bad:.qt.qetl.reference.policy.refusal[`bad;];
    .qunit.assertTrue[(bad `rule_id`priority#rs) like "reference_policy: bad: rules must have exactly the columns*";"a table missing columns"];
    .qunit.assertEquals[.qt.qetl.reference.policy.refusal[`best_top;rs];"reference_policy: best_top is a registered variant - a spec's reference names one or the other";
        "a policy cannot take a variant's name"];
    .qetl.reference.register[`lp_ab;`venues`stale`offset`transform`combine!(`LP_A`LP_B;`spec;0D;`top;`best)];
    narrow:.qt.qetl.reference.policy.refusal[`bad;update reference_variant:`lp_ab from rs where rule_id=`london_open];
    .qt.qetl.reference.policy.forget[`symbol$();enlist `lp_ab];
    .qunit.assertEquals[narrow;"reference_policy: bad.london_open: sources LP_C are not among variant lp_ab's venues";"a source the variant itself excludes"];
    .qunit.assertFalse[.qetl.reference.policy.is `bad;"and nothing is registered"]};

/ --- refused at run time -------------------------------------------------------------------------------------

.qt.qetl.reference.policy.test_an_instrument_no_rule_names_is_refused_at_run_time_as_unmatched:{[t]
    p:.qetl.reference.policy.register[`eur_only;.qt.qetl.reference.policy.rule[`eur;0;00:00;24:00;`all;`EURUSD;`all;`best_top]];
    e:@[.qetl.reference.policy.select[p;;enlist 2026.09.17D09:00:00];enlist `GBPUSD;{[e] e}];
    dom:.qpipe.transform.markout_core.table_domain[([] sym:`EURUSD`GBPUSD; quote_ccy:`USD`USD; legs:(enlist `EURUSD;enlist `GBPUSD); invert:(enlist 0b;enlist 0b));
        0#([] quote_ccy:`symbol$(); report_ccy:`symbol$(); leg:`symbol$(); invert:`boolean$())];
    sp:`horizons`report_ccy`rate_timing`max_age`reference!(enlist 0D00:00:10;`USD;`execution;0D00:00:05;p);
    ex:([] source_time:enlist 2026.09.17D09:00:00; venue:enlist `x; sym:enlist `GBPUSD; side:enlist 1; size:enlist 1f; price:enlist 1.5; fill_id:enlist `g1; clock:enlist `venue);
    md:.qt.qetl.reference.policy.books[enlist 2026.09.17D09:00:00;0D00:00:01;4#`venue];
    c:@[.qpipe.transform.markout_core.calc[dom;sp;ex;];md;{[e] e}];
    .qt.qetl.reference.policy.forget[enlist `eur_only;`symbol$()];
    .qunit.assertEquals[e;"reference_policy: unmatched_reference_rule: eur_only has no rule for GBPUSD at 2026.09.17D09:00:00.000000000";
        "named, with the instrument and the instant"];
    .qunit.assertEquals[c;e;"and a markout of it is refused the same way, not scored against some other source"]};

/ --- pricing: the rule's sources, method, clock and staleness ---------------------------------------------

.qt.qetl.reference.policy.test_a_source_outside_the_selected_rule_cannot_set_the_price:{[t]
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    tm:2026.09.17D03:00:00 2026.09.17D09:00:00 2026.09.17D13:00:00 2026.09.19D09:00:00;
    tob:.qt.qetl.reference.policy.books[tm;0D00:00:01;4#`venue];
    r:(.qt.qetl.reference.policy.priced[p;tob;0D00:00:05;tm])`same;
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    / Asia (LP_A, LP_B; best) 1.25/1.75; London (LP_A, LP_C; median) bids 1.0 1.5, asks 2.0 1.625, mids 1.5 1.5625;
    / the US (LP_B, LP_D; best) 1.25/1.75; Saturday's fallback (every source; best) LP_C's 1.5/1.625
    .qunit.assertEquals[r`bid;1.25 1.25 1.25 1.5;"LP_C has the best bid, and sets it only where the rule lets it"];
    .qunit.assertEquals[r`ask;1.75 1.8125 1.75 1.625;"the median of LP_A's and LP_C's asks in London"];
    .qunit.assertEquals[r`mid;1.5 1.53125 1.5 1.5625;"each instant priced by its rule's own method"]};

.qt.qetl.reference.policy.test_a_rules_books_are_matched_on_the_targets_clock:{[t]
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    / LP_B stamps its books on the receipt clock
    tob:.qt.qetl.reference.policy.books[enlist 2026.09.17D03:00:00;0D00:00:01;`venue`receipt`venue`venue];
    r:.qt.qetl.reference.policy.priced[p;tob;0D00:00:05;enlist 2026.09.17D03:00:00];
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertEquals[((r`same)`bid;(r`same)`ask);(enlist 1.0;enlist 2.0);"on the venue clock Asia has LP_A alone: LP_B is on another clock and LP_C is not in the rule"];
    .qunit.assertEquals[((r`any)`bid;(r`any)`ask);(enlist 1.25;enlist 1.75);"and on any clock LP_B is seen, which is how a mismatch is told from a gap"]};

.qt.qetl.reference.policy.test_a_rules_max_age_replaces_the_staleness_and_a_null_one_keeps_the_spec_s:{[t]
    rs:update max_age:0D00:00:01 from .qt.qetl.reference.policy.rules[] where rule_id=`asia_open;
    p:.qetl.reference.policy.register[`strict;rs];
    tm:2026.09.17D03:00:00 2026.09.17D13:00:00;
    tob:.qt.qetl.reference.policy.books[tm;0D00:00:02;4#`venue];
    r:(.qt.qetl.reference.policy.priced[p;tob;0D00:00:05;tm])`same;
    s:.qetl.reference.policy.universe p;
    .qt.qetl.reference.policy.forget[enlist `strict;`symbol$()];
    .qunit.assertEquals[r`mid;0n 1.5;"Asia's books are 2s old against its 1s; the US keeps the spec's 5s"];
    .qunit.assertEquals[exec sources from s where rule_id=`asia_open;enlist `LP_A`LP_B;"the declared source universe of each rule is exposed"]};

/ --- the markout ----------------------------------------------------------------------------------------------

/ A EURUSD domain, quoted in the reporting currency, and a spec scoring at +10s under a reference.
.qt.qetl.reference.policy.dom:{[]
    .qpipe.transform.markout_core.table_domain[([] sym:enlist `EURUSD; quote_ccy:enlist `USD; legs:enlist enlist `EURUSD; invert:enlist enlist 0b);
        0#([] quote_ccy:`symbol$(); report_ccy:`symbol$(); leg:`symbol$(); invert:`boolean$())]}

.qt.qetl.reference.policy.spec:{[ref] `horizons`report_ccy`rate_timing`max_age`reference!(enlist 0D00:00:10;`USD;`execution;0D00:00:05;ref)}

/ A buy of 1,000,000 EURUSD at 1.4, five seconds before London opens.
.qt.qetl.reference.policy.fill:{[]
    ([] source_time:enlist 2026.09.17D07:59:55; venue:enlist `x; sym:enlist `EURUSD; side:enlist 1; size:enlist 1e6; price:enlist 1.4; fill_id:enlist `e1; clock:enlist `venue)}

.qt.qetl.reference.policy.test_t0_and_t1_select_their_rules_independently_and_the_rows_trace_back_to_them:{[t]
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    dom:.qt.qetl.reference.policy.dom[];
    sp:.qt.qetl.reference.policy.spec[p];
    md:.qt.qetl.reference.policy.books[2026.09.17D07:59:55 2026.09.17D08:00:05;0D00:00:01;4#`venue];
    r:.qpipe.transform.markout_core.calc[dom;sp;.qt.qetl.reference.policy.fill[];md];
    pinned:.qpipe.transform.config_markout.pin[dom;sp;"a policy's markout"];
    lin:.qpipe.transform.config_markout.reference_rules r;
    pure:.qetl.reference.policy.lineage[.qetl.reference.policy.describe p;enlist `EURUSD;enlist 2026.09.17D07:59:55;enlist 2026.09.17D08:00:05];
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertEquals[exec (ref_t0;ref_t1) from r where kind=`total;(enlist 1.5;enlist 1.53125);
        "t0 (07:59:55) is Asia's best of LP_A and LP_B, 1.5; t1 (08:00:05) is London's median of LP_A and LP_C, 1.53125"];
    .qunit.assertEquals[exec move from r;(1.53125-1.4;1.5-1.4);"the total and the execution's move, each against its own rule's reference"];
    .qunit.assertEquals[distinct r`status;enlist `ok;"both priced"];
    .qunit.assertEquals[distinct r`config_id;enlist pinned;"the rows name the snapshot that records the policy"];
    .qunit.assertEquals[distinct lin;([] ref_rule_t0:enlist `asia_open; ref_rule_t1:enlist `london_open; ref_variant_t0:enlist `best_top; ref_variant_t1:enlist `median_top);
        "every row is traced to the rule and variant at t0 and at t1, from its config_id and its timestamps"];
    .qunit.assertEquals[count lin;count r;"one lineage row per row"];
    .qunit.assertEquals[pure;1#lin;"the store-free derivation agrees"]};

.qt.qetl.reference.policy.test_lineage_is_read_from_the_recorded_policy_not_the_registered_one:{[t]
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    dom:.qt.qetl.reference.policy.dom[];
    sp:.qt.qetl.reference.policy.spec[p];
    md:.qt.qetl.reference.policy.books[2026.09.17D07:59:55 2026.09.17D08:00:05;0D00:00:01;4#`venue];
    r:.qpipe.transform.markout_core.calc[dom;sp;.qt.qetl.reference.policy.fill[];md];
    .qpipe.transform.config_markout.pin[dom;sp;"before the policy changed"];
    / the policy is re-declared with London opening at 07:00: a new configuration, and the old rows keep theirs
    .qetl.reference.policy.register[p;update active_to:07:00 from (update active_from:07:00 from .qt.qetl.reference.policy.rules[] where rule_id=`london_open) where rule_id=`asia_open];
    later:.qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp]];
    lin:.qpipe.transform.config_markout.reference_rules r;
    plain:.qpipe.transform.config_markout.reference_rules ([] config_id:enlist .qpipe.transform.config_markout.pin[dom;`horizons`report_ccy`rate_timing!(enlist 0D00:00:10;`USD;`execution);"no policy"];
        sym:enlist `EURUSD; source_time:enlist 2026.09.17D07:59:55; horizon:enlist 0D00:00:10);
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertTrue[later<>first r`config_id;"another policy content is another configuration"];
    .qunit.assertEquals[first lin`ref_rule_t0;`asia_open;"07:59:55 was Asia's under the policy the rows were scored with"];
    .qunit.assertEquals[plain;([] ref_rule_t0:enlist `; ref_rule_t1:enlist `; ref_variant_t0:enlist `best_top; ref_variant_t1:enlist `best_top);
        "a row scored without a policy has no rule, and variant #1"]};

.qt.qetl.reference.policy.test_a_policy_of_variant_one_alone_prices_as_variant_one_to_the_bit:{[t]
    p:.qetl.reference.policy.register[`only_default;.qetl.reference.policy.example];
    system "S 23";
    n:3000;
    md:([] source_time:2026.09.17D10:00:00+n?600000000000; sym:n?`EURUSD`NOBOOK; source:n?`LP_A`LP_B`LP_C`LP_D;
        bid:{?[x<0.1;0n;1+x]} n?1f; ask:{?[x<0.1;0n;2+x]} n?1f; clock:n?`venue`receipt);
    m:400;
    ex:([] source_time:2026.09.17D10:00:00+m?600000000000; venue:m?`x`y; sym:m?`EURUSD`NOBOOK; side:m?1 -1; size:m?10f; price:1.5+m?1f;
        fill_id:`$string til m; clock:m?`venue`receipt);
    dom:.qpipe.transform.markout_core.table_domain[([] sym:`EURUSD`NOBOOK; quote_ccy:`USD`USD; legs:(enlist `EURUSD;enlist `NOBOOK); invert:(enlist 0b;enlist 0b));
        0#([] quote_ccy:`symbol$(); report_ccy:`symbol$(); leg:`symbol$(); invert:`boolean$())];
    sp:`horizons`report_ccy`rate_timing`max_age!(-0D00:00:01 0D00:00:01 0D00:00:30;`USD;`execution;0D00:00:05);
    plain:.qpipe.transform.markout_core.score[dom;sp;ex;md];
    pol:.qpipe.transform.markout_core.score[dom;sp,enlist[`reference]!enlist p;ex;md];
    .qt.qetl.reference.policy.forget[enlist `only_default;`symbol$()];
    .qunit.assertTrue[(any `ok=plain`status) and any `ok<>plain`status;"the fixture has priced rows and unpriced ones"];
    .qunit.assertEquals[delete config_id from pol;delete config_id from plain;"grouping by rule and pricing each group is the variant's own road"];
    .qunit.assertTrue[(first pol`config_id)<>first plain`config_id;"but a policy is recorded, so it is another configuration"]};

.qt.qetl.reference.policy.test_a_rule_max_age_longer_than_the_spec_s_is_refused_and_an_equal_one_accepted:{[t]
    rs:update max_age:0D00:00:09 from .qt.qetl.reference.policy.rules[] where rule_id=`us_open;
    p:.qetl.reference.policy.register[`loose;rs];
    dom:.qt.qetl.reference.policy.dom[];
    e:@[.qpipe.transform.markout_core.validate[dom;];.qt.qetl.reference.policy.spec[p];{[e] e}];
    eq:(.qpipe.transform.markout_core.validate[dom;.qt.qetl.reference.policy.spec[p],enlist[`max_age]!enlist 0D00:00:09])`max_age;
    open:(.qpipe.transform.markout_core.validate[dom;.qt.qetl.reference.policy.spec[p],enlist[`max_age]!enlist 0Wn])`max_age;
    .qt.qetl.reference.policy.forget[enlist `loose;`symbol$()];
    .qunit.assertEquals[e;"reference_policy: loose.us_open: max_age 0D00:00:09.000000000 is longer than the spec's max_age 0D00:00:05.000000000 - the job reads no book older than the spec's limit, so raise the spec's max_age or lower the rule's";
        "the spec's 5s cannot honour the US rule's 9s: refused, naming the rule, both limits and the fix"];
    .qunit.assertEquals[eq;0D00:00:09;"a rule as old as the spec's limit is accepted"];
    .qunit.assertEquals[open;0Wn;"and so is any rule under a spec with no limit"]};

.qt.qetl.reference.policy.test_a_null_max_age_is_held_to_the_spec_s_through_its_variant_s_stale:{[t]
    .qetl.reference.register[`stale9;`venues`stale`offset`transform`combine!(`all;0D00:00:09;0D;`top;`best)];
    rs:update reference_variant:`stale9 from .qt.qetl.reference.policy.rules[] where rule_id=`us_open;
    p:.qetl.reference.policy.register[`inherits;rs];
    dom:.qt.qetl.reference.policy.dom[];
    e:@[.qpipe.transform.markout_core.validate[dom;];.qt.qetl.reference.policy.spec[p];{[e] e}];
    eq:(.qpipe.transform.markout_core.validate[dom;.qt.qetl.reference.policy.spec[p],enlist[`max_age]!enlist 0D00:00:09])`max_age;
    pq:.qetl.reference.policy.register[`own;update max_age:0D00:00:05 from rs where rule_id=`us_open];
    own:(.qpipe.transform.markout_core.validate[dom;.qt.qetl.reference.policy.spec[pq]])`reference;
    .qt.qetl.reference.policy.forget[`inherits`own;enlist `stale9];
    .qunit.assertEquals[e;"reference_policy: inherits.us_open: null max_age takes variant stale9's stale 0D00:00:09.000000000, longer than the spec's max_age 0D00:00:05.000000000 - raise the spec's max_age or give the rule one within it";
        "the US rule declares no max_age and its variant allows 9s against the spec's 5s: refused, naming the variant"];
    .qunit.assertEquals[eq;0D00:00:09;"a variant's stale as old as the spec's limit is accepted"];
    .qunit.assertEquals[own;`own;"and a rule's own max_age within the spec's replaces the variant's, so it is accepted too"]};

/ --- the configuration -------------------------------------------------------------------------------------

.qt.qetl.reference.policy.test_a_policy_is_recorded_in_full_and_its_content_is_independent_of_declaration_order:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    id:{[dom;sp;ref] .qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp,enlist[`reference]!enlist ref]]}[dom;sp];
    plain:.qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp]];
    a:id .qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    c:.qpipe.transform.config_markout.content[dom;sp,enlist[`reference]!enlist `sessions];
    b:id .qetl.reference.policy.register[`sessions;reverse .qt.qetl.reference.policy.rules[]];
    moved:id .qetl.reference.policy.register[`sessions;update priority:31 from .qt.qetl.reference.policy.rules[] where rule_id=`us_open];
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertTrue[a<>plain;"a policy is another configuration than the spec without it"];
    .qunit.assertEquals[b;a;"the same rules declared in another order are the same configuration"];
    .qunit.assertTrue[moved<>a;"a changed priority is a changed configuration"];
    .qunit.assertEquals[(c`reference)`kind;`policy;"recorded as a policy ..."];
    .qunit.assertEquals[((c`reference)`rules)`rule_id;`asia_open`fallback`london_open`us_open;"... with every rule ..."];
    .qunit.assertEquals[(((c`reference)`effective)`london_open)`venues;`LP_A`LP_C;"... and the definition each prices with"]};

.qt.qetl.reference.policy.test_a_spec_names_a_variant_or_a_policy_never_an_unknown_name:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[dom;];sp,enlist[`reference]!enlist `nope;
        "markout_core: reference must be a registered variant or reference policy*";"a misspelt reference fails when the job is declared"];
    p:.qetl.reference.policy.register[`sessions;.qt.qetl.reference.policy.rules[]];
    ok:(.qpipe.transform.markout_core.validate[dom;sp,enlist[`reference]!enlist p])`reference;
    .qt.qetl.reference.policy.forget[enlist `sessions;`symbol$()];
    .qunit.assertEquals[ok;`sessions;"a registered policy is accepted"]};
