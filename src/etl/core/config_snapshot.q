/ config_snapshot.q - write-once configuration snapshots (.qetl.snapshot, #1999).
/ .
/ WHAT WAS MISSING. A materialised row said which window and which run produced it
/ (.qetl.coverage, .qetl.run) but not under WHAT CONFIGURATION: the horizons, the
/ reference-age limit, the reporting currency, the declared rates, the revision of
/ the rules that price an instrument. A markout row repeated three of those
/ (contract_version, rate_timing, report_ccy), which did not identify the rest, so a
/ historical result could not be tied back to the conditions that made it - least of
/ all one a partial backfill had since replaced. A snapshot is that record, and a
/ result row carries ONE compact reference to it (the dataset's `config_id`).
/ .
/ WHY HERE AND NOT A NEW REGISTRY. A snapshot is run metadata of the same kind as
/ etl_runs and etl_run_meta, and it is kept the same way: a table in the status
/ directory, written under a named file lock through durable_set, reloaded and
/ shape-checked by attach, read by a short-lived q from `uqs`. What it is NOT is a
/ schema registry: q stays authoritative for the domain's rules, and a snapshot
/ only RECORDS the parameters and the rules' revision a producer ran under. The
/ producer builds the content (markout_config.q); this file knows nothing of
/ markouts, only that a snapshot has a kind, content and a digest.
/ .
/ WRITE-ONCE, AT THE APPLICATION LEVEL. q has no immutable table, so the guarantee
/ is the interface's:
/ .
/   - a new configuration is a new snapshot; recording the same one again returns
/     the same id and changes nothing (the first actor, time and reason stand);
/   - an id that exists with different content is REFUSED, never overwritten;
/   - nothing here updates or deletes a row: `record_as` appends, and that is the
/     only writer (tests/q/test_config_snapshot.q holds the public surface to it);
/   - the digest is recomputed from the stored content on every load, so a file
/     edited by hand, or damaged, is refused by id instead of being believed;
/   - a writer refuses to append to a store it cannot verify.
/ .
/ This is not tamper-resistance: someone who can write the status directory can
/ rewrite the file, and the digest then says so on the next load, which is the most
/ q can promise. External, tamper-proof storage is out of scope (#1999).
/ .
/ THE ID IS THE CONTENT'S. An id is `<kind>_<first 16 hex of the digest>`, so two
/ producers that ran the same configuration agree on it without talking, and the
/ calculation can stamp it on a row as a pure function of its inputs - it never
/ reads this store. Only the PUBLISH path does (require_pinned): a row naming a
/ snapshot nobody persisted is refused, so a result cannot become visible before
/ the configuration that explains it.

\d .

/ ---------------------------------------------------------------- SCHEMA

/ The store's columns, in order. One constant in one place: a change is an edit
/ here, in empty, and in the writer - the same discipline as .qetl.run.run_schema.
/ content is the snapshot's parameters, a dictionary of symbol to value, SERIALISED (-8!): a
/ column of dictionaries is not a column q keeps as one - a list of dictionaries with the
/ same keys becomes a table, and a row of different keys then fails to insert on PeachQ - so
/ the cell is the bytes and `decode` is the one way back, with horizons still timespans.
/ reason is a string.
.qetl.snapshot.schema:`snapshot_id`kind`digest`content`created_at`actor`reason

/ The store's shape, typed and empty.
/ @return the empty table
/ @eg cols .qetl.snapshot.empty[]
.qetl.snapshot.empty:{[] ([] snapshot_id:`symbol$(); kind:`symbol$(); digest:`symbol$(); content:(); created_at:`timestamp$(); actor:`symbol$(); reason:())}

/ How many hex digits of the digest an id carries: enough that two different
/ configurations never share one in practice, and record_as refuses it if they do.
.qetl.snapshot.id_digits:16

/ The ids this process has verified are in the store. Positive answers only, and
/ safe to cache because nothing here ever removes a snapshot.
.qetl.snapshot.verified:`symbol$()

/ The root table, so no read names `etl_config_snapshots` bare (a bare name inside
/ \d .qetl.snapshot would resolve to a variable that does not exist).
/ @return the in-memory store
.qetl.snapshot.store:{[] value `etl_config_snapshots}

