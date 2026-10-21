/ test_enrich.q - .qf.mkt.lookup (the pure as-of rule) and .qetl.enrich (the
/ reference buffer a streaming job keeps) (#1223).
/ .
/ Every expectation is a reference value worked out by hand from the times
/ below, or an identity between the buffer and the pure function.


.qt.qetl.enrich.t0:2026.10.01D10:00:00
.qt.qetl.enrich.s:{[n] .qt.qetl.enrich.t0+n*0D00:00:01}

/ The lookup spec the tests share, one key.
.qt.qetl.enrich.spec:{[tol] `keys`ref_time`event_time`attach`tolerance!(enlist `sym;`time;`time;enlist[`mid]!enlist `mid;tol)}
/ The same over two keys.
.qt.qetl.enrich.spec2:`keys`ref_time`event_time`attach`tolerance!(`venue`sym;`time;`time;enlist[`mid]!enlist `mid;0D00:00:10)

.qt.qetl.enrich.refs:{[] ([] sym:`a`a`a`b; time:.qt.qetl.enrich.s[0 10 20 5]; mid:1 2 3 9f)}
.qt.qetl.enrich.ev:{[syms;ns] ([] sym:syms; time:.qt.qetl.enrich.s[ns]; id:til count syms)}

/ ------------------------------------------------------------- THE LOOKUP

.qt.qetl.enrich.test_prevailing_row_is_the_latest_at_or_before_the_event:{[t]
    r:.qf.mkt.lookup[.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`a`a`a`b;4 10 25 5];.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[r`mid;1 2 3 9f;"4 sees the 0 row, 10 sees the row stamped exactly then, 25 sees 20, b sees its own"];
    .qunit.assertEquals[r`staleness;0D00:00:01*4 0 5 0;"staleness is event time minus reference time"];
    .qunit.assertEquals[r`id;0 1 2 3;"events keep their own order and columns"]};

