// test_registries.q - the one q trap every declaration registry in this tree
// has to defend against, and whether each one's defence works (.regtest).
// Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/testutil.q
// before this file.

\d .regtest

/ THE TRAP. A dictionary whose values are same-keyed dictionaries is a
/ TABLE, and q makes it one silently - on the FIRST entry, not the second.
/ From then on the registry is a table pretending to be a dictionary, and
/ it has two failure modes that pull in opposite directions:
/ .
/   a WIDER declaration - one extra key - throws a bare `mismatch, naming
/   nothing and pointing at the registration line rather than the cause.
/ .
/   a NARROWER one SUCCEEDS, and is stored with a null for the key it did
/   not have. `key` then reports that key as present, so a consumer asking
/   "was this declared?" is told yes and reads a null. That is the worse
/   half, and it is the half nobody expects.
/ .
/ The registries in this tree defend in one of two ways. .qetl.job.stream and
/ .qetl.job.stream.normalizer ENLIST every declaration, which is
/ shape-independent. The rest DECLARE the table: .qalloc.methods,
/ .qetl.transform.registry, .qetl.source.sources, .qetl.job.bounded.worker_cfg
/ and .qetl.dag.jobs are keyed tables from the start, with every column typed
/ or general and every optional field given its default by define (#512), so
/ there is no collapse to depend on - which is also what lets them load on
/ PeachQ, where adding a row by key to a collapsed dict throws. A new registry
/ that went back to a dictionary of dictionaries and accepted an optional key
/ would reintroduce the bug, so this file checks the defences rather than
/ trusting that the next author reads a header explaining the trap.

/ Every test here registers into a LIVE registry, so every test has to
/ take it back out. Without this the suite passes only while this file's
/ namespace happens to run after the contract tests that enumerate what is
/ registered - .srctest's "every source has a .qpipe.source namespace" and
/ .sjtest's "every job is registered" both fail if it runs first.
forget:{[reg;names]
    r:get reg;
    / A registry is a dict, or a keyed table (.qalloc.methods), whose `key`
    / is a table - `names inter` it would match nothing and forget nothing.
    reg set $[98h=type key r;
        ![r;enlist (in;first cols key r;enlist names);0b;`symbol$()];
        (names inter key r) _ r];
    }

forget_job:{[job]
    forget[`.qetl.job.stream.jobs;enlist job];
    pn:key[.qetl.job.stream.procnames] where job=value .qetl.job.stream.procnames;
    forget[`.qetl.job.stream.procnames;pn];
    }

/ Executable documentation: the trap itself, on a registry with no guard.
test_the_trap_is_real_and_has_two_halves:{[t]
    reg:(`symbol$())!();
    reg[`x]:`p`q!(1;2);
    .qunit.assertEquals[type value reg;98h;
        "one dict-valued entry and the registry is already a table, not a dictionary"];
    reg[`y]:`p`q!(3;4);
    .qunit.assertThrows[{[r] r[`z]:`p`q`r!(5;6;7)};reg;"*mismatch*";
        "a WIDER declaration throws, naming nothing"];
    reg[`w]:(enlist `p)!enlist 9;
    .qunit.assertTrue[`q in key reg`w;
        "and a NARROWER one is accepted, then reports the key it never declared as present"];
    .qunit.assertTrue[null reg[`w]`q;
        "holding a null - which is why the silent half is the dangerous one"]};

/ ------------------------------------------------- THE SHIPPED REGISTRIES

/ Each entry: the registry, and a niladic that registers two declarations
/ of DIFFERENT shape through the public API. Shape variation is the whole
/ point - registering twice with the same keys proves nothing, because that
/ is the case the collapse handles fine.
/ Dicts of same-keyed dicts that are NOT registries, each with its reason.
/ They have a registry's shape - so dict_registries[] finds them - but nothing
/ registers into them, so there is no second declaration whose shape could
/ collapse or be refused. An exemption is a claim; this keeps it argued.
not_registries:(enlist `.qcal.mock_conventions)!enlist
    "sample FX conventions, passed as an argument to .qcal.spot_date and friends; nothing registers into it"

