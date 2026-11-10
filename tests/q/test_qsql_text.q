// test_qsql_text.q - tests for src/etl/core/qsql_text.q (.qetl.qsql), the TRACE
// rendering of a source's query as qSQL on the vendored patmok/unparse
// (lib/unparse), and for the hook that logs it in source_fetch.q.
//
// Run on KDB-X: the vendored unparse is the renderer under test.

.qt.qetl.qsql.w:(2026.10.09D10:00:00.000000000;2026.10.09D11:00:00.000000000)

/ A table of n symbol names, for the widths the console cannot show.
.qt.qetl.qsql.names:{[n] `$"c",/:string til n}

/ The tree of "select <n columns> from t", built without a parse of a long string.
.qt.qetl.qsql.wide_select:{[n]
    cn:.qt.qetl.qsql.names n;
    (?;`t;();0b;cn!cn)}

/ Every `add` in the upstream test file as its query text, read from the vendored
/ file itself: the file defines a root table `t` and a `.unparse.tests`, so it is
/ never loaded here - each `add` line is run against a collector of our own.
.qt.qetl.qsql.upstream_cases:{[]
    ls:read0 `:lib/unparse/testcases.q;
    ls:ls where (ls like "add*") and not ls like "add:*";
    `.qt.qetl.qsql.acc set ();
    {value ".qt.qetl.qsql.collect",3_x} each ls;
    .qt.qetl.qsql.acc}

.qt.qetl.qsql.collect:{`.qt.qetl.qsql.acc set .qt.qetl.qsql.acc,enlist(),x}

.qt.qetl.qsql.test_unparse_is_vendored_and_loaded:{[t]
    .qunit.assertTrue[.qetl.qsql.available[];"the tree carries lib/unparse and the module loaded it"];
    .qunit.assertEquals[.unparse.unparse parse"1+/:1 2 3*5";"+/:[1;1 2 3*5]";"upstream's own README example"]};

/ qsql_text.q reads nothing from the working directory (#2653): loaded by a q
/ whose cwd holds a lib/unparse/unparse.q of its own, it must not run it - the
/ load plan (src/etl/init.q) names that file from the repository root, and a
/ declaration that did it itself loaded whatever the cwd held.
.qt.qetl.qsql.test_loading_the_module_does_not_read_the_working_directory:{[t]
    dir:first system "mktemp -d";
    system "mkdir -p ",dir,"/lib/unparse";
    (hsym `$dir,"/lib/unparse/unparse.q") 0: enlist ".unparse.planted:1b";
    root:first system "pwd";
    (hsym `$dir,"/child.q") 0: (
        "\\l ",root,"/src/etl/core/declared.q";
        "\\l ",root,"/src/etl/core/qsql_text.q";
        "-1 \"planted=\",string `planted in key `.unparse;";
        "-1 \"available=\",string .qetl.qsql.available[];";
        "exit 0");
    out:system "sh -c 'cd ",dir," && ",.qt.support.q_interpreter[]," child.q < /dev/null 2>&1'";
    system "rm -rf ",dir;
    .qunit.assertTrue["planted=0" in out;"the cwd's own lib/unparse was not loaded: ",-3!out];
    .qunit.assertTrue["available=0" in out;"so unparse is reported absent, not found by a path guess"]};

.qt.qetl.qsql.test_upstream_cases_pass_but_one_which_this_q_cannot_parse:{[t]
    cases:.qt.qetl.qsql.upstream_cases[];
    ok:{@[{p~parse .unparse.unparse p:parse x};x;0b]} each cases;
    .qunit.assertEquals[(count cases;`int$sum ok);(121;120i);"120 of upstream's 121 cases round-trip on KDB-X"];
    .qunit.assertEquals[cases where not ok;enlist "select x,y,z+1 by z+i,x from t";
        "the one that does not: KDB-X itself refuses to parse it"];
    .qunit.assertThrows[parse;"select x,y,z+1 by z+i,x from t";"*dup names*";
        "'dup names for cols/groups x' - a duplicate x in the by and select lists, from parse and not from unparse"]};

