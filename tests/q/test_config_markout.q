/ test_config_markout.q - a markout's configuration snapshot and the lineage from a result
/ row to it (src/etl/transforms/config_markout.q, .qpipe.transform.config_markout, #1999).
/ .
/ What is held here, against the issue's acceptance list:
/ .
/   every result resolves to its configuration   the calculation stamps config_id, pure; the
/                                                enriched read resolves it from the snapshot
/   runs pin before they calculate               tests/q/test_hdb_*_backfill.q and
/                                                test_fx_markout.q hold the jobs to it; this
/                                                file holds the pin and the publish guard
/   partial backfills keep lineage               a window re-scored under another configuration
/                                                replaces its own rows only; every row still
/                                                resolves to the snapshot that produced it
/   no latest-configuration substitution         a newer snapshot never answers for an older row
/   missing and conflicting ids fail explicitly  exact text
/   the pure calculation stays pure              it stamps an id and touches no store

.qt.qpipe.transform.config_markout.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;"build/test-status-config-markout"];
    system"mkdir -p build/test-status-config-markout";
    }

.qt.qpipe.transform.config_markout.setUp_fresh:{[] .qt.support.reset_snapshots[];}

.qt.qpipe.transform.config_markout.dom:{[] .qpipe.transform.fx_domain.domain}

/ The shipped FX job spec, and the same with one thing changed.
.qt.qpipe.transform.config_markout.spec:{[changes] .qpipe.transform.fx_markout_outputs.job_spec,changes}

/ A EURUSD buy and the books around it, `dt` later than the sample's, as fill `id`.
/ @return executions and market_data
.qt.qpipe.transform.config_markout.inputs:{[dt;id]
    e:.qpipe.transform.fx_markout_outputs.sample_executions[];
    m:.qpipe.transform.fx_markout_outputs.sample_books[];
    (update source_time:source_time+dt, fill_id:id from e;update source_time:source_time+dt from m)}

/ --- the calculation is pure ----------------------------------------------------------

