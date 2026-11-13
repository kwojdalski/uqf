/ test_declared.q - the two rules every declaration shares: it refuses a key it
/ does not take, and it checks a function it is given at declaration time
/ (.qetl.declared, #2112, #2114). Load src/init.q, src/etl/init.q,
/ tests/lib/qunit.q and tests/lib/support.q before this file.
/ .
/ The two GATES here are what stop the rules drifting again. Both are
/ discovered rather than listed: a new `define` is found by scanning the
/ namespaces, and a new hand-rolled "is it a function" is found by scanning
/ the source. A new declaration that skips either rule fails a test that
/ names it, and fixing that is a line in the registry below, not a hunt.


/ --- the predicate -----------------------------------------------------------

.qt.qetl.declared.test_a_lambda_a_projection_a_composition_and_an_iterator_are_callable:{[t]
    c:.qetl.declared.callable;
    .qunit.assertEquals[c each ({x};{x+y}[1];{x}\;neg;'[neg;abs];{x}/); 6#1b; "everything q can apply"];
    .qunit.assertEquals[c {x+y}[1;]; 1b; "a projection - what binding a handle or a config produces"]};

.qt.qetl.declared.test_the_null_function_and_data_are_not_callable:{[t]
    c:.qetl.declared.callable;
    .qunit.assertEquals[c (::); 0b; "(::) is type 101h like neg, and would pass a type range"];
    .qunit.assertEquals[c each (1;`a;"x";();1 2 3;(`a`b!1 2);([] a:1 2)); 7#0b; "data"]};

.qt.qetl.declared.test_only_a_lambda_has_a_parameter_list_to_read:{[t]
    .qunit.assertEquals[.qetl.declared.lambda each ({x};{x+y}[1;];neg;(::)); 1000b; "arity is checked on a lambda, and only on one"]};

/ --- the unknown-key refusal -------------------------------------------------

.qt.qetl.declared.test_an_unknown_key_is_refused_naming_it_and_the_keys_taken:{[t]
    .qunit.assertThrows[.qetl.declared.refuse_unknown["define: x";"a thing";;`a`b];`a`zz`yy!1 2 3;
        "define: x declares zz, yy, which a thing does not take - the keys are a, b";
        "every unknown key, then every key it takes"];
    .qunit.assertEquals[.qetl.declared.refuse_unknown["define: x";"a thing";`a`b!1 2;`a`b`c]; ::; "known keys pass"]};

.qt.qetl.declared.test_a_non_callable_is_refused_naming_its_key:{[t]
    .qunit.assertThrows[.qetl.declared.require_callable["define: x";`on_batch;;"a function of the rows"];(::);
        "define: x's on_batch must be a function of the rows"; "(::) is not a function"]};

/ --- GATE: every define refuses a misspelled key -----------------------------

/ Every `define` entry point, found by scanning the namespaces that hold
/ functions. Test scaffolding is not a declaration.
.qt.qetl.declared.defines:{[]
    nss:.qf.util.functional[];
    nss:nss where not any nss like/: (".qt*";".qunit*");
    nss:nss where {100h=type @[value;` sv x,`define;{[e] (::)}]} each nss;
    ` sv/: nss,\:`define}

/ A define the probe cannot reach is listed with the reason, never silently.
.qt.qetl.declared.exempt:(`symbol$())!();
.qt.qetl.declared.exempt[`.qf.exec.define]:"the library's matching-method registry (src/portfolio) cannot call .qetl; it refuses an unknown key itself (require_method) and tests/q/test_allocation.q holds it to that"

/ The shortest declaration that is wrong in one way only: a key nothing reads.
.qt.qetl.declared.misspelled:{[] enlist[`zz_misspelt]!enlist 1b}

/ Does the text contain the fragment? (`like` takes at most two wildcards on KDB-X, so not `like`.)
.qt.qetl.declared.has:{[text;fragment] 0<count text ss fragment}

.qt.qetl.declared.test_every_define_is_found:{[t]
    d:.qt.qetl.declared.defines[];
    .qunit.assertTrue[all `.qetl.job.stream.define`.qetl.job.bounded.define`.qetl.source.define`.qetl.transform.define`.qetl.job.stream.normalizer.define in d;
        "the scan finds the entry points the issue names"];
    .qunit.assertTrue[(count d)>=10; "and the rest: ",", " sv string d]};

.qt.qetl.declared.test_every_define_refuses_a_misspelled_key:{[t]
    d:.qt.qetl.declared.defines[] except key .qt.qetl.declared.exempt;
    .qunit.assertTrue[0<count d; "something to probe"];
    err:{[f] .qt.qetl.declared.try[f;`zz_probe;.qt.qetl.declared.misspelled[]]} each d;
    ok:(.qt.qetl.declared.has[;"zz_misspelt, which"] each err) and .qt.qetl.declared.has[;"does not take"] each err;
    .qunit.assertEquals[d where not ok; `symbol$();
        "every define refuses an unknown key first, naming it - a new declaration must call .qetl.declared.refuse_unknown"]};

.qt.qetl.declared.test_every_exemption_is_still_a_define_and_says_why:{[t]
    ex:key .qt.qetl.declared.exempt;
    .qunit.assertEquals[ex except .qt.qetl.declared.defines[]; `symbol$(); "an exemption for something that is gone is dead weight"];
    .qunit.assertTrue[all 0<count each value .qt.qetl.declared.exempt; "and carries its reason"]};

/ The misspellings the issue names, on the entry points it names.
.qt.qetl.declared.test_the_misspellings_in_the_issue_are_refused:{[t]
    stream:`procname`subscribe_to`outputs`on_batch!(`zzdecl1;enlist `trades;`symbol$();{[t;x]});
    {[b;k] .qunit.assertThrows[.qetl.job.stream.define[`zz_decl;];b,enlist[k]!enlist 1b;
        "*declares ",string[k],", which a streaming job does not take*";"a misspelt optional key"]}[stream] each `start_with_al`on_endofdya`transfrom`stat`replya`ephemral;
    .qunit.assertFalse[`zz_decl in .qetl.job.stream.defined[]; "and nothing was registered"];
    .qunit.assertThrows[.qetl.job.stream.normalizer.define[`zz_norm;];`procname`output`input`start_with_al!(`zznorm1;([] a:`long$());()!();1b);
        "*declares start_with_al, which a normalizer does not take*";"a normalizer"];
    .qunit.assertThrows[.qetl.transform.define[`zz_xf;];`inputs`output`fn`examples`transfrom!(()!();([] a:`long$());{x};();1b);
        "*declares transfrom, which a transform does not take*";"a transform"]};

/ --- WHICH Q: the `q` key a streaming job and a bounded worker take (#2747) ---

.qt.qetl.declared.test_each_q_term_form_is_accepted:{[t]
    .qunit.assertEquals[.qetl.declared.q_term each ("kdbx";"peachq";"kdbx>=4.1";"kdbx=4";"kdbx=4.1.2");
        11111b; "an interpreter, a minimum and a version prefix"];
    .qunit.assertEquals[.qetl.declared.require_q["define: x";"kdbx>=4.1  peachq"]; "kdbx>=4.1  peachq";
        "the value is returned as declared, spaces and all"]};

.qt.qetl.declared.test_a_malformed_q_term_is_refused_naming_it:{[t]
    .qunit.assertEquals[.qetl.declared.q_term each ("kdbx>=";"kdbx=4.";"kdbx>=4.x";"peachq>=1";"q5";"KDBX");
        000000b; "a missing or broken version, a version on peachq, an unknown interpreter, a capital"];
    .qunit.assertThrows[.qetl.declared.require_q["define: x"];"kdbx q5 peachq";
        "define: x's q holds q5, which is not kdbx, kdbx>=VERSION, kdbx=VERSION or peachq";"the bad term, by name"]};

.qt.qetl.declared.test_a_q_that_is_not_a_string_or_names_nothing_is_refused:{[t]
    .qunit.assertThrows[.qetl.declared.require_q["define: x"];`kdbx;"define: x's q must be a string*";"a symbol"];
    .qunit.assertThrows[.qetl.declared.require_q["define: x"];"  ";
        "define: x's q names no interpreter - drop the key to run on every q";"blanks"]};

.qt.qetl.declared.test_a_bounded_worker_stores_its_q_and_refuses_a_bad_one:{[t]
    row:first .qt.qetl.declared.function_declarations[] where `.qetl.job.bounded.define=.qt.qetl.declared.function_declarations[][;`define];
    .qunit.assertEquals[.qt.qetl.declared.attempt[row;row[`base],enlist[`q]!enlist "kdbx=4 peachq"]; ""; "declared"];
    .qunit.assertEquals[(.qetl.job.bounded.def row`name)`q; "kdbx=4 peachq"; "stored as declared"];
    .qt.qetl.declared.forget[row];
    .qunit.assertEquals[.qt.qetl.declared.attempt[row;row`base]; ""; "undeclared"];
    .qunit.assertEquals[(.qetl.job.bounded.def row`name)`q; ""; "stored as every q"];
    .qt.qetl.declared.forget[row];
    .qunit.assertTrue[.qt.qetl.declared.has[.qt.qetl.declared.attempt[row;row[`base],enlist[`q]!enlist "peachq=5"];
        "zz_decl_worker's q holds peachq=5"]; "a bad one is refused by name"];
    .qt.qetl.declared.forget[row]};

/ --- GATE: every function-valued key goes through the shared predicate -------

/ A valid declaration for each define that takes functions, so spoiling ONE key
/ proves that key is what was refused. Adding a function-valued key to a
/ declaration means adding it to its row below; the test then holds it to the rule.
.qt.qetl.declared.stream_base:{[]
    `procname`subscribe_to`outputs`on_batch`period`on_timer`on_start`on_endofday`check`on_fail`replay`on_replayed!(
        `zzdecl1;enlist `trades;`symbol$();{[t;x]};0D00:00:01;{[] ()};{[] ()};{[d] ()};{[x] 0#x};`drop;1b;{[] ()})}

.qt.qetl.declared.bounded_base:{[]
    (`ns`procname`note`twin_of`twin_calculation) _ @[.qt.support.declaration `demo_deals_backfill;`outputs;:;enlist `zz_decl_out]}

.qt.qetl.declared.source_base:{[]
    @[.qetl.source.def `demo_deals;`complete_through`ready;:;({[h] 2026.09.01D00:00};{[b;f;t] ()})]}

.qt.qetl.declared.transform_base:{[]
    `inputs`output`fn`examples!(enlist[`a]!enlist ([] x:`long$()); ([] x:`long$()); {[a] a};
        enlist `inputs`expected!(enlist[`a]!enlist ([] x:enlist 1); ([] x:enlist 1)))}

/ define, a name to declare under, its valid declaration, its function-valued
/ keys, and the keys whose stored "none" is (::) - for those only a non-function
/ is refused, since (::) is how a worker says it declares nothing.
.qt.qetl.declared.function_declarations:{[]
    (`define`name`base`keys`none!(`.qetl.job.stream.define;`zz_decl;.qt.qetl.declared.stream_base[];`on_batch`on_timer`on_start`on_endofday`check`on_replayed;`symbol$());
     `define`name`base`keys`none!(`.qetl.job.bounded.define;`zz_decl_worker;.qt.qetl.declared.bounded_base[];`check`facts`pin;`check`facts`pin);
     `define`name`base`keys`none!(`.qetl.source.define;`zz_decl_source;.qt.qetl.declared.source_base[];`query`fixture`complete_through`ready;`symbol$());
     `define`name`base`keys`none!(`.qetl.transform.define;`zz_decl_xf;.qt.qetl.declared.transform_base[];enlist `fn;`symbol$()))}

/ The error a define raises for a declaration, or "" when it accepts it.
.qt.qetl.declared.try:{[def;name;decl] .[{[f;n;d] f[n;d]; ""};(get def;name;decl);{x}]}
.qt.qetl.declared.attempt:{[row;decl] .qt.qetl.declared.try[row`define;row`name;decl]}

/ Remove what an accepted base registered, so probing leaves the tree as it found it.
.qt.qetl.declared.forget:{[row]
    n:row`name;
    `.qetl.job.stream.jobs set (enlist n) _ .qetl.job.stream.jobs;
    `.qetl.job.stream.initial set (enlist n) _ .qetl.job.stream.initial;
    `.qetl.job.stream.procnames set (enlist `zzdecl1) _ .qetl.job.stream.procnames;
    .qt.support.drop_rows[`.qetl.source.sources;n];
    .qt.support.drop_rows[`.qetl.transform.registry;n];
    .qt.support.drop_rows[`.qetl.job.bounded.worker_cfg;n];}

.qt.qetl.declared.test_a_valid_base_is_accepted_so_the_probe_proves_something:{[t]
    {[row]
        .qunit.assertEquals[.qt.qetl.declared.attempt[row;row`base]; ""; "the base of ",string[row`define]," registers"];
        .qt.qetl.declared.forget[row]} each .qt.qetl.declared.function_declarations[]};

.qt.qetl.declared.test_every_function_key_is_refused_when_it_is_not_a_function:{[t]
    spoilt:raze {[row]
        raze {[row;k]
            vals:$[k in row`none; enlist 42; (::;42)];
            e:{[row;k;v] .qt.qetl.declared.attempt[row;@[row`base;k;:;v]]}[row;k] each vals;
            .qt.qetl.declared.forget[row];
            refused:.qt.qetl.declared.has[;"'s ",string[k]," must be"] each e;
            $[all refused; `symbol$(); enlist `$"." sv (string row`define;string k)]}[row] each row`keys} each .qt.qetl.declared.function_declarations[];
    .qunit.assertEquals[spoilt; `symbol$(); "every function-valued key is refused at define when it is not a function"]};

.qt.qetl.declared.test_a_projection_is_accepted_wherever_a_function_is_and_declares_in_full:{[t]
    p:{[a;b] ()}[1;];
    .qunit.assertEquals[.qetl.job.bounded.require_hooks[`zz_hooks;`check`facts!(p;p)]; (::); "a bounded check and facts may be projections (#2114); no pin declared"];
    row:`define`name`base!(`.qetl.source.define;`zz_decl_source;@[.qt.qetl.declared.source_base[];`query`fixture`complete_through`ready;:;({[tbl;h;f;t] ([] time:`timestamp$())}[`EVENTS;;;];{[tbl;u] ([] time:`timestamp$())}[`EVENTS;];{[x;h] 2026.09.01D00:00}[0;];{[x;b;f;t] ()}[0;;;])]);
    .qunit.assertEquals[.qt.qetl.declared.attempt[row;row`base]; ""; "a source's query, fixture, complete_through and ready may be projections"];
    .qt.qetl.declared.forget[row]};

.qt.qetl.declared.test_a_bounded_worker_with_a_non_function_check_or_facts_is_refused_by_define:{[t]
    b:.qt.qetl.declared.bounded_base[];
    .qunit.assertThrows[.qetl.job.bounded.define[`zz_decl_worker;]; @[b;`check;:;"not a function"]; "*'s check must be a function*"; "check, at define rather than the first window"];
    .qunit.assertThrows[.qetl.job.bounded.define[`zz_decl_worker;]; @[b;`facts;:;`nope]; "*'s facts must be a function*"; "facts, which was never checked"]};

/ --- GATE: no declaration spells "is a function" for itself ------------------

/ Source lines that test a type range for "is it a function" other than in declared.q.
/ Each is a different question, listed with why it is not the declaration rule.
.qt.qetl.declared.other_questions:(
    ("src/etl/core/io_remote.q";"t within 100 112h";"is a value plain data: a function anywhere in a request is refused, (::) included");
    ("src/etl/core/source_fetch.q";"100h=type f";"how to print a call: a lambda has its own text");
    ("src/etl/streaming/kafka_feed.q";"within 100 107h";"chains a previous .z.exit at run time; not a declaration");
    ("src/namespaces.q";"100h=type";"which namespaces hold functions; the library cannot call .qetl");
    ("src/portfolio/allocation.q";"within 100 104h";"the library's matching methods; src/portfolio cannot call .qetl"))

.qt.qetl.declared.test_no_declaration_spells_is_a_function_for_itself:{[t]
    files:system "find src -name '*.q' -not -name declared.q";
    pats:("within 100 1";"100h=type";"100h<=");
    hits:raze {[pats;f]
        ls:read0 hsym `$f;
        code:ls where not "/"=first each trim each ls;
        m:code where {[pats;l] any 0<count each l ss/: pats}[pats] each code;
        (f,": "),/:trim each m}[pats] each files;
    others:.qt.qetl.declared.other_questions;
    known:{[others;h] any {[h;o] (.qt.qetl.declared.has[h;o 0]) and .qt.qetl.declared.has[h;o 1]}[h] each others}[others] each hits;
    .qunit.assertEquals[hits where not known; (); "a declaration asks .qetl.declared.callable / .lambda; a new question is added to other_questions with its reason"]};