/ A stored content, read back as the dictionary it was.
/ @param cell a content cell of the store
/ @return the content dictionary
/ @throws error when the bytes are not a serialised value
/ @eg .qetl.snapshot.decode[-8!enlist[`a]!enlist 1]  ->  enlist[`a]!enlist 1
.qetl.snapshot.decode:{[cell] -9!cell}

/ ------------------------------------------------------------------ DIGEST

/ Private: a float with enough digits to tell two declared values apart, whatever
/ the console precision is. The previous precision is put back even on a throw.
/ @private
.qetl.snapshot.float_text:{[f]
    p:@[system;"P";{[e] 7}];
    @[system;"P 17";::];
    r:@[{$[0>type x; string x; "," sv string x]};f;{[e] "?"}];
    @[system;"P ",string p;::];
    r}

/ Private: one value as text that tells it from every other: a type tag, then its
/ digits. A dictionary is rendered in key order, so the order a producer built it
/ in does not change the digest; a general list renders item by item; a float at
/ full precision. Anything else (a table, a function) is not a configuration value.
/ @private
.qetl.snapshot.render:{[v]
    t:type v;
    tag:(string abs t),":";
    $[98h=t; tag,.qetl.snapshot.render flip v;
      99h=t; $[98h=type key v; tag,"keyed",.qetl.snapshot.render 0!v;
          tag,"{",(";" sv {[d;k] (string k),"=",.qetl.snapshot.render d k}[v;] each asc key v),"}"];
      0h=t; tag,"(",(";" sv .qetl.snapshot.render each v),")";
      10h=t; tag,v;
      t in -9 9h; tag,.qetl.snapshot.float_text v;
      (t within -19 19h) and t<>0h; $[t<0; tag,string v; tag,"," sv string v];
      '"render: a snapshot holds atoms, lists, dictionaries and tables, not a value of type ",string t]}

/ The digest of a snapshot's kind and content.
/ .
/ Pure: a function of its arguments, so a calculation can compute the id it will
/ stamp without touching the store. The creation time, actor and reason are NOT in
/ it: they say who recorded the configuration, not what it is, and the same
/ configuration recorded twice must be one snapshot.
/ @param kind the snapshot kind, e.g. `markout
/ @param content a dictionary of symbol to value
/ @return the md5 digest, as a symbol of 32 hex digits
/ @throws error when the content is not a symbol-keyed dictionary or holds an unsupported value
/ @eg count string .qetl.snapshot.digest[`markout;enlist[`a]!enlist 1]  ->  32
.qetl.snapshot.digest:{[kind;content]
    if[not (-11h=type kind) and not null kind; '"snapshot: kind must be a non-null symbol"];
    if[not (99h=type content) and 11h=type key content; '"snapshot: content must be a dictionary of symbol to value"];
    `$raze string md5 (string kind),"|",.qetl.snapshot.render content}

/ The id a content has: its kind and the first digits of its digest.
/ @param kind the snapshot kind
/ @param content a dictionary of symbol to value
/ @return the snapshot id, a symbol
/ @throws error as digest does
/ @eg .qetl.snapshot.id_of[`markout;enlist[`a]!enlist 1] like "markout_*"  ->  1b
.qetl.snapshot.id_of:{[kind;content]
    `$(string kind),"_",.qetl.snapshot.id_digits#string .qetl.snapshot.digest[kind;content]}

/ ------------------------------------------------------------ PERSISTENCE

/ Where the store lives between processes: the status directory, beside the run
/ ledger and the coverage ledger, for the reason they share it.
/ @return the file path, a string
/ @eg .qetl.snapshot.path[] like "*etl_config_snapshots"  ->  1b
.qetl.snapshot.path:{[] (.qetl.job.bounded.state.lock_dir[]),"/etl_config_snapshots"}

/ Private: the rows a store file holds, refusing a different shape.
/ @private
.qetl.snapshot.require_shape:{[t]
    if[not 98h=type t; '"snapshot: etl_config_snapshots is not a table"];
    want:.qetl.snapshot.schema;
    absent:want where not want in cols t;
    extra:(cols t) where not (cols t) in want;
    if[count absent; '"snapshot: etl_config_snapshots is missing ",(", " sv string absent)];
    if[count extra; '"snapshot: etl_config_snapshots has unexpected ",", " sv string extra];
    .qetl.job.bounded.state.require_types[`etl_config_snapshots;t;.qetl.snapshot.empty[]];
    t}

/ Private: a boolean per row, whether its stored digest is the digest of its stored
/ kind and content. A row whose content cannot be rendered is not ok.
/ @private
.qetl.snapshot.digest_ok:{[t]
    {[k;b;d] d~@[{[k;b] .qetl.snapshot.digest[k;.qetl.snapshot.decode b]}[k;];b;{[e] `}]}'[t`kind;t`content;t`digest]}

/ Every snapshot in the file, with a digest_ok column, and no throw over a bad
/ digest: for a reader that must SHOW a damaged store (`uqs data snapshot list`),
/ not trust it. A shape it cannot read at all is still refused.
/ @return the stored rows, each with digest_ok
/ @throws error when the file exists but is not a snapshot table
/ @eg count .qetl.snapshot.audit[]
.qetl.snapshot.audit:{[]
    p:hsym `$.qetl.snapshot.path[];
    if[()~key p; :update digest_ok:`boolean$() from .qetl.snapshot.empty[]];
    t:.qetl.snapshot.require_shape .qetl.job.bounded.state.durable_get .qetl.snapshot.path[];
    update digest_ok:.qetl.snapshot.digest_ok t from t}

/ Replace the in-memory store with what is on disk, verifying every digest.
/ .
/ A file that does not hold the digest of what it says it holds is REFUSED by id,
/ whole: the alternative, serving the rows that are fine, would let a reader trust
/ a store known to be damaged. No file is not an error - nothing is recorded yet.
/ @return the table name
/ @throws error naming each snapshot whose digest does not match its content
/ @eg .qetl.snapshot.reload[]
.qetl.snapshot.reload:{[]
    a:.qetl.snapshot.audit[];
    if[count bad:exec snapshot_id from a where not digest_ok;
        '"snapshot: the digest of ",(", " sv string bad)," does not match its content - the store was changed outside the interface"];
    `etl_config_snapshots set (.qetl.snapshot.schema)#a;
    `etl_config_snapshots}

/ Create the store if absent, and load what earlier processes recorded.
/ .
/ Niladic and idempotent: scripts/generate/export_contract_surface.q discovers
/ this tree's tables by calling every niladic `attach` in an owned namespace.
/ @return the table name
/ @eg .qetl.snapshot.attach[]
.qetl.snapshot.attach:{[]
    if[not `etl_config_snapshots in tables `.; `etl_config_snapshots set .qetl.snapshot.empty[]];
    .qetl.snapshot.reload[]}

/ ----------------------------------------------------------------- WRITING

/ Record a snapshot under an id the caller names, or confirm it is already there.
/ .
/ THE WRITE-ONCE RULE. Under the store's lock, after reloading what other
/ processes wrote: an id that is absent is appended; an id present with the same
/ digest is returned unchanged; an id present with another digest is refused.
/ Nothing is ever updated, so the first actor, time and reason stand.
/ @param id the snapshot id
/ @param kind the snapshot kind
/ @param content a dictionary of symbol to value
/ @param actor who is recording it, a symbol
/ @param reason why, a non-empty string
/ @return the id
/ @throws error for an id in use with different content, an empty reason, a content that cannot be rendered, or a store that fails verification
/ @eg .qetl.snapshot.record_as[`markout_eg;`markout;enlist[`a]!enlist 1;`eg;"example"]
.qetl.snapshot.record_as:{[id;kind;content;actor;reason]
    if[not (-11h=type id) and not null id; '"snapshot: id must be a non-null symbol"];
    reason:(),reason;
    if[not (10h=type reason) and 0<count reason; '"snapshot: a reason is required - say why this configuration was recorded"];
    if[not (-11h=type actor) and not null actor; '"snapshot: actor must be a non-null symbol"];
    dg:.qetl.snapshot.digest[kind;content];
    / one row as a table: a row given as a list reads a vector in it as several rows
    row:([] snapshot_id:enlist id; kind:enlist kind; digest:enlist dg; content:enlist -8!content;
        created_at:enlist .z.p; actor:enlist actor; reason:enlist reason);
    .qetl.job.bounded.state.with_file_lock[`etl_config_snapshots;{[row]
        .qetl.snapshot.attach[];
        id:first row`snapshot_id;
        held:select from .qetl.snapshot.store[] where snapshot_id=id;
        if[count held;
            if[not (first held`digest)~first row`digest;
                '"snapshot: ",string[id]," already exists with a different configuration (digest ",string[first held`digest]," against ",string[first row`digest],") - a snapshot is write-once; a changed configuration is a new snapshot"];
            :id];
        `etl_config_snapshots insert row;
        .qetl.job.bounded.state.durable_set[.qetl.snapshot.path[];.qetl.snapshot.store[]];
        id};enlist row];
    .qetl.snapshot.verified::distinct .qetl.snapshot.verified,id;
    id}

/ Record a configuration under the id its content gives it.
/ @param kind the snapshot kind
/ @param content a dictionary of symbol to value
/ @param reason why it is being recorded, a non-empty string
/ @return the id
/ @throws error as record_as does
/ @eg .qetl.snapshot.record[`markout;enlist[`a]!enlist 1;"example"] like "markout_*"  ->  1b
.qetl.snapshot.record:{[kind;content;reason]
    .qetl.snapshot.record_as[.qetl.snapshot.id_of[kind;content];kind;content;.z.u;reason]}

