/ source_coercion.q - the shared type-coercion step every source's rows pass through.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

/ ------------------------------------------------------------- COERCION

/ The coercion function for each declared q type character.
/ .
/ Declared here rather than left to each adapter, because the coercion question was
/ "is there ONE shared coercion layer" and the answer is only true if every
/ source reaches it by default. A source that casts its own text is a source
/ that can get the decimal comma or the date-only timestamp wrong privately.
/ .
/ `p` deliberately maps to to_timestamp, which REFUSES a date-only value
/ rather than widening it to midnight. A source whose column really is a
/ date, and for which midnight is correct, has to say so by coercing with
/ .qetl.coerce.to_date_as_midnight explicitly - which is greppable, unlike an
/ accident of q's casting rules.
/ A KNOWN LIMITATION, recorded rather than shipped silently: this maps every
/ `s` column to to_symbol, which UPPER-CASES. That is right for currency
/ pairs, which is this library's convention throughout - but it is a guess
/ for enum-like columns. A text source returning "buy" yields `BUY, while
/ demo_deals' own fixture uses `buy, so the two paths disagree on case for
/ that one column.
/ .
/ It does not bite today: the fixture returns typed data, so `coerce` is
/ never applied to it. It WOULD bite the first time a real text source lands
/ a side or venue column, and the fix is a per-field coercer override in the
/ declaration rather than a cleverer default - there is no case rule that is
/ right for both `EURUSD and `buy. Left undone deliberately because
/ inventing the override mechanism before a source needs it would be
/ guessing at its shape.
coercers:(!). flip (
    ("f";.qetl.coerce.to_float);
    ("j";.qetl.coerce.to_long);
    ("p";.qetl.coerce.to_timestamp);
    ("s";.qetl.coerce.to_symbol))

/ Coerce a table of TEXT columns into the declared types.
/ .
/ For a source that returns text - which is what coercion is about - this is the
/ step between fetch and validate. Returns the coerced table plus a per-
/ column failure count, so the worker can decide: a few bad rows in a
/ million might be tolerable and worth logging, while a column that failed
/ entirely means the format changed and the window must not be published.
/ That judgement is the worker's, not this layer's.
/ @param source a registered source name
/ @param tbl a table whose declared columns hold text
/ @return dict of `table (coerced) and `failures (field -> count)
/ @throws error when a declared type has no coercer
coerce:{[source;tbl]
    decl:def[source];
    present:column_names[tbl];
    columns:decl[`columns] where decl[`columns] in present;
    chars:(decl`types) (decl`columns)?columns;
    unknown:distinct chars where not chars in key coercers;
    if[count unknown;
        '"coerce: no coercer for declared type(s) \"",unknown,"\" in ",string[source],
         " - add one to .qetl.source.coercers deliberately rather than casting privately"];
    results:{[tb;f;c] .qetl.coerce.coerce_column[coercers c;tb f]}[tbl;;] .' flip (columns;chars);
    coerced:tbl;
    coerced:{[tb;f;r] @[tb;f;:;r`values]}/[coerced;columns;results];
    failures:columns!results[;`failed];
    .[{.qetl.log.dbg[x;y;z]};(source;"coerced";`rows`columns`failures!(count tbl;columns;failures));::];
    `table`failures!(coerced;failures)}

\d .
