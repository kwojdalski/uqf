// test_log.q - tests for src/etl/core/log.q (.qetl.log), the logging
// contract. Runs WITHOUT TorQ loaded, which is deliberate: the ETL core is
// unit-tested standalone, and the layer must behave identically in both
// transports or a test would pass here and the worker would behave
// differently in production.
//
// Output is captured by redirecting the fallback transport into a global,
// so a test can assert on what WAS and WAS NOT emitted - the second being
// the property that matters for a debug level.
//
// Load src/etl/core/log.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .logtest

captured:()

/ The real transport and renderer, saved before any setUp replaces them, so
/ the tests that swap one out can put it back.
real_emit:.qetl.log.emit
real_render:.qetl.log.render

/ Replace the transport's stdout write with a capture. `emit` calls -1 on
/ the fallback path; we shadow it by swapping `emit` itself for a version
/ that appends to `captured` under the same gating, so the gating logic is
/ what is tested, not bypassed.
setUp_capture:{[]
    `.logtest.captured set ();
    .qetl.log.debug[0b];
    .qetl.log.trace[0b];
    `.qetl.log.emit set {[level;id;msg]
        $[not .qetl.log.switched level; ::;
          `.logtest.captured set .logtest.captured,enlist (level;id;msg)]};
    }

tearDown_restore:{[] .qetl.log.debug[0b]; .qetl.log.trace[0b];}

last_msg:{[] last .logtest.captured[;2]}

/ --- structure: fields render k=v, in order ------------------------------

test_fields_render_as_key_value_pairs:{[t]
    .qetl.log.info[`w;"window published";`rows`worker!(1234;`demo)];
    .qunit.assertEquals[.logtest.last_msg[];"window published rows=1234 worker=`demo";"values are rendered unambiguously and in declared order"]};

test_a_timestamp_field_renders_readably:{[t]
    .qetl.log.info[`w;"at";(enlist `ts)!enlist 2026.09.11D00:00:00.000000000];
    .qunit.assertEquals[.logtest.last_msg[] like "*ts=2026.09.11D00:00:00.000000000*";1b;"a timestamp is not abbreviated or reformatted"]};

test_no_fields_gives_the_bare_text:{[t]
    .qetl.log.info[`w;"started";()!()];
    .qunit.assertEquals[.logtest.last_msg[];"started";"an empty dict adds nothing, not a trailing space"]};

/ Field order is preserved so a worker that always logs (worker;window;rows)
/ produces columns a human can scan down. A sorted or hashed order would
/ interleave them differently per line.
test_field_order_is_preserved:{[t]
    .qetl.log.info[`w;"x";`zebra`apple`mid!(1;2;3)];
    .qunit.assertEquals[.logtest.last_msg[];"x zebra=1 apple=2 mid=3";"declared order, not alphabetical"]};

/ --- levels: the id becomes TorQ's id column ----------------------------

test_the_level_is_carried:{[t]
    .qetl.log.warn[`w;"careful";()!()];
    .qunit.assertEquals[first last .logtest.captured;`WARNING;"warn emits at WARNING"]};

test_the_id_is_carried:{[t]
    .qetl.log.info[`demo_deals_backfill;"x";()!()];
    .qunit.assertEquals[.logtest.captured[0;1];`demo_deals_backfill;"the worker name is the id, so a published log is filterable by worker"]};

/ err records and RETURNS. TorQ's .lg.e throws or exits depending on .proc
/ state, which is right for init and wrong for a worker noting one failed
/ window and moving on.
test_err_does_not_throw:{[t]
    / (enlist `err)!enlist "boom", not `err!enlist "boom": a symbol ATOM
    / keying a list is a type error in q, and the first draft of this test
    / threw on that line - making it look as if err itself threw, which is
    / the opposite of what it was checking. The atom-vs-vector trap, again.
    / Assert the ABSENCE of a throw, which is the property, rather than a
    / particular return value - the transport's return (-1 from a stdout
    / write, () from .lg.l) is incidental and differs between them.
    threw:@[{.qetl.log.err[`w;"window failed";(enlist `err)!enlist "boom"]; 0b};::;{[e] 1b}];
    .qunit.assertEquals[threw;0b;"err records the failure and returns, leaving the caller to decide whether to abort"]};

/ --- debug: off by default, on per process ------------------------------

/ The property that matters. A DEBUG line must NOT appear unless asked for,
/ or a million-row backfill floods its log with per-window detail.
test_debug_is_suppressed_by_default:{[t]
    .qetl.log.dbg[`w;"per-window detail";()!()];
    .qunit.assertEquals[count .logtest.captured;0;"nothing emitted: debug is opt-in"]};

test_debug_appears_once_enabled:{[t]
    .qetl.log.debug[1b];
    .qetl.log.dbg[`w;"per-window detail";()!()];
    .qunit.assertEquals[(count .logtest.captured;first last .logtest.captured);(1;`DEBUG);"after debug[1b] the same call emits"]};

test_debug_can_be_switched_off_again:{[t]
    .qetl.log.debug[1b];
    .qetl.log.debug[0b];
    .qetl.log.dbg[`w;"x";()!()];
    .qunit.assertEquals[count .logtest.captured;0;"debug[0b] restores suppression"]};

/ Suppression is DEBUG-specific: enabling or disabling debug must not affect
/ the other three levels, or turning debug off would silence errors.
test_other_levels_are_unaffected_by_debug:{[t]
    .qetl.log.debug[0b];
    .qetl.log.info[`w;"a";()!()]; .qetl.log.warn[`w;"b";()!()]; .qetl.log.err[`w;"c";()!()];
    .qunit.assertEquals[.logtest.captured[;0];`INFO`WARNING`ERROR;"INFO, WARNING and ERROR emit regardless of the debug switch"]};

/ --- trace: below debug, with its own switch ----------------------------
/ .
/ TRACE carries every query a source is sent - one long line per window - so
/ it must stay off unless asked for, and switching debug on to follow a run
/ must not switch it on too.

test_trace_is_suppressed_by_default:{[t]
    .qetl.log.trc[`odbc;"sql sent";enlist[`statement]!enlist "SELECT 1"];
    .qunit.assertEquals[count .logtest.captured;0;"nothing emitted: trace is opt-in"]};

test_trace_appears_once_enabled:{[t]
    .qetl.log.trace[1b];
    .qetl.log.trc[`odbc;"sql sent";enlist[`statement]!enlist "SELECT 1"];
    .qunit.assertEquals[(.logtest.captured[;0];last_msg[]);(enlist `TRACE;"sql sent statement=\"SELECT 1\"");
        "after trace[1b] the statement is emitted, at TRACE"]};

test_debug_does_not_switch_trace_on:{[t]
    .qetl.log.debug[1b];
    .qetl.log.trc[`odbc;"sql sent";()!()];
    .qetl.log.dbg[`w;"window start";()!()];
    .qunit.assertEquals[.logtest.captured[;0];enlist `DEBUG;"debug alone shows DEBUG, not every query"]};

test_trace_does_not_switch_debug_on:{[t]
    .qetl.log.trace[1b];
    .qetl.log.trc[`odbc;"sql sent";()!()];
    .qetl.log.dbg[`w;"window start";()!()];
    .qunit.assertEquals[.logtest.captured[;0];enlist `TRACE;"trace alone shows queries, not DEBUG detail"]};

test_enabled_reports_the_trace_gate:{[t]
    off:.qetl.log.enabled `TRACE;
    .qetl.log.trace[1b];
    .qunit.assertEquals[(off;.qetl.log.enabled `TRACE);01b;"enabled follows the trace switch"]};

/ --- scoped context ----------------------------------------------------
/ .
/ What a line is about (run, worker, source, window, attempt), added to every
/ line inside the scope, and gone when the scope ends - however it ends.

test_a_line_inside_a_context_carries_it:{[t]
    .qetl.log.with_context[`worker`source!(`w;`s);{.qetl.log.info[`x;"fetched";enlist[`rows]!enlist 3]};enlist(::)];
    .qunit.assertEquals[last_msg[];"fetched rows=3 worker=`w source=`s";"own fields first, then the context"]};

