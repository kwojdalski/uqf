// test_schema.q - tests for src/foundation/schema.q (.qschema). Load
// src/foundation/schema.q, tests/lib/qunit.q and tests/lib/testutil.q before
// this file.

\d .schematest

/ The passing case returns generic null rather than the table, so a caller
/ cannot accidentally use it as a filter and get a silently different table.
test_a_table_with_every_required_column_passes:{[t]
    tbl:([] sym:enlist `EURUSD; time:enlist 2026.01.01D00:00:00.000000000; mid:enlist 1.1);
    .qunit.assertEquals[.qschema.require_cols[`f;`quotes;tbl;`sym`time`mid];(::);"present columns return generic null"]};

/ The message is the whole point of #418: one wording, carrying the caller's
/ name, the table's role and every missing column - not just the first.
test_the_message_names_the_caller_the_table_and_every_missing_column:{[t]
    tbl:([] sym:enlist `EURUSD);
    .qunit.assertThrows[{.qschema.require_cols[`markout_at_horizons;`trades;x;`sym`time`side]};tbl;
        "markout_at_horizons: trades is missing required column(s) time, side";
        "one wording, caller and table named, both missing columns listed in order"]};

/ A single symbol is a legal req - several call sites build theirs from a
/ variable that is an atom when one column is asked for.
test_a_single_required_column_may_be_an_atom:{[t]
    .qunit.assertThrows[{.qschema.require_cols[`f;`t;x;`b]};([] a:1 2);
        "f: t is missing required column(s) b";"an atom req behaves as a one-element vector"];
    .qunit.assertEquals[.qschema.require_cols[`f;`t;([] a:1 2);`a];(::);"and passes when present"]};

/ Membership only, deliberately: the thirteen sites this replaced checked
/ membership and nothing else, so a wrong TYPE must still pass here.
test_a_present_column_of_the_wrong_type_passes:{[t]
    .qunit.assertEquals[.qschema.require_cols[`f;`t;([] sym:enlist "EURUSD");enlist `sym];(::);
        "a char column named sym satisfies a required `sym - this checks names, not types"]};

/ A keyed table's key columns count as present. measure passes an unkeyed
/ copy today, but nothing in the contract says a caller must.
test_a_keyed_tables_key_columns_count_as_present:{[t]
    .qunit.assertEquals[.qschema.require_cols[`f;`book;([sym:enlist `EURUSD] qty:enlist 1.0);`sym`qty];(::);
        "cols on a keyed table includes its key"]};

\d .
