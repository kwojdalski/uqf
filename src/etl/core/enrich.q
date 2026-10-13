/ enrich.q - the reference buffer behind a streaming job's as-of enrichment
/ (.qetl.enrich, #1223): the state half of "attach the prevailing quote/mid
/ to each event". The lookup itself is the pure .qasof.lookup, so a backfill
/ twin runs it unchanged over a table it read from the HDB.
/ .
/ A HELPER, NOT A JOB KIND. A job keeps one of these in a namespace global,
/ add[]s each reference batch to it and lookup[]s each event batch against it (not `asof`: that is a q keyword).
/ Promoting that to a kind (holding early events until the reference
/ catches up, late-reference amendment, replay) is a later step, once a third
/ job needs it; nothing here holds an event.
/ .
/ THE BUFFER IS A VALUE. new[] returns a state dict and add[] returns the next
/ one, so a test and a restart reason about it as data. The rows are kept
/ sorted by (keys, time), STABLY: of two rows at the same time the one added
/ later is the one that prevails.
/ .
/ BOUNDED. Per key, a row is kept while it is within `retention` of that
/ key's newest row, plus ONE anchor: the newest row older than that. The
/ anchor is what makes the buffer answer correctly for an event exactly at the
/ retention edge - the row prevailing there is older than the edge. So the
/ key's latest row is always kept (it is within retention of itself), and a
/ buffer never holds more than the retention window plus one row per key.

\d .qetl.enrich

/ A new, empty buffer.
/ @param spec dict: `keys (symbol list), `time (the reference time column),
/   `retention (a non-negative timespan), `schema (an empty table with the key
/   columns, the time column and every column that will be attached)
/ @return the buffer state
/ @throws error if a spec key is missing, the schema lacks a key or time
/   column, or the retention is not a non-negative timespan
/ @eg count .qetl.enrich.rows .qetl.enrich.new[`keys`time`retention`schema!(enlist `sym;`time;0D00:01:00;([] sym:`symbol$(); time:`timestamp$(); mid:`float$()))]  ->  0
new:{[spec]
    miss:`keys`time`retention`schema except key spec;
    if[count miss; '"qetl.enrich.new: spec is missing ",", " sv string miss];
    if[not -16h=type spec`retention; '"qetl.enrich.new: retention must be a timespan"];
    if[spec[`retention]<0D; '"qetl.enrich.new: retention must not be negative"];
    .qschema.require_cols[`qetl_enrich_new;`schema;spec`schema;(),spec[`keys],spec`time];
    `keys`time`retention`rows!((),spec`keys;spec`time;spec`retention;0#spec`schema)}

/ The rows a buffer holds, sorted by (keys, time).
/ @param state a buffer from new or add
/ @return the held rows
/ @eg .qetl.enrich.rows .qetl.enrich.new[`keys`time`retention`schema!(enlist `sym;`time;0D00:01:00;([] sym:`symbol$(); time:`timestamp$()))]  ->  ([] sym:`symbol$(); time:`timestamp$())
rows:{[state] state`rows}

/ Add reference rows, keeping the buffer sorted and bounded.
/ .
/ Rows may arrive in any order, and a row older than a key's held rows is
/ inserted where it belongs; one older than that key's retention edge is
/ simply trimmed again (it cannot prevail for any event the buffer still
/ answers correctly). Rows must carry every column of the buffer's schema;
/ extra columns are dropped.
/ @param state a buffer from new or add
/ @param refs reference rows
/ @return the next buffer state
/ @throws error naming every column of the schema the rows lack
/ @eg count .qetl.enrich.rows .qetl.enrich.add[.qetl.enrich.new[`keys`time`retention`schema!(enlist `sym;`time;0D00:00:10;([] sym:`symbol$(); time:`timestamp$(); mid:`float$()))];([] sym:3#`a; time:2026.10.01D10:00:00 + 0D00:00:01 * 0 5 30; mid:1 2 3f)]  ->  2
add:{[state;refs]
    held:cols state`rows;
    .qschema.require_cols[`qetl_enrich_add;`refs;refs;held];
    if[0=count refs; :state];
    ks:state`keys;
    combined:((ks,state`time) xasc (state`rows),held#refs);
    state,enlist[`rows]!enlist .qetl.enrich.bound[state;combined]}

/ Attach the prevailing reference row to each event, from the buffer.
/ .
/ A thin wrapper: the buffer supplies the keys and the reference time column,
/ and the answer is .qasof.lookup's over the held rows - the same function a
/ backfill calls on the full history, so the two agree for every event at or
/ after the retention edge of its key (older events may find their
/ prevailing row trimmed).
/ @param state a buffer from new or add
/ @param events the events: the buffer's key columns and event_time
/ @param spec dict: `event_time (events' time column), `attach (a dict new
/   name!reference column, or a symbol list), `tolerance (timespan, 0Nn for none)
/ @return events, then attached columns, staleness, status (see .qasof.lookup)
/ @throws error as .qasof.lookup does
/ @eg .qetl.enrich.lookup[.qetl.enrich.add[.qetl.enrich.new[`keys`time`retention`schema!(enlist `sym;`time;0D00:01:00;([] sym:`symbol$(); time:`timestamp$(); mid:`float$()))];([] sym:enlist `a; time:enlist 2026.10.01D10:00:00; mid:enlist 1f)];([] sym:enlist `a; time:enlist 2026.10.01D10:00:01);`event_time`attach`tolerance!(`time;enlist `mid;0D00:00:05)]  ->  ([] sym:enlist `a; time:enlist 2026.10.01D10:00:01; mid:enlist 1f; staleness:enlist 0D00:00:01; status:enlist `ok)
lookup:{[state;events;spec]
    .qasof.lookup[state`rows;events;(`keys`ref_time!(state`keys;state`time)),spec]}

/ Private: drop what is outside each key's retention window, keeping one
/ anchor row per key. Rows must already be sorted by (keys, time).
/ @param state the buffer, for its keys, time and retention
/ @param r the sorted rows
/ @return the trimmed rows
/ @private
bound:{[state;r]
    ks:state`keys;
    t:r state`time;
    newest:(max;t) fby ks#r;
    edge:newest-state`retention;
    old:where t<edge;
    if[0=count old; :r];
    o:update qetl_ix:old from (ks#r) old;
    anchors:exec qetl_ix from ?[o;();ks!ks;(enlist `qetl_ix)!enlist (last;`qetl_ix)];
    r asc (where t>=edge),anchors}

\d .