covered:`.qetl.job.stream.jobs`.qetl.transform.registry`.qalloc.methods`.qetl.job.stream.normalizer.registry`.qetl.source.sources`.qetl.job.bounded.worker_cfg`.qetl.dag.jobs

empty_in:([] time:`timestamp$(); x:`long$())
rows_in:([] time:enlist 2026.01.01D00:00:00; x:enlist 1)

test_a_streaming_job_without_a_timer_does_not_gain_one:{[t]
    / The historical bug, as a regression. The four feeds registered first
    / with identical fields, so `jobs` became a keyed table, and markout's
    / declaration - which carries an on_batch the feeds do not - was
    / refused with a bare `mismatch. It is enlisted now.
    .qetl.job.stream.define[`regtest_feed;`procname`subscribe_to`publishes`period`on_timer!(
        `regtest_feed1;`symbol$();enlist `regtest_out;0D00:00:01;{[] })];
    .qetl.job.stream.define[`regtest_sub;`procname`subscribe_to`publishes`on_batch!(
        `regtest_sub1;enlist `regtest_in;enlist `regtest_out2;{[a;b] })];
    feed:.qetl.job.stream.def `regtest_feed;
    sub:.qetl.job.stream.def `regtest_sub;
    .qunit.assertFalse[`on_batch in key feed;
        "a feed has no on_batch, and must not acquire one as a null"];
    .qunit.assertFalse[`period in key sub;
        "and a subscriber has no timer"];
    forget_job each `regtest_feed`regtest_sub};

test_a_transform_without_as_of_does_not_gain_one:{[t]
    / .qetl.transform declares its table (#512): as_of is a typed column, and an
    / undeclared one is stored false - the default define chooses, not a null.
    .qetl.transform.define[`regtest_plain;`inputs`output`fn`examples!(
        (enlist `i)!enlist empty_in;empty_in;{[i] i};
        enlist `inputs`expected!((enlist `i)!enlist rows_in;rows_in))];
    .qetl.transform.define[`regtest_clocked;`inputs`output`fn`examples`as_of!(
        (enlist `i)!enlist empty_in;empty_in;{[i;a] i};
        enlist `inputs`expected`as_of!((enlist `i)!enlist rows_in;rows_in;2026.01.01D00:00:00);1b)];
    .qunit.assertEquals[.qetl.transform.def[`regtest_plain]`as_of;0b;
        "an undeclared as_of is FALSE, not a null - normalising means choosing the default explicitly"];
    .qunit.assertEquals[.qetl.transform.def[`regtest_clocked]`as_of;1b;"and a declared one survives"];
    forget[`.qetl.transform.registry;`regtest_plain`regtest_clocked]};

test_an_allocation_method_without_a_description_does_not_gain_a_null:{[t]
    / The case that would have reintroduced the bug: an optional key on a
    / registry that normalises rather than enlists.
    .qalloc.define[`regtest_described;`open`pick`why!(.qalloc.append_lot;.qalloc.pick_first;"a reason")];
    .qalloc.define[`regtest_bare;`open`pick!(.qalloc.append_lot;.qalloc.pick_first)];
    .qunit.assertEquals[.qalloc.def[`regtest_bare]`why;"";
        "an omitted description is an empty string, not a null - and `why` is still present, so every stored method is one shape"];
    .qunit.assertEquals[.qalloc.def[`regtest_described]`why;"a reason";"and a given one survives"];
    forget[`.qalloc.methods;`regtest_described`regtest_bare]};

