/ test_io_remote_contract.q - the receiver contract: derived from declarations, checked on every batch (#1717).
/ .
/ A receiver serves a grant made from a contract, not from a loaded worker. These cases derive
/ contracts from this tree's workers, refuse malformed and unsupported ones, and send batches
/ through .qt.qetl.io.remote's in-process client to a grant made under a contract: each
/ mismatch is refused before anything is written.

/ A contract for .qt.qetl.io.remote's deals, as the receiver reads one: through .j.k.
.qt.qetl.io.remote.contract.json:{[]
    .j.k .j.j `format`version`dataset`columns`time_column`window_column`row_key`partition_column`partition`conflict_policies`origin!(
        .qetl.io.remote.contract.format;1;`deals;(("time";"p");("sym";"s");("notional";"f"));
        `time;`time;`sym`time;`;`;`append`upsert;`worker`source`transform!`w`s`t)}

.qt.qetl.io.remote.contract.grant:{[k]
    .qetl.io.remote.serve[`uat;`deals;`mgr`target`schema`max_rows`users`contract!(
        .qetl.io.memory;`rdeals;.qetl.io.remote.contract.schema k;100;`symbol$();k)]}

/ A fresh receiver granted deals under the contract `c` (a .j.k dict) says.
.qt.qetl.io.remote.contract.serving:{[c]
    .qt.qetl.io.remote.reset[];
    .qt.qetl.io.remote.contract.grant[.qetl.io.remote.contract.require c]}

/ Send the deals batch under strategy s with opts changed by `change`.
.qt.qetl.io.remote.contract.send:{[s;change]
    .qetl.io.write_keyed[.qt.qetl.io.remote.client[];`rdeals;.qt.qetl.io.remote.batch[];(.qt.qetl.io.remote.opts s),change]}

/ --- derived from the declarations ----------------------------------------------------

.qt.qetl.io.remote.contract.test_a_contract_is_derived_from_the_workers_declarations:{[t]
    c:.qetl.io.remote.contract.of `demo_deals_backfill;
    cfg:.qetl.job.bounded.def `demo_deals_backfill;
    out:(.qetl.transform.def cfg`transform)`output;
    .qunit.assertEquals[c`dataset;`demo_deals;"the worker's dataset"];
    .qunit.assertEquals[`$first each c`columns;cols out;"the transform's output columns"];
    .qunit.assertEquals[raze last each c`columns;exec t from meta out;"in its types"];
    .qunit.assertEquals[c`time_column;(.qetl.source.def cfg`source)`time_column;"the source's time column"];
    .qunit.assertEquals[c`row_key;(),cfg`target_key;"the worker's target key"];
    .qunit.assertEquals[c`version;last .qetl.io.remote.contract.versions;"the version this tree writes"]};

.qt.qetl.io.remote.contract.test_a_nested_column_carries_the_plants_element_type:{[t]
    c:.qetl.io.remote.contract.of `hdb_crypto_book_backfill;
    ty:(`$first each c`columns)!raze last each c`columns;
    .qunit.assertEquals[ty`bid_prices;"F";"a () column is typed by .qetl.plant.nested"]};

.qt.qetl.io.remote.contract.test_replace_is_permitted_only_where_a_window_can_be_cleared:{[t]
    cfg:`window_column`partition_column`partition!(`time;`;.qetl.job.bounded.unpartitioned);
    .qunit.assertEquals[.qetl.io.remote.contract.policies cfg;asc .qetl.io.strategies;"unpartitioned, with a window column"];
    .qunit.assertEquals[.qetl.io.remote.contract.policies @[cfg;`window_column;:;`];
        asc .qetl.io.strategies except `replace;"no window column"];
    .qunit.assertEquals[.qetl.io.remote.contract.policies @[cfg;`partition;:;`p1];
        asc .qetl.io.strategies except `replace;"a partition with no column to tell it by"]};

.qt.qetl.io.remote.contract.test_a_derived_contract_reads_back_as_itself:{[t]
    k:.qetl.io.remote.contract.require .j.k .j.j .qetl.io.remote.contract.of `demo_deals_backfill;
    .qunit.assertEquals[k`dataset;`demo_deals;"through JSON and back"];
    .qunit.assertEquals[k`row_key;enlist `deal_id;"a one-column key stays a list"];
    .qunit.assertEquals[meta .qetl.io.remote.contract.schema k;
        meta 0#(.qetl.transform.def (.qetl.job.bounded.def `demo_deals_backfill)`transform)`output;
        "its schema is the transform's output"]};

/ --- malformed and unsupported ----------------------------------------------------------

