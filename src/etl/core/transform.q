/ transform.q - the transform building block every ETL job is built from
/ (.qxf).
/ .
/ A job, whether a bounded backfill (.qbw) or a tickerplant subscriber
/ (scripts/torq_*_etl.q), is the same three things: SOURCE rows in, a
/ TRANSFORM, rows out to a SINK. The source and sink are effects - a query, a
/ subscription, a tickerplant publish, a coverage record - and cannot be
/ deterministic. The transform can, and this file is where it has to be.
/ .
/ WHAT A DECLARED TRANSFORM IS
/ .
/   inputs    name -> empty typed table. The exact tables the transform
/             reads, so a caller handing it a mistyped or widened table is
/             refused at the boundary rather than half-way through a join.
/   output    an empty typed table. The exact columns, IN ORDER, that come
/             out - order matters because .u.upd publishes columns
/             positionally.
/   fn        a function of the input tables, in declared order. With
/             `as_of` declared, the instant is appended as a last argument.
/   examples  input tables and the output table expected from them, written
/             by hand. At least one must carry rows.
/ .
/ WHY THE CLOCK IS AN ARGUMENT
/ .
/ A transform that reads .z.p is not a function of its inputs: the same rows
/ produce a different table on every call, so no expected table can be
/ written for it and no rerun can reproduce it. Declaring `as_of` makes the
/ instant an input like any other - the caller reads the clock once, and the
/ examples say which instant they were written for.
/ .
/ WHAT `verify` PROVES AND WHAT IT CANNOT
/ .
/ For every example: the output has the declared schema, it matches the
/ expected table, and a second call on the same inputs returns an identical
/ table. And, for free on every transform, all-empty inputs return an empty
/ table of the declared schema - the window with no rows is the case most
/ often broken and least often written down.
/ .
/ The repeat check catches a transform that stamps .z.p or draws a random
/ number into its output. It cannot prove purity: a transform that reads a
/ global passes it as long as the global does not change between the two
/ calls. q has no way to enforce that; the declaration's input list is the
/ statement of what a transform may read, and review holds it to that.
/ .
/ tests/q/test_transform.q verifies every registered transform on every run
/ of the suite, so a transform whose examples fail fails the build.

\d .qxf

