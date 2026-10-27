/ io_remote_contract.q - what a remote receiver is approved to write, as data (#1717).
/ .
/ WHY. A receiver (io_remote.q's .qetl.io.remote.serve, started by
/ scripts/dev/remote_receiver.q) must know the batches it may take: their
/ dataset, columns and types, the timestamp column the HDB partitions by, the
/ row key and the conflict policies. Until this file it found them by loading
/ the worker that produces them, so the stack's machine needed the job's
/ source, transform and worker code just to write its rows.
/ .
/ A RECEIVER CONTRACT is those facts and nothing else - no code, no path, no
/ credential. It is DERIVED from the worker's declarations on the machine that
/ has them (.qetl.io.remote.contract.of, via `uqs job contract export`), never
/ written by hand, and it is versioned so a receiver refuses a shape it does
/ not understand before anything is written. Its identity (a digest of the
/ canonical contract) is uqs's to compute, where the contract is exported and
/ approved (python/uqs/src/uqs/attach/receiver_contract.py).
/ .
/   time_column       the column the HDB writer partitions by: the source's
/                     time column, as .qetl.io.hdb_scoped takes it
/   window_column     the column a batch's window is cut on - what a batch's
/                     opts name as time_column; ` when the output has none
/   row_key           the columns a keyed strategy matches rows on
/   partition_column  the output column holding the worker's partition, and
/   partition         the partition itself; ` for an unpartitioned worker
/   conflict_policies the strategies the declarations admit; an operator may
/                     narrow them when approving, never widen
/ .
/ What binds a contract to a destination - which HDB, which users, how many
/ rows a message may carry - is the receiving operator's, and stays out of
/ the contract: the same contract can be approved for two attachments.

\d .

/ The format a contract names, and the versions a receiver reads.
.qetl.io.remote.contract.format:"uqf-receiver-contract"
.qetl.io.remote.contract.versions:enlist 1

/ The keys a contract carries; origin is informational, and identity is
/ added by uqs when the contract is exported.
.qetl.io.remote.contract.keys:`format`version`dataset`columns`time_column`window_column`row_key`partition_column`partition`conflict_policies`origin

/ Private: the strategies a worker's declarations admit. replace clears a
/ window by its window column, and only within the worker's partition, so it
/ needs the one and a way to tell the other.
/ @private
.qetl.io.remote.contract.policies:{[cfg]
    scoped:(not null cfg`partition_column) or cfg[`partition]=.qetl.job.bounded.unpartitioned;
    asc .qetl.io.strategies except $[(not null cfg`window_column) and scoped; `symbol$(); `replace]}

/ Private: a column's type for the contract - meta's, or for an untyped ()
/ column the element type the plant declares for it, " " when none.
/ @private
.qetl.io.remote.contract.element:{[dataset;c;x]
    if[not x=" "; :x];
    e:exec element from .qetl.plant.elements where table=dataset, column=c;
    $[count e; first e; " "]}