.qt.qetl.qsql.test_every_supported_upstream_case_comes_back_as_qsql_that_parses_to_the_same_tree:{[t]
    cases:.qt.qetl.qsql.upstream_cases[];
    cases:cases except enlist "select x,y,z+1 by z+i,x from t";
    r:.qetl.qsql.render each parse each cases;
    .qunit.assertEquals[distinct r[;0] except `call;enlist `qsql;"each is rendered, as qSQL or as the ?[...] call unparse spells for a few"];
    .qunit.assertEquals[all (.qetl.qsql.quote each parse each cases)~'parse each r[;1];1b;
        "and each text parses to the tree it came from (a ?[...] call, to the quoted tree its qSQL stands for)"];
    .qunit.assertEquals[sum r[;0]=`call;4i;"four are written as the call: their arguments name variables (t, c) that cannot be evaluated here"]};

.qt.qetl.qsql.test_a_functional_form_reads_as_qsql_and_round_trips:{[t]
    / The trees qSQL parses to, as the sources' own select/exec/update/delete are.
    sql:("select from t where sym=`EURUSD";"select m:max px by s:sym from t where px>1.5";
        "exec m:max px from t where px>1.5";"update px:px*2 from t where px<2f";"delete px from t where sym=`GBPUSD");
    r:.qetl.qsql.render each parse each sql;
    .qunit.assertEquals[r[;0];5#`qsql;"select, exec, update and delete are all rendered"];
    .qunit.assertEquals[r[;1];("select from t where sym=`EURUSD";"exec m:max[px] by s:sym from t where px>1.5";
        "exec m:max[px] from t where px>1.5";"update px:px*2 from t where px<2f";"delete px from t where sym=`GBPUSD");
        "as unparse spells them: max[px] in prefix form, and select-by as exec-by (the same tree, the same result)"];
    .qunit.assertEquals[(parse each sql)~'parse each r[;1];11111b;"and parse of the text is the tree, form by form"]};

.qt.qetl.qsql.test_a_functional_call_is_quoted_and_then_read_as_qsql:{[t]
    / ?[t;c;b;a] with its arguments as expressions - the shape fetch_window's fixture uses.
    calls:("?[t;enlist(=;`sym;enlist`EURUSD);0b;()]";"?[t;enlist(>;`px;1.5);0b;(enlist`m)!enlist(max;`px)]";
        "![t;enlist(<;`px;2f);0b;(enlist`px)!enlist(*;`px;2)]";"![t;enlist(=;`sym;enlist`GBPUSD);0b;enlist`px]");
    r:.qetl.qsql.render each parse each calls;
    .qunit.assertEquals[r;((`qsql;"select from t where sym=`EURUSD");(`qsql;"select m:max[px] from t where px>1.5");
        (`qsql;"update px:px*2 from t where px<2f");(`qsql;"delete px from t where sym=`GBPUSD"));
        "the call reads as the qSQL it is the functional form of"]};

.qt.qetl.qsql.test_nested_expressions_render_and_round_trip:{[t]
    qs:("select from bt where a in(exec aa from bt2 where i<5,i in til 5)";
        "select x,y+5 by z from t lj y where date=last .z.d";
        "(a+b)*c";"a+b*c";"update c:sum x by z from select from t where 1b";
        "select from t where x in (select y from u where z>(count v)+1)");
    r:.qetl.qsql.render each parse each qs;
    .qunit.assertEquals[distinct r[;0];enlist `qsql;"nested selects and arithmetic are rendered, not dumped"];
    .qunit.assertEquals[(parse each qs)~'parse each r[;1];111111b;"each nested form parses back to itself"]};

.qt.qetl.qsql.test_a_long_target_table_is_not_cut_at_the_console_width:{[t]
    tbl:`$"very_long_table_name_",200#"x";
    tree:parse "select from ",string[tbl]," where px>1.5";
    r:.qetl.qsql.render tree;
    .qunit.assertEquals[r 0;`qsql;"rendered as qSQL"];
    .qunit.assertEquals[r 1;"select from ",string[tbl]," where px>1.5";"the whole 221-character name, not 79 and \"..\""];
    .qunit.assertEquals[tree~parse r 1;1b;"and it parses back to the same tree"]};

.qt.qetl.qsql.test_a_query_wider_than_2000_columns_is_complete_as_qsql:{[t]
    tree:.qt.qetl.qsql.wide_select 3000;
    r:.qetl.qsql.render tree;
    .qunit.assertEquals[r 0;`qsql;"3000 columns are still qSQL"];
    .qunit.assertEquals[(2000<count r 1;"c2999:c2999 from t"~-18#r 1;0<count ss[r 1;".."]);(1b;1b;0b);
        "far past the console's 2000 columns, ending at the last column and the table, with no cut marker"];
    .qunit.assertEquals[tree~parse r 1;1b;"and it parses back to the same tree"]};

.qt.qetl.qsql.test_a_form_unparse_cannot_spell_comes_back_as_the_whole_tree_untruncated:{[t]
    / One symbol vector of 3000 names is a single leaf wider than any console, so
    / unparse's .Q.s1 cuts it - the round trip catches that and the tree is returned.
    syms:.qt.qetl.qsql.names 3000;
    tree:(?;`t;enlist(in;`sym;enlist syms);0b;());
    r:.qetl.qsql.render tree;
    .qunit.assertEquals[r 0;`tree;"not claimed as qSQL"];
    .qunit.assertEquals[(2000<count r 1;r[1] like "*c2999*";r[1] like "*c0*");(1b;1b;1b);
        "the tree is whole: it holds the first and the last name, and runs well past 2000 characters"];
    .qunit.assertEquals[0<count ss[r 1;".."];0b;"no cut marker anywhere in it"]};

.qt.qetl.qsql.test_full_splits_a_vector_wider_than_the_console_into_pieces:{[t]
    v:til 3000;
    s:.qetl.qsql.full v;
    .qunit.assertEquals[(2000<count s;s like "*2999)*");(1b;1b);"all 3000 longs, past the console's cap"];
    .qunit.assertEquals[(.qetl.qsql.full (+;1;2));"(+;1;2)";"a short tree reads as -3! does"]};

.qt.qetl.qsql.test_the_console_width_is_put_back:{[t]
    c:system "c";
    .qetl.qsql.render parse "select from t";
    .qetl.qsql.full til 5000;
    .qunit.assertEquals[system "c";c;"the console is as the caller left it"];
    r:@[.qetl.qsql.wide[{'"boom"}];1;{[e] e}];
    .qunit.assertEquals[(r;system "c");("boom";c);"even when the rendering throws"]};

.qt.qetl.qsql.test_arguments_are_resolved_into_the_query:{[t]
    f:{[syms;from_ts;to_ts] select from trade where sym in syms, time>=from_ts, time<to_ts};
    r:.qetl.qsql.of_call[f[`EURUSD`GBPUSD];.qt.qetl.qsql.w];
    .qunit.assertEquals[r;(`qsql;"select from trade where sym in `EURUSD`GBPUSD,>=[time;2026.10.09D10:00:00.000000000],time<2026.10.09D11:00:00.000000000");
        "the projection's pairs and the call's timestamps appear as values"];
    .qunit.assertEquals[0<count ss[r 1;"syms"],ss[r 1;"from_ts"],ss[r 1;"to_ts"];0b;"and none of the lambda's parameter names"];
    r1:.qetl.qsql.of_call[f[enlist`EURUSD];.qt.qetl.qsql.w];
    .qunit.assertEquals[(r1 0;0<count ss[r1 1;"in enlist"]);(`qsql;1b);"a one-element vector keeps being a vector"];
    r2:.qetl.qsql.of_call[f[`EURUSD];.qt.qetl.qsql.w];
    .qunit.assertEquals[(r2 0;0<count ss[r2 1;"in `EURUSD,"]);(`qsql;1b);"a symbol atom stays an atom"]};

.qt.qetl.qsql.test_a_lambda_with_every_argument_given_at_the_call:{[t]
    r:.qetl.qsql.of_call[{[a;b] select from t where x>a, y<b};1 2];
    .qunit.assertEquals[r;(`qsql;"select from t where x>1,y<2");"no projection: the call's arguments fill the parameters"];
    r:.qetl.qsql.of_call[{[a;b] select from t where x>a};enlist 5];
    .qunit.assertEquals[r 0;`lambda;"an argument count that does not match the parameters is reported, not guessed"]};

.qt.qetl.qsql.test_without_unparse_the_query_is_the_whole_tree:{[t]
    keep:.qetl.qsql.available;
    .qetl.qsql.available:{[] 0b};
    r:@[.qetl.qsql.render;parse "select from t where x>1";{[e] e}];
    .qetl.qsql.available:keep;
    .qunit.assertEquals[r 0;`tree;"a release without lib/ degrades to the tree"];
    .qunit.assertEquals[r 1;.qetl.qsql.full parse "select from t where x>1";"complete, not cut"]};

/ --- the TRACE hook in source_fetch.q ---------------------------------

.qt.qetl.qsql.logged:.qt.support.captured_log[1b]
.qt.qetl.qsql.silent:.qt.support.captured_log[0b]

.qt.qetl.qsql.fake_handle:{value x}

.qt.qetl.qsql.query:{[]
    .qetl.source.ipc[.qt.qetl.qsql.fake_handle;{[syms;a;b] ([] sym:syms)}[`EURUSD`GBPUSD];.qt.qetl.qsql.w 0;.qt.qetl.qsql.w 1]}

.qt.qetl.qsql.test_trace_shows_the_query_with_its_values:{[t]
    lines:.qt.qetl.qsql.logged {.qt.qetl.qsql.query[]};
    sent:(first lines)[3];
    .qunit.assertEquals[(sent`qsql_form;sent`qsql);(`qsql;"([]sym:`EURUSD`GBPUSD)");
        "the traced query carries the currency pairs as values"];
    .qunit.assertEquals[0<count ss[sent`call;"syms"];1b;"the lambda text stays as it was"]};

.qt.qetl.qsql.test_trace_off_emits_nothing_and_renders_nothing:{[t]
    n:0;
    keep:.qetl.qsql.of_call;
    `.qt.qetl.qsql.hits set 0;
    .qetl.qsql.of_call:{[f;a] .qt.qetl.qsql.hits+:1; (`qsql;"")};
    lines:.qt.qetl.qsql.silent {.qt.qetl.qsql.query[]};
    .qetl.qsql.of_call:keep;
    .qunit.assertEquals[(count lines;.qt.qetl.qsql.hits);(0;0);"no line, and the query is never even rendered"]};

.qt.qetl.qsql.test_no_credential_reaches_the_log:{[t]
    secret:"alice:s3cr3tpw";
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;"db.internal.example:5010:",secret];
    `.qt.qetl.qsql.h set {([] deal_id:`long$())};
    lines:.qt.qetl.qsql.logged {.qetl.source.fetch_window[`demo_deals;.qt.qetl.qsql.h;2026.09.10D00:00;2026.09.11D00:00]};
    setenv[`UQF_SOURCE_CRED_DEMO_DEALS;""];
    all_text:raze {.qetl.log.value1 x} each lines;
    .qunit.assertTrue[0<count lines;"something was traced"];
    .qunit.assertEquals[(0<count ss[all_text;"s3cr3tpw"];0<count ss[all_text;"alice"];0<count ss[all_text;"db.internal"]);(0b;0b;0b);
        "neither password, user nor host appears in any line"];
    sent:first lines where lines[;2]~\:"query sent";
    .qunit.assertTrue[`qsql in key sent 3;"the query itself is traced beside them"]};

.qt.qetl.qsql.test_a_rendering_that_throws_does_not_cost_the_query:{[t]
    keep:.qetl.qsql.of_call;
    .qetl.qsql.of_call:{[f;a] '"render broke"};
    lines:.qt.qetl.qsql.logged {`.qt.qetl.qsql.r set .qt.qetl.qsql.query[]};
    .qetl.qsql.of_call:keep;
    .qunit.assertEquals[.qt.qetl.qsql.r;([] sym:`EURUSD`GBPUSD);"the rows still come back"];
    .qunit.assertEquals[((first lines)[3])`qsql_form;`error;"and the trace says its rendering failed"]};
