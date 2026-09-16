// test_log.q - tests for src/etl/core/log.q (.qlog), the K-01 logging
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

/ The real transport, saved before any setUp replaces it, so the one test
/ that exercises the transport itself can put it back.
real_emit:.qlog.emit

/ Replace the transport's stdout write with a capture. `emit` calls -1 on
/ the fallback path; we shadow it by swapping `emit` itself for a version
/ that appends to `captured` under the same gating, so the gating logic is
/ what is tested, not bypassed.
setUp_capture:{[]
    `.logtest.captured set ();
    .qlog.debug[0b];
    `.qlog.emit set {[level;id;msg]
        $[(level=`DBG) and not .qlog.debug_enabled; ::;
          `.logtest.captured set .logtest.captured,enlist (level;id;msg)]};
    }

tearDown_restore:{[] .qlog.debug[0b];}

last_msg:{[] last .logtest.captured[;2]}

/ --- structure: fields render k=v, in order ------------------------------

test_fields_render_as_key_value_pairs:{[t]
    .qlog.info[`w;"window published";`rows`worker!(1234;`demo)];
    .qunit.assertEquals[.logtest.last_msg[];"window published rows=1234 worker=`demo";"values are rendered unambiguously and in declared order"]};

test_a_timestamp_field_renders_readably:{[t]
    .qlog.info[`w;"at";(enlist `ts)!enlist 2026.09.11D00:00:00.000000000];
    .qunit.assertEquals[.logtest.last_msg[] like "*ts=2026.09.11D00:00:00.000000000*";1b;"a timestamp is not abbreviated or reformatted"]};

test_no_fields_gives_the_bare_text:{[t]
    .qlog.info[`w;"started";()!()];
    .qunit.assertEquals[.logtest.last_msg[];"started";"an empty dict adds nothing, not a trailing space"]};

/ Field order is preserved so a worker that always logs (worker;window;rows)
/ produces columns a human can scan down. A sorted or hashed order would
/ interleave them differently per line.
test_field_order_is_preserved:{[t]
    .qlog.info[`w;"x";`zebra`apple`mid!(1;2;3)];
    .qunit.assertEquals[.logtest.last_msg[];"x zebra=1 apple=2 mid=3";"declared order, not alphabetical"]};

/ --- levels: the id becomes TorQ's id column ----------------------------

test_the_level_is_carried:{[t]
    .qlog.warn[`w;"careful";()!()];
    .qunit.assertEquals[first last .logtest.captured;`WARN;"warn emits at WARN"]};

test_the_id_is_carried:{[t]
    .qlog.info[`demo_deals_backfill;"x";()!()];
    .qunit.assertEquals[.logtest.captured[0;1];`demo_deals_backfill;"the worker name is the id, so a published log is filterable by worker"]};

/ err records and RETURNS. TorQ's .lg.e throws or exits depending on .proc
/ state, which is right for init and wrong for a worker noting one failed
/ window and moving on (M-05).
test_err_does_not_throw:{[t]
    / (enlist `err)!enlist "boom", not `err!enlist "boom": a symbol ATOM
    / keying a list is a type error in q, and the first draft of this test
    / threw on that line - making it look as if err itself threw, which is
    / the opposite of what it was checking. The atom-vs-vector trap, again.
    / Assert the ABSENCE of a throw, which is the property, rather than a
    / particular return value - the transport's return (-1 from a stdout
    / write, () from .lg.l) is incidental and differs between them.
    threw:@[{.qlog.err[`w;"window failed";(enlist `err)!enlist "boom"]; 0b};::;{[e] 1b}];
    .qunit.assertEquals[threw;0b;"err records the failure and returns, leaving the caller to decide whether to abort"]};

/ --- debug: off by default, on per process ------------------------------

/ The property that matters. A DBG line must NOT appear unless asked for,
/ or a million-row backfill floods its log with per-window detail.
test_debug_is_suppressed_by_default:{[t]
    .qlog.dbg[`w;"per-window detail";()!()];
    .qunit.assertEquals[count .logtest.captured;0;"nothing emitted: debug is opt-in"]};

test_debug_appears_once_enabled:{[t]
    .qlog.debug[1b];
    .qlog.dbg[`w;"per-window detail";()!()];
    .qunit.assertEquals[(count .logtest.captured;first last .logtest.captured);(1;`DBG);"after debug[1b] the same call emits"]};

test_debug_can_be_switched_off_again:{[t]
    .qlog.debug[1b];
    .qlog.debug[0b];
    .qlog.dbg[`w;"x";()!()];
    .qunit.assertEquals[count .logtest.captured;0;"debug[0b] restores suppression"]};

/ Suppression is DBG-specific: enabling or disabling debug must not affect
/ the other three levels, or turning debug off would silence errors.
test_other_levels_are_unaffected_by_debug:{[t]
    .qlog.debug[0b];
    .qlog.info[`w;"a";()!()]; .qlog.warn[`w;"b";()!()]; .qlog.err[`w;"c";()!()];
    .qunit.assertEquals[.logtest.captured[;0];`INF`WARN`ERR;"INF, WARN and ERR emit regardless of the debug switch"]};

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
    `.lg.outmap set `ERR`INF`WARN!2 1 1;
    `.lg.pubmap set `ERR`INF`WARN!1 0 1;
    detected:.qlog.torq_loaded[];
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[detected;1b;"a populated .lg is detected, so TorQ's transport is used rather than the fallback"]};

test_torq_is_not_detected_when_absent:{[t]
    .qunit.assertEquals[.qlog.torq_loaded[];0b;"no .lg.l means the fallback, and no error"]};

/ With TorQ present the message must reach .lg.l - the whole point of the
/ layer being thin. Uses the real emit (not the capture stub), so this is
/ the transport being exercised, not the gating.
test_a_message_reaches_torqs_lg_when_present:{[t]
    `.qlog.emit set .logtest.real_emit;
    `.lg.l set {[level;proctype;proc;id;message;dict] `.logtest.torq_got set (level;id;message)};
    `.lg.outmap set `ERR`INF`WARN!2 1 1;
    `.lg.pubmap set `ERR`INF`WARN!1 0 1;
    `.logtest.torq_got set ();
    .qlog.info[`w;"routed";(enlist `k)!enlist 1];
    got:.logtest.torq_got;
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[got;(`INF;`w;"routed k=1");"level, id and rendered message arrive at .lg.l"]};

test_register_adds_dbg_to_torqs_routing_tables_when_present:{[t]
    `.lg.l set {[a;b;c;d;e;f] ::};
    `.lg.outmap set `ERR`INF`WARN!2 1 1;
    `.lg.pubmap set `ERR`INF`WARN!1 0 1;
    r:.qlog.register[];
    outm:.lg.outmap;
    ![`.lg;();0b;`l`outmap`pubmap];
    .qunit.assertEquals[(r;outm`DBG);(1b;0);"DBG is registered, and OFF by default so nothing changes for an existing process"]};

/ --- without TorQ, register is a harmless no-op -------------------------

test_register_without_torq_is_a_no_op:{[t]
    .qunit.assertEquals[.qlog.register[];0b;"no .lg to register with, and no error either - the core loads standalone"]};

test_the_level_set_matches_torqs_plus_debug:{[t]
    .qunit.assertEquals[.qlog.levels;`DBG`INF`WARN`ERR;"exactly TorQ's three plus DBG, so outmap and pubmap apply unchanged"]};

\d .
