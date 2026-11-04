/ test_config_snapshot.q - write-once configuration snapshots
/ (src/etl/core/config_snapshot.q, .qetl.snapshot, #1999).
/ .
/ The store is held to what the issue asks of it: a new configuration is a new
/ snapshot, an id reused with different content is refused, nothing updates or
/ deletes a row, the digest is checked on every load, a result cannot name a
/ snapshot nobody persisted, and all of it survives a restart. The digest is held to a
/ reference value worked once from the rendering rules, so a change to how content is
/ rendered - which would orphan every stored snapshot - fails here by name.

.qt.qetl.snapshot.dir:"build/test-status-snapshots"

.qt.qetl.snapshot.beforeNamespace_isolate:{[]
    setenv[`UQF_STATUS_DIR;.qt.qetl.snapshot.dir];
    system"mkdir -p ",.qt.qetl.snapshot.dir;
    }

.qt.qetl.snapshot.setUp_fresh:{[] .qt.support.reset_snapshots[];}

/ A content with a list, a timespan list, a dictionary and a float.
.qt.qetl.snapshot.content:{[] `b`a`rates!(2;0D00:00:10 0D00:00:01;`JPY`USDT!(`a`USDJPY;1f))}

/ --- the digest and the id ---------------------------------------------------------

.qt.qetl.snapshot.test_the_digest_of_a_content_is_a_reference_value:{[t]
    c:.qt.qetl.snapshot.content[];
    .qunit.assertEquals[.qetl.snapshot.digest[`markout;c];`2e4f42b4125b85e9177f088201ddb006;"the md5 of the kind and the rendered content"];
    .qunit.assertEquals[.qetl.snapshot.id_of[`markout;c];`markout_2e4f42b4125b85e9;"the id is the kind and the first 16 digits"]};

.qt.qetl.snapshot.test_key_order_and_console_precision_do_not_change_the_digest:{[t]
    c:.qt.qetl.snapshot.content[];
    reordered:`rates`a`b!(c`rates;c`a;c`b);
    .qunit.assertEquals[.qetl.snapshot.digest[`markout;reordered];.qetl.snapshot.digest[`markout;c];"the order a producer built the dictionary in"];
    p:first system"P";
    system"P 3";
    d:.qetl.snapshot.digest[`markout;c];
    system"P ",string p;
    .qunit.assertEquals[d;.qetl.snapshot.digest[`markout;c];"the console's float precision"]};

.qt.qetl.snapshot.test_any_changed_value_changes_the_digest_and_so_the_id:{[t]
    c:.qt.qetl.snapshot.content[];
    base:.qetl.snapshot.id_of[`markout;c];
    ids:(.qetl.snapshot.id_of[`markout;@[c;`b;:;3]];
         .qetl.snapshot.id_of[`markout;@[c;`a;:;0D00:00:10 0D00:00:02]];
         .qetl.snapshot.id_of[`markout;@[c;`rates;:;`JPY`USDT!(`a`USDJPY;1.0000001)]];
         .qetl.snapshot.id_of[`other;c]);
    .qunit.assertEquals[count distinct base,ids;5;"an integer, a horizon, a rate in its seventh digit and the kind each make another configuration"]};

.qt.qetl.snapshot.test_a_value_it_cannot_render_is_refused:{[t]
    .qunit.assertThrows[.qetl.snapshot.digest[`markout;];enlist[`f]!enlist {x};"*a snapshot holds atoms, lists, dictionaries and tables, not a value of type 100*";"a function is not configuration"];
    .qunit.assertThrows[.qetl.snapshot.digest[`markout;];1 2 3;"*content must be a dictionary of symbol to value*";"content is a dictionary"]};

/ --- write-once --------------------------------------------------------------------

.qt.qetl.snapshot.test_a_new_configuration_is_a_new_snapshot_and_the_same_one_is_not_recorded_twice:{[t]
    c:.qt.qetl.snapshot.content[];
    a:.qetl.snapshot.record_as[`markout_a;`markout;c;`alice;"first"];
    again:.qetl.snapshot.record_as[`markout_a;`markout;c;`bob;"second"];
    other:.qetl.snapshot.record[`markout;@[c;`b;:;3];"changed horizons"];
    .qunit.assertEquals[again;a;"recording the same configuration under the same id returns it"];
    s:.qetl.snapshot.list[`];
    .qunit.assertEquals[count s;2;"and adds nothing"];
    first_:first select from s where snapshot_id=`markout_a;
    .qunit.assertEquals[(first_`actor;first_`reason);(`alice;"first");"the first actor and reason stand"];
    .qunit.assertTrue[other<>a;"a changed configuration is another snapshot"];
    .qunit.assertEquals[.qetl.snapshot.content_of[a];c;"and the content comes back as it went in"]};

.qt.qetl.snapshot.test_reusing_an_id_with_different_content_is_refused_and_changes_nothing:{[t]
    c:.qt.qetl.snapshot.content[];
    .qetl.snapshot.record_as[`markout_a;`markout;c;`alice;"first"];
    before:.qetl.snapshot.audit[];
    .qunit.assertThrows[.qetl.snapshot.record_as[`markout_a;`markout;;`bob;"overwrite"];@[c;`b;:;99];
        "snapshot: markout_a already exists with a different configuration (digest *) - a snapshot is write-once; a changed configuration is a new snapshot";"the id is taken"];
    .qunit.assertEquals[.qetl.snapshot.audit[];before;"the store is byte for byte what it was"];
    .qunit.assertEquals[.qetl.snapshot.content_of[`markout_a];c;"and so is the content"]};

.qt.qetl.snapshot.test_a_snapshot_needs_a_reason_an_actor_and_an_id:{[t]
    c:.qt.qetl.snapshot.content[];
    .qunit.assertThrows[.qetl.snapshot.record_as[`markout_a;`markout;c;`alice;];"";"snapshot: a reason is required - say why this configuration was recorded";"no reason"];
    .qunit.assertThrows[.qetl.snapshot.record_as[`markout_a;`markout;c;`;];"why";"snapshot: actor must be a non-null symbol";"no actor"];
    .qunit.assertThrows[.qetl.snapshot.record_as[`;`markout;c;`alice;];"why";"snapshot: id must be a non-null symbol";"no id"];
    .qunit.assertEquals[count .qetl.snapshot.list[`];0;"nothing was recorded"]};

.qt.qetl.snapshot.test_nothing_in_the_interface_updates_or_deletes_a_snapshot:{[t]
    names:string key `.qetl.snapshot;
    risky:names where any each names like/: ("*delete*";"*update*";"*remove*";"*drop*";"*overwrite*";"*clear*";"*amend*";"*replace*");
    .qunit.assertEquals[risky;();"no function whose name says it changes or removes a snapshot"];
    lines:read0 `:src/etl/core/config_snapshot.q;
    code:" " sv lines where not (first each lines)="/";
    .qunit.assertEquals[0<count ss[code;"delete"];0b;"and the file never deletes from the store"];
    .qunit.assertEquals[0<count ss[code;"upsert"];0b;"or upserts into it: a row is only ever inserted"];
    / the stored table is assigned only by reload (what is on disk) and attach (an empty one to load into)
    .qunit.assertEquals[count ss[code;"`etl_config_snapshots set"];2;"and replaced only by those two"]};

/ --- restart and the digest on load ---------------------------------------------------

.qt.qetl.snapshot.test_snapshots_survive_a_restart:{[t]
    c:.qt.qetl.snapshot.content[];
    a:.qetl.snapshot.record[`markout;c;"before the restart"];
    / a restart: the process loses everything it held in memory
    ![`.;();0b;enlist `etl_config_snapshots];
    `.qetl.snapshot.verified set `symbol$();
    .qunit.assertEquals[.qetl.snapshot.attach[];`etl_config_snapshots;"attach loads the store"];
    .qunit.assertEquals[(.qetl.snapshot.store[])`snapshot_id;enlist a;"the snapshot is back"];
    .qunit.assertEquals[.qetl.snapshot.content_of[a];c;"with its content"];
    .qunit.assertEquals[.qetl.snapshot.require_pinned[enlist a];enlist a;"and a result naming it is publishable"]};

.qt.qetl.snapshot.test_a_changed_digest_is_refused_on_load_and_by_every_reader:{[t]
    c:.qt.qetl.snapshot.content[];
    a:.qetl.snapshot.record_as[`markout_a;`markout;c;`alice;"first"];
    b:.qetl.snapshot.record[`markout;@[c;`b;:;3];"second"];
    p:.qetl.snapshot.path[];
    s:.qetl.job.bounded.state.durable_get p;
    / someone edits the file: the first snapshot's digest no longer matches its content
    .qetl.job.bounded.state.durable_set[p;update digest:`deadbeefdeadbeefdeadbeefdeadbeef from s where snapshot_id=a];
    ![`.;();0b;enlist `etl_config_snapshots];
    `.qetl.snapshot.verified set `symbol$();
    .qunit.assertThrows[.qetl.snapshot.reload;::;"snapshot: the digest of markout_a does not match its content - the store was changed outside the interface";"load"];
    .qunit.assertThrows[.qetl.snapshot.require;a;"snapshot: the digest of markout_a does not match its content - the store was changed outside the interface";"a single read"];
    .qunit.assertThrows[.qetl.snapshot.require_pinned;enlist a;"snapshot: the digest of markout_a does not match its content - the store was changed outside the interface";"the publish guard"];
    .qunit.assertThrows[.qetl.snapshot.record[`markout;@[c;`b;:;4];];"third";"snapshot: the digest of markout_a does not match its content - the store was changed outside the interface";"and a writer will not append to a store it cannot verify"];
    audit:.qetl.snapshot.audit[];
    .qunit.assertEquals[exec snapshot_id!digest_ok from audit;(a,b)!01b;"the audit shows the damaged one without throwing"]};

.qt.qetl.snapshot.test_a_store_of_another_shape_is_refused:{[t]
    .qetl.snapshot.record_as[`markout_a;`markout;.qt.qetl.snapshot.content[];`alice;"first"];
    p:.qetl.snapshot.path[];
    .qetl.job.bounded.state.durable_set[p;update extra:1 from .qetl.job.bounded.state.durable_get p];
    ![`.;();0b;enlist `etl_config_snapshots];
    .qunit.assertThrows[.qetl.snapshot.audit;::;"snapshot: etl_config_snapshots has unexpected extra";"a column this version does not know"]};

/ --- there is no latest-snapshot fallback ---------------------------------------------

.qt.qetl.snapshot.test_an_unknown_id_is_an_error_never_a_default:{[t]
    .qetl.snapshot.record[`markout;.qt.qetl.snapshot.content[];"the only one"];
    .qunit.assertThrows[.qetl.snapshot.require;`markout_nope;"snapshot: markout_nope is not recorded - a result must name the configuration that produced it; there is no default to fall back to";"unknown id, though another snapshot exists"];
    .qunit.assertThrows[.qetl.snapshot.require_many;`markout_nope`markout_other;"snapshot: markout_nope, markout_other is not recorded*";"every missing id is named"];
    .qunit.assertThrows[.qetl.snapshot.require_many;enlist `;"snapshot: a result row names no configuration (null config_id)";"a null id"]};

.qt.qetl.snapshot.test_the_publish_guard_refuses_an_unrecorded_or_null_id:{[t]
    a:.qetl.snapshot.record[`markout;.qt.qetl.snapshot.content[];"guard"];
    .qunit.assertEquals[.qetl.snapshot.require_pinned[a,a];enlist a;"a recorded id passes, once"];
    .qunit.assertThrows[.qetl.snapshot.require_pinned;a,`markout_unrecorded;"snapshot: markout_unrecorded is not recorded*";"an id nobody recorded"];
    .qunit.assertThrows[.qetl.snapshot.require_pinned;a,`;"snapshot: a result row names no configuration (null config_id)";"a null id"]};

/ --- reading ------------------------------------------------------------------------

.qt.qetl.snapshot.test_list_filters_by_kind_and_names_the_datasets_a_content_declares:{[t]
    c:.qt.qetl.snapshot.content[];
    .qetl.snapshot.record[`markout;c,enlist[`datasets]!enlist `fx_markout`fx_markout_wide;"with datasets"];
    .qetl.snapshot.record[`other;c;"no datasets"];
    l:.qetl.snapshot.list[`markout];
    .qunit.assertEquals[(count l;first l`datasets;first l`digest_ok);(1;`fx_markout`fx_markout_wide;1b);"the markout snapshot, its datasets, digest ok"];
    .qunit.assertEquals[count .qetl.snapshot.list[`];2;"all kinds"];
    .qunit.assertEquals[(exec first datasets from .qetl.snapshot.list[`] where kind=`other);`symbol$();"a content that names none lists none"]};

.qt.qetl.snapshot.test_show_gives_every_recorded_field_and_parameter:{[t]
    c:.qt.qetl.snapshot.content[];
    a:.qetl.snapshot.record_as[`markout_a;`markout;c;`alice;"why not"];
    s:.qetl.snapshot.show a;
    .qunit.assertEquals[s`field;`snapshot_id`kind`digest`created_at`actor`reason`b`a`rates;"the record's fields, then the content's keys in order"];
    f:exec field!text from s;
    .qunit.assertEquals[(f`snapshot_id;f`actor;f`reason;f`b);("markout_a";"alice";"why not";enlist "2");"as text"]};