test_a_source_with_a_scalar_row_key_is_stored_as_a_vector:{[t]
    / .qetl.source stores row_key as a VECTOR whatever the spelling, so no
    / consumer has to ask whether a one-column key needs enlisting - and a
    / general column holding an atom on one row and a vector on the next is a
    / column nothing can read uniformly. Both spellings must land as vectors.
    / The query and fixture lambdas build their own table rather than
    / closing over a local: a q lambda does not capture its enclosing
    / scope, so `{[] e}` would throw 'e the moment anything called it -
    / which registration does not, making it a fixture that is broken and
    / silent until the day it is used.
    base:`table_name`time_column`columns`types`query`fixture`tz!(`regtest_t;`time;`time`sym;"ps";
        {[a;b] ([] time:`timestamp$(); sym:`symbol$())};{[] ([] time:`timestamp$(); sym:`symbol$())};`$"UTC");
    .qetl.source.define[`regtest_scalar;(`source`row_key!(`regtest_scalar;`sym)),base];
    .qetl.source.define[`regtest_vector;(`source`row_key!(`regtest_vector;`time`sym)),base];
    .qunit.assertEquals[.qetl.source.def[`regtest_scalar]`row_key;enlist `sym;
        "a scalar row_key is stored enlisted, so every stored declaration has one shape"];
    .qunit.assertEquals[.qetl.source.def[`regtest_vector]`row_key;`time`sym;"and a vector is unchanged"];
    forget[`.qetl.source.sources;`regtest_scalar`regtest_vector]};

test_a_worker_config_without_an_optional_key_gets_the_documented_default:{[t]
    / .qetl.job.bounded normalises the four optional keys to (::) rather than leaving
    / them absent, which is what keeps every stored cfg one shape. The
    / point of the test is that the DEFAULT is chosen, not inherited from
    / a collapse: a missing `check` is (::), not a null of whatever type
    / the first worker's check happened to be.
    cfg:.qetl.job.bounded.normalised `source`dataset`width`transform!(`regtest_src;`regtest_ds;1D;`regtest_xf);
    .qunit.assertTrue[all `check`io`facts`partition in key cfg;
        "every optional key is present after normalisation"];
    .qunit.assertEquals[cfg`check;(::);
        "and an omitted one holds the generic null, the documented default - not a typed null borrowed from a neighbour"]};

/ WHAT CANNOT BE CHECKED GENERICALLY, and why this file has one test per
/ registry instead of one test over all of them.
/ .
/ The obvious general check is "no stored declaration carries a null under
/ a key its caller never supplied" - scan every registry, flag every null.
/ It was written, and it fails on .qetl.job.bounded.worker_cfg, where four workers have
/ a null `partition`. That is not a collapse artefact: ` is .qcov's
/ documented "this dataset has no partition dimension" sentinel
/ (bounded_worker.q's `unpartitioned`), a legitimate declared value.
/ .
/ A null cannot be told apart from an invented one after the fact, because
/ after the collapse every entry has the same keys and nothing records
/ which were supplied. So the property has to be checked BEHAVIOURALLY, at
/ registration, one registry at a time: register a narrow declaration and
/ assert the key it omitted is either absent or holds its documented
/ default rather than a null. That is what the three tests above do, and
/ what test_every_dict_valued_registry_is_covered_here forces a new
/ registry to add.

/ ------------------------------------------- THE DECLARED KEYED TABLES (#512)

/ The four registries that were dictionaries of dictionaries, depending on q's
/ collapse into a table. Each is now a keyed table on `name`.
keyed:`.qetl.transform.registry`.qetl.source.sources`.qetl.job.bounded.worker_cfg`.qetl.dag.jobs

test_the_four_registries_are_declared_keyed_tables:{[t]
    .qunit.assertEquals[{98h=type key get x} each keyed;1111b;
        "each registry's key is a table: declared, not a dictionary that collapsed"];
    .qunit.assertEquals[{first cols key get x} each keyed;4#`name;
        "keyed on `name, as .qalloc.methods is"]};

test_no_keyed_registry_is_classified_as_a_collapsed_dictionary:{[t]
    .qunit.assertEquals[keyed inter .regtest.dict_registries[];`symbol$();
        "none of the four relies on a collapse any more, so the scan does not report it"]};

/ A source declaring credential_example and one that does not, side by side:
/ the declared one keeps its string, the other gets the documented default "",
/ which .qetl.source.credential_example reads as "not declared". Registered
/ narrow-first and wide-first, because a collapsed dictionary differed by
/ order: the narrower one gained a NULL when it came second.
test_sources_with_different_optional_fields_coexist:{[t]
    base:`table_name`time_column`row_key`columns`types`query`fixture`tz!(`regtest_t;`time;`time;`time`sym;"ps";
        {[a;b] ([] time:`timestamp$(); sym:`symbol$())};{[] ([] time:`timestamp$(); sym:`symbol$())};`UTC);
    .qetl.source.define[`regtest_narrow;(enlist[`source]!enlist `regtest_narrow),base];
    .qetl.source.define[`regtest_wide;(`source`transport`credential_example!(`regtest_wide;`odbc;"DSN=x")),base];
    .qunit.assertEquals[.qetl.source.def[`regtest_narrow]`credential_example;"";
        "an omitted credential_example is the empty string, not a null"];
    .qunit.assertEquals[.qetl.source.def[`regtest_narrow]`transport;`ipc;"and an omitted transport is ipc"];
    .qunit.assertEquals[.qetl.source.def[`regtest_wide]`credential_example;"DSN=x";"a declared one survives"];
    .qunit.assertEquals[.qetl.source.def[`regtest_wide]`transport;`odbc;"as does a declared transport"];
    forget[`.qetl.source.sources;`regtest_narrow`regtest_wide]};

test_a_source_key_no_column_holds_is_refused_by_name:{[t]
    decl:`source`table_name`time_column`row_key`columns`types`query`fixture`tz`colour!(`regtest_bad;`regtest_t;`time;`time;`time`sym;"ps";
        {[a;b] ([] time:`timestamp$(); sym:`symbol$())};{[] ([] time:`timestamp$(); sym:`symbol$())};`UTC;`red);
    .qunit.assertThrows[.qetl.source.define[`regtest_bad];decl;"*declares colour*";
        "an unknown key is refused naming it - it once failed as a bare 'mismatch, or not at all"];
    .qunit.assertFalse[`regtest_bad in .qetl.source.defined[];"and nothing is registered"]};

/ Two workers on the shipped demo source, one with a check and one with a
/ partition and a note: each keeps what it declared and gets the default for
/ what it did not - (::) for an omitted check, ` for an omitted partition.
test_workers_with_different_optional_fields_coexist:{[t]
    base:`source`dataset`width`transform!(`demo_deals;`regtest_ds;1D;`demo_deals_passthrough);
    .qetl.job.bounded.define[`regtest_checked;base,enlist[`check]!enlist {[b] .qetl.job.bounded.no_failures[]}];
    .qetl.job.bounded.define[`regtest_sliced;@[base;`dataset;:;`regtest_ds2],`partition`note!(`EURUSD;"a slice")];
    c:.qetl.job.bounded.def `regtest_checked;
    s:.qetl.job.bounded.def `regtest_sliced;
    .qunit.assertEquals[(c`partition;c`note;s`check);(`;"";::);
        "each omitted optional field holds its documented default, not a neighbour's typed null"];
    .qunit.assertEquals[(s`partition;s`note);(`EURUSD;"a slice");"and the declared ones survive"];
    .qunit.assertEquals[100h;type c`check;"a declared check is the function it was given"];
    forget[`.qetl.job.bounded.worker_cfg;`regtest_checked`regtest_sliced]};

test_a_worker_key_no_column_holds_is_refused_by_name:{[t]
    decl:`source`dataset`width`transform`colour!(`demo_deals;`regtest_ds;1D;`demo_deals_passthrough;`red);
    .qunit.assertThrows[.qetl.job.bounded.define[`regtest_bad];decl;"*declares colour*";
        "an unknown key is refused naming it"];
    .qunit.assertFalse[`regtest_bad in .qetl.job.bounded.defined[];"and nothing is registered"]};

test_re_registration_replaces_the_row:{[t]
    .qetl.dag.register[`regtest_job;`kind`inputs`outputs!(`stream;`regtest_a;`regtest_x)];
    .qetl.dag.register[`regtest_job;`kind`inputs`outputs!(`bounded;`regtest_b`regtest_c;`symbol$())];
    .qunit.assertEquals[count where `regtest_job=.qetl.dag.defined[];1;"one row per name, not two"];
    .qunit.assertEquals[.qetl.dag.def[`regtest_job]`kind`inputs;(`bounded;`regtest_b`regtest_c);
        "and it is the second declaration"];
    forget[`.qetl.dag.jobs;enlist `regtest_job]};

test_lookup_enumeration_removal_and_reset_on_empty_and_populated:{[t]
    keep:.qetl.dag.jobs;
    .qetl.dag.reset[];
    .qunit.assertEquals[.qetl.dag.defined[];`symbol$();"an empty registry enumerates no names, as a symbol vector"];
    .qunit.assertThrows[.qetl.dag.def;`regtest_none;"*not a registered job*";"and a lookup is refused by name"];
    .qetl.dag.register[`regtest_one;`kind`inputs`outputs!(`stream;`regtest_a;`symbol$())];
    .qunit.assertEquals[.qetl.dag.defined[];enlist `regtest_one;"a populated one enumerates its names"];
    forget[`.qetl.dag.jobs;enlist `regtest_one];
    .qunit.assertEquals[.qetl.dag.defined[];`symbol$();"and removal by name empties it again"];
    .qetl.dag.register[`regtest_two;`kind`inputs`outputs!(`stream;`regtest_a;`symbol$())];
    .qetl.dag.reset[];
    .qunit.assertEquals[(.qetl.dag.defined[];98h=type key .qetl.dag.jobs);(`symbol$();1b);
        "reset empties it and leaves it a declared keyed table"];
    `.qetl.dag.jobs set keep};

test_every_dict_valued_registry_is_covered_here:{[t]
    / The completeness half. A registry added to src/ without a check here
    / is a registry whose guard nobody has tested - and the guards are not
    / interchangeable, so "it looks like the others" is not evidence.
    .qunit.assertTrue[0<count covered;"the coverage list is not empty"];
    / dict_registries[] finds registries whose values have COLLAPSED into a
    / table - which is exactly the set that relies on normalising to a
    / fixed key set. One that enlists (.qetl.job.stream, .qetl.job.stream.normalizer) keeps a general
    / list and is safe whatever shape arrives, so it does not appear here
    / and does not need to.
    missing:.regtest.dict_registries[] except covered,key not_registries;
    .qunit.assertEquals[missing;`$();
        "every registry relying on normalisation is listed in `covered` and has a test above registering two declarations of different shape - copying another registry's guard is not evidence, because the guards are not interchangeable"]};

test_a_job_graph_declaration_normalises_its_edges:{[t]
    / .qetl.dag normalises instead of enlisting: every registration writes the
    / same three keys, so `jobs` collapses into a table. That is safe only
    / because `inputs` and `outputs` are forced to VECTORS with `(),` on the
    / way in - an atom stored in one row's column and vectors in the rest
    / makes `count` answer 1 both for a job with one input and for a job
    / whose input is a single symbol that was never a list.
    / .
    / This registry reached `covered` late, and how says something. The
    / completeness check below had been reporting it for as long as it had
    / existed; it only reported it when .dagtest happened to run FIRST and
    / leave `jobs` populated, because an empty dict has not collapsed and
    / does not look like a registry that normalises. With the suite order
    / derived rather than hand-written, that stopped being luck.
    .qetl.dag.register[`regtest_one;`kind`inputs`outputs!(`stream;`regtest_a;`regtest_out)];
    .qetl.dag.register[`regtest_many;`kind`inputs`outputs!(`stream;`regtest_a`regtest_b;`symbol$())];
    one:.qetl.dag.def `regtest_one;
    many:.qetl.dag.def `regtest_many;
    .qunit.assertEquals[count one`inputs;1;
        "a scalar input is stored as a one-element vector, not as an atom"];
    .qunit.assertEquals[abs type one`inputs;11h;
        "and it is a SYMBOL vector, whatever shape the declaration arrived in"];
    .qunit.assertEquals[count many`inputs;2;"a vector keeps its length"];
    .qunit.assertEquals[count many`outputs;0;
        "an empty output stays empty rather than becoming a one-element null"];
    forget[`.qetl.dag.jobs;`regtest_one`regtest_many]};

/ Every symbol-keyed registry in src/ whose values are dictionaries, read
/ from the live namespaces rather than from a list kept by hand.
dict_registries:{[]
    nss:.qns.functional[] except `.q`.qunit;
    raze {[ns]
        nms:key[ns] except `;
        hits:nms where {[ns;nm]
            v:@[get;` sv ns,nm;`NO];
            $[v~`NO; 0b;
              not 99h=type v; 0b;
              not 11h=abs type key v; 0b;
              0=count v; 0b;
              / dict-valued: either a general list of dicts, or already
              / collapsed into a table
              $[98h=type value v; 1b; all 99h=type each value v]]}[ns] each nms;
        {[ns;nm] ` sv ns,nm}[ns] each hits
      } each nss}


\d .
