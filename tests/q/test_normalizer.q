// test_normalizer.q - tests for src/etl/core/normalizer.q. Load the ETL tree
// (src/etl/init.q), tests/lib/qunit.q and tests/lib/testutil.q before this
// file: the executions and marks normalizers it exercises register as their
// files load.

\d .normtest

/ A canonical table of this suite's own, and a mapping onto it, so the
/ shell's refusals can be tested without touching the real normalizers.
canon:([] a:`long$(); b:`symbol$())

/ Register a transform this suite controls. Re-registering replaces, so a
/ test that wants a different shape for the same name just defines again.
mk_xf:{[name;output;fn]
    .qxf.define[name;`inputs`output`fn`examples!(
        (enlist `src)!enlist ([] time:`timestamp$(); x:`long$(); y:`symbol$());
        output;
        fn;
        enlist `inputs`expected!(
            (enlist `src)!enlist ([] time:enlist 2026.01.01D09:00:00; x:enlist 1; y:enlist `p);
            fn ([] time:enlist 2026.01.01D09:00:00; x:enlist 1; y:enlist `p)))]}

good:{[batch] select a:x, b:y from batch}

/ .qstream.define refuses a second job on one procname, and a test that
/ defines the same normalizer twice would trip that - so each test uses a
/ name and procname of its own, and forgets them afterwards.
forget:{[name]
    `.qnorm.registry set (enlist name) _ .qnorm.registry;
    `.qstream.jobs set (enlist name) _ .qstream.jobs;
    pn:key[.qstream.procnames] where name=value .qstream.procnames;
    `.qstream.procnames set pn _ .qstream.procnames;
    }

define_ok:{[name;pn]
    mk_xf[`normtest_good;canon;good];
    (` sv `.qsub,name,`publish) set .qstream.unwired name;
    .qnorm.define[name;`procname`output`input!(pn;canon;(enlist `src)!enlist `normtest_good)]}

/ ------------------------------------------------------------- DEFINING

test_define_registers_the_job_with_its_edges_derived:{[t]
    define_ok[`nt_a;`nta1];
    d:.qstream.declaration `nt_a;
    .qunit.assertEquals[(d`subscribes;d`publishes);(enlist `src;enlist `nt_a);
        "subscribes is the source list and publishes is the normalizer's own name - an instance cannot declare them differently from its mappings"];
    .qunit.assertTrue[`nt_a in .qnorm.defined[];"and it is a defined normalizer"];
    forget `nt_a};

test_a_missing_key_is_named:{[t]
    .qunit.assertThrows[.qnorm.define[`nt_b;];`procname`output!(`ntb1;canon);
        "*is missing input*";"a normalizer with no sources is refused by the key it lacks"]};

test_an_output_with_time_is_refused:{[t]
    .qunit.assertThrows[.qnorm.define[`nt_c;];
        `procname`output`input!(`ntc1;([] time:`timestamp$(); a:`long$());(enlist `src)!enlist `normtest_good);
        "*carries `time`*";"the plant stamps time; a source's own stamp is a column named for what it is"]};

test_an_unregistered_transform_is_refused:{[t]
    .qunit.assertThrows[.qnorm.define[`nt_d;];
        `procname`output`input!(`ntd1;canon;(enlist `src)!enlist `normtest_nonesuch);
        "*is not registered*";"a mapping is a declared transform, so its examples are verified"]};

test_a_mapping_whose_output_drifts_is_refused:{[t]
    / The check this kind exists for. Same columns in the wrong order is
    / drift too: the output is published positionally.
    mk_xf[`normtest_drift;([] b:`symbol$(); a:`long$());{[batch] select b:y, a:x from batch}];
    .qunit.assertThrows[.qnorm.define[`nt_e;];
        `procname`output`input!(`nte1;canon;(enlist `src)!enlist `normtest_drift);
        "*does not produce the canonical table*";"a mapping that emits the columns in another order lands as a misaligned table"];
    mk_xf[`normtest_wider;([] a:`long$(); b:`symbol$(); c:`float$());{[batch] select a:x, b:y, c:0f from batch}];
    .qunit.assertThrows[.qnorm.define[`nt_f;];
        `procname`output`input!(`ntf1;canon;(enlist `src)!enlist `normtest_wider);
        "*unexpected column*";"and one that emits an extra column is named for it"]};

test_a_mapping_with_two_inputs_is_refused:{[t]
    .qxf.define[`normtest_two;`inputs`output`fn`examples!(
        `p`q!(([] x:`long$());([] y:`long$()));
        ([] a:`long$(); b:`symbol$());
        {[p;q] ([] a:p`x; b:(count p)#`z)};
        enlist `inputs`expected!(`p`q!(([] x:enlist 1);([] y:enlist 2));([] a:enlist 1; b:enlist `z)))];
    .qunit.assertThrows[.qnorm.define[`nt_g;];
        `procname`output`input!(`ntg1;canon;(enlist `src)!enlist `normtest_two);
        "*takes 2 inputs*";"a mapping reads one source"]};

/ ---------------------------------------------------------- NORMALIZING

test_normalize_projects_the_batch_onto_what_the_mapping_declares:{[t]
    define_ok[`nt_h;`nth1];
    / The wire carries more than the mapping reads: an attribute, an extra
    / column the plant or a sibling added. The mapping never sees them.
    batch:([] time:enlist 2026.01.01D09:00:00; x:enlist 7; y:`g#enlist `k; extra:enlist 1f);
    .qunit.assertEquals[.qnorm.normalize[`nt_h;`src;batch];([] a:enlist 7; b:enlist `k);
        "the canonical rows, from the declared columns only"];
    forget `nt_h};

test_normalize_names_a_column_the_batch_lacks:{[t]
    define_ok[`nt_i;`nti1];
    .qunit.assertThrows[.qnorm.normalize[`nt_i;`src;];([] time:enlist 2026.01.01D09:00:00; x:enlist 7);
        "*missing required column(s) y*";"a batch without a declared column is refused by name, not silently mapped"];
    forget `nt_i};

test_normalize_refuses_a_table_that_is_not_a_source:{[t]
    define_ok[`nt_j;`ntj1];
    .qunit.assertThrows[.qnorm.normalize[`nt_j;`other;];([] x:enlist 1);
        "*is not a source of nt_j*";"asking to normalize the wrong table is a wiring bug"];
    forget `nt_j};

test_an_undefined_normalizer_is_named:{[t]
    .qunit.assertThrows[.qnorm.declaration;`nonesuch;"*is not a defined normalizer*";
        "looking one up that was never defined says so"]};

/ ----------------------------------------------------------- DISPATCHING

test_dispatch_publishes_through_the_instances_own_seam:{[t]
    define_ok[`nt_k;`ntk1];
    `.normtest.got set ();
    .qstream.wire[`nt_k;{[tbl;rows] `.normtest.got set .normtest.got,enlist (tbl;rows); count rows}];
    .qnorm.dispatch[`nt_k;`src;([] time:enlist 2026.01.01D09:00:00; x:enlist 3; y:enlist `m)];
    .qunit.assertEquals[.normtest.got;enlist (`nt_k;([] a:enlist 3; b:enlist `m));
        "one publication, onto the canonical table, of the normalized rows"];
    forget `nt_k};

test_dispatch_publishes_nothing_for_an_empty_batch:{[t]
    define_ok[`nt_l;`ntl1];
    `.normtest.got set ();
    .qstream.wire[`nt_l;{[tbl;rows] `.normtest.got set .normtest.got,enlist (tbl;rows); count rows}];
    .qnorm.dispatch[`nt_l;`src;0#([] time:`timestamp$(); x:`long$(); y:`symbol$())];
    .qnorm.dispatch[`nt_l;`not_a_source;([] x:enlist 1)];
    .qunit.assertEmpty[.normtest.got;"an empty batch, and a batch on a foreign table, publish nothing"];
    forget `nt_l};

/ ------------------------------------------------------ THE REAL ONES

test_the_shipped_normalizers_are_defined_and_registered:{[t]
    .qunit.assertEquals[`executions`marks in .qnorm.defined[];11b;"executions and marks are defined"];
    .qunit.assertEquals[.qstream.declaration[`executions]`subscribes;`trades`crypto_trades;
        "executions reads both fill tables"];
    .qunit.assertEquals[.qstream.declaration[`marks]`subscribes;`quote`crypto_book;
        "marks reads both books"];
    .qunit.assertEquals[.qdag.kinds;`bounded`continuous`stream`reaction`normalizer;
        "and normalizer is a kind the job graph knows"]};

test_every_shipped_mapping_verifies:{[t]
    / The examples in executions.q and marks.q, run - test_transform.q does
    / this for every transform too, but a reader of THIS file should see the
    / four mappings pass here.
    xfs:raze value each (.qnorm.declaration[`executions]`input;.qnorm.declaration[`marks]`input);
    failed:select from raze .qxf.verify each xfs where not passed;
    .qunit.assertEmpty[failed;"every mapping's examples produce what they say"]};

\d .
