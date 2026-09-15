// etl_test_doubles.q - adapter doubles for testing stateful control flow
// (.qetldbl). Implements requirement E-19.
//
// E-19 permits doubling exactly four things - fetch, publish, checkpoint and
// logging - and then says the part that matters twice:
//
//   "Do not let adapter doubles substitute for transform and coverage tests.
//    Doubling the edges is not the same as testing the middle."
//
// That warning is ENFORCED here rather than repeated as a comment. The set of
// doubleable adapters is closed, and asking to double a transform or the
// coverage ledger throws with E-19's own wording. The failure it prevents is
// specific and plausible: a suite where .qcov.stage_completion is a double
// passes whatever the real ledger does, so the interval arithmetic that
// decides whether a range is complete goes untested while the test names all
// say "coverage".
//
// So: the EDGES are doubled here; the MIDDLE (.qcov's arithmetic, and any
// transform) is tested against the real implementation in test_coverage.q.

\d .qetldbl

/ The closed set E-19 permits. Everything about this list is deliberate,
/ including its shortness.
doubleable:`fetch`publish`checkpoint`log

/ Named explicitly so the refusal message can say WHY, rather than only that
/ the name is unknown. These are the things whose real behaviour the suite
/ exists to check.
protected:`transform`coverage`stage_completion`compose`gaps`is_covered

/ adapter -> the function standing in for it.
installed:(`symbol$())!();

/ Every doubled call, in order, as (adapter;args). A list rather than a
/ counter because ORDER is what the lifecycle tests assert: publish must
/ precede checkpoint, and a counter cannot tell you it did.
calls:();

/ Install a double.
/ @throws error when the adapter is protected, or simply not doubleable
/ @eg .qetldbl.install[`fetch;{[a;b] ([] px:1 2 3.)}]
install:{[adapter;impl]
    if[adapter in protected;
        / Short by necessity: a thrown string is truncated at 255 bytes, and
        / E-19's full sentence plus the adapter name exceeded it. The quote
        / is in this file's header; what belongs here is the instruction.
        '"install: ",string[adapter]," must not be doubled (E-19: doubling the edges is not testing the middle) - test it against the real implementation"];
    if[not adapter in doubleable;
        '"install: ",string[adapter]," is not a doubleable adapter - E-19 permits ",", " sv string doubleable];
    installed[adapter]:impl;
    adapter}

/ Call a doubled adapter, recording the call.
/ .
/ Arguments are passed as a LIST and applied with `.`, for the reason
/ .qwrt.commit documents: a fully-applied projection in q is a call, not a
/ deferred one, so building the argument would perform the effect. And
/ `enlist(::)` rather than `()`, because `f . ()` is a type error.
/ @throws error when nothing is installed for the adapter
call:{[adapter;args]
    if[not adapter in key installed;
        '"call: no double installed for ",string[adapter]," - a test that reaches an undoubled adapter would hit the real one"];
    calls,:enlist (adapter;args);
    (installed adapter) . $[0=count args; enlist(::); args]}

/ Clear every double and every recorded call. Call from setUp, so no test
/ depends on another's doubles - the failure mode otherwise is a suite that
/ passes in order and fails in isolation.
reset:{[]
    installed::(`symbol$())!();
    calls::();
    ()}

/ How many times an adapter was called.
/ .
/ Guarded on empty: `first each ()` is (), and `where`/`sum` over that is a
/ type error rather than a zero.
/ "j"$ because `sum` of a boolean vector is an INT, and an int 3 does not
/ match a long 3 under ~ - which is what assertEquals uses. The test would
/ fail with "expected 3 3 3, actual (3;3i;3i)", which reads like an arity
/ problem rather than a type one.
call_count:{[adapter]
    if[0=count calls; :0j];
    "j"$sum adapter~/: first each calls}

/ The adapters that were called, in order. This is the assertion E-19's
/ "stateful control flow" actually needs: that publish happened before
/ checkpoint, not merely that both did.
call_order:{[]
    if[0=count calls; :`symbol$()];
    (,/) first each calls}

/ The arguments of the nth call to an adapter (0-based).
call_args:{[adapter;n]
    matching:calls where adapter~/: first each calls;
    if[n>=count matching;
        '"call_args: ",string[adapter]," was called ",string[count matching]," time(s), no call ",string n];
    last matching n}

\d .
