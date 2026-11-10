/ test_markout_job.q - the declaration every markout streaming job is built from (.qpipe.transform.markout_job, #2651).
/ Load src/init.q, src/etl/init.q, tests/lib/qunit.q and tests/lib/support.q before this file.
/ .
/ The 26 keys used to be a positional list under a key list, so two same-typed values swapped between them
/ re-labelled a value instead of failing. These assert BY NAME what each job declared, from the owner of
/ each value, so a value under the wrong key fails here and does not run quietly under another job's name.

/ A declaration that states every key a markout job owns, each as its own name.
/ @return a list of (key; value) pairs
.qt.qpipe.transform.markout_job.pairs:{[] {(x;x)} each .qpipe.transform.markout_job.own}

.qt.qpipe.transform.markout_job.test_the_declaration_has_every_key_once_in_the_order_jobs_declare_them:{[tc]
    d:.qpipe.transform.markout_job.decl[`demo;.qt.qpipe.transform.markout_job.pairs[]];
    .qunit.assertEquals[key d;.qpipe.transform.markout_job.keys;"the 26 keys, in the canonical order"];
    .qunit.assertEquals[count d;26;"a count"];
    .qunit.assertEquals[d`period;0D00:00:01;"a shared value is filled in"];
    .qunit.assertEquals[d`procname;`procname;"a stated value sits under the name it was stated with"]};

.qt.qpipe.transform.markout_job.test_the_order_the_job_states_its_keys_in_does_not_matter:{[tc]
    p:.qt.qpipe.transform.markout_job.pairs[];
    .qunit.assertTrue[(.qpipe.transform.markout_job.decl[`demo;p])~.qpipe.transform.markout_job.decl[`demo;reverse p];"reversed pairs, same declaration"]};

.qt.qpipe.transform.markout_job.test_a_missing_key_is_refused_by_name:{[tc]
    p:.qt.qpipe.transform.markout_job.pairs[];
    .qunit.assertThrows[{.qpipe.transform.markout_job.decl[`demo;x where not `lateness=first each x]};p;"markout_job.decl: demo does not state lateness";"one missing"];
    .qunit.assertThrows[{.qpipe.transform.markout_job.decl[`demo;x where not (first each x) in `lateness`keep]};p;"markout_job.decl: demo does not state lateness, keep";"two missing, both named"]};

.qt.qpipe.transform.markout_job.test_a_key_a_job_does_not_own_or_states_twice_is_refused:{[tc]
    p:.qt.qpipe.transform.markout_job.pairs[];
    .qunit.assertThrows[{.qpipe.transform.markout_job.decl[`demo;x,enlist (`period;0D00:00:05)]};p;"markout_job.decl: demo states period*";"a shared key"];
    .qunit.assertThrows[{.qpipe.transform.markout_job.decl[`demo;x,enlist (`keep;`again)]};p;"markout_job.decl: demo states keep more than once";"twice"];
    .qunit.assertThrows[{.qpipe.transform.markout_job.decl[`demo;x]};p,enlist (`odd;1;2);"markout_job.decl: demo takes (key; value) pairs*";"a pair holds two items"]};

/ The values each job's declaration should carry, from the module that owns each, by job.
/ @return a dictionary job -> (key -> expected value)
.qt.qpipe.transform.markout_job.expected:{[]
    spec:.qpipe.transform.fx_markout_outputs.job_spec;
    w:.qpipe.transform.fx_markout.wait[spec;.qpipe.transform.fx_markout_outputs.settle];
    lb:.qpipe.transform.fx_markout.lookback spec;
    k:`procname`transform`horizon`lookback`max_age`lateness`expire_after`remember;
    e:()!();
    e[`eq_markout]:k!(`eq_markout1;`eq_markout_score;.qpipe.transform.eq_markout_outputs.wait[];.qpipe.transform.eq_markout_outputs.lookback[];(.qpipe.transform.eq_markout_outputs.job_spec)`max_age;.qpipe.transform.eq_markout_outputs.lateness;.qpipe.transform.eq_markout_outputs.expire_after[];.qpipe.transform.eq_markout_outputs.remember);
    e[`crypto_markout]:k!(`crypto_markout1;`crypto_markout_score;.qpipe.transform.crypto_markout_outputs.wait[];.qpipe.transform.crypto_markout_outputs.lookback[];(.qpipe.transform.crypto_markout_outputs.job_spec)`max_age;.qpipe.transform.crypto_markout_outputs.lateness;.qpipe.transform.crypto_markout_outputs.expire_after[];.qpipe.transform.crypto_markout_outputs.remember);
    e[`fx_markout]:k!(`fxmarkout1;`fx_markout_score_long;w;lb;spec`max_age;.qpipe.transform.fx_markout_outputs.lateness;.qpipe.transform.fx_markout_outputs.expire_after[];.qpipe.transform.fx_markout_outputs.remember);
    e[`fx_markout_wide]:k!(`fxmarkoutwide1;`fx_markout_score_wide;w;lb;spec`max_age;.qpipe.transform.fx_markout_outputs.lateness;.qpipe.transform.fx_markout_outputs.expire_after[];.qpipe.transform.fx_markout_outputs.remember);
    e}

.qt.qpipe.transform.markout_job.test_each_job_declares_the_values_its_owner_computes_under_the_right_key:{[tc]
    e:.qt.qpipe.transform.markout_job.expected[];
    {[j;e]
        d:.qetl.job.stream.horizons.def j;
        .qunit.assertEquals[(key e)#d;e;string[j]," declares what its owner computes, key by key"];
        .qunit.assertEquals[d`outputs;enlist j;string[j]," publishes the table of its own name"];
        .qunit.assertEquals[d`events`reference;`executions`market_data;string[j]," events and reference"];
        .qunit.assertEquals[(.qpipe.transform.markout_job.keys) except key d;`symbol$();string[j]," holds all 26 keys"]}'[key e;value e]};

.qt.qpipe.transform.markout_job.test_the_four_markout_jobs_are_the_ones_built_from_the_declaration:{[tc]
    jobs:key .qt.qpipe.transform.markout_job.expected[];
    .qunit.assertEquals[asc jobs;asc `eq_markout`crypto_markout`fx_markout`fx_markout_wide;"the four jobs"];
    .qunit.assertEquals[jobs except .qetl.job.stream.horizons.defined[];`symbol$();"each is a registered horizon job"]};