.qt.qpipe.transform.config_markout.test_the_calculation_stamps_the_id_it_computes_and_touches_no_store:{[t]
    spec:.qt.qpipe.transform.config_markout.spec[()!()];
    in_:.qt.qpipe.transform.config_markout.inputs[0D;`x1];
    r:.qpipe.transform.fx_markout.calc[spec;in_ 0;in_ 1];
    d:.qt.qpipe.transform.config_markout.dom[];
    id:.qpipe.transform.config_markout.id[d;.qpipe.transform.markout_core.validate[d;spec]];
    .qunit.assertEquals[distinct r`config_id;enlist id;"every row names the one configuration"];
    .qunit.assertEquals[count .qetl.snapshot.list[`];0;"and nothing was recorded by calculating"];
    lines:read0 `:src/etl/transforms/markout_core.q;
    code:" " sv lines where not (first each lines)="/";
    pats:("snapshot.record";"snapshot.require";"snapshot.audit";"snapshot.reload";"snapshot.attach";"snapshot.list";"snapshot.show";"snapshot.store";"etl_config_snapshots";"config_markout.pin");
    found:pats where {[c;p] 0<count ss[c;p]}[code] each pats;
    .qunit.assertEquals[found;();"markout_core.q never reaches a store: it asks only for the id, a function of the spec"]};

.qt.qpipe.transform.config_markout.test_the_shipped_specs_ids_are_those_of_their_validated_specs:{[t]
    f:{[d;s] .qpipe.transform.config_markout.id[d;.qpipe.transform.markout_core.validate[d;s]]};
    .qunit.assertEquals[.qpipe.transform.fx_markout_outputs.config_id;f[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout_outputs.job_spec];"fx job spec"];
    .qunit.assertEquals[.qpipe.transform.fx_markout.config_id;f[.qpipe.transform.fx_domain.domain;.qpipe.transform.fx_markout.default_spec];"fx default spec"];
    .qunit.assertEquals[.qpipe.transform.crypto_markout_outputs.config_id;f[.qpipe.transform.crypto_markout.domain;.qpipe.transform.crypto_markout_outputs.job_spec];"crypto job spec"]};

.qt.qpipe.transform.config_markout.test_a_spec_that_states_a_default_is_the_spec_that_leaves_it_out:{[t]
    d:.qt.qpipe.transform.config_markout.dom[];
    full:.qt.qpipe.transform.config_markout.spec[()!()];
    bare:(`max_age`tolerance) _ full;
    .qunit.assertEquals[.qpipe.transform.config_markout.content[d;bare];.qpipe.transform.config_markout.content[d;full];"the completed spec is what is recorded"]};

.qt.qpipe.transform.config_markout.test_each_parameter_the_snapshot_records_makes_another_configuration:{[t]
    d:.qt.qpipe.transform.config_markout.dom[];
    s:.qt.qpipe.transform.config_markout.spec;
    id:{[d;sp] .qpipe.transform.config_markout.id[d;.qpipe.transform.markout_core.validate[d;sp]]}[d;];
    base:id s[()!()];
    others:(id s[enlist[`horizons]!enlist 0D00:00:01 0D00:00:20];
            id s[enlist[`max_age]!enlist 0D00:00:09];
            id s[enlist[`report_ccy]!enlist `EUR];
            id s[enlist[`rate_timing]!enlist `horizon];
            id s[enlist[`rates]!enlist enlist[`PLN]!enlist 0.25];
            id s[enlist[`tolerance]!enlist 1e-3];
            id[s[()!()]] ;
            .qpipe.transform.config_markout.id[@[d;`revision;:;`fx_r2];.qpipe.transform.markout_core.validate[d;s[()!()]]]);
    .qunit.assertEquals[others 6;base;"the same spec is the same configuration"];
    .qunit.assertEquals[count distinct base,others;8;"horizons, max_age, report_ccy, rate_timing, a declared rate, the FX tolerance and the domain's rules revision each make another"]};

.qt.qpipe.transform.config_markout.test_the_snapshot_records_the_code_contract_and_domain_revisions:{[t]
    c:.qpipe.transform.config_markout.content[.qt.qpipe.transform.config_markout.dom[];.qt.qpipe.transform.config_markout.spec[()!()]];
    .qunit.assertEquals[(c`contract_version;c`code_revision;c`domain;c`domain_revision);(1;1;`fx;`fx_r1);"contract, code, domain and its rules"];
    .qunit.assertEquals[c`datasets;`fx_markout`fx_markout_wide;"the datasets the spec produces"];
    .qunit.assertEquals[(c`report_ccy;c`rate_timing;c`horizons);(`USD;`execution;-0D00:00:01 0D00:00:00 0D00:00:01 0D00:00:10);"the validated parameters, horizons ascending"];
    .qunit.assertTrue[`convention in key c`domain_spec;"and the domain's own spec keys"]};

/ --- pinning and the publish guard ----------------------------------------------------

.qt.qpipe.transform.config_markout.test_pinning_records_the_snapshot_once_and_reports_why:{[t]
    d:.qt.qpipe.transform.config_markout.dom[];
    sp:.qt.qpipe.transform.config_markout.spec[()!()];
    a:.qpipe.transform.config_markout.pin[d;sp;"first pin"];
    b:.qpipe.transform.config_markout.pin[d;sp;"second pin"];
    .qunit.assertEquals[(a;count .qetl.snapshot.list[`]);(b;1);"pinning twice is one snapshot"];
    l:first .qetl.snapshot.list[`markout];
    .qunit.assertEquals[(l`snapshot_id;l`reason;l`datasets;l`digest_ok);(.qpipe.transform.fx_markout_outputs.config_id;"first pin";`fx_markout`fx_markout_wide;1b);"the id the rows will carry, the first reason, the datasets, a good digest"];
    .qunit.assertThrows[.qpipe.transform.config_markout.pin[d;;"bad"];enlist[`rate_timing]!enlist `never;"*spec is missing*";"a spec the core refuses is not recorded"];
    .qunit.assertEquals[count .qetl.snapshot.list[`];1;"and nothing was recorded for it"]};

.qt.qpipe.transform.config_markout.test_the_publish_guard_holds_rows_naming_an_unpinned_configuration:{[t]
    in_:.qt.qpipe.transform.config_markout.inputs[0D;`x1];
    rows:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.config_markout.spec[()!()];in_ 0;in_ 1];
    f:.qpipe.transform.config_markout.check_pinned rows;
    .qunit.assertEquals[(f`check;f`status);(enlist `config_unpinned;enlist `fail);"calculated, not pinned: the batch is refused"];
    d:first f`detail;
    .qunit.assertTrue[(d like "snapshot: markout_*") and d like "*is not recorded*";"naming the snapshot"];
    .qpipe.transform.fx_markout_outputs.pin[];
    .qunit.assertEquals[count .qpipe.transform.config_markout.check_pinned rows;0;"pinned, it passes"];
    nameless:update config_id:` from rows;
    .qunit.assertEquals[(.qpipe.transform.config_markout.check_pinned nameless)`detail;enlist "snapshot: a result row names no configuration (null config_id)";"a row naming none cannot be explained"];
    .qunit.assertEquals[count .qpipe.transform.config_markout.check_pinned 0#rows;0;"an empty batch has nothing to explain"]};

.qt.qpipe.transform.config_markout.test_a_snapshot_pinned_before_a_failed_publication_survives_with_no_results:{[t]
    / the interruption point between "pinned" and "published": the pin is on disk, no row exists
    id:first .qpipe.transform.fx_markout_outputs.pin[];
    ![`.;();0b;enlist `etl_config_snapshots];
    `.qetl.snapshot.verified set `symbol$();
    .qetl.snapshot.attach[];
    .qunit.assertEquals[.qetl.snapshot.list[`][`snapshot_id];enlist id;"after a restart the snapshot is there"];
    .qunit.assertEquals[first (.qetl.snapshot.require id)`kind;`markout;"and readable"];
    / re-running pins the same one: no second snapshot for the retry
    .qunit.assertEquals[(first .qpipe.transform.fx_markout_outputs.pin[];count .qetl.snapshot.list[`]);(id;1);"a retry reuses it"]};

/ --- reading a result's configuration ---------------------------------------------------

.qt.qpipe.transform.config_markout.test_the_enriched_read_shows_the_configuration_beside_the_results:{[t]
    in_:.qt.qpipe.transform.config_markout.inputs[0D;`x1];
    rows:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.config_markout.spec[()!()];in_ 0;in_ 1];
    .qpipe.transform.fx_markout_outputs.pin[];
    e:.qpipe.transform.config_markout.enrich rows;
    .qunit.assertEquals[count e;count rows;"one row in, one row out"];
    .qunit.assertEquals[(cols e) except cols rows;`contract_version`code_revision`domain`domain_revision`report_ccy`rate_timing`max_age`legacy`digest;"the configuration's fields are added"];
    .qunit.assertEquals[distinct select contract_version, report_ccy, rate_timing from e;([] contract_version:enlist 1; report_ccy:enlist `USD; rate_timing:enlist `execution);"read from the producing snapshot"];
    .qunit.assertEquals[distinct e`domain_revision;enlist `fx_r1;"with the rules' revision"]};

.qt.qpipe.transform.config_markout.test_a_row_whose_snapshot_is_missing_fails_and_is_never_given_another:{[t]
    in_:.qt.qpipe.transform.config_markout.inputs[0D;`x1];
    rows:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.config_markout.spec[()!()];in_ 0;in_ 1];
    / a different configuration IS pinned, and is the latest - it must not answer for these rows
    .qpipe.transform.config_markout.pin[.qt.qpipe.transform.config_markout.dom[];.qt.qpipe.transform.config_markout.spec[enlist[`report_ccy]!enlist `EUR];"the newer one"];
    id:first distinct rows`config_id;
    msg:"snapshot: ",string[id]," is not recorded - a result must name the configuration that produced it; there is no default to fall back to";
    .qunit.assertThrows[.qpipe.transform.config_markout.enrich;rows;msg;"enrich"];
    .qunit.assertThrows[.qpipe.transform.config_markout.params;rows;msg;"params"];
    .qunit.assertThrows[.qpipe.transform.config_markout.require_version[;1];rows;msg;"a consumer pinned to a version"]};

.qt.qpipe.transform.config_markout.test_a_consumer_pinned_to_a_version_refuses_another:{[t]
    d:.qt.qpipe.transform.config_markout.dom[];
    in_:.qt.qpipe.transform.config_markout.inputs[0D;`x1];
    rows:.qpipe.transform.fx_markout.calc[.qt.qpipe.transform.config_markout.spec[()!()];in_ 0;in_ 1];
    .qpipe.transform.fx_markout_outputs.pin[];
    .qunit.assertEquals[count .qpipe.transform.config_markout.require_version[rows;1];count rows;"the pinned version passes"];
    .qunit.assertThrows[.qpipe.transform.config_markout.require_version[;2];rows;"config_markout: contract version 1 is not the pinned 2";"another version"]};

/ --- partial backfills keep lineage ------------------------------------------------------

.qt.qpipe.transform.config_markout.test_a_partial_backfill_replaces_its_window_and_every_row_still_resolves_to_its_producer:{[t]
    d:.qt.qpipe.transform.config_markout.dom[];
    specA:.qt.qpipe.transform.config_markout.spec[()!()];
    specB:.qt.qpipe.transform.config_markout.spec[enlist[`max_age]!enlist 0D00:00:09];
    idA:.qpipe.transform.config_markout.pin[d;specA;"the first backfill"];
    / two windows an hour apart, both scored under A
    w1:.qt.qpipe.transform.config_markout.inputs[0D;`x1];
    w2:.qt.qpipe.transform.config_markout.inputs[0D01:00;`x2];
    score:{[spec;in_] .qpipe.transform.fx_markout_outputs.stamped_with[spec;`long;in_ 0;in_ 1]};
    existing:(score[specA;w1]),score[specA;w2];
    / a later partial backfill re-scores ONLY the first window, under B, replacing it
    idB:.qpipe.transform.config_markout.pin[d;specB;"a corrected reference age"];
    batch:score[specB;w1];
    opts:`row_key`target`time_column`range_from`range_to`partition_column`partition!(`venue`fill_id`horizon`kind`step;`fx_markout;`source_time;2026.09.17D10:00:00;2026.09.17D11:00:00;`;`);
    after:.qetl.io.resolve[`replace;existing;batch;opts];
    .qunit.assertTrue[idA<>idB;"the changed reference age is another configuration"];
    .qunit.assertEquals[(count after;count existing);(count existing;count existing);"as many rows: the window was replaced, not added to"];
    first_window:select from after where source_time<2026.09.17D11:00:00;
    second_window:select from after where source_time>=2026.09.17D11:00:00;
    .qunit.assertEquals[(distinct first_window`config_id;distinct second_window`config_id);(enlist idB;enlist idA);"the replaced rows name B, the untouched rows still name A"];
    p:.qpipe.transform.config_markout.params after;
    pa:first select from p where config_id=idA;
    pb:first select from p where config_id=idB;
    .qunit.assertEquals[(pa`max_age;pb`max_age);(0D00:00:05;0D00:00:09);"each resolves to the max_age its own snapshot recorded"];
    .qunit.assertEquals[(count .qetl.snapshot.list[`markout];.qetl.snapshot.require[idA]`snapshot_id);(2;enlist idA);"the superseded configuration is still recorded: nothing replaced a snapshot"]};
