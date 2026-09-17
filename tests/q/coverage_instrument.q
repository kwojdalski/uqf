/ coverage_instrument.q - runtime CALL coverage for this tree's q functions.
/ .
/ Loaded by `scripts/test.py coverage`, which appends the target list and the
/ report path and then calls .qqc.install. Not loaded by any other lane.
/ .
/ WHY WRAPPING RATHER THAN A GREP. q has no coverage tool. The obvious
/ substitute - grep each function name in tests/ - counts a function named in
/ a COMMENT as tested, and misses one reached only through a dispatch table.
/ Wrapping counts calls, which is the thing actually being asked about.
/ .
/ WHEN IT MUST RUN. After every source file has loaded and before any test
/ file does. Earlier, and the load that defines the function overwrites the
/ wrapper. run_tests.q loads it at exactly that point.
/ .
/ WHAT IT CANNOT SEE, stated here because a coverage number that overstates
/ itself is worse than none. A function whose VALUE was captured before the
/ wrapper was installed is called through that copy, not through the wrapper:
/ .
/   .qio.memory is (enlist `write)!enlist write_memory - the dict holds the
/   function, so .qio.write_memory reports uncalled while every bounded
/   worker in the suite runs through it.
/ .
/   .qsrc.register stores a source's declaration, `query` included, so a
/   source's query function reports uncalled for the same reason.
/ .
/ So an uncalled result is EVIDENCE TO CHECK, not a verdict. A called result
/ is conclusive: it ran.

\d .qqc

hits:(`symbol$())!`long$();
orig:(`symbol$())!();

/ Record one call. Separate from the wrapper body so the generated string
/ stays short enough to read when something goes wrong with it.
hit:{[nm] hits[nm]:1+0^hits nm;}

/ Replace one function with an arity-preserving counter.
/ .
/ ARITY MATTERS: q dispatches on it, so a wrapper taking `x` where the
/ original took three arguments turns every call into a rank error. The
/ params come from `(value f)[1]`, and a NILADIC lambda reports those as
/ `enlist ` rather than an empty vector - which reads as one argument named
/ "" if taken literally, so it is special-cased.
/ @param nm the fully-qualified name, e.g. `.qfwd.fwd_cont
/ @return `wrapped, `skipped (not a lambda) or `failed
wrap:{[nm]
    f:@[value;nm;{[e] (::)}];
    if[not 100h=type f; :`skipped];
    ps:(value f)[1];
    n:$[ps~enlist `;0;count ps];
    orig[nm]:f;
    s:string nm;
    args:$[0=n; ""; ";" sv string ps];
    body:"{[",args,"] .qqc.hit[`",s,"]; .qqc.orig[`",s,"][",args,"]}";
    @[{[nm;b] nm set value b; `wrapped}[nm];body;{[e] `failed}]}

/ Wrap every target. Reports the tally so a run that silently wrapped
/ nothing - a stale target list, a namespace that failed to load - cannot be
/ mistaken for a run in which everything was covered.
install:{[]
    r:wrap each targets;
    -1 "coverage: instrumented ",.Q.s1 count each group r;
    count targets}

/ Write the names that were never called, one per line, for scripts/test.py
/ to read back. A file rather than stdout: the suite's own output is long,
/ and parsing a list out of it would break the first time a test printed
/ something that looked like a name.
report:{[]
    never:targets where not targets in key hits;
    (hsym `$report_path) 0: string never;
    / PARENTHESISED deliberately: q evaluates right to left, so
    / `count targets - count never` is `count (targets - count never)` -
    / which throws 'type on symbols rather than counting anything. It did,
    / the first time this ran.
    called:(count targets) - count never;
    -1 "coverage: ",string[called]," of ",string[count targets],
       " declared q functions were called";
    count never}

\d .