.qt.qetl.io.remote.contract.test_an_unsupported_version_is_refused:{[t]
    c:@[.qt.qetl.io.remote.contract.json[];`version;:;2f];
    .qunit.assertThrows[.qetl.io.remote.contract.require;c;
        "contract: version 2 is not supported*";"a version this receiver does not read"]};

.qt.qetl.io.remote.contract.test_a_malformed_contract_is_refused:{[t]
    c:.qt.qetl.io.remote.contract.json[];
    .qunit.assertThrows[.qetl.io.remote.contract.require;@[c;`format;:;"other"];
        "contract: not a receiver contract*";"another format"];
    .qunit.assertThrows[.qetl.io.remote.contract.require;`row_key _ c;
        "contract: missing row_key";"a missing key"];
    .qunit.assertThrows[.qetl.io.remote.contract.require;@[c;`columns;:;enlist ("time";" ")];
        "contract: every column needs a concrete type";"an untyped column"];
    .qunit.assertThrows[.qetl.io.remote.contract.require;@[c;`row_key;:;enlist "nope"];
        "contract: row_key names no column nope";"a key that is not a column"];
    .qunit.assertThrows[.qetl.io.remote.contract.require;@[c;`conflict_policies;:;enlist "overwrite"];
        "contract: conflict_policies must name strategies*";"an unknown policy"];
    .qunit.assertThrows[.qetl.io.remote.contract.require;"not a dict";
        "contract: a contract is a JSON object";"not an object"]};

/ --- every batch is checked against it -----------------------------------------------

.qt.qetl.io.remote.contract.test_a_batch_that_keeps_to_the_contract_is_committed:{[t]
    .qt.qetl.io.remote.contract.serving .qt.qetl.io.remote.contract.json[];
    .qunit.assertEquals[.qt.qetl.io.remote.contract.send[`upsert;()!()];2;"written"];
    .qunit.assertEquals[count rdeals;2;"through the grant's manager"]};

.qt.qetl.io.remote.contract.test_a_conflict_policy_the_contract_does_not_permit_is_refused:{[t]
    .qt.qetl.io.remote.contract.serving .qt.qetl.io.remote.contract.json[];
    .qunit.assertThrows[{[ignored;s] .qt.qetl.io.remote.contract.send[s;()!()]}[::];`replace;
        "remote: on_conflict replace is not one the contract permits*";"refused"];
    .qunit.assertEquals[`rdeals in tables `.;0b;"nothing was written"]};

.qt.qetl.io.remote.contract.test_a_row_key_window_column_or_partition_mismatch_is_refused:{[t]
    .qt.qetl.io.remote.contract.serving .qt.qetl.io.remote.contract.json[];
    f:{[ignored;ch] .qt.qetl.io.remote.contract.send[`upsert;ch]}[::];
    .qunit.assertThrows[f;(enlist `row_key)!enlist `sym;"remote: the batch's row key sym is not the contract's sym time";"row key"];
    .qunit.assertThrows[f;(enlist `time_column)!enlist `sym;"remote: the batch's window column sym is not the contract's time";"window column"];
    .qunit.assertThrows[f;(enlist `partition)!enlist `p1;"remote: the batch is for partition p1*";"partition"];
    .qunit.assertThrows[f;(enlist `partition_column)!enlist `sym;"remote: the batch's partition column*";"partition column"];
    .qunit.assertEquals[`rdeals in tables `.;0b;"nothing was written"];
    .qunit.assertEquals[count .qetl.io.remote.journal;0;"nor journalled"]};

.qt.qetl.io.remote.contract.test_a_schema_mismatch_against_the_contract_is_refused:{[t]
    .qt.qetl.io.remote.contract.serving .qt.qetl.io.remote.contract.json[];
    b:update notional:`long$notional from .qt.qetl.io.remote.batch[];
    .qunit.assertThrows[{[ignored;x] .qetl.io.write_keyed[.qt.qetl.io.remote.client[];`rdeals;x;.qt.qetl.io.remote.opts `upsert]}[::];b;
        "remote: rdeals's batch types differ from its schema";"refused"];
    .qunit.assertEquals[`rdeals in tables `.;0b;"nothing was written"]};

.qt.qetl.io.remote.contract.test_a_contract_for_another_dataset_is_not_granted:{[t]
    .qt.qetl.io.remote.reset[];
    k:.qetl.io.remote.contract.require @[.qt.qetl.io.remote.contract.json[];`dataset;:;"trades"];
    .qunit.assertThrows[.qt.qetl.io.remote.contract.grant;k;"remote serve: the contract is for trades, not deals";"refused"]};

/ --- the approved record a receiver starts from -------------------------------------

.qt.qetl.io.remote.contract.record:{[binding]
    f:hsym `$(first system"mktemp -d"),"/approved.json";
    f 0: enlist .j.j `contract`binding!(.qt.qetl.io.remote.contract.json[];binding);
    f}

.qt.qetl.io.remote.contract.test_a_record_may_narrow_its_policies_never_widen_them:{[t]
    k:.qetl.io.remote.contract.read .qt.qetl.io.remote.contract.record[(enlist `conflict_policies)!enlist enlist `upsert];
    .qunit.assertEquals[k`conflict_policies;enlist `upsert;"the binding narrows"];
    .qunit.assertThrows[.qetl.io.remote.contract.read;
        .qt.qetl.io.remote.contract.record[(enlist `conflict_policies)!enlist `upsert`replace];
        "contract: the binding may narrow*";"never widens"]};

.qt.qetl.io.remote.contract.test_a_missing_or_unreadable_record_is_refused:{[t]
    d:first system"mktemp -d";
    .qunit.assertThrows[.qetl.io.remote.contract.read;hsym `$d,"/none.json";"contract: no file*";"missing"];
    f:hsym `$d,"/bad.json";
    f 0: enlist "{not json";
    .qunit.assertThrows[.qetl.io.remote.contract.read;f;"*is not JSON*";"not JSON"]};
