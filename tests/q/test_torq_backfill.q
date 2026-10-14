// test_torq_backfill.q - the per-run flag handling of scripts/processes/torq_backfill.q
// (.backfillproctest).
//
// torq_backfill.q starts a process and exits, so it cannot be loaded whole.
// Like test_torq_pipeline.q, this loads the definitions it tests from the
// file itself: flag_vs_env, use_on_conflict and use_mode, qualified instead
// of entered by a context switch; a flattened copy arrives qualified already.

\d .backfillproctest

load_flag_handling:{[]
    ls:read0 `:scripts/processes/torq_backfill.q;
    from_i:first where ls like "*flag_vs_env:{*";
    to_i:first where ls like "*use_fixture:{*";
    / back up over the comment block that documents use_fixture
    isc:"/"={first x} each ls til to_i;
    to_i:1+last where not isc;
    body:from_i _ to_i # ls;
    body:{$[(first x) in .Q.a; ".qproc.backfill.",x; x]} each body;
    / the bodies call flag_vs_env by its bare name (a flattened copy has done this already; [[] is a literal [ to like and ssr), which means nothing outside the context
    body:{$[(x like "*flag_vs_env[[]*") and not x like "*.flag_vs_env[[]*"; ssr[x;"flag_vs_env[[]";".qproc.backfill.flag_vs_env["]; x]} each body;
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

/ --- scripts/dev/run_backfill.q (#1319) ---------------------------------
/ The dev launcher promises torq_backfill.q's flags unchanged, and read
/ neither -mode nor -on_conflict: -mode plan wrote rows into the -hdb root.
/ It starts a process and exits, so these run it as one, in a scratch root.

/ Run scripts/dev/run_backfill.q for the demo worker with extra flags, with
/ no UQF_MODE / UQF_ON_CONFLICT in its environment unless given.
/ @param extra the flags after the range, as one string
/ @param envs "VAR=value " prefixes for the child's environment
/ @return (output lines; the scratch HDB root; the exit code)
run_dev:{[extra;envs]
    root:first system "mktemp -d";
    cmd:"env -u UQF_MODE -u UQF_ON_CONFLICT ",envs,"UQF_STATUS_DIR=",root,"/st ",
        $[""~getenv `QCMD; "q"; getenv `QCMD],
        " scripts/dev/run_backfill.q -worker demo_deals_backfill -version v2 -from 2026.09.13D00:00 -to 2026.09.14D00:00 -fixture ",
        extra," -hdb ",root,"/hdb < /dev/null > ",root,"/out.txt 2>&1; echo $?";
    code:"J"$first system cmd;
    / the child's own logging goes to the file: q's stdout capture misses it
    (read0 hsym `$root,"/out.txt";root,"/hdb";code)}

/ Whether the scratch HDB holds anything but the directory itself.
hdb_written:{[root] 0<count @[key;hsym `$root;()]}

test_a_dev_run_with_mode_plan_writes_nothing_to_the_hdb:{[t]
    r:.backfillproctest.run_dev["-mode plan";""];
    .qunit.assertEquals[r 2;0;"a plan is a clean exit"];
    .qunit.assertFalse[.backfillproctest.hdb_written r 1;"the -hdb root is untouched by a read-only plan"];
    .qunit.assertTrue[any (r 0) like\: "*plan - nothing opened, nothing written*";"and says what it did"]}

test_a_dev_run_without_a_mode_still_writes_the_hdb:{[t]
    r:.backfillproctest.run_dev["";""];
    .qunit.assertEquals[r 2;0;"a plain run completes"];
    .qunit.assertTrue[.backfillproctest.hdb_written r 1;"and writes: the plan test above is not passing for want of a working run"]}

test_a_dev_run_refuses_an_unknown_mode_by_name:{[t]
    r:.backfillproctest.run_dev["-mode bogus";""];
    .qunit.assertEquals[r 2;2;"refused, not run"];
    .qunit.assertTrue[any (r 0) like\: "mode: bogus is not one of*";"the refusal names the mode"];
    .qunit.assertFalse[.backfillproctest.hdb_written r 1;"and wrote nothing"]}

test_a_dev_run_refuses_a_mode_the_environment_would_outrank:{[t]
    r:.backfillproctest.run_dev["-mode plan";"UQF_MODE=run "];
    .qunit.assertEquals[r 2;2;"refused, not run"];
    .qunit.assertTrue[any (r 0) like\: "run_backfill: -mode plan would be ignored - UQF_MODE=run outranks it*";
        "the refusal is the shared text with this launcher's name"];
    .qunit.assertFalse[.backfillproctest.hdb_written r 1;"nothing written"]}

test_a_dev_run_refuses_an_unknown_on_conflict:{[t]
    r:.backfillproctest.run_dev["-on_conflict nonsense";""];
    .qunit.assertEquals[r 2;2;"refused, not run"];
    .qunit.assertFalse[.backfillproctest.hdb_written r 1;"before anything is written"]}

test_the_shared_flag_helpers_set_the_config_and_name_their_launcher:{[t]
    r:.backfillproctest.with_env["";"";
        {oc:.qetl.job.bounded.runtime.use_on_conflict["run_backfill";enlist[`on_conflict]!enlist enlist "replace"];
         md:.qetl.job.bounded.runtime.use_mode["run_backfill";enlist[`mode]!enlist enlist "plan"];
         (md;oc;.qetl.cfg.raw `on_conflict)}];
    .qunit.assertEquals[r;(`plan;`replace;"replace");"-on_conflict reaches .qetl.cfg, -mode is the mode in force"];
    r2:.backfillproctest.with_env["run";"";
        {.qetl.job.bounded.runtime.use_mode["run_backfill";enlist[`mode]!enlist enlist "plan"]}];
    .qunit.assertEquals[r2;(`err;"run_backfill: -mode plan would be ignored - UQF_MODE=run outranks it; unset it or drop the flag");
        "the launcher is named in the refusal"]}

\d .