/ The receiver contract of a bounded worker, from its declarations: what a
/ receiver must be approved for before it takes the worker's batches.
/ @param worker the worker's name, a symbol; it must be loaded
/ @return a dict of .qetl.io.remote.contract.keys, ready for .j.j
/ @throws error for a worker that declares its own IO manager, or an output column with no concrete type
/ @eg (.qetl.io.remote.contract.of `demo_deals_backfill)`dataset  ->  `demo_deals
.qetl.io.remote.contract.of:{[worker]
    cfg:.qetl.job.bounded.def worker;
    if[not (::)~cfg`io;
        '"contract: ",string[worker]," declares its own IO manager, which would write where it likes"];
    out:(.qetl.transform.def cfg`transform)`output;
    / a nested column is () in the output; its element type is the plant's (.qetl.plant.nested)
    t:.qetl.io.remote.contract.element[cfg`dataset]'[cols out;exec t from meta out];
    if[count bad:cols[out] where " "=t;
        '"contract: ",string[worker],"'s output column(s) ",(", " sv string bad)," have no concrete type"];
    .qetl.io.remote.contract.keys!(
        .qetl.io.remote.contract.format;
        last .qetl.io.remote.contract.versions;
        cfg`dataset;
        {[c;y] (string c;enlist y)}'[cols out;t];
        (.qetl.source.def cfg`source)`time_column;
        cfg`window_column;
        (),cfg`target_key;
        cfg`partition_column;
        cfg`partition;
        .qetl.io.remote.contract.policies[cfg];
        `worker`source`transform!(worker;cfg`source;cfg`transform))}

/ Private: a JSON string (a char vector, or one char) as a symbol; ` for "".
/ @private
.qetl.io.remote.contract.sym:{[k;v]
    if[not (type v) in -10 10h; '"contract: ",string[k]," must be a string"];
    / not `$"": PeachQ casts an empty string to an empty symbol list
    $[count v; `$(),v; `]}

/ Private: a JSON array of strings as a symbol vector.
/ @private
.qetl.io.remote.contract.syms:{[k;v]
    if[not (type v) in 0 10h; '"contract: ",string[k]," must be an array of strings"];
    if[10h=type v; '"contract: ",string[k]," must be an array of strings"];
    `symbol$.qetl.io.remote.contract.sym[k] each v}

/ Private: a contract's version, refused unless this receiver reads it.
/ @private
.qetl.io.remote.contract.version:{[v]
    if[not (type v) in -9 -7h; '"contract: version must be a number"];
    if[not v=`long$v; '"contract: version must be a whole number"];
    v:`long$v;
    if[not v in .qetl.io.remote.contract.versions;
        '"contract: version ",string[v]," is not supported - this receiver reads version(s) ",
         " " sv string .qetl.io.remote.contract.versions];
    v}

/ Private: a contract's columns - [name, type] pairs - as (names; types).
/ @private
.qetl.io.remote.contract.columns:{[cs]
    if[(0=count cs) or not 0h=type cs; '"contract: columns must be a non-empty array of [name, type] pairs"];
    if[not all {[p] (0h=type p) and (2=count p) and all (type each p) in -10 10h} each cs;
        '"contract: columns must be a non-empty array of [name, type] pairs"];
    names:.qetl.io.remote.contract.sym[`columns] each first each cs;
    types:{[p] (),last p} each cs;
    if[not all 1=count each types; '"contract: a column's type is one of meta's characters"];
    types:raze types;
    if[any null names; '"contract: a column has no name"];
    if[not count[names]=count distinct names; '"contract: a column is named twice"];
    if[any types=" "; '"contract: every column needs a concrete type"];
    if[not all (lower types) in "bghijefcspmdznuvt"; '"contract: a column's type is not one of meta's characters"];
    (names;types)}

/ Refuse a contract a receiver cannot take, and return it in q's terms.
/ .
/ Takes the contract as .j.k reads it - strings, floats, general lists - so a
/ malformed or unsupported one fails here, before a grant is made and so
/ before anything is written.
/ @param c a dict, as .j.k returns a contract
/ @return dict: format, version, identity, dataset, columns, types (meta's
/   chars), time_column, window_column, row_key, partition_column, partition
/   and conflict_policies
/ @throws error naming what is wrong
/ @eg (.qetl.io.remote.contract.require .j.k .j.j .qetl.io.remote.contract.of `demo_deals_backfill)`dataset  ->  `demo_deals
.qetl.io.remote.contract.require:{[c]
    if[not 99h=type c; '"contract: a contract is a JSON object"];
    if[not .qetl.io.remote.contract.format~c`format;
        '"contract: not a receiver contract (format must be ",.qetl.io.remote.contract.format,")"];
    v:.qetl.io.remote.contract.version[c`version];
    if[count missing:(.qetl.io.remote.contract.keys except `origin) except key c;
        '"contract: missing ",", " sv string missing];
    nt:.qetl.io.remote.contract.columns[c`columns];
    names:first nt; types:last nt;
    tc:.qetl.io.remote.contract.sym[`time_column;c`time_column];
    if[not tc in names; '"contract: time_column ",string[tc]," is not a column"];
    wc:.qetl.io.remote.contract.sym[`window_column;c`window_column];
    if[not (null wc) or wc in names; '"contract: window_column ",string[wc]," is not a column"];
    rk:.qetl.io.remote.contract.syms[`row_key;c`row_key];
    if[count bad:rk except names; '"contract: row_key names no column ",", " sv string bad];
    pc:.qetl.io.remote.contract.sym[`partition_column;c`partition_column];
    if[not (null pc) or pc in names; '"contract: partition_column ",string[pc]," is not a column"];
    pt:.qetl.io.remote.contract.sym[`partition;c`partition];
    cp:.qetl.io.remote.contract.syms[`conflict_policies;c`conflict_policies];
    if[(0=count cp) or count bad:cp except .qetl.io.strategies;
        '"contract: conflict_policies must name strategies among ",", " sv string .qetl.io.strategies];
    id:$[`identity in key c; .qetl.io.remote.contract.sym[`identity;c`identity]; `];
    `format`version`identity`dataset`columns`types`time_column`window_column`row_key`partition_column`partition`conflict_policies!(
        c`format;v;id;.qetl.io.remote.contract.sym[`dataset;c`dataset];names;types;tc;wc;rk;pc;pt;cp)}

/ The empty table a contract's batches must match.
/ @param k a contract, as .qetl.io.remote.contract.require returns it
/ @return an empty table of its columns and types
/ @eg meta .qetl.io.remote.contract.schema `columns`types!(`time`sym;"ps")
.qetl.io.remote.contract.schema:{[k]
    flip k[`columns]!{[t] $[t within "a","z"; t$(); ()]} each k`types}

/ Refuse a batch's options that do not keep to the contract: the window
/ column, row key and partition must be the contract's, and the conflict
/ policy one it permits. Called by .qetl.io.remote.require_admitted for a
/ grant made under a contract.
/ @param k the grant's contract
/ @param o the batch's opts
/ @throws error naming the first mismatch
/ @eg .qetl.io.remote.contract.require_opts[`window_column`row_key`partition_column`partition`conflict_policies!(`time;`time`sym;`;`;enlist `upsert);`on_conflict`row_key`time_column!(`upsert;`time`sym;`time)]
.qetl.io.remote.contract.require_opts:{[k;o]
    opt:.qetl.io.remote.opt[o];
    if[not (opt `on_conflict) in k`conflict_policies;
        '"remote: on_conflict ",string[opt `on_conflict]," is not one the contract permits (",
         (", " sv string k`conflict_policies),")"];
    if[not (k`window_column)~opt `time_column;
        '"remote: the batch's window column ",string[opt `time_column]," is not the contract's ",string k`window_column];
    if[not ((),k`row_key)~(),opt `row_key;
        '"remote: the batch's row key ",(" " sv string (),opt `row_key)," is not the contract's ",
         " " sv string k`row_key];
    if[not (k`partition_column)~opt `partition_column;
        '"remote: the batch's partition column is not the contract's ",string k`partition_column];
    if[not (k`partition)~.qetl.io.remote.contract.sym[`partition;string opt `partition];
        '"remote: the batch is for partition ",string[opt `partition],", not the contract's ",string k`partition];
    }

/ Read a contract a receiver was started with: the approved record uqs
/ stores (`uqs job contract approve`), whose contract is checked by
/ .qetl.io.remote.contract.require and whose binding may narrow its policies.
/ @param path a file symbol naming the record's JSON
/ @return the contract, as require returns it, with the binding's policies
/ @throws error for an unreadable, malformed or unsupported record
.qetl.io.remote.contract.read:{[path]
    if[()~key path; '"contract: no file ",1_string path];
    r:@[.j.k;raze read0 path;{[p;e] '"contract: ",(1_string p)," is not JSON (",e,")"}[path]];
    if[not 99h=type r; '"contract: the record is a JSON object"];
    if[not `contract in key r; '"contract: the record carries no contract"];
    k:.qetl.io.remote.contract.require[r`contract];
    b:$[`binding in key r; r`binding; ()!()];
    if[not 99h=type b; '"contract: the binding is a JSON object"];
    if[`conflict_policies in key b;
        p:.qetl.io.remote.contract.syms[`conflict_policies;b`conflict_policies];
        if[(0=count p) or count p except k`conflict_policies;
            '"contract: the binding may narrow the contract's conflict policies, never widen them"];
        k[`conflict_policies]:p];
    k}

\d .
