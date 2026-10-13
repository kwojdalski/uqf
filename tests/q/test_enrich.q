/ test_enrich.q - .qasof.lookup (the pure as-of rule) and .qetl.enrich (the
/ reference buffer a streaming job keeps) (#1223).
/ .
/ Every expectation is a reference value worked out by hand from the times
/ below, or an identity between the buffer and the pure function.

\d .enrichtest

t0:2026.10.01D10:00:00
s:{[n] t0+n*0D00:00:01}

/ The lookup spec the tests share, one key.
spec:{[tol] `keys`ref_time`event_time`attach`tolerance!(enlist `sym;`time;`time;enlist[`mid]!enlist `mid;tol)}
/ The same over two keys.
spec2:`keys`ref_time`event_time`attach`tolerance!(`venue`sym;`time;`time;enlist[`mid]!enlist `mid;0D00:00:10)

refs:{[] ([] sym:`a`a`a`b; time:s[0 10 20 5]; mid:1 2 3 9f)}
ev:{[syms;ns] ([] sym:syms; time:s[ns]; id:til count syms)}

/ ------------------------------------------------------------- THE LOOKUP

test_prevailing_row_is_the_latest_at_or_before_the_event:{[t]
    r:.qasof.lookup[refs[];ev[`a`a`a`b;4 10 25 5];spec[0Nn]];
    .qunit.assertEquals[r`mid;1 2 3 9f;"4 sees the 0 row, 10 sees the row stamped exactly then, 25 sees 20, b sees its own"];
    .qunit.assertEquals[r`staleness;0D00:00:01*4 0 5 0;"staleness is event time minus reference time"];
    .qunit.assertEquals[r`id;0 1 2 3;"events keep their own order and columns"]};

test_no_look_ahead:{[t]
    / The only reference row for a is stamped at 10; an event at 9.999s must not see it.
    one:([] sym:enlist `a; time:enlist s 10; mid:enlist 7f);
    r:.qasof.lookup[one;([] sym:`a`a; time:(s[10])-1 0);spec[0Nn]];
    .qunit.assertEquals[r`status;`missing`ok;"a row stamped after the event is never attached, one stamped at it is"];
    .qunit.assertEquals[r`mid;0n 7f;"the early event gets null, not the future row"]};

test_tolerance_boundary_is_inclusive:{[t]
    r:.qasof.lookup[([] sym:enlist `a; time:enlist s 0; mid:enlist 1f);([] sym:`a`a`a; time:s[9 10 11]);spec[0D00:00:10]];
    .qunit.assertEquals[r`status;`ok`ok`stale;"age exactly at the tolerance is ok; one nanosecond past is not - here a second past"];
    .qunit.assertEquals[r`mid;1 1 1f;"a stale row still carries its value; the status says what to do with it"]};

test_null_tolerance_never_stales:{[t]
    r:.qasof.lookup[([] sym:enlist `a; time:enlist s 0; mid:enlist 1f);([] sym:enlist `a; time:enlist s 100000);spec[0Nn]];
    .qunit.assertEquals[r`status;enlist `ok;"no tolerance means no limit"]};