.qt.qetl.enrich.test_no_look_ahead:{[t]
    / The only reference row for a is stamped at 10; an event at 9.999s must not see it.
    one:([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 10; mid:enlist 7f);
    r:.qf.mkt.lookup[one;([] sym:`a`a; time:(.qt.qetl.enrich.s[10])-1 0);.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[r`status;`missing`ok;"a row stamped after the event is never attached, one stamped at it is"];
    .qunit.assertEquals[r`mid;0n 7f;"the early event gets null, not the future row"]};

.qt.qetl.enrich.test_tolerance_boundary_is_inclusive:{[t]
    r:.qf.mkt.lookup[([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0; mid:enlist 1f);([] sym:`a`a`a; time:.qt.qetl.enrich.s[9 10 11]);.qt.qetl.enrich.spec[0D00:00:10]];
    .qunit.assertEquals[r`status;`ok`ok`stale;"age exactly at the tolerance is ok; one nanosecond past is not - here a second past"];
    .qunit.assertEquals[r`mid;1 1 1f;"a stale row still carries its value; the status says what to do with it"]};

.qt.qetl.enrich.test_null_tolerance_never_stales:{[t]
    r:.qf.mkt.lookup[([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0; mid:enlist 1f);([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 100000);.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[r`status;enlist `ok;"no tolerance means no limit"]};

.qt.qetl.enrich.test_missing_reference_is_explicit:{[t]
    r:.qf.mkt.lookup[.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`c`a;5 5];.qt.qetl.enrich.spec[0D00:00:10]];
    .qunit.assertEquals[r`status;`missing`ok;"an unknown key, and a known one"];
    .qunit.assertEquals[first r`mid;0n;"missing means null values"];
    .qunit.assertEquals[first r`staleness;0Nn;"and a null staleness"]};

.qt.qetl.enrich.test_empty_reference_and_empty_events:{[t]
    r:.qf.mkt.lookup[0#.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`a`b;1 2];.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[r`status;`missing`missing;"no reference at all is all missing, not an error"];
    e:.qf.mkt.lookup[.qt.qetl.enrich.refs[];0#.qt.qetl.enrich.ev[`a;1];.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[(count e;cols e);(0;`sym`time`id`mid`staleness`status);"no events is an empty result with the full shape"]};

.qt.qetl.enrich.test_reference_need_not_be_sorted:{[t]
    shuffled:.qt.qetl.enrich.refs[] 3 1 0 2;
    .qunit.assertEquals[.qf.mkt.lookup[shuffled;.qt.qetl.enrich.ev[`a`a`a`b;4 10 25 5];.qt.qetl.enrich.spec[0Nn]];.qf.mkt.lookup[.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`a`a`a`b;4 10 25 5];.qt.qetl.enrich.spec[0Nn]];"order of the reference rows does not change any answer"]};

.qt.qetl.enrich.test_two_keys_do_not_mix:{[t]
    r2:([] venue:`x`y`x; sym:`a`a`a; time:.qt.qetl.enrich.s[0 3 8]; mid:1 2 3f);
    e:([] venue:`x`y`y; sym:`a`a`a; time:.qt.qetl.enrich.s[9 9 2]);
    r:.qf.mkt.lookup[r2;e;.qt.qetl.enrich.spec2];
    .qunit.assertEquals[r`mid;3 2 0n;"x at 9 sees x's 8; y at 9 sees y's 3; y at 2 has nothing yet - x's row at 0 is not y's"];
    .qunit.assertEquals[r`status;`ok`ok`missing;"statuses follow"]};

.qt.qetl.enrich.test_attach_renames_and_several_columns:{[t]
    q:([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0; bid:enlist 1f; ask:enlist 3f);
    sp:`keys`ref_time`event_time`attach`tolerance!(enlist `sym;`time;`time;`ref_bid`ref_ask!`bid`ask;0Nn);
    r:.qf.mkt.lookup[q;([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 1);sp];
    .qunit.assertEquals[cols r;`sym`time`ref_bid`ref_ask`staleness`status;"renamed columns, then staleness and status"];
    .qunit.assertEquals[(first r`ref_bid;first r`ref_ask);(1f;3f);"values"]};

.qt.qetl.enrich.test_event_and_reference_time_columns_may_differ:{[t]
    q:([] sym:enlist `a; source_time:enlist .qt.qetl.enrich.s 0; mid:enlist 1f);
    sp:`keys`ref_time`event_time`attach`tolerance!(enlist `sym;`source_time;`time;enlist `mid;0Nn);
    r:.qf.mkt.lookup[q;([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 4);sp];
    .qunit.assertEquals[first r`staleness;0D00:00:04;"event time is read from the event column, reference time from its own"]};

.qt.qetl.enrich.test_refusals:{[t]
    .qunit.assertThrows[{.qf.mkt.lookup[.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`a;1];`keys`ref_time!(enlist `sym;`time)]};();"asof.lookup: spec is missing event_time, attach, tolerance*";"every missing spec key is named"];
    .qunit.assertThrows[{.qf.mkt.lookup[([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0);.qt.qetl.enrich.ev[`a;1];.qt.qetl.enrich.spec[0Nn]]};();"qasof_lookup: refs is missing*";"a reference table without the attached column"];
    .qunit.assertThrows[{.qf.mkt.lookup[.qt.qetl.enrich.refs[];([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 1; status:enlist `x);.qt.qetl.enrich.spec[0Nn]]};();"asof.lookup: result columns already in events: status*";"a result column cannot overwrite an event column"];
    .qunit.assertThrows[{.qf.mkt.lookup[.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`a;1];.qt.qetl.enrich.spec[neg 0D00:00:01]]};();"asof.lookup: tolerance must not be negative*";"a negative tolerance"];
    .qunit.assertThrows[{.qf.mkt.lookup[.qt.qetl.enrich.refs[];.qt.qetl.enrich.ev[`a;1];.qt.qetl.enrich.spec[10]]};();"asof.lookup: tolerance must be a timespan*";"a bare long is not a duration"]};

.qt.qetl.enrich.test_null_time_reference_row_is_never_attached:{[t]
    / A null-time row sorts first, so aj would match it for an event with no earlier
    / valid row: the status said missing while the row's mid came along (#1251).
    nr:([] sym:`a`a; time:(0Np;.qt.qetl.enrich.s[10]); mid:1.3 1.2);
    r:.qf.mkt.lookup[nr;.qt.qetl.enrich.ev[`a`a;5 12];.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[r`status;`missing`ok;"the early event has no valid prevailing row"];
    .qunit.assertEquals[r`mid;0n 1.2;"missing means null values: the null-time row's mid is not attached"];
    .qunit.assertEquals[r`staleness;(0Nn;0D00:00:02);"missing means null staleness"];
    r2:.qf.mkt.lookup[nr;([] sym:enlist `a; time:enlist 0Np; id:enlist 0);.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[(r2`status;r2`mid);(enlist `missing;enlist 0n);"a null event time finds nothing either"]};

/ ------------------------------------------------------------- THE BUFFER

.qt.qetl.enrich.mkbuf:{[ret] .qetl.enrich.new `keys`time`retention`schema!(enlist `sym;`time;ret;0#.qt.qetl.enrich.refs[])}

.qt.qetl.enrich.test_buffer_sorts_out_of_order_inserts:{[t]
    b:.qetl.enrich.add[.qt.qetl.enrich.mkbuf[0D01:00:00];.qt.qetl.enrich.refs[] 2 0];
    b:.qetl.enrich.add[b;.qt.qetl.enrich.refs[] 3 1];
    held:.qetl.enrich.rows b;
    .qunit.assertEquals[held;`sym`time xasc .qt.qetl.enrich.refs[];"rows added in any order are held sorted by (keys, time)"];
    .qunit.assertEquals[(.qetl.enrich.lookup[b;.qt.qetl.enrich.ev[`a`a`a`b;4 10 25 5];.qt.qetl.enrich.spec[0Nn]])`mid;1 2 3 9f;"and a lookup is correct"]};

.qt.qetl.enrich.test_buffer_later_arrival_wins_a_time_tie:{[t]
    b:.qetl.enrich.add[.qt.qetl.enrich.mkbuf[0D01:00:00];([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0; mid:enlist 1f)];
    b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0; mid:enlist 2f)];
    .qunit.assertEquals[first (.qetl.enrich.lookup[b;.qt.qetl.enrich.ev[enlist `a;enlist 1];.qt.qetl.enrich.spec[0Nn]])`mid;2f;"of two rows at the same time the later arrival prevails"]};

.qt.qetl.enrich.test_buffer_trims_but_keeps_latest_and_an_anchor:{[t]
    / Rows at 0, 10, 20, 30 for a; retention 5s: cut = 25s. Kept: 30, plus the
    / anchor, the newest row older than the cut (20).
    b:.qetl.enrich.add[.qt.qetl.enrich.mkbuf[0D00:00:05];([] sym:4#`a; time:.qt.qetl.enrich.s[0 10 20 30]; mid:1 2 3 4f)];
    .qunit.assertEquals[(.qetl.enrich.rows b)`mid;3 4f;"the window plus one anchor; older rows are gone"];
    r:.qetl.enrich.lookup[b;.qt.qetl.enrich.ev[`a`a;25 40];.qt.qetl.enrich.spec[0Nn]];
    .qunit.assertEquals[r`mid;3 4f;"an event at the retention edge still sees the row prevailing there"]};

.qt.qetl.enrich.test_buffer_never_drops_a_keys_latest_row:{[t]
    / Zero retention, a key that stops updating while another goes on.
    b:.qetl.enrich.add[.qt.qetl.enrich.mkbuf[0D00:00:00];([] sym:`a`b`b`b; time:.qt.qetl.enrich.s[0 1 50 100]; mid:1 2 3 4f)];
    h:.qetl.enrich.rows b;
    .qunit.assertEquals[h`mid;1 3 4f;"a keeps its only row; b keeps its latest and its one anchor, not the whole history"];
    b:.qetl.enrich.add[b;([] sym:enlist `b; time:enlist .qt.qetl.enrich.s 200; mid:enlist 5f)];
    .qunit.assertEquals[count .qetl.enrich.rows b;3;"a stays however far b moves"]};

.qt.qetl.enrich.test_buffer_stays_bounded:{[t]
    b:.qt.qetl.enrich.mkbuf[0D00:00:10];
    i:0;
    while[i<200; b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist .qt.qetl.enrich.s i; mid:enlist "f"$i)]; i+:1];
    / Window [189,199] holds 11 rows, plus the anchor at 188.
    .qunit.assertEquals[count .qetl.enrich.rows b;12;"200 inserts, 12 rows held: the retention window plus one anchor"]};

.qt.qetl.enrich.test_buffer_late_old_row_is_inserted_then_trimmed_when_beyond_retention:{[t]
    / Rows at 90 (the anchor: older than the 95 edge), 100, 105.
    b:.qetl.enrich.add[.qt.qetl.enrich.mkbuf[0D00:00:10];([] sym:3#`a; time:.qt.qetl.enrich.s[90 100 105]; mid:0 1 2f)];
    b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 102; mid:enlist 9f)];
    .qunit.assertEquals[(.qetl.enrich.rows b)`mid;0 1 9 2f;"a late row inside the window is inserted in order"];
    b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 3; mid:enlist 7f)];
    .qunit.assertEquals[(.qetl.enrich.rows b)`mid;0 1 9 2f;"one older than the anchor never displaces it"]};

.qt.qetl.enrich.test_buffer_refuses_a_row_missing_a_column:{[t]
    .qunit.assertThrows[{.qetl.enrich.add[.qt.qetl.enrich.mkbuf[0D00:00:10];([] sym:enlist `a; time:enlist .qt.qetl.enrich.s 0)]};();"qetl_enrich_add: refs is missing*";"the buffer's schema is checked on every add"]};

.qt.qetl.enrich.test_buffer_refuses_a_negative_retention:{[t]
    .qunit.assertThrows[{.qt.qetl.enrich.mkbuf[neg 0D00:00:01]};();"qetl.enrich.new: retention must not be negative*";"negative retention"]};

/ ------------------------------------------- THE BUFFER AGREES WITH THE PURE FUNCTION

.qt.qetl.enrich.test_buffer_and_pure_lookup_agree_over_the_full_history:{[t]
    / 300 reference rows over two venues and two syms, added in shuffled batches;
    / events from the retention edge on, for every key. The live buffer and
    / the pure function over the whole history (what a backfill has) agree.
    n:300;
    full:([] venue:n?`x`y; sym:n?`a`b; time:.qt.qetl.enrich.s[til n]; mid:"f"$til n);
    shuffled:full iasc n?1000;
    ret:0D00:01:00;
    b:.qetl.enrich.new `keys`time`retention`schema!(`venue`sym;`time;ret;0#full);
    k:0;
    while[k<n; b:.qetl.enrich.add[b;shuffled k+til 50]; k+:50];
    edge:.qt.qetl.enrich.s[n-61];
    m:200;
    events:([] venue:m?`x`y`z; sym:m?`a`b; time:edge+(m?60)*0D00:00:01; id:til m);
    sp:.qt.qetl.enrich.spec2;
    live:.qetl.enrich.lookup[b;events;`event_time`attach`tolerance!(`time;enlist[`mid]!enlist `mid;sp`tolerance)];
    pure:.qf.mkt.lookup[full;events;sp];
    .qunit.assertEquals[live;pure;"the buffer's answer is the pure function's over the full history"]};

/ ------------------------------------------------------- THE MARKOUT USES IT

.qt.qetl.enrich.test_markout_at_horizons_still_reads_the_prevailing_quote:{[t]
    trades:([] time:enlist .qt.qetl.enrich.s 0; sym:enlist `EURUSD; side:enlist 1; trade_price:enlist 1.1; pip_factor:enlist 10000);
    quotes:([] sym:3#`EURUSD; time:.qt.qetl.enrich.s[8 0 20]; mid:1.2 1.1005 1.3);
    r:.qf.exec.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10];
    .qunit.assertEquals[r`ref_price;1.1005 1.2;"unsorted quotes: the 1s horizon reads the 0s quote, the 10s one the 8s quote - never the 20s one"]};