test_a_lines_own_field_wins_over_the_context:{[t]
    .qetl.log.with_context[enlist[`range_from]!enlist 1;{.qetl.log.info[`x;"m";enlist[`range_from]!enlist 2]};enlist(::)];
    .qunit.assertEquals[last_msg[];"m range_from=2";"a line's own value, not the context's"]};

test_contexts_nest_and_unwind:{[t]
    .qetl.log.with_context[enlist[`worker]!enlist `w;
        {.qetl.log.with_context[enlist[`attempt]!enlist 2;{.qetl.log.info[`x;"in";()!()]};enlist(::)];
         .qetl.log.info[`x;"out";()!()]};enlist(::)];
    .qunit.assertEquals[.logtest.captured[;2];("in worker=`w attempt=2";"out worker=`w");
        "the inner scope adds its field, and leaving it takes only that field away"]};

test_the_context_is_restored_when_the_work_throws:{[t]
    r:@[.qetl.log.with_context[enlist[`worker]!enlist `w;;enlist(::)];{'"boom"};{x}];
    .qunit.assertEquals[(r;.qetl.log.context);("boom";()!());
        "the error reaches the caller, and the context cannot leak into the next worker"]};

test_a_unary_primitive_argument_is_passed_not_mistaken_for_no_arguments:{[t]
    / neg is 101h, like ::; only :: itself means "call f with no arguments".
    .qunit.assertEquals[.qetl.log.with_context[enlist[`worker]!enlist `w;{x 5};enlist neg];-5;
        "f is applied to neg"]};

test_with_context_returns_what_the_work_returns:{[t]
    .qunit.assertEquals[.qetl.log.with_context[enlist[`worker]!enlist `w;{x+1};enlist 1];2;"a pass-through"]};

/ --- values render IN FULL ---------------------------------------------
/ .
/ .Q.s1 stops at the console width on KDB-X - 79 characters and "..". A
/ traced query is exactly the long field that was meant to be read whole.

test_a_long_string_field_is_not_cut_at_the_console_width:{[t]
    s:500#"select x from t where s=1 ";
    r:.qetl.log.render[enlist[`statement]!enlist s];
    .qunit.assertEquals[(count r;r like "*..");(count["statement="]+502;0b);
        "all 500 characters, quoted, and no trailing .."]};

test_a_string_is_quoted_exactly_as_q_writes_it:{[t]
    / every byte, short enough that -3! is not cut either
    strs:{"a",x,"b"} each `char$til 256;
    .qunit.assertEquals[.qetl.log.quoted each strs;-3!'strs;
        "quotes, backslashes, newlines and every control or high byte escaped as -3! does"]};

test_a_long_list_field_gets_the_widest_console:{[t]
    / not unlimited - q's widest console is 2000 - but far past 80
    .qunit.assertTrue[1000<count .qetl.log.value1 til 1000;"a 3,889-character list is not cut at 80"]};

test_rendering_puts_the_console_width_back:{[t]
    c:system"c";
    .qetl.log.render[`a`b!(til 1000;"x")];
    .qunit.assertEquals[system"c";c;"the widened console is restored after rendering"]};

/ --- lazy rendering -----------------------------------------

/ The suppression check must precede rendering, because DEBUG is off by
/ default and is the level a worker emits per WINDOW - a million-row
/ backfill with debug off would otherwise render and discard one message
/ per window.
/ .
/ Proved by a field value whose RENDERING throws: if the message were built
/ before the gate, the throw would escape.
test_a_suppressed_message_is_not_rendered:{[t]
    `.logtest.rendered set 0b;
    `.qetl.log.render set {[fields] `.logtest.rendered set 1b; "x"};
    .qetl.log.dbg[`w;"expensive";(enlist `k)!enlist 1];
    r:.logtest.rendered;
    `.qetl.log.render set .logtest.real_render;
    .qunit.assertEquals[r;0b;"debug off means the fields are never rendered, not rendered and discarded"]};

test_an_emitted_message_is_rendered:{[t]
    `.logtest.rendered set 0b;
    `.qetl.log.render set {[fields] `.logtest.rendered set 1b; "x"};
    .qetl.log.info[`w;"wanted";(enlist `k)!enlist 1];
    r:.logtest.rendered;
    `.qetl.log.render set .logtest.real_render;
    .qunit.assertEquals[r;1b;"an INFO message is rendered, or the gate is refusing everything"]};

test_enabled_reports_the_gate:{[t]
    .qetl.log.debug[0b];
    off:.qetl.log.enabled `DEBUG;
    .qetl.log.debug[1b];
    on:.qetl.log.enabled `DEBUG;
    .qetl.log.debug[0b];
    .qunit.assertEquals[(off;on;.qetl.log.enabled `ERROR);(0b;1b;1b);"exported so a caller can skip building an expensive field value itself"]};

/ --- transport detection: the bug that made every process use the fallback --

/ `l in key `.lg, NOT `lg in key `.: key `. lists the root namespace's own
/ names and namespaces are not among them, so the latter is ALWAYS false.
/ The first draft used it, and this layer took the stdout fallback in every
/ process - including ones with TorQ fully loaded - with correct level
/ gating, the wrong transport, and nothing to say so. This pins the correct
/ check by building a minimal .lg and requiring it to be detected, then
/ removing it and requiring it not to be.
test_torq_is_detected_when_its_logging_namespace_exists:{[t]
    `.lg.l set {[a;b;c;d;e;f] `.logtest.torq_got set (a;d;e)};
    `.lg.outmap set `ERR`INFO`WARN!2 1 1;
    `.lg.pubmap set `ERR`INFO`WARN!1 0 1;
    detected:.qetl.log.torq_loaded[];
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[detected;1b;"a populated .lg is detected, so TorQ's transport is used rather than the fallback"]};

test_torq_is_not_detected_when_absent:{[t]
    .qunit.assertEquals[.qetl.log.torq_loaded[];0b;"no .lg.l means the fallback, and no error"]};

/ With TorQ present the message must reach .lg.l - the whole point of the
/ layer being thin. Uses the real emit (not the capture stub), so this is
/ the transport being exercised, not the gating.
test_a_message_reaches_torqs_lg_when_present:{[t]
    `.qetl.log.emit set .logtest.real_emit;
    `.lg.l set {[level;proctype;proc;id;message;dict] `.logtest.torq_got set (level;id;message)};
    `.lg.outmap set `ERR`INFO`WARN!2 1 1;
    `.lg.pubmap set `ERR`INFO`WARN!1 0 1;
    `.logtest.torq_got set ();
    .qetl.log.info[`w;"routed";(enlist `k)!enlist 1];
    got:.logtest.torq_got;
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[got;(`INFO;`w;"routed k=1");"level, id and rendered message arrive at .lg.l"]};

test_register_adds_this_files_levels_to_torqs_routing_tables_when_present:{[t]
    `.lg.l set {[a;b;c;d;e;f] ::};
    `.lg.outmap set `ERR`INFO`WARN!2 1 1;
    `.lg.pubmap set `ERR`INFO`WARN!1 0 1;
    r:.qetl.log.register[];
    outm:.lg.outmap;
    pubm:.lg.pubmap;
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[(r;outm`DEBUG`TRACE);(1b;0 0);"DEBUG and TRACE are registered, and OFF by default so nothing changes for an existing process"];
    .qunit.assertEquals[(outm`INFO`WARNING`ERROR;pubm`INFO`WARNING`ERROR);(1 1 2;0 1 1);
        "INFO, WARNING and ERROR route as TorQ routes INF, WARN and ERR - unregistered, INFO would print nothing"]};

/ The bug the registration exists for: TorQ's outmap knows INF, not INFO,
/ and a level it does not hold is never printed. The first line logged must
/ register the names, with no init call anyone could forget.
test_the_first_line_logged_under_torq_is_printed:{[t]
    `.qetl.log.emit set .logtest.real_emit;
    `.lg.l set {[level;proctype;proc;id;message;dict] `.logtest.torq_got set (level;id;message)};
    `.lg.outmap set `ERR`INFO`WARN!2 1 1;
    `.lg.pubmap set `ERR`INFO`WARN!1 0 1;
    `.logtest.torq_got set ();
    .qetl.log.warn[`w;"first";()!()];
    got:.logtest.torq_got;
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[got;(`WARNING;`w;"first");"a WARNING reaches TorQ's logger before anything registered it"]};

/ --- without TorQ, register is a harmless no-op -------------------------

test_register_without_torq_is_a_no_op:{[t]
    .qunit.assertEquals[.qetl.log.register[];0b;"no .lg to register with, and no error either - the core loads standalone"]};

test_the_level_set_matches_torqs_plus_trace_and_debug:{[t]
    .qunit.assertEquals[.qetl.log.levels;`TRACE`DEBUG`INFO`WARNING`ERROR;"the five full names, and nothing else"]};


/ log.q keeps its own copy of the renderer (processes load it without the
/ library); it must not drift from .qrender's.
test_the_log_renderer_matches_the_librarys:{[t]
    / `samples`, not `vs`: vs is a q builtin.
    samples:(300#"a";til 500;`a`b!1 2;"tab\there";([] x:til 50));
    .qunit.assertEquals[.qetl.log.value1 each samples;.qrender.full each samples;"value1 and .qrender.full agree"]};

\d .