test_missing_reference_is_explicit:{[t]
    r:.qasof.lookup[refs[];ev[`c`a;5 5];spec[0D00:00:10]];
    .qunit.assertEquals[r`status;`missing`ok;"an unknown key, and a known one"];
    .qunit.assertEquals[first r`mid;0n;"missing means null values"];
    .qunit.assertEquals[first r`staleness;0Nn;"and a null staleness"]};

test_empty_reference_and_empty_events:{[t]
    r:.qasof.lookup[0#refs[];ev[`a`b;1 2];spec[0Nn]];
    .qunit.assertEquals[r`status;`missing`missing;"no reference at all is all missing, not an error"];
    e:.qasof.lookup[refs[];0#ev[`a;1];spec[0Nn]];
    .qunit.assertEquals[(count e;cols e);(0;`sym`time`id`mid`staleness`status);"no events is an empty result with the full shape"]};

test_reference_need_not_be_sorted:{[t]
    shuffled:refs[] 3 1 0 2;
    .qunit.assertEquals[.qasof.lookup[shuffled;ev[`a`a`a`b;4 10 25 5];spec[0Nn]];.qasof.lookup[refs[];ev[`a`a`a`b;4 10 25 5];spec[0Nn]];"order of the reference rows does not change any answer"]};

test_two_keys_do_not_mix:{[t]
    r2:([] venue:`x`y`x; sym:`a`a`a; time:s[0 3 8]; mid:1 2 3f);
    e:([] venue:`x`y`y; sym:`a`a`a; time:s[9 9 2]);
    r:.qasof.lookup[r2;e;spec2];
    .qunit.assertEquals[r`mid;3 2 0n;"x at 9 sees x's 8; y at 9 sees y's 3; y at 2 has nothing yet - x's row at 0 is not y's"];
    .qunit.assertEquals[r`status;`ok`ok`missing;"statuses follow"]};

test_attach_renames_and_several_columns:{[t]
    q:([] sym:enlist `a; time:enlist s 0; bid:enlist 1f; ask:enlist 3f);
    sp:`keys`ref_time`event_time`attach`tolerance!(enlist `sym;`time;`time;`ref_bid`ref_ask!`bid`ask;0Nn);
    r:.qasof.lookup[q;([] sym:enlist `a; time:enlist s 1);sp];
    .qunit.assertEquals[cols r;`sym`time`ref_bid`ref_ask`staleness`status;"renamed columns, then staleness and status"];
    .qunit.assertEquals[(first r`ref_bid;first r`ref_ask);(1f;3f);"values"]};

test_event_and_reference_time_columns_may_differ:{[t]
    q:([] sym:enlist `a; source_time:enlist s 0; mid:enlist 1f);
    sp:`keys`ref_time`event_time`attach`tolerance!(enlist `sym;`source_time;`time;enlist `mid;0Nn);
    r:.qasof.lookup[q;([] sym:enlist `a; time:enlist s 4);sp];
    .qunit.assertEquals[first r`staleness;0D00:00:04;"event time is read from the event column, reference time from its own"]};

test_refusals:{[t]
    .qunit.assertThrows[{.qasof.lookup[refs[];ev[`a;1];`keys`ref_time!(enlist `sym;`time)]};();"qasof.lookup: spec is missing event_time, attach, tolerance*";"every missing spec key is named"];
    .qunit.assertThrows[{.qasof.lookup[([] sym:enlist `a; time:enlist s 0);ev[`a;1];spec[0Nn]]};();"qasof_lookup: refs is missing*";"a reference table without the attached column"];
    .qunit.assertThrows[{.qasof.lookup[refs[];([] sym:enlist `a; time:enlist s 1; status:enlist `x);spec[0Nn]]};();"qasof.lookup: result columns already in events: status*";"a result column cannot overwrite an event column"];
    .qunit.assertThrows[{.qasof.lookup[refs[];ev[`a;1];spec[neg 0D00:00:01]]};();"qasof.lookup: tolerance must not be negative*";"a negative tolerance"];
    .qunit.assertThrows[{.qasof.lookup[refs[];ev[`a;1];spec[10]]};();"qasof.lookup: tolerance must be a timespan*";"a bare long is not a duration"]};

/ ------------------------------------------------------------- THE BUFFER

