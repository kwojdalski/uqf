/ source_validation.q - validating a table against a source's declared contract.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

/ ------------------------------------------------------------ VALIDATION

/ Private: the q type character for each column of a table.
/ .
/ `meta`'s `t` column is exactly this, and using it rather than `type each`
/ means an empty table validates the same way a populated one does - which
/ matters because a fixture may legitimately be empty, and a source may
/ legitimately return no rows for a window.
/ @private
type_chars:{[tbl] exec t from 0!meta tbl}

column_names:{[tbl] exec c from 0!meta tbl}

/ Validate a table against a source's declaration.
/ .
/ This is the single function both the fixture check and the live metadata
/ check go through, which is what makes "that same contract" true rather
/ than aspirational.
/ .
/ Extra columns are ALLOWED, declared columns are not optional. A source
/ growing a column is routine and breaking on it would make every upstream
/ addition an outage; a source LOSING a declared column, or changing its
/ type, is precisely the silent breakage worth failing on - a missing column
/ reads as a null in most q code rather than as an error.
/ A source with supporting inputs returns a dict of tables, and each is held
/ to its own contract - see SUPPORTING INPUTS.
/ @param source a registered source name
/ @param tbl a table to check, or a dict of input name -> table
/ @return 1b when the table satisfies the declaration
/ @throws error naming every missing field and every type mismatch at once
validate:{[source;tbl]
    decl:def[source];
    if[count decl`supporting; :validate_inputs[source;decl;tbl]];
    check_table[source;"";decl`columns;decl`types;tbl];
    .[{.qetl.log.dbg[x;y;z]};(source;"contract satisfied";`rows`columns!(count tbl;count decl`columns));::];
    1b}

/ Private: refuse `tbl` unless it carries `columns` with `types`, naming
/ every missing field and every type mismatch at once. `what` is "" for a
/ source's only input, or "'s input <name>" for one of several. A type of
/ " " - a general column in a supporting input's contract - accepts any.
/ @private
check_table:{[source;what;columns;types;tbl]
    present:column_names[tbl];
    chars:type_chars[tbl];
    missing:columns where not columns in present;
    / Report missing columns AND type mismatches together. Reporting only the
    / first class means fixing the columns, re-running, and only then
    / learning the types are wrong too.
    checkable:columns where columns in present;
    expected:types columns?checkable;
    actual:chars present?checkable;
    bad:not (expected=actual) or expected=" ";
    wrong:checkable where bad;
    if[count missing,wrong;
        '"validate: ",string[source],what," does not satisfy its contract",
         $[count missing; " - missing field(s): ",", " sv string missing; ""],
         $[count wrong;
            " - type mismatch on ",", " sv {x[0]," (expected ",x[1],", got ",x[2],")"} each
                flip (string wrong;enlist each expected where bad;enlist each actual where bad);
            ""]];
    }

/ Private: validate what a source with supporting inputs returned - a dict
/ holding exactly its inputs, each satisfying its own contract.
/ @private
validate_inputs:{[source;decl;given]
    names:(enlist decl`table_name),key decl`supporting;
    if[not 99h=type given;
        '"validate: ",string[source]," declares supporting input(s), so it must return a dict of ",
         (", " sv string names)," - got type ",string type given];
    if[not (asc key given)~asc names;
        '"validate: ",string[source]," must return input(s) ",(", " sv string names),
         " - got ",", " sv string key given];
    check_table[source;"'s input ",string decl`table_name;decl`columns;decl`types;given decl`table_name];
    {[source;given;nm;contract]
        check_table[source;"'s input ",string nm;cols contract;type_chars[contract];given nm]
      }[source;given]'[key decl`supporting;value decl`supporting];
    .[{.qetl.log.dbg[x;y;z]};(source;"contract satisfied";
        `rows`inputs!(count given decl`table_name;names));::];
    1b}

/ Validate a source's own fixture against the same contract as live data.
/ .
/ Runs in the deterministic suite, with no connection anywhere. A fixture
/ that does not satisfy the contract is a broken test double, and finding
/ that out from a failing worker test is a much longer path.
validate_fixture:{[source] validate[source;(def[source]`fixture)[]]}

/ Validate LIVE external metadata against what the adapter reads.
/ .
/ For an identity adapter that is its own contract - table_name against
/ columns/types, each supporting input against its contract - so the fixture
/ and the live table are held to the same thing. For one declaring `raw`, each
/ physical table against its raw contract instead: the output contract
/ describes rows the adapter makes, which no physical table holds (see RAW
/ INPUTS). Every table is checked, and the first one off is named.
/ .
/ Belongs to the `smoke` lane, not the deterministic suite: the
/ deterministic suite proves local behaviour, not that a configured external
/ service is reachable or compatible.
/ @param source a registered source name
/ @param h an open handle to the external source
/ @return 1b
/ @throws error naming the table, and every missing column or each column's
/   expected and actual type
validate_live:{[source;h]
    reader:(transport_def[def[source]`transport])[`metadata][h;];
    live_check_table[reader] .' live_contracts source;
    1b}

/ What validate_live reads, one row per physical table: (label for a
/ diagnostic; table name; columns; type characters).
/ @param source a registered source name
/ @return a list of 4-item lists
/ @eg count .qetl.source.live_contracts `demo_deals  ->  1
live_contracts:{[source]
    d:def[source];
    if[count d`raw;
        :{[nm;c] ("raw input ",string nm;nm;cols c;type_chars[c])}'[key d`raw;value d`raw]];
    (enlist (string d`table_name;d`table_name;d`columns;d`types)),
        {[nm;c] ("supporting input ",string nm;nm;cols c;type_chars[c])}'[key d`supporting;value d`supporting]}

/ Private: hold one physical table's metadata to (want;want_t). A type of
/ " " accepts any.
/ @private
live_check_table:{[reader;what;nm;want;want_t]
    m:@[reader;nm;{[nm;err] '"validate_live: cannot read metadata for ",string[nm]," (",err,")"}[nm;]];
    have:exec c from m;
    absent:want where not want in have;
    if[count absent;
        '"validate_live: ",what," is missing ",(", " sv string absent),
         " - the external schema has changed, or this declaration was always wrong"];
    have_t:(exec t from m) have?want;
    off:where not (want_t=have_t) or want_t=" ";
    if[count off;
        '"validate_live: ",what," type mismatch on ",", " sv
            {[c;e;a] string[c]," (expected ",e,", got ",a,")"}'[want off;enlist each want_t off;enlist each have_t off]];
    }

\d .