/ name -> declaration.
registry:(`symbol$())!();

required_keys:`inputs`output`fn`examples

/ Absolute tolerance for float columns when comparing an output to its
/ expected table. Hand-written expected values are decimal literals, and a
/ product like 1.0842*149.82 does not land on the same binary value as the
/ literal a person types for it. 1e-9 is far below any price or P&L
/ precision this library reports, and far above the rounding noise.
float_tolerance:1e-9

/ ----------------------------------------------------------------- SCHEMA

/ Private: column -> meta type character.
col_types:{[tbl] exec c!t from meta tbl}

/ Private: the problems stopping `tbl` from matching a declared schema, as
/ strings - empty when it matches.
/ .
/ A declared type of " " (a general list, e.g. a column of float vectors
/ declared as ()) accepts any list type, because an empty general column has
/ no element type to declare. An empty ACTUAL column of type " " is accepted
/ for the same reason in reverse.
/ @param schema an empty typed table
/ @param tbl the table to check
/ @param ordered 1b when column order must match too (outputs)
/ @return list of strings, empty when tbl conforms
problems:{[schema;tbl;ordered]
    if[not 98h=type tbl;
        :enlist "expected an unkeyed table, got type ",string type tbl];
    want:col_types schema;
    got:col_types tbl;
    missing:(key want) except key got;
    extra:(key got) except key want;
    out:();
    if[count missing; out,:enlist "missing column(s) ",", " sv string missing];
    if[count extra; out,:enlist "unexpected column(s) ",", " sv string extra];
    if[count out; :out];
    if[ordered and not (key want)~key got;
        :enlist "columns out of order: expected ",(" " sv string key want),", got ",(" " sv string key got)];
    shared:key want;
    bad:shared where not type_ok'[want shared;got shared;count tbl];
    $[count bad;
        enlist "wrong type(s): ",", " sv {[w;g;c] string[c],"(expected '",w,"', got '",g,"')"}'[want bad;got bad;bad];
        ()]}

/ Private: does an actual meta type satisfy a declared one?
type_ok:{[declared;actual;n]
    if[declared=actual; :1b];
    if[declared=" "; :1b];
    (actual=" ") and n=0}

/ ---------------------------------------------------------------- COMPARE

/ Private: why `actual` differs from `expected`, as strings - empty when
/ they match. Row order is significant: a deterministic transform returns
/ its rows in a deterministic order, and a consumer that publishes them
/ sees that order.
differences:{[expected;actual]
    if[not (count expected)=count actual;
        :enlist "expected ",string[count expected]," row(s), got ",string count actual];
    s:problems[expected;actual;1b];
    if[count s; :s];
    t:col_types expected;
    bad:(key t) where not col_equal'[t key t;expected key t;actual key t];
    if[0=count bad; :()];
    / Name the first differing value, not just the column: "mismatched column(s)
    / unrealized_pnl" sends the reader back to recompute every row by hand.
    c:first bad;
    row:first where not {[typ;e;a] col_equal[typ;enlist e;enlist a]}[t c]'[expected c;actual c];
    enlist "mismatched column(s) ",(", " sv string bad),
        " - first at row ",string[row]," of ",string[c],
        ": expected ",(.Q.s1 expected[c] row),", got ",.Q.s1 actual[c] row}

/ Private: one column's values equal, with float tolerance.
col_equal:{[typ;e;a]
    if[not typ in "fe"; :e~a];
    all (null[e]=null a) and (null e) or float_tolerance>=abs e-a}

/ -------------------------------------------------------------- DECLARING

/ Declare a transform.
/ .
/ The declaration is checked here, the examples are RUN by `verify`: a
/ transform may call library functions that load after it, and q binds a
/ call when it happens rather than when it is written.
/ @param name the transform's name
/ @param decl dict of inputs, output, fn and examples, and optionally
/   as_of (1b when fn takes the instant as its last argument)
/ @return name
/ @throws error naming what is wrong with the declaration
/ @eg .qxf.define[`mid_quotes;`inputs`output`fn`examples!(enlist[`quotes]!enlist ([] sym:`symbol$(); bid:`float$(); ask:`float$()); ([] sym:`symbol$(); mid:`float$()); {[q] select sym, mid:(bid+ask)%2 from q}; enlist `inputs`expected!(enlist[`quotes]!enlist ([] sym:enlist`EURUSD; bid:1.1; ask:1.2); ([] sym:enlist`EURUSD; mid:1.15)))]
define:{[name;decl]
    who:"define: transform ",string[name];
    if[not 99h=type decl; '"define: a transform declaration must be a dictionary"];
    missing:required_keys where not required_keys in key decl;
    if[count missing; 'who," is missing ",", " sv string missing];
    ins:decl`inputs;
    if[not (99h=type ins) and 11h=type key ins;
        'who,"'s inputs must be a dictionary of name -> empty typed table"];
    if[0=count ins; 'who," reads no inputs - a transform of nothing is a constant"];
    if[not all 98h=type each value ins;
        'who,"'s inputs must each be an unkeyed table"];
    if[not 98h=type decl`output;
        'who,"'s output must be an unkeyed table - a keyed table cannot be published"];
    clock:$[`as_of in key decl; decl`as_of; 0b];
    if[not -1h=type clock; 'who,"'s as_of must be a boolean"];
    fn:decl`fn;
    if[not (type fn) within 100 112h; 'who,"'s fn must be a function"];
    arity:(count ins)+clock;
    if[(100h=type fn) and not arity=count (value fn) 1;
        'who,"'s fn must take ",string[arity]," argument(s): ",
         (", " sv string key ins),$[clock;", as_of";""]];
    exs:decl`examples;
    if[0=count exs; 'who," has no examples - a transform with no expected output asserts nothing"];
    check_example[name;ins;decl`output;clock] each exs;
    if[not any {[e] any 0<count each value e`inputs} each exs;
        'who,"'s examples are all empty - at least one must carry rows"];
    registry[name]:`inputs`output`fn`examples`as_of!(ins;decl`output;fn;exs;clock);
    name}

/ Private: an example is well-formed against its transform's declaration.
check_example:{[name;ins;output;clock;ex]
    who:"define: transform ",string[name];
    if[not 99h=type ex; 'who,"'s examples must be dictionaries of inputs and expected"];
    if[not all `inputs`expected in key ex; 'who," has an example without inputs and expected"];
    if[clock and not `as_of in key ex; 'who," takes as_of, so every example must say which instant it was written for"];
    if[clock and not -12h=type ex`as_of; 'who,"'s example as_of must be a timestamp"];
    given:ex`inputs;
    if[not (99h=type given) and (asc key given)~asc key ins;
        'who," has an example whose inputs are not exactly ",", " sv string key ins];
    {[who;schema;nm;tbl]
        p:problems[schema;tbl;0b];
        if[count p; 'who,"'s example input ",string[nm],": ","; " sv p]
      }[who]'[ins key given;key given;value given];
    p:problems[output;ex`expected;1b];
    if[count p; 'who,"'s example expected output: ","; " sv p];
    }

/ Declare a transform that publishes its one input unchanged.
/ .
/ For a job that genuinely changes nothing - a backfill copying a source into
/ its target. Declaring it is the point: "this job transforms nothing" becomes
/ a named, tested claim rather than a missing step, and the day the job does
/ need a transform there is one place to write it.
/ @param name the transform's name
/ @param input_name the name its one input is read under
/ @param schema the empty typed table in and out
/ @param rows a non-empty example of that table
/ @return name
/ @eg .qxf.passthrough[`demo_deals_passthrough;`batch;0#.qfeed.demo_deals.fixture[];.qfeed.demo_deals.fixture[]]
passthrough:{[name;input_name;schema;rows]
    define[name;`inputs`output`fn`examples!(
        (enlist input_name)!enlist schema;
        schema;
        {[batch] batch};
        enlist `inputs`expected!((enlist input_name)!enlist rows;rows))]}

/ A transform's declaration, or an error naming it.
/ @throws error when no such transform is registered
declaration:{[name]
    if[not name in key registry;
        '"transform ",string[name]," is not registered - declare it with .qxf.define"];
    registry name}

/ The input names a transform reads, in the order its fn takes them.
input_names:{[name] key (declaration name)`inputs}

/ The empty output table a transform produces.
output_schema:{[name] (declaration name)`output}

/ --------------------------------------------------------------- APPLYING

/ Run a transform that takes no instant.
/ .
/ The inputs are checked against the declaration before the call and the
/ output after it. Errors from fn itself are NOT trapped: whether a failed
/ transform fails the window, the tick or the process is the caller's
/ decision, not this layer's.
/ @param name the transform
/ @param given dict input name -> table
/ @return the output table
/ @throws error when an input or the output does not match the declaration
/ @eg .qxf.define[`eg_mid;`inputs`output`fn`examples!(enlist[`q]!enlist ([] sym:`symbol$(); bid:`float$(); ask:`float$()); ([] sym:`symbol$(); mid:`float$()); {[q] select sym, mid:(bid+ask)%2 from q}; enlist `inputs`expected!(enlist[`q]!enlist ([] sym:enlist `EURUSD; bid:1.1; ask:1.2); ([] sym:enlist `EURUSD; mid:1.15)))];
/   .qxf.apply[`eg_mid;enlist[`q]!enlist ([] sym:`EURUSD`GBPUSD; bid:1.10 1.25; ask:1.12 1.27)]  ->  ([] sym:`EURUSD`GBPUSD; mid:1.11 1.26)
apply:{[name;given]
    d:declaration name;
    if[d`as_of; '"apply: transform ",string[name]," takes as_of - use .qxf.apply_as_of"];
    run[name;d;given;()]}

/ Run a transform that takes the instant as its last argument.
/ @param name the transform
/ @param given dict input name -> table
/ @param as_of the instant, read once by the caller
/ @return the output table
/ @throws error when an input or the output does not match the declaration
apply_as_of:{[name;given;as_of]
    d:declaration name;
    if[not d`as_of; '"apply_as_of: transform ",string[name]," takes no as_of - use .qxf.apply"];
    if[not -12h=type as_of; '"apply_as_of: as_of must be a timestamp"];
    run[name;d;given;enlist as_of]}

/ Private: check inputs, call, check the output.
run:{[name;d;given;extra]
    ins:d`inputs;
    if[not (99h=type given) and (asc key given)~asc key ins;
        '"transform ",string[name]," takes inputs ",(", " sv string key ins),
         " - got ",$[99h=type given; ", " sv string key given; "a non-dictionary"]];
    {[name;schema;nm;tbl]
        p:problems[schema;tbl;0b];
        if[count p; '"transform ",string[name]," input ",string[nm],": ","; " sv p]
      }[name]'[ins key ins;key ins;given key ins];
    args:(given key ins),extra;
    out:(d`fn) . args;
    p:problems[d`output;out;1b];
    if[count p; '"transform ",string[name]," output: ","; " sv p];
    out}

/ -------------------------------------------------------------- VERIFYING

/ Run a transform's examples, and its empty-input case.
/ @param name the transform
/ @return table of example (index, or `empty), passed and detail - one row
/   per example plus one for the empty case
/ @eg .qxf.verify `mid_quotes
verify:{[name]
    d:declaration name;
    exs:d`examples;
    rows:verify_example[name;d] each exs;
    empty_inputs:{0#x} each d`inputs;
    empty_case:`inputs`expected!(empty_inputs;d`output);
    if[d`as_of; empty_case[`as_of]:$[count exs; first exs`as_of; 0Np]];
    rows,:enlist verify_example[name;d;empty_case];
    ([] example:(`$string til count exs),`empty;
        passed:rows[;0];
        detail:rows[;1])}

/ Private: (passed; detail) for one example.
verify_example:{[name;d;ex]
    call:$[d`as_of;
        {[name;ex;x] apply_as_of[name;ex`inputs;ex`as_of]}[name;ex];
        {[name;ex;x] apply[name;ex`inputs]}[name;ex]];
    first_out:@[call;::;{[e] (`error;e)}];
    if[(0h=type first_out) and `error~first first_out;
        :(0b;"threw: ",last first_out)];
    diff:differences[ex`expected;first_out];
    if[count diff; :(0b;"; " sv diff)];
    second_out:@[call;::;{[e] (`error;e)}];
    if[not first_out~second_out;
        :(0b;"not deterministic: a second call on the same inputs returned a different table")];
    (1b;"")}

/ Verify every registered transform.
/ @return table of transform, example, passed, detail
verify_all:{[]
    raze {[name] update transform:name from verify name} each key registry}

/ Verify one transform, or throw naming the first failure.
/ @throws error naming the transform, the example and why it failed
require_verified:{[name]
    r:select from verify name where not passed;
    if[count r;
        '"transform ",string[name]," failed example ",string[first r`example],": ",first r`detail];
    name}

\d .
