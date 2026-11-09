/ source_registry.q - the source registry: supporting and raw inputs, define, def.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .

/ ---------------------------------------------------- SUPPORTING INPUTS
/ .
/ A source can hand a worker more than one table (#617). A markout needs the
/ window's fills AND the quotes they are marked against; before this, the
/ only place to join them was the source's query, where the transform gates
/ never saw the scoring and the fixture path skipped it.
/ .
/ `supporting` names the extra inputs, each with its CONTRACT - an empty
/ table of the columns and types it must carry - keyed by the name the
/ transform reads it under, which is also the table name a live check reads
/ metadata from:
/ .
/   supporting:enlist[`quote]!enlist ([] time:`timestamp$(); sym:`symbol$(); bid:`float$(); ask:`float$())
/ .
/ With it, query and fixture return a DICTIONARY: the source's own
/ table_name -> the window's rows, plus each supporting name -> its rows.
/ The roles are not symmetric, and the asymmetry is the point:
/ .
/   - the PRIMARY input (table_name, contract `columns`/`types`) owns the
/     window. Only it is cut to [range_from;range_to) on time_column, only
/     its rows are counted, and a window whose primary is empty publishes
/     nothing whatever the supporting inputs hold.
/   - a SUPPORTING input is context. Its rows may lie outside the window -
/     the quote a fill is priced against can predate the window and the one
/     at its last horizon can follow it - so it is validated, never windowed.
/ .
/ A zoned source cannot declare supporting inputs: only the primary's
/ time_column is converted to UTC, so a supporting table would be read in
/ the wrong clock with nothing to say so.

/ ------------------------------------------------------------ RAW INPUTS
/ .
/ `columns`/`types` (and each supporting contract) describe what the adapter
/ RETURNS. For an identity adapter that is also what the physical table holds,
/ and the live check reads the table's metadata against it. An adapter that
/ renames, casts or derives - reading CREATED_AT and a JSON payload, returning
/ source_time, sym and typed prices - returns columns no physical table has,
/ so that check refused a valid adapter before its query ran.
/ .
/ `raw` declares the physical side separately: physical table name -> an empty
/ table of the columns and types the adapter READS from it (a general column,
/ type " ", accepts any type - a payload whose type depends on the driver):
/ .
/   raw:enlist[`EVENTS]!enlist ([] CREATED_AT:`timestamp$(); PAYLOAD:())
/ .
/ With `raw`, the live check holds each physical table to its raw contract,
/ and the adapted rows to columns/types - two checks, two diagnostics. Without
/ it the adapter is an identity one and nothing changes. Names and casing are
/ the physical source's own: they are the site's, not this framework's.

/ name -> declaration, as a KEYED TABLE declared with its columns (#512).
/ .
/ A dictionary of dictionaries collapses into a table on its first entry, and
/ then a narrower declaration silently gains a null for the key it lacked: it
/ is how half the tree's sources came to carry a null credential_example. On
/ PeachQ adding the second row throws. Declared, every column has its type or
/ is general, and every optional one its default, before any source arrives.
/ .
/ `source` stays a column beside the `name` key, so .qetl.source.def returns
/ the declaration it always did.
.qetl.source.sources:([name:`symbol$()] source:`symbol$(); table_name:`symbol$();
    time_column:`symbol$(); row_key:(); columns:(); types:(); query:(); fixture:();
    tz:`symbol$(); transport:`symbol$(); credential_example:(); supporting:(); raw:(); complete_through:(); ready:(); reference_read:(); batch_read:())

/ ------------------------------------------------------- COMPLETE THROUGH
/ .
/ A coverage claim is permanent, and the framework makes it on one condition:
/ the window was fetched and published without error (#1187). That is true
/ only of CLOSED data. A source over data still growing - a recorder's open
/ file, a live process's last few seconds - can return a successful, PARTIAL
/ window, and coverage would then record it as whole and never plan it again.
/ .
/ `complete_through:{[h] -> timestamp}` is the source saying how far it can
/ vouch: every row stamped before the returned UTC instant is present. Planning
/ clips the range to it (see .qetl.source.clip_to_complete), so the clipped
/ remainder stays uncovered and the next run picks it up. It is EXCLUSIVE, like
/ every window end. A null answer means "nothing is complete yet", not
/ "unbounded"; unbounded is 0Wp, the default. The fixture path has no source
/ and is closed data, so it is never clipped. A settle lag (a per-worker
/ allowance for late rows below the bound) is deliberately not built: add it
/ when a source shows rows arriving behind its own bound.

/ -------------------------------------------------------------- READINESS
/ .
/ A coverage claim is permanent, so a window must not be claimed on inputs it
/ could not yet read (#1927). A source with SUPPORTING inputs (reference data a
/ transform scores the primary against) can fetch a window whose primary rows
/ are all there and whose reference rows have not landed. Scored anyway, every
/ row comes out unavailable, coverage records the window as done at this
/ version, and nothing ever plans it again.
/ .
/ `ready:{[batch;range_from;range_to] -> unmet}` is the source saying what the
/ window cannot be scored without. `batch` is what fetch returned (a dict of
/ tables when the source has supporting inputs), the bounds are the window's.
/ It returns a list of strings, one per unmet requirement, and () when ready.
/ A window with unmet requirements stops after fetch and before publish: no
/ rows, no coverage, a failed window (so the run reports partial) that names
/ what was missing, and the next run plans it again with no new mechanism.
/ .
/ Readiness is NOT the same as an empty answer. A window that has no primary
/ rows is ready (there is nothing to score), and a window whose reference data
/ has no entry for one leg is ready (that leg is a legitimate unavailable row):
/ what is unready is a window whose reference data is ABSENT. Where the
/ reference data is merely not yet as far along as the window needs,
/ complete_through is the tool, because it clips the plan rather than failing
/ windows. The default hook is ready for everything.

/ ------------------------------------------------------- REFERENCE READ
/ .
/ A source that hands a worker supporting inputs read as REFERENCE - the books an
/ execution is marked against - decides how much of them to read. Left to its
/ query that is three numbers written as literals: how many days either side of
/ the window the date partitions are widened (the window is on a source clock, the
/ HDB is partitioned on the plant's), how far back the last reference before the
/ window is looked for, and which symbols are read at all. A literal is a number
/ nobody can ask the source about, and one a second source copies.
/ .
/ `reference_read` declares them, so the contract validates them with the rest:
/ .
/   date_skew     whole days, 0 or more, the partition constraint is widened by on
/                 each side of the window
/   lookback_cap  a positive timespan, how far back to look when the reference
/                 has no age limit (max_age 0Wn); see reference_lookback
/   symbols       a lambda taking (spec;rows) - the job's spec and the window's
/                 primary rows - and returning the symbols to read. The ONE place
/                 a symbol the reference needs is added (a conversion pair, #1931)
/ .
/ Every key is required once reference_read is declared: a half-declared read would
/ leave the missing number to a default nobody chose. A source that declares none
/ reads as it always did; its query owns the decision.

/ How far back a reference read looks before its window.
/ .
/ A reference older than max_age is not a reference, so with a finite max_age the
/ look-back ends where no reference can still count: max_age plus `horizon_lookback`,
/ the furthest an execution is scored BEFORE its own time (the most negative
/ horizon, 0 when none is). With max_age 0Wn ("as of however old") nothing ends it,
/ so the declared cap does.
/ @param source a registered source name that declares reference_read
/ @param max_age the oldest a reference may be and still count, 0Wn for no limit
/ @param horizon_lookback how far before an execution's time it is scored, 0D or more
/ @return a timespan: max_age + horizon_lookback, or lookback_cap for max_age 0Wn
/ @throws error when the source declares no reference_read
/ @eg .qetl.source.reference_lookback[`hdb_fx_markouts;0D00:00:05;0D00:00:30]  ->  0D00:00:35
.qetl.source.reference_lookback:{[source;max_age;horizon_lookback]
    rr:.qetl.source.reference_read[source];
    $[max_age=0Wn; rr`lookback_cap; max_age+horizon_lookback]}

/ A source's declared reference read, or a refusal naming it.
/ @param source a registered source name
/ @return the dict date_skew, lookback_cap, symbols
/ @throws error when the source declares no reference_read
/ @eg key .qetl.source.reference_read `hdb_fx_markouts  ->  `date_skew`lookback_cap`symbols
.qetl.source.reference_read:{[source]
    rr:(.qetl.source.def[source])`reference_read;
    if[not 99h=type rr;
        '"reference_read: ",string[source]," declares no reference_read - its query owns what it reads"];
    rr}

/ Private: refuse a malformed reference_read, naming every fault at once.
/ @private
.qetl.source.check_reference_read:{[source;rr]
    who:"define: ",string[source],"'s reference_read ";
    if[not 99h=type rr; 'who,"must be a dict of date_skew, lookback_cap and symbols"];
    .qetl.declared.refuse_unknown[who;"a reference_read";rr;`date_skew`lookback_cap`symbols];
    miss:`date_skew`lookback_cap`symbols except key rr;
    if[count miss; 'who,"is missing ",", " sv string miss];
    bad:();
    if[not (-7h=type rr`date_skew) and rr[`date_skew]>=0; bad,:enlist "date_skew (a whole number of days, 0 or more)"];
    if[not (-16h=type rr`lookback_cap) and (rr[`lookback_cap]>0D) and rr[`lookback_cap]<0Wn; bad,:enlist "lookback_cap (a positive, finite timespan)"];
    sy:rr`symbols;
    if[not (.qetl.declared.callable sy) and $[.qetl.declared.lambda sy; 2=count (value sy) 1; 1b]; bad,:enlist "symbols (a lambda taking spec and rows)"];
    if[count bad; 'who,"has malformed ",", " sv bad];
    }

/ Register an external source table.
/ .
/ Registration VALIDATES immediately, unlike .qetl.job.bounded.state.register which
/ deliberately defers. The asymmetry is deliberate and worth stating: a
/ bounded worker's methods appear as its file loads, so validating early
/ would force declaration order. A source declaration is a single literal
/ with no such ordering problem, so the earliest possible failure is the best
/ one.
/ @param source a name for this source, e.g. `deals_analogue
/ @param decl a dict carrying every name in required_declarations:
/   table   - the external table name, as a symbol
/   time_column - the column the window is taken on, as a symbol
/   row_key - the column(s) identifying a row uniquely, as a symbol vector
/   columns  - the columns this adapter RETURNS, as a symbol vector - also
/             what the physical table holds, unless `raw` says otherwise
/   types   - the expected q type characters, one per field, as a string
/   query   - a parameterised lambda taking (handle;range_from;range_to)
/   fixture - a niladic lambda returning a synthetic table of the same shape
/   tz - the zone the source's time_column is expressed in, as a
/     symbol: `UTC, or a tz-database name such as `$"Europe/London"
/ and optionally:
/   transport - `ipc (the default), `odbc or `local, see `transports`
/   credential_example - what this source's credential LOOKS like, as a
/     string, for the warning a worker logs when none is set. Optional
/     because a generic one per transport is better than nothing; declared
/     because per transport is not good enough. Every ODBC source in this
/     tree is a DuckDB file, whose "connection string" is a path and holds
/     no secret at all, and the generic ODBC example asked all three of them
/     for a SERVER, PORT, UID and PWD that DuckDB has no concept of.
/   raw - physical table name -> an empty table of the columns and types the
/     adapter reads from it, for an adapter whose output is not its input
/     (see RAW INPUTS)
/   complete_through - a lambda taking the source's handle and returning the
/     UTC instant (a timestamp) through which the source holds EVERY row, for
/     a source over data that is still growing (see COMPLETE THROUGH). The
/     default, {[h] 0Wp}, is closed data: complete forever.
/   ready - a lambda {[batch;range_from;range_to]} returning the window's unmet
/     requirements as a list of strings, () when it can be scored (see READINESS).
/     The default is ready for everything.
/   batch_read - window, needs, read, keys and sort: how the source reads its
/     supporting inputs a batch at a time, for a worker that batches (see
/     source_batch_read.q, #1921)
/   reference_read - date_skew, lookback_cap and symbols, how much of its
/     supporting inputs the source reads as reference (see REFERENCE READ). Needs
/     supporting inputs to describe.
/ @return the source name
/ @throws error naming every missing or malformed declaration at once
.qetl.source.define:{[source;decl]
    / Where rows go is the WORKER's dataset (#769), so a source naming a
    / destination was a second answer to that question, shown and never used
    / (#1092).
    if[`target in key decl;
        '"define: ",string[source]," declares target, which is gone (#1092) - rows go to the worker's dataset; drop it"];
    .qetl.declared.refuse_unknown["define: ",string source;"a source";decl;.qetl.source.required_declarations,key .qetl.source.optional_declarations];
    missing:.qetl.source.required_declarations where not .qetl.source.required_declarations in key decl;
    if[count missing;
        '"define: ",string[source]," is missing declaration(s): ",", " sv string missing];
    if[not 11h=abs type decl`columns;
        '"define: ",string[source],"'s columns must be a symbol vector"];
    if[not 10h=abs type decl`types;
        '"define: ",string[source],"'s types must be a string of q type characters, one per field"];
    if[(count decl`columns)<>count decl`types;
        '"define: ",string[source]," declares ",string[count decl`columns],
         " field(s) but ",string[count decl`types]," type(s) - the source contract requires a type per required field"];
    if[not .qetl.declared.callable decl`query;
        '"define: ",string[source],"'s query must be a function (parameterised, never concatenated)"];
    if[not .qetl.declared.callable decl`fixture;
        '"define: ",string[source],"'s fixture must be a niladic function (the path must be exercisable with no driver)"];
    .qetl.source.check_arity[source;decl];
    if[not -11h=type decl`tz;
        '"define: ",string[source],"'s tz must be a single symbol - `UTC, or a tz-database name such as `$\"Europe/London\""];
    / The window is taken on time_column, so time_column must be a field this
    / adapter actually READS. Without this check a typo registers happily and
    / surfaces two layers down: validate never checks the column (it is not
    / in `columns`), the live query filters on something the declaration never
    / described, and only window_fixture notices - at fetch time, mid-run.
    / row_key: declared and VALIDATED, deliberately not yet used.
    / .
    / The mechanism lands ahead of the semantics on purpose. docs/restatement-
    / design.md sets out why: answering "rows can be superseded in
    / place" changes what is_covered MEANS, and sixteen files rest on the
    / current meaning - so the shape of that change needs agreeing before
    / any of it is built. A declared key is the one piece that is a
    / prerequisite either way and has zero blast radius on its own.
    / .
    / It also earns its place independently of restatements. A failed
    / window was
    / answered "leave the published rows, record no coverage, re-run redoes
    / the window", and that duplicates rows unless the publish path can
    / dedupe - the framework promises retry-SAFE publication, which is explicitly
    / weaker than exactly-once. A row key is exactly what makes that dedupe
    / possible, so this is worth having even if supersession is deferred.
    / `11h=abs type`, not `-11h=abs type`: abs is always positive, so the
    / latter can never be true and rejected every row_key including a
    / correct one. 11h=abs accepts both a symbol atom (-11h) and a symbol
    / vector (11h), which is the point - a single-column key should not have
    / to be enlisted at the call site.
    if[not 11h=abs type decl`row_key;
        '"define: ",string[source],"'s row_key must be a symbol or symbol vector naming the column(s) that identify a row uniquely"];
    key_cols:(),decl`row_key;
    key_absent:key_cols where not key_cols in decl`columns;
    if[count key_absent;
        '"define: ",string[source],"'s row_key names ",(", " sv string key_absent),
         " which is not among its declared columns - a key this contract cannot see cannot identify a row"];

    if[not (decl`time_column) in decl`columns;
        '"define: ",string[source],"'s time_column ",string[decl`time_column],
         " is not one of its declared columns (",(", " sv string decl`columns),
         ") - the window is taken on that column, so it must be one the contract describes"];
    / Enforced rather than hoped for: the window column must be a
    / TIMESTAMP. q's datetime (`z`) is a float count of days, so z->p is a
    / rounding that loses sub-second precision silently - 999 of 1000
    / nanosecond-spaced instants do not survive it, and whole seconds do,
    / which is why it passes every hand-check. A window cut on such a column
    / produces plausible numbers and misplaced rows rather than an error.
    time_idx:(decl`columns)?decl`time_column;
    time_char:(decl`types)[time_idx];
    if[not "p"=time_char;
        / Short by necessity: q truncates a thrown string at 255 bytes, so the
        / long form lives in the comment above rather than in the message.
        '"define: ",string[source],"'s time_column ",string[decl`time_column],
         " is type \"",time_char,"\", not \"p\" - the window column must be a timestamp; a datetime rounds sub-second values silently"];
    sup:$[`supporting in key decl; decl`supporting; ()!()];
    if[not 99h=type sup;
        '"define: ",string[source],"'s supporting must be a dict of input name -> empty table, that input's contract"];
    if[count sup;
        if[not 11h=type key sup;
            '"define: ",string[source],"'s supporting must be keyed by input name, as symbols"];
        if[not all 98h=type each value sup;
            '"define: ",string[source],"'s supporting inputs must each be a table - an empty one, its contract"];
        if[(decl`table_name) in key sup;
            '"define: ",string[source],"'s supporting names ",string[decl`table_name],
             ", its own table_name - the primary input is described by columns and types"];
        if[not `UTC~decl`tz;
            '"define: ",string[source]," is ",string[decl`tz],
             " and declares supporting inputs - only the primary is converted to UTC, so they need tz `UTC"]];
    raw:$[`raw in key decl; decl`raw; ()!()];
    if[not 99h=type raw;
        '"define: ",string[source],"'s raw must be a dict of physical table name -> empty table, what the adapter reads"];
    if[count raw;
        if[not 11h=type key raw;
            '"define: ",string[source],"'s raw must be keyed by physical table name, as symbols"];
        if[not all 98h=type each value raw;
            '"define: ",string[source],"'s raw inputs must each be a table - an empty one, the columns the adapter reads"]];
    if[`complete_through in key decl;
        ct:decl`complete_through;
        if[not .qetl.declared.callable ct;
            '"define: ",string[source],"'s complete_through must be a function taking the source's handle and returning a timestamp"];
        / `{[] ...}` reports one parameter, a null symbol, so a niladic lambda
        / is counted as one unless that is looked for. Only a lambda has a
        / parameter list to read (.qetl.declared).
        params:$[.qetl.declared.lambda ct; (value ct) 1; enlist `h];
        if[(not 1=count params) or null first params;
            '"define: ",string[source],"'s complete_through must take exactly one argument, the source's handle"]];
    if[`ready in key decl;
        rd:decl`ready;
        if[not .qetl.declared.callable rd;
            '"define: ",string[source],"'s ready must be a function taking the fetched batch and the window's bounds and returning its unmet requirements"];
        if[(.qetl.declared.lambda rd) and 3<>count (value rd) 1;
            '"define: ",string[source],"'s ready must take exactly three arguments, the batch and the window's range_from and range_to"]];
    / A declaration read back from the registry carries the default, (): not declared.
    if[(`reference_read in key decl) and not ()~decl`reference_read;
        if[0=count sup;
            '"define: ",string[source]," declares reference_read but no supporting inputs - there is no reference to read"];
        .qetl.source.check_reference_read[source;decl`reference_read]];
    / A declaration read back from the registry carries the default, (): not declared (see source_batch_read.q).
    if[(`batch_read in key decl) and not ()~decl`batch_read;
        .qetl.source.check_batch_read[source;decl`batch_read;decl]];
    tr:$[`transport in key decl; decl`transport; .qetl.source.default_transport];
    if[not tr in .qetl.source.transports[];
        '"define: ",string[source],"'s transport must be one of ",(", " sv string .qetl.source.transports[])];
    / Every declaration is stored with every column: a key no column holds
    / would have nowhere to go, so it is refused by name rather than dropped.
    decl[`transport]:tr;
    / Store row_key NORMALISED to a vector, always.
    / .
    / Two reasons, and the second is not obvious. Semantically it means no
    / consumer has to decide whether a single-column key needs enlisting.
    / Mechanically it is required: `sources[source]:decl` throws `type` when
    / one stored declaration's row_key is an atom and another's is a vector,
    / because the dict's value list has already settled on a shape. Storing
    / one shape keeps every declaration mutually assignable.
    / columns and types to vectors too: one field's atom would otherwise type
    / a general column on the first registration and refuse the next one.
    decl:.qetl.source.optional_declarations,@[decl;`row_key`columns`types;:;(key_cols;(),decl`columns;(),decl`types)];
    `.qetl.source.sources upsert (source,decl cols value .qetl.source.sources);
    .[{.qetl.log.ch.dbg[x;y;z]};(`config;"source registered";
        `source`columns`time_column`tz`transport`row_key!(source;decl`columns;decl`time_column;decl`tz;tr;key_cols));::];
    source}

/ Private: refuse a query or fixture lambda of the wrong arity - now, rather
/ than at the first live window's bare 'rank (#2484). Only a lambda's parameter
/ list is readable. A bracketless `{...}` reports the implicit x it is called
/ with, (::), so it counts as niladic like `{[] ...}`. Apart from define,
/ whose locals are at q's limit once coverage instruments it.
/ @private
.qetl.source.check_arity:{[source;decl]
    if[(.qetl.declared.lambda decl`query) and 3<>count (value decl`query) 1;
        '"define: ",string[source],"'s query must take 3 arguments (h; range_from; range_to)"];
    if[not .qetl.declared.lambda decl`fixture; :()];
    params:(value decl`fixture) 1;
    implicit:((enlist `x)~params) and not "{["~2#string decl`fixture;
    if[not ((enlist `)~params) or implicit;
        '"define: ",string[source],"'s fixture must take no arguments, {[] ...}"];
    }

/ Every registered source's name.
/ .
/ The registry IS the list - there is no second declaration of which sources
/ exist, which is what makes the question bank true: adding a source is a file plus a
/ registration, with no core change.
/ @return a symbol vector, empty when nothing has registered yet
/ @eg .qetl.source.defined[]
.qetl.source.defined:{[] (key .qetl.source.sources)`name}

/ The inputs a source hands its worker, primary first: its table_name, then
/ each supporting input's name. One name for a source with no supporting
/ inputs.
/ @param source a registered source name
/ @return a symbol vector
/ @eg .qetl.source.input_names `demo_deals  ->  enlist `demo_deals
.qetl.source.input_names:{[source] d:.qetl.source.def[source]; (enlist d`table_name),key d`supporting}

/ The primary input of what a source returned: the window's own rows, which
/ are counted and which own the window. The batch itself for a source with
/ one input; its table_name entry for one with supporting inputs.
/ @param source a registered source name
/ @param fetched what the source's query or fixture returned
/ @return a table
.qetl.source.primary:{[source;fetched] d:.qetl.source.def[source]; $[count d`supporting; fetched d`table_name; fetched]}

/ The column(s) identifying a row uniquely, always as a vector.
/ .
/ Exported so a future dedupe or restatement path has one place to ask,
/ rather than each caller reaching into the declaration and deciding for
/ itself whether a single symbol needs enlisting.
/ Stored normalised by `define`, so this is already a vector - the `(),`
/ is belt-and-braces for a declaration written directly into `sources` by a
/ test rather than through register.
.qetl.source.row_key:{[source] (),(.qetl.source.def[source])`row_key}

/ One source's full declaration, or a refusal naming it.
/ .
/ The single read path every other module uses to reach a source's contract -
/ five files call it - so it throws rather than returning a null: a caller
/ that got an empty dict back would fail later, somewhere else, on a missing
/ key.
/ @param source the registered source's name, as a symbol
/ @return the declaration dict (source, table, time_column, row_key,
/   columns, types, query, fixture, tz, transport, credential_example,
/   supporting)
/ @throws error naming the source when it was never registered
/ @eg .qetl.source.def `demo_deals
.qetl.source.def:{[source]
    if[not source in .qetl.source.defined[];
        '"def: ",string[source]," is not a registered source - sources register centrally, so an unregistered source is a wiring bug rather than a lookup miss"];
    .qetl.source.sources source}

\d .
