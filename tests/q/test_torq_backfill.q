// test_torq_backfill.q - the per-run flag handling of scripts/processes/torq_backfill.q
// (.backfillproctest).
//
// torq_backfill.q starts a process and exits, so it cannot be loaded whole.
// Like test_torq_pipeline.q, this loads the definitions it tests from the
// file itself: flag_vs_env, use_on_conflict and use_mode, qualified instead
// of entered by a context switch.

\d .backfillproctest

load_flag_handling:{[]
    ls:read0 `:scripts/processes/torq_backfill.q;
    from_i:first where ls like "flag_vs_env:{*";
    to_i:first where ls like "use_fixture:{*";
    / back up over the comment block that documents use_fixture
    isc:(ls til to_i) like "/*";
    to_i:1+last where not isc;
    body:from_i _ to_i # ls;
    body:{$[(first x) in .Q.a; ".qproc.backfill.",x; x]} each body;
    / the bodies call flag_vs_env by its bare name ([[] is a literal [ to like and ssr), which means nothing outside the context
    body:{$[x like "*flag_vs_env[[]*"; ssr[x;"flag_vs_env[[]";".qproc.backfill.flag_vs_env["]; x]} each body;
    f:hsym `$(first system "mktemp -d"),"/flag_handling.q";
    f 0: body;
    system "l ",1_string f}

/ Run g with UQF_MODE and UQF_ON_CONFLICT set as given ("" unsets), restoring
/ the config layers and the environment afterwards.
with_env:{[mode;oc;g]
    saved:(getenv `UQF_MODE;getenv `UQF_ON_CONFLICT);
    setenv[`UQF_MODE;mode];
    setenv[`UQF_ON_CONFLICT;oc];
    r:@[g;::;{[e] (`err;e)}];
    setenv[`UQF_MODE;saved 0];
    setenv[`UQF_ON_CONFLICT;saved 1];
    .qetl.cfg.set_override[`mode;""];
    .qetl.cfg.set_override[`on_conflict;""];
    r}

test_a_mode_flag_the_environment_would_outrank_is_refused:{[t]
    .backfillproctest.load_flag_handling[];
    r:.backfillproctest.with_env["run";"";{.qproc.backfill.use_mode enlist[`mode]!enlist enlist "plan"}];
    .qunit.assertEquals[r;
        (`err;"torq_backfill: -mode plan would be ignored - UQF_MODE=run outranks it; unset it or drop the flag");
        "plan asked, run in the environment: refused with the text"]}

test_an_on_conflict_flag_the_environment_would_outrank_is_refused:{[t]
    .backfillproctest.load_flag_handling[];
    r:.backfillproctest.with_env["";"upsert";{.qproc.backfill.use_on_conflict enlist[`on_conflict]!enlist enlist "replace"}];
    .qunit.assertEquals[r;
        (`err;"torq_backfill: -on_conflict replace would be ignored - UQF_ON_CONFLICT=upsert outranks it; unset it or drop the flag");
        "replace asked, upsert in the environment: refused with the text"]}

test_a_flag_the_environment_agrees_with_is_accepted:{[t]
    .backfillproctest.load_flag_handling[];
    r:.backfillproctest.with_env["PLAN";"upsert";
        {(.qproc.backfill.use_mode enlist[`mode]!enlist enlist "plan";
          .qproc.backfill.use_on_conflict enlist[`on_conflict]!enlist enlist "upsert")}];
    .qunit.assertEquals[r;(`plan;`upsert);"the same value, in any case, is no conflict"]}

test_the_flags_alone_set_the_mode_and_report_the_strategy_in_force:{[t]
    .backfillproctest.load_flag_handling[];
    r:.backfillproctest.with_env["";"";
        {(.qproc.backfill.use_mode enlist[`mode]!enlist enlist "plan";
          .qproc.backfill.use_on_conflict enlist[`on_conflict]!enlist enlist "replace")}];
    .qunit.assertEquals[r;(`plan;`replace);"with a clean environment the flags are what runs"]}

\d .