/ ----------------------------------------------------------------- READING

/ Several snapshots, digest checked, in the order asked.
/ .
/ Reads the file, not memory, so a snapshot another process recorded is seen, and
/ it reads it ONCE however many ids are asked for. It never falls back to another
/ snapshot: an unknown id is an error, because a lookup that substituted the latest
/ configuration would answer for the wrong conditions and look right.
/ @param ids the snapshot ids
/ @return the snapshots, one row per id, in the order of ids (the schema's columns)
/ @throws error when an id is null, not recorded, or its digest does not match its content
/ @eg count .qetl.snapshot.require_many `symbol$()
.qetl.snapshot.require_many:{[ids]
    if[any null ids; '"snapshot: a result row names no configuration (null config_id)"];
    a:.qetl.snapshot.audit[];
    if[count gone:ids where not ids in a`snapshot_id;
        '"snapshot: ",(", " sv string distinct gone)," is not recorded - a result must name the configuration that produced it; there is no default to fall back to"];
    r:a ((a`snapshot_id)?ids);
    if[count bad:r[`snapshot_id] where not r`digest_ok;
        '"snapshot: the digest of ",(", " sv string distinct bad)," does not match its content - the store was changed outside the interface"];
    .qetl.snapshot.verified::distinct .qetl.snapshot.verified,ids;
    (.qetl.snapshot.schema)#r}

/ One snapshot, digest checked: require_many for a single id.
/ @param id the snapshot id
/ @return a one-row table of the snapshot
/ @throws error when the id is null, not recorded, or its digest does not match its content
/ @eg .qetl.snapshot.require .qetl.snapshot.record[`markout;enlist[`a]!enlist 1;"example"]
.qetl.snapshot.require:{[id] .qetl.snapshot.require_many enlist id}

/ A snapshot's content.
/ @param id the snapshot id
/ @return the content dictionary
/ @throws error as require does
/ @eg .qetl.snapshot.content_of .qetl.snapshot.record[`markout;enlist[`a]!enlist 1;"example"]
.qetl.snapshot.content_of:{[id] .qetl.snapshot.decode first (.qetl.snapshot.require id)`content}

/ Refuse ids that are not durably recorded: the publish path's guard.
/ .
/ A result must not become visible before the snapshot that explains it is on disk.
/ An id this process has already verified is trusted (nothing removes a snapshot);
/ any other is looked up in the file, digest checked.
/ @param ids the snapshot ids the rows about to be published name
/ @return the distinct ids
/ @throws error naming an id that is null, not recorded, or whose digest fails
/ @eg .qetl.snapshot.require_pinned `symbol$()  ->  `symbol$()
.qetl.snapshot.require_pinned:{[ids]
    ids:distinct ids;
    if[any null ids; '"snapshot: a result row names no configuration (null config_id)"];
    if[count todo:ids where not ids in .qetl.snapshot.verified; .qetl.snapshot.require_many todo];
    ids}

/ Every snapshot, oldest first, without its content - what a listing shows.
/ .
/ `datasets` is the datasets a snapshot's content says it configures (its `datasets`
/ field, empty when it names none), so a reader can ask "the snapshots of fx_markout"
/ without knowing what a kind's content looks like.
/ @param kind a kind to keep, or ` for all
/ @return snapshot_id, kind, datasets, digest, created_at, actor, reason, digest_ok
/ @throws error when the file is not a snapshot table
/ @eg count .qetl.snapshot.list[`]
.qetl.snapshot.list:{[kind]
    a:.qetl.snapshot.audit[];
    a:`created_at xasc a;
    k:kind;
    a:$[null k; a; select from a where kind=k];
    a:update datasets:{[b] c:.qetl.snapshot.decode b; $[`datasets in key c; `symbol$c`datasets; `symbol$()]} each content from a;
    `snapshot_id`kind`datasets`digest`created_at`actor`reason`digest_ok#a}

/ A snapshot as a reader shows it: one row per recorded field, then one per parameter.
/ .
/ Digest checked, so a snapshot whose content no longer matches its digest is an
/ error here rather than a display of values nobody can vouch for.
/ @param id the snapshot id
/ @return field, text: snapshot_id, kind, digest, created_at, actor, reason, then each content key
/ @throws error when the id is not recorded or its digest does not match its content
/ @eg count .qetl.snapshot.show .qetl.snapshot.record[`markout;enlist[`a]!enlist 1;"example"]
.qetl.snapshot.show:{[id]
    s:.qetl.snapshot.require id;
    own:`snapshot_id`kind`digest`created_at`actor`reason!first each (s`snapshot_id;s`kind;s`digest;s`created_at;s`actor;s`reason);
    all_:own,.qetl.snapshot.decode first s`content;
    ([] field:key all_; text:{$[10h=type x; x; -11h=type x; string x; .qf.util.full x]} each value all_)}

\d .
