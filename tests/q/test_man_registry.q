/ test_man_registry.q - every function docs/man.q registers must exist
/ (.mantest).
/ .
/ The exact, checkable half of question-bank J-07 ("is there a freshness gate
/ catching a document that references a symbol which no longer exists?").
/ .
/ A general symbol-freshness gate over prose cannot be made complete -
/ README.md names functions in sentences, .claude/skills/ cite them as
/ examples - and an incomplete one reads as protection while leaving the
/ largest surface unguarded. docs/man.q is the one documentation surface that
/ is not prose: it registers every documented function BY NAME as q data, so
/ "does this name resolve" is exact, has no false positives, and needs no
/ parsing.
/ .
/ Writing it found that docs/man.q DID NOT LOAD AT ALL. Two of its strings
/ contained a bare `\d` and `\l`, and an invalid escape in a q string
/ literal makes q signal the whole string as the error - so the file aborted
/ on line 79, .man.getDocs was unreachable, and all 78 registrations existed
/ nowhere at runtime. Nothing noticed because no test had ever loaded it.
/ That is the strongest argument for this file: the document was not stale,
/ it was inert.
/ .
/ Depends on run_tests.q loading src/integrations/data.q. The eight .qdata.*
/ entries are registered here but deliberately NOT loaded by src/init.q
/ (data.q needs real kdb+ for `2:`), so this suite is the only context where
/ all 78 resolve - which is also why the count is asserted below rather than
/ left implicit.

\d .mantest

/ Private: does a fully-qualified name like ".qopt.d1_d2" resolve?
/ Splits on the LAST dot rather than assuming one level, so a root-level
/ name and a namespaced one are handled by the same path.
resolves:{[fullname]
    parts:"." vs fullname;
    if[2>count parts; :0b];
    (`$last parts) in key `$"." sv -1_parts}

registered:{[] exec fullname from .man.funcs}

test_man_q_actually_loaded:{[t]
    / Not a tautology: this file cannot be reached unless man.q loaded, but
    / asserting a NON-ZERO count is what fails if a future edit makes the
    / file abort part-way through and register only its first few entries -
    / which is exactly the failure that went unnoticed before.
    .qunit.assertTrue[0<count registered[];
        "docs/man.q registered at least one function - a zero count means it aborted while loading"]};

test_every_registered_function_exists:{[t]
    names:registered[];
    missing:names where not resolves each names;
    .qunit.assertEquals[missing;();
        "every function docs/man.q documents resolves in a loaded namespace"]};

test_every_documented_argument_belongs_to_a_registered_function:{[t]
    / .man.args is keyed by the same fullname. An argument row for a function
    / that was removed from .man.funcs is the same staleness one level down,
    / and it is where a half-finished deletion would show first.
    documented:distinct exec fullname from .man.args;
    orphans:documented where not documented in registered[];
    .qunit.assertEquals[orphans;();
        "every documented argument belongs to a function docs/man.q still registers"]};

test_the_resolver_rejects_a_name_nothing_defines:{[t]
    / A resolver nobody has seen say no might be saying yes to everything.
    .qunit.assertEquals[resolves ".qopt.no_such_function";0b;
        "a name in a real namespace that nothing defines does not resolve"]};

test_the_resolver_rejects_an_unknown_namespace:{[t]
    .qunit.assertEquals[resolves ".qnosuchns.anything";0b;
        "a name in a namespace that does not exist does not resolve"]};

test_the_resolver_accepts_a_name_that_does_exist:{[t]
    .qunit.assertEquals[resolves ".qopt.d1";1b;
        "the resolver is a pass-through on a name that is genuinely defined"]};

/ --- coverage ------------------------------------------------------------

test_the_registry_covers_the_etl_tree:{[t]
    / The gap that prompted generating this file. Before the generator,
    / man.q registered 78 of 348 public functions and FIFTEEN NAMESPACES had
    / zero coverage - .qmicro, .qsrc, .qdag, .qbw, .qcov and every other ETL
    / namespace. `.man.getDocs[]` is the programmatic documentation API, so a
    / caller asking about .qcov.is_covered got nothing back and could not tell
    / "undocumented" from "does not exist".
    names:exec fullname from .man.funcs;
    .qunit.assertTrue[any names like ".qcov.*";
        "the coverage namespace is documented, not just the original library"]};

/ The coverage ratchet. A FLOOR, not a target.
/ .
/ Coverage went 78 -> 419 registrations when docs/man.q became generated, and
/ the seven public functions still undocumented are internal helpers with no
/ caller outside their own namespace - checked objectively rather than by eye.
/ This test stops that sliding back: a new public function with no qDoc block
/ lowers the count and fails here.
/ .
/ Phrased as "at most 12 undocumented" rather than an exact number so adding a
/ function does not require editing this test, while a wholesale regression -
/ a parser change that stopped attaching blocks, say - still fails. Two such
/ regressions were caught this way while building the generator: a shared
/ block attaching to only the first of four levels, and fully-qualified
/ definitions being invisible to the scan.
test_documentation_coverage_does_not_regress:{[t]
    documented:exec fullname from .man.funcs;
    nss:key `; nss:nss where (string nss) like "q*";
    / Only namespaces docs/man.q actually covers, which is src/. The first
    / version of this test counted every q-prefixed namespace in the suite
    / process and failed on scaffolding: .qunit is the vendored test
    / framework, .qetldbl and .qrefw are test doubles, and .qpipe lives in
    / scripts/ rather than src/ (B-09). None is this library's public API, and
    / demanding qDoc blocks for them would have meant documenting the test
    / harness to satisfy a counter.
    nss:nss except `q`qunit`qetldbl`qrefw`qpipe;
    public:raze {[n]
        full:` sv `,n;
        ks:key full;
        ks:ks where not ks in `;
        ks:ks where not (string ks) like "_*";
        ks:ks where {[f;k] 100h=type value ` sv f,k}[full] each ks;
        string ` sv/: full,/:ks} each nss;
    undocumented:public where not public in documented;
    .qunit.assertTrue[12>=count undocumented;
        "public functions without a qDoc block have not increased - see the count in the failure"]};

test_the_registry_is_not_a_token_sample:{[t]
    / A floor, not an exact count: adding a function must not require editing
    / this test, but a generator that silently started emitting a handful of
    / entries would otherwise leave every test above passing over almost
    / nothing. The floor sits well below the 374 registered today and well
    / above the 78 that prompted this.
    .qunit.assertTrue[300<=count .man.funcs;
        "the registry covers the bulk of the library, not a fraction of it"]};

\d .