mkbuf:{[ret] .qetl.enrich.new `keys`time`retention`schema!(enlist `sym;`time;ret;0#refs[])}

test_buffer_sorts_out_of_order_inserts:{[t]
    b:.qetl.enrich.add[mkbuf[0D01:00:00];refs[] 2 0];
    b:.qetl.enrich.add[b;refs[] 3 1];
    held:.qetl.enrich.rows b;
    .qunit.assertEquals[held;`sym`time xasc refs[];"rows added in any order are held sorted by (keys, time)"];
    .qunit.assertEquals[(.qetl.enrich.lookup[b;ev[`a`a`a`b;4 10 25 5];spec[0Nn]])`mid;1 2 3 9f;"and a lookup is correct"]};

test_buffer_later_arrival_wins_a_time_tie:{[t]
    b:.qetl.enrich.add[mkbuf[0D01:00:00];([] sym:enlist `a; time:enlist s 0; mid:enlist 1f)];
    b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist s 0; mid:enlist 2f)];
    .qunit.assertEquals[first (.qetl.enrich.lookup[b;ev[enlist `a;enlist 1];spec[0Nn]])`mid;2f;"of two rows at the same time the later arrival prevails"]};

test_buffer_trims_but_keeps_latest_and_an_anchor:{[t]
    / Rows at 0, 10, 20, 30 for a; retention 5s: cut = 25s. Kept: 30, plus the
    / anchor, the newest row older than the cut (20).
    b:.qetl.enrich.add[mkbuf[0D00:00:05];([] sym:4#`a; time:s[0 10 20 30]; mid:1 2 3 4f)];
    .qunit.assertEquals[(.qetl.enrich.rows b)`mid;3 4f;"the window plus one anchor; older rows are gone"];
    r:.qetl.enrich.lookup[b;ev[`a`a;25 40];spec[0Nn]];
    .qunit.assertEquals[r`mid;3 4f;"an event at the retention edge still sees the row prevailing there"]};

test_buffer_never_drops_a_keys_latest_row:{[t]
    / Zero retention, a key that stops updating while another goes on.
    b:.qetl.enrich.add[mkbuf[0D00:00:00];([] sym:`a`b`b`b; time:s[0 1 50 100]; mid:1 2 3 4f)];
    h:.qetl.enrich.rows b;
    .qunit.assertEquals[h`mid;1 3 4f;"a keeps its only row; b keeps its latest and its one anchor, not the whole history"];
    b:.qetl.enrich.add[b;([] sym:enlist `b; time:enlist s 200; mid:enlist 5f)];
    .qunit.assertEquals[count .qetl.enrich.rows b;3;"a stays however far b moves"]};

test_buffer_stays_bounded:{[t]
    b:mkbuf[0D00:00:10];
    i:0;
    while[i<200; b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist s i; mid:enlist "f"$i)]; i+:1];
    / Window [189,199] holds 11 rows, plus the anchor at 188.
    .qunit.assertEquals[count .qetl.enrich.rows b;12;"200 inserts, 12 rows held: the retention window plus one anchor"]};

test_buffer_late_old_row_is_inserted_then_trimmed_when_beyond_retention:{[t]
    / Rows at 90 (the anchor: older than the 95 edge), 100, 105.
    b:.qetl.enrich.add[mkbuf[0D00:00:10];([] sym:3#`a; time:s[90 100 105]; mid:0 1 2f)];
    b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist s 102; mid:enlist 9f)];
    .qunit.assertEquals[(.qetl.enrich.rows b)`mid;0 1 9 2f;"a late row inside the window is inserted in order"];
    b:.qetl.enrich.add[b;([] sym:enlist `a; time:enlist s 3; mid:enlist 7f)];
    .qunit.assertEquals[(.qetl.enrich.rows b)`mid;0 1 9 2f;"one older than the anchor never displaces it"]};

test_buffer_refuses_a_row_missing_a_column:{[t]
    .qunit.assertThrows[{.qetl.enrich.add[mkbuf[0D00:00:10];([] sym:enlist `a; time:enlist s 0)]};();"qetl_enrich_add: refs is missing*";"the buffer's schema is checked on every add"]};

test_buffer_refuses_a_negative_retention:{[t]
    .qunit.assertThrows[{mkbuf[neg 0D00:00:01]};();"qetl.enrich.new: retention must not be negative*";"negative retention"]};

/ ------------------------------------------- THE BUFFER AGREES WITH THE PURE FUNCTION

test_buffer_and_pure_lookup_agree_over_the_full_history:{[t]
    / 300 reference rows over two venues and two syms, added in shuffled batches;
    / events from the retention edge on, for every key. The live buffer and
    / the pure function over the whole history (what a backfill has) agree.
    n:300;
    full:([] venue:n?`x`y; sym:n?`a`b; time:s[til n]; mid:"f"$til n);
    shuffled:full iasc n?1000;
    ret:0D00:01:00;
    b:.qetl.enrich.new `keys`time`retention`schema!(`venue`sym;`time;ret;0#full);
    k:0;
    while[k<n; b:.qetl.enrich.add[b;shuffled k+til 50]; k+:50];
    edge:s[n-61];
    m:200;
    events:([] venue:m?`x`y`z; sym:m?`a`b; time:edge+(m?60)*0D00:00:01; id:til m);
    sp:spec2;
    live:.qetl.enrich.lookup[b;events;`event_time`attach`tolerance!(`time;enlist[`mid]!enlist `mid;sp`tolerance)];
    pure:.qasof.lookup[full;events;sp];
    .qunit.assertEquals[live;pure;"the buffer's answer is the pure function's over the full history"]};

/ ------------------------------------------------------- THE MARKOUT USES IT

test_markout_at_horizons_still_reads_the_prevailing_quote:{[t]
    trades:([] time:enlist s 0; sym:enlist `EURUSD; side:enlist 1; trade_price:enlist 1.1; pip_factor:enlist 10000);
    quotes:([] sym:3#`EURUSD; time:s[8 0 20]; mid:1.2 1.1005 1.3);
    r:.qexec.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10];
    .qunit.assertEquals[r`ref_price;1.1005 1.2;"unsorted quotes: the 1s horizon reads the 0s quote, the 10s one the 8s quote - never the 20s one"]};
