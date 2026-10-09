/ source_transports.q - how a source is reached: the transport registry and its rows.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

/ ------------------------------------------------------------- TRANSPORTS
/ .
/ How a source is reached - the one OPTIONAL declaration - and everything that
/ differs by it, in ONE place (#616). Each transport is one row: how to open a
/ credential into a handle, how to close it, how to read a table's metadata,
/ and the words an operator and the scaffold need. A worker connecting, a
/ worker cleaning up, validate_live and the credential hint all DISPATCH
/ through the row; none of them branches on a transport's name, so a missed
/ branch can no longer fall through to ipc in silence. Adding `local` touched
/ fourteen files and missed one of those branches (#615); adding a transport
/ now is its implementation, one register_transport, a contract-surface
/ refresh and tests.
/ .
/ A KEYED TABLE declared with its columns, not a dictionary of dictionaries -
/ that collapses into a table on the first entry (#512).
/ .
/ THE CONTRACTS, the same for every transport:
/ .
/   open[credential]       a string in, a handle out - an ipc handle, an ODBC
/                          handle, or for `local` the HDB root as a file
/                          symbol. Throws when the credential cannot be opened.
/   close[handle]          releases it; (::) - a no-op for a local root.
/   metadata[handle;table] the table's columns as ([] c:symbols; t:chars), one
/                          row per column, `t` the q type character. One shape
/                          however it is read: a q process answers with its
/                          own meta, an HDB from its files, an ODBC database
/                          from an empty SELECT through the driver.
/   expects                what the credential IS, for an operator.
/   example                a credential of that shape. A source's own
/                          credential_example declaration wins over it.
/   query_note             how a source's query reaches it, for the scaffold.
transport:([name:`symbol$()] open:(); close:(); metadata:(); expects:(); example:(); query_note:())

/ The fields a transport registers, and their kinds.
transport_fields:`open`close`metadata`expects`example`query_note
transport_functions:`open`close`metadata

/ Register a transport, or refuse naming what is wrong.
/ @param name the transport, as a symbol
/ @param decl a dict of every one of transport_fields
/ @return name
/ @throws error naming a missing or unknown field, a non-function operation,
/   or a non-string description
register_transport:{[name;decl]
    who:"register_transport: ",string name;
    if[not -11h=type name; '"register_transport: a transport's name is a symbol"];
    if[not 99h=type decl; 'who,": the declaration must be a dictionary of ",", " sv string transport_fields];
    missing:transport_fields except key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    unknown:(key decl) except transport_fields;
    if[count unknown; 'who," declares ",(", " sv string unknown),", which a transport does not take"];
    notfn:transport_functions where not {(type x) within 100 112h} each decl transport_functions;
    if[count notfn; 'who,": ",(", " sv string notfn)," must be function(s)"];
    words:transport_fields except transport_functions;
    notstr:words where not 10h=type each decl words;
    if[count notstr; 'who,": ",(", " sv string notstr)," must be string(s)"];
    `.qetl.source.transport upsert (name,decl transport_fields);
    name}

/ Every registered transport's name.
/ @return a symbol vector
/ @eg .qetl.source.transports[]  ->  `ipc`odbc`local`mock
transports:{[] (key transport)`name}

/ A transport's row, or a refusal naming it and the ones there are - before
/ anything is opened or dispatched.
/ @param name the transport
/ @return dict of transport_fields
/ @throws error naming an unknown transport
transport_def:{[name]
    if[not name in transports[];
        '"transport ",string[name]," is not registered - the transports are ",", " sv string transports[]];
    transport name}

/ A source's transport row.
/ @param source a registered source
/ @return dict of transport_fields
for_source:{[source] transport_def (def[source])`transport}

register_transport[`ipc;transport_fields!(
    {[cred] hopen (hsym `$":",cred;5000j)};
    {[h] @[hclose;h;::]; (::)};
    {[h;table_name] select c, t from 0!h({0!meta x};table_name)};
    "host:port, or host:port:user:password";
    "localhost:5010";
    "/ Parameterised, NEVER concatenated (src/etl/core/source_contract.q refuses a string).\n/ The bounds are arguments to a functional select evaluated on the remote\n/ side, so no caller value is ever spliced into query text. Send it with\n/ .qetl.source.ipc[h;{[from_ts;to_ts] select ...};range_from;range_to], not\n/ h(...) directly, so `uqs backfill --trace` shows the query.")];

register_transport[`odbc;transport_fields!(
    {[cred] .qetl.io.odbc.open cred};
    {[h] .qetl.io.odbc.close h};
    / The table name is a declaration symbol, as in .qetl.io.odbc.window_query;
    / WHERE 1=0 asks the driver for the columns and types and no rows.
    {[h;table_name] select c, t from 0!meta .qetl.io.odbc.run_sql[h;"SELECT * FROM ",string[table_name]," WHERE 1=0"]};
    "an ODBC connection string";
    "DRIVER=<driver>;<driver-specific settings>";
    "/ `h` is an ODBC handle from .qetl.io.odbc.open. Build the SELECT with every\n/ bound through .qetl.io.odbc.literal - never string concatenation of a raw\n/ value - run it with .qetl.io.odbc.run_sql, and return the declared columns\n/ and types (src/etl/sources/duckdb_deals.q's sql_for and adapt).")];

register_transport[`local;transport_fields!(
    {[cred] .qetl.source.local_root cred};
    {[h] (::)};
    {[h;table_name] .qetl.source.local_meta[h;table_name]};
    "the path of an HDB directory on this machine";
    "/path/to/hdb";
    "/ `h` is the HDB directory, read from its files with no process in between.\n/ Send the query with .qetl.source.local[h;{[read;from_ts;to_ts] ...};range_from;range_to]:\n/ read[`table;from_ts;to_ts] returns the whole date partitions the window\n/ touches, symbols decoded against the HDB's own sym file - filter the rows\n/ to [from_ts;to_ts) yourself. read[(`table;`col1`col2);from_ts;to_ts] reads\n/ only those columns' files.")];

/ A source with nothing behind it: the credential is an integer seed, and the
/ query GENERATES the window's rows from it. For exercising a worker - a
/ backfill over any range, its coverage and reactions - with no data stored
/ anywhere. sidecars/mockups/mock_trades.q is one.
register_transport[`mock;transport_fields!(
    {[cred] .qetl.source.mock_seed cred};
    {[h] (::)};
    {[h;table_name] .qetl.source.mock_meta table_name};
    "an integer seed: the same seed and window always give the same rows";
    "42";
    "/ `h` is the seed: the credential, opened as a long - there is nothing to\n/ connect to. Generate the window's rows from it and the bounds,\n/ deterministically: the same seed and window must give the same rows\n/ (sidecars/mockups/mock_trades.q).")];

default_transport:`ipc

/ The optional declarations, and what a source that omits one stores.
/ credential_example's empty string is what .qetl.source.credential_example
/ reads as "not declared" - it was a null borrowed from whichever source
/ registered first, when this registry was a collapsed dictionary.
/ supporting's empty dict is a source with one input, as every source was
/ before #617 - see SUPPORTING INPUTS below.
optional_declarations:`transport`credential_example`supporting`raw!(default_transport;"";()!();()!())

\d .
