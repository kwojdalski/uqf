/ test_reference_variant.q - the reference a markout is scored against, as a declared variant
/ (src/etl/core/reference_variant.q, .qetl.reference, #2288).
/ .
/ A variant is a SELECT (venues, staleness, offset) over the flat keys, a TRANSFORM of one book and a COMBINE across
/ venues. Variant #1 is today's rule and must stay it bit for bit: this file holds it equal to
/ .qf.mkt.best_across_venues, the function every other consumer prices from, on books with withdrawn sides, tied
/ times, quiet venues and two clocks. And the variant a run used is part of its configuration - except the first,
/ which is what the contract's version means, so the ids of everything scored before variants existed are unchanged.


/ Books of three sources on two clocks across ten minutes, a tenth of each side withdrawn, some times repeated.
.qt.qetl.reference.books:{[n;seed]
    system "S ",string seed;
    syms:`EURUSD`GBPUSD`USDJPY;
    md:([] sym:n?syms; source:n?`a`b`c`d; source_time:2026.09.17D10:00:00+n?600000000000;
        bid:{?[x<0.1;0n;1+x]} n?1f; ask:{?[x<0.1;0n;2+x]} n?1f);
    md:update clock:?[source in `a`b;`venue;`receipt] from md;
    md,select from md where i<n div 10}

.qt.qetl.reference.targets:{[m;seed]
    system "S ",string seed;
    ([] sym:m?`EURUSD`GBPUSD`USDJPY`NOBOOK; time:2026.09.17D10:00:00+m?700000000000; clock:m?`venue`receipt)}

/ What .qf.mkt.best_across_venues says on the books of one clock (or all of them with `).
.qt.qetl.reference.legacy:{[md;tg;max_age;clk]
    t:$[null clk; md; select from md where clock=clk];
    tob:select time:source_time, sym, venue:source, bid, ask from t;
    .qf.mkt.best_across_venues[tob;select sym, time from tg;max_age]}

/ Take test variants (and combines) back out of the registries.
.qt.qetl.reference.forget:{[drop_variants;drop_combines]
    v:.qetl.reference.variants;
    c:.qetl.reference.combines;
    `.qetl.reference.variants set drop_variants _ v;
    `.qetl.reference.combines set drop_combines _ c}

/ --- variant #1 is the rule every consumer prices from --------------------------------------------------

.qt.qetl.reference.test_variant_one_is_best_across_venues_on_the_targets_own_clock_and_on_any:{[t]
    md:.qt.qetl.reference.books[3000;11];
    tg:.qt.qetl.reference.targets[2000;12];
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from md;
    {[md;tg;tob;ma]
        r:.qetl.reference.at[`best_top;tob;ma;tg`sym;tg`time;tg`clock];
        lv:.qt.qetl.reference.legacy[md;tg;ma;`venue];
        lr:.qt.qetl.reference.legacy[md;tg;ma;`receipt];
        own:([] bid:?[(tg`clock)=`venue;lv`bid;lr`bid]; ask:?[(tg`clock)=`venue;lv`ask;lr`ask]; mid:?[(tg`clock)=`venue;lv`mid;lr`mid]);
        .qunit.assertEquals[r`same;own;"on the target's own clock, bit for bit, at max_age ",string ma];
        .qunit.assertEquals[r`any;.qt.qetl.reference.legacy[md;tg;ma;`];"and on any clock"]}[md;tg;tob] each 0D00:00:05 0D00:01:00 0Wn;
    r:.qetl.reference.at[`best_top;tob;0D00:00:05;tg`sym;tg`time;tg`clock];
    .qunit.assertTrue[(any not null (r`same)`mid) and any null (r`same)`mid;"the fixture has priced rows and rows no venue was live for"];
    .qunit.assertTrue[all null ((r`same) where (tg`sym)=`NOBOOK)`mid;"a sym with no book is unpriced, not an error"]};

.qt.qetl.reference.test_a_target_with_no_sym_or_no_books_is_null_and_an_empty_table_prices_nothing:{[t]
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from .qt.qetl.reference.books[100;13];
    r:.qetl.reference.at[`best_top;tob;0Wn;``EURUSD;2#2026.09.17D10:05:00;2#`venue];
    .qunit.assertTrue[null first (r`same)`mid;"a null sym is no target"];
    e:.qetl.reference.at[`best_top;0#tob;0Wn;enlist `EURUSD;enlist 2026.09.17D10:05:00;enlist `venue];
    .qunit.assertEquals[(e`same)`mid;enlist 0n;"an empty table prices nothing"]};

.qt.qetl.reference.test_a_rate_book_is_one_sources_latest_within_max_age_with_both_sides:{[t]
    md:([] sym:4#`USDJPY; source:`a`a`a`b; source_time:2026.09.17D10:00:00 2026.09.17D10:00:03 2026.09.17D10:00:04 2026.09.17D10:00:05;
        bid:150 151 0n 99f; ask:150.5 151.5 152 99.5; clock:4#`venue);
    tob:.qetl.mapped.arrange md;
    r:.qetl.reference.book_at[tob;0D00:00:05;`a;`USDJPY;2026.09.17D10:00:04 2026.09.17D10:00:03 2026.09.17D10:00:20;`venue`venue`venue];
    .qunit.assertEquals[r`live;010b;"the 10:00:04 book has no bid so is no rate; 10:00:03 is the latest at 10:00:03; 10:00:20 is past max_age"];
    .qunit.assertEquals[(r`quoted) 1;2026.09.17D10:00:03;"and says which book it was"];
    any_:.qetl.reference.book_at[tob;0D00:00:05;`a;`USDJPY;enlist 2026.09.17D10:00:03;`receipt];
    .qunit.assertEquals[any_`live;enlist 0b;"a book on the other clock is not a rate at that clock"];
    .qunit.assertEquals[(.qetl.reference.book_at[tob;0D00:00:05;`a;`USDJPY;enlist 2026.09.17D10:00:03;()])`live;enlist 1b;"and any clock is accepted when none is asked for"]};

/ --- a variant is data: venues, staleness, offset, a transform and a combine ------------------------------

.qt.qetl.reference.def_of:{[changes] (`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)),changes}

.qt.qetl.reference.test_a_variant_is_registered_validated_and_described:{[t]
    .qunit.assertEquals[.qetl.reference.default;`best_top;"variant #1 is the default"];
    .qunit.assertEquals[(.qetl.reference.def `best_top)`combine;`best;"and combines the best bid and ask across venues"];
    .qunit.assertEquals[first key .qetl.reference.describe `best_top;`name;"described with its name, then its definition"];
    bad:{[e] @[{.qetl.reference.register[`bad;x]};e;{[e] e}]};
    .qunit.assertTrue[bad[enlist[`venues]!enlist `all] like "*must define exactly venues, stale, offset, transform, combine*";"every key"];
    .qunit.assertTrue[bad[.qt.qetl.reference.def_of[enlist[`venues]!enlist 7]] like "*venues must be `all or a list of sources*";"venues"];
    .qunit.assertTrue[bad[.qt.qetl.reference.def_of[enlist[`stale]!enlist -1D]] like "*stale must be `spec or a timespan*";"stale"];
    .qunit.assertTrue[bad[.qt.qetl.reference.def_of[enlist[`offset]!enlist -1D]] like "*offset must be a timespan of zero or more*";"offset"];
    .qunit.assertTrue[bad[.qt.qetl.reference.def_of[enlist[`transform]!enlist `nope]] like "*transform nope is not registered*";"transform"];
    .qunit.assertTrue[bad[.qt.qetl.reference.def_of[enlist[`combine]!enlist `nope]] like "*combine nope is not registered*";"combine"];
    .qunit.assertTrue[@[{.qetl.reference.def x};`nope;{[e] e}] like "*nope is not a registered variant*";"an unregistered variant is refused by name"]};

.qt.qetl.reference.test_the_select_narrows_by_venue_staleness_and_offset:{[t]
    md:([] sym:4#`EURUSD; source:`a`a`b`b; source_time:2026.09.17D10:00:00 2026.09.17D10:00:08 2026.09.17D10:00:01 2026.09.17D10:00:07;
        bid:1.0 1.2 1.1 1.3; ask:2.0 2.2 2.1 2.3; clock:4#`venue);
    tob:.qetl.mapped.arrange md;
    at:{[tob;v;ma;tm] first ((.qetl.reference.at[v;tob;ma;enlist `EURUSD;enlist tm;enlist `venue])`same)`bid};
    .qetl.reference.register[`only_a;.qt.qetl.reference.def_of[enlist[`venues]!enlist enlist `a]];
    .qetl.reference.register[`loose;.qt.qetl.reference.def_of[enlist[`stale]!enlist 0D00:00:09]];
    .qetl.reference.register[`early;.qt.qetl.reference.def_of[enlist[`offset]!enlist 0D00:00:06]];
    tm:2026.09.17D10:00:09;
    r:(at[tob;`best_top;0D00:00:02;tm];at[tob;`only_a;0D00:00:02;tm];at[tob;`loose;0D00:00:01;tm];at[tob;`early;0D00:00:02;tm]);
    / delete the test variants again
    .qt.qetl.reference.forget[`only_a`loose`early;`symbol$()];
    .qunit.assertEquals[r 0;1.3;"variant #1 at 10:00:09, 2s stale: b's 10:00:07 (1.3) beats a's 10:00:08 (1.2)"];
    .qunit.assertEquals[r 1;1.2;"venues a only: a's 10:00:08"];
    .qunit.assertEquals[r 2;1.3;"stale `9s` ignores the spec's 1s: the same answer as a wide limit"];
    .qunit.assertEquals[r 3;1.1;"an offset of 6s prices 10:00:03: b's 10:00:01 (1.1) is the best bid, a's 10:00:00 is 1.0"]};

.qt.qetl.reference.test_a_combine_and_a_transform_plug_in_by_name:{[t]
    md:([] sym:2#`EURUSD; source:`a`b; source_time:2#2026.09.17D10:00:00; bid:1.0 1.2; ask:2.0 2.2; clock:2#`venue);
    tob:.qetl.mapped.arrange md;
    / the WORST bid across venues instead of the best: a different COMBINE, the same SELECT and TRANSFORM
    `.qetl.reference.combines set .qetl.reference.combines,enlist[`worst]!enlist {[tid;px;n]
        g:0!select b:min bid, a:max ask by tid from ([] tid:tid; bid:px`bid; ask:px`ask);
        bb:@[n#0n;g`tid;:;g`b];
        ba:@[n#0n;g`tid;:;g`a];
        ([] bid:bb; ask:ba; mid:0.5*bb+ba)};
    .qetl.reference.register[`worst_top;.qt.qetl.reference.def_of[enlist[`combine]!enlist `worst]];
    r:(.qetl.reference.at[`worst_top;tob;0Wn;enlist `EURUSD;enlist 2026.09.17D10:00:01;enlist `venue])`same;
    .qt.qetl.reference.forget[enlist `worst_top;enlist `worst];
    .qunit.assertEquals[(r`bid;r`ask);(enlist 1.0;enlist 2.2);"min bid and max ask: the variant changes the answer and nothing else does"]};

/ --- the variant is part of the configuration ----------------------------------------------------------------

.qt.qetl.reference.test_a_non_default_variant_changes_the_config_id_and_variant_one_does_not:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    plain:.qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp]];
    named:.qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp,enlist[`reference]!enlist `best_top]];
    .qunit.assertEquals[named;plain;"saying variant #1 changes no id: it is what contract_version 1 means"];
    .qunit.assertFalse[`reference in key .qpipe.transform.config_markout.content[dom;sp];"and is not repeated in the snapshot"];
    .qetl.reference.register[`only_a;.qt.qetl.reference.def_of[enlist[`venues]!enlist enlist `a]];
    other:.qpipe.transform.config_markout.id[dom;.qpipe.transform.markout_core.validate[dom;sp,enlist[`reference]!enlist `only_a]];
    c:.qpipe.transform.config_markout.content[dom;sp,enlist[`reference]!enlist `only_a];
    .qt.qetl.reference.forget[enlist `only_a;`symbol$()];
    .qunit.assertTrue[other<>plain;"another variant is another configuration"];
    .qunit.assertEquals[(c`reference)`name;`only_a;"recorded in full in the snapshot ..."];
    .qunit.assertEquals[(c`reference)`venues;enlist `a;"... with its definition"]};

.qt.qetl.reference.test_an_unregistered_variant_in_a_spec_is_refused_at_declaration:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    .qunit.assertThrows[.qpipe.transform.markout_core.validate[dom;];sp,enlist[`reference]!enlist `nope;"markout_core: reference must be a registered variant*";"a misspelt variant fails when the job is declared"]};

.qt.qetl.reference.test_the_core_scores_by_the_spec_s_variant:{[t]
    dom:.qpipe.transform.markout_core.fixture_domain;
    sp:.qpipe.transform.markout_core.fixture_spec;
    tm:2026.09.17D10:00:00;
    md:.qetl.mapped.arrange ([] source_time:(tm-0D00:00:01;tm-0D00:00:01;tm+0D00:00:09;tm+0D00:00:09); sym:4#`$"BTC-USDT"; source:`a`b`a`b;
        bid:61990 61995 62010 62015f; ask:62010 62005 62030 62025f; clock:4#`venue);
    e:([] source_time:enlist tm; venue:enlist `x; sym:enlist `$"BTC-USDT"; side:enlist 1; size:enlist 0.5; price:enlist 61998f; fill_id:enlist `c1; clock:enlist `venue);
    .qetl.reference.register[`only_a;.qt.qetl.reference.def_of[enlist[`venues]!enlist enlist `a]];
    best:.qpipe.transform.markout_core.calc[dom;sp;e;md];
    onlya:.qpipe.transform.markout_core.calc[dom;sp,enlist[`reference]!enlist `only_a;e;md];
    .qt.qetl.reference.forget[enlist `only_a;`symbol$()];
    .qunit.assertEquals[first exec ref_t0 from best where kind=`total;62000f;"variant #1: best bid 61995 and best ask 62005 across a and b"];
    .qunit.assertEquals[first exec ref_t0 from onlya where kind=`total;62000f;"source a alone: 61990/62010, mid 62000"];
    .qunit.assertEquals[first exec ref_t1 from best where kind=`total;62020f;"variant #1 at +10s: 62015/62025"];
    .qunit.assertEquals[first exec ref_t1 from onlya where kind=`total;62020f;"source a at +10s: 62010/62030"];
    .qunit.assertTrue[(first best`config_id)<>first onlya`config_id;"and the rows name different configurations"]};
