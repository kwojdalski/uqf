// test_hdb_alter.q - scripts/gates/hdb_alter.q, the standalone script behind
// `uqs data hdb-alter` and `uqs data hdb-check --table` (.qt.qproc.hdbalter, #1755).
//
// The script exits when it has run, so each test runs it as a child q over a
// temporary HDB built here: five date partitions, the table `pb` on the last
// three only (a backfilled table), and `other` on all five, both enumerated
// against one shared sym file. Assertions read the bytes back: a change that
// "worked" is told from one that left the database unreadable only on disk.
//
// KDB-X only: the script refuses PeachQ, which cannot write a partitioned HDB
// natively, so every case here is in tests/q/peachq_known_gaps.txt.


/ A scratch directory, as a string: resolved, as the script resolves the HDB
/ (macOS's temporary directory is behind the /var -> /private/var link).
/ "(" first, so q's system does not take the leading `cd` as its own \cd.
.qt.qproc.hdbalter.scratch:{[] first system "(cd \"$(mktemp -d)\" && pwd -P)"}

/ The partitions the fixture writes, and the three that hold `pb`.
.qt.qproc.hdbalter.parts:2026.01.01+til 5
.qt.qproc.hdbalter.pb_parts:2026.01.03+til 3

/ A fixture HDB: `other` on every date, `pb` on the last three.
/ @return the HDB root, as a string
.qt.qproc.hdbalter.build:{[]
    root:.qt.qproc.hdbalter.scratch[],"/hdb";
    r:hsym `$root;
    {[r;d] (` sv r,(`$string d),`other`) set .Q.en[r] ([] time:3#d+0D; sym:`a`b`c; v:1 2 3f)}[r] each .qt.qproc.hdbalter.parts;
    {[r;d] (` sv r,(`$string d),`pb`) set .Q.en[r] ([] time:d+0D 0D01; sym:`x`a; amt:1.5 2.5; n:3 4i)}[r] each .qt.qproc.hdbalter.pb_parts;
    root}

/ Every regular file under a directory, with its bytes.
/ @param root a directory, as a string
/ @return file path -> bytes
.qt.qproc.hdbalter.snapshot:{[root]
    fs:system "find ",root," -type f | sort";
    fs!read1 each hsym each `$fs}

/ Run the script as a child q.
/ @param args its command line after the script, as a string
/ @return (output lines; exit code)
.qt.qproc.hdbalter.run:{[args]
    out:.qt.qproc.hdbalter.scratch[],"/out.txt";
    / `env` first: q's `system` reads a command starting with a q command letter as its own
    cmd:"env ",.qt.support.q_interpreter[]," scripts/gates/hdb_alter.q ",args,
        " -q < /dev/null > ",out," 2>&1; echo $?";
    code:"J"$first system cmd;
    (read0 hsym `$out;code)}

/ Run it over `pb` in the fixture HDB `root`.
.qt.qproc.hdbalter.on:{[root;args] .qt.qproc.hdbalter.run["-hdb ",root," -table pb ",args]}

/ The journals beside an HDB, newest last.
.qt.qproc.hdbalter.journals:{[root]
    d:hsym `$root,".uqf-alter";
    $[()~key d; `symbol$(); ` sv/:d,/:asc (key d) where (key d) like "*.json"]}

/ The newest journal, as a path string.
.qt.qproc.hdbalter.journal:{[root] 1_string last .qt.qproc.hdbalter.journals[root]}

/ A journal's status.
.qt.qproc.hdbalter.status:{[root] (.j.k raze read0 last .qt.qproc.hdbalter.journals[root])`status}

/ A table's .d in one partition.
.qt.qproc.hdbalter.dcols:{[root;d;t] get ` sv (hsym `$root),(`$string d),t,`.d}

/ Rows of a cross-date select over the HDB, run in a child q: (exit code; output).
.qt.qproc.hdbalter.select:{[root;query]
    f:.qt.qproc.hdbalter.scratch[],"/select.q";
    (hsym `$f) 0: ("system \"l ",root,"\";";"-1 .j.j ",query,";";"exit 0");
    out:.qt.qproc.hdbalter.scratch[],"/select.out";
    code:"J"$first system "env ",.qt.support.q_interpreter[]," ",f," -q < /dev/null > ",out," 2>&1; echo $?";
    (code;read0 hsym `$out)}

/ Whether some line of the output holds a text, as it is: `ss`, not `like`,
/ whose patterns KDB-X refuses ('nyi) past a couple of wildcards.
.qt.qproc.hdbalter.says:{[r;text] any {[line;text] 0<count line ss text}[;text] each r 0}

/ --- a dry run ---------------------------------------------------------------

.qt.qproc.hdbalter.test_a_dry_run_writes_nothing_and_creates_no_journal:{[t]
    root:.qt.qproc.hdbalter.build[];
    before:.qt.qproc.hdbalter.snapshot[root];
    ops:("-reorder n,amt,sym,time";"-rename amt=amount";"-attr sym:g";"-cast n:long";"-fill");
    codes:{[root;op] last .qt.qproc.hdbalter.on[root;op]}[root] each ops;
    .qunit.assertEquals[codes;5#0;"each plan is valid, so each dry run exits 0"];
    .qunit.assertEquals[.qt.qproc.hdbalter.snapshot[root];before;"every file's bytes are unchanged"];
    .qunit.assertEquals[key hsym `$root,".uqf-alter";();"and no journal directory exists"]};

.qt.qproc.hdbalter.test_the_plan_names_the_journal_the_touched_and_the_skipped_partitions:{[t]
    root:.qt.qproc.hdbalter.build[];
    r:.qt.qproc.hdbalter.on[root;"-cast n:long -json"];
    .qunit.assertEquals[r 1;0;"a valid plan exits 0"];
    p:.j.k last r 0;
    .qunit.assertEquals[p`partitions_touched;string .qt.qproc.hdbalter.pb_parts;"the partitions holding pb are touched"];
    .qunit.assertEquals[p`partitions_lacking;string 2026.01.01 2026.01.02;"and the others reported, not touched"];
    .qunit.assertTrue[(0<count (p`journal) ss ".uqf-alter/") and (p`journal) like "*-pb.json";"the journal is named"];
    .qunit.assertEquals[(count root)#p`journal;root;"beside the HDB"];
    .qunit.assertTrue[0<p`bytes_rewritten;"a cast says how many bytes it rewrites"];
    .qunit.assertEquals[p`status;"planned";"and nothing more"]};

/ --- hdb-check --table: the fill ------------------------------------------------

.qt.qproc.hdbalter.test_fill_adds_the_empty_table_only_where_missing:{[t]
    root:.qt.qproc.hdbalter.build[];
    before:.qt.qproc.hdbalter.snapshot[root];
    r:.qt.qproc.hdbalter.on[root;"-fill -apply"];
    .qunit.assertEquals[r 1;0;"the fill exits clean"];
    after:.qt.qproc.hdbalter.snapshot[root];
    .qunit.assertEquals[after key before;value before;"every file that existed - pb's rows, other, sym - is byte-identical"];
    added:(key after) except key before;
    .qunit.assertTrue[all added like root,"/2026.01.0[12]/pb/*";"only the two partitions lacking pb gained files"];
    got:{[root;d] .qt.qproc.hdbalter.dcols[root;d;`pb]}[root] each 2026.01.01 2026.01.02;
    .qunit.assertEquals[got;2#enlist `time`sym`amt`n;"in the template's column order"];
    q:.qt.qproc.hdbalter.select[root;"exec (count i;type n;type amt) from select from pb"];
    .qunit.assertEquals[q 0;0;"a cross-date select now runs"];
    .qunit.assertEquals[.j.k last q 1;6 6 9f;"six rows; n still int, amt float - the template's types"]};

.qt.qproc.hdbalter.test_fill_takes_its_schema_from_a_declaration_when_one_is_given:{[t]
    root:.qt.qproc.hdbalter.build[];
    decl:.qt.qproc.hdbalter.scratch[],"/decl.q";
    (hsym `$decl) 0: enlist "nt:([] time:`timestamp$(); sym:`symbol$(); note:())";
    sym_before:read1 hsym `$root,"/sym";
    r:.qt.qproc.hdbalter.run["-hdb ",root," -table nt -fill -schema ",decl," -apply"];
    .qunit.assertEquals[r 1;0;"a table no partition holds is written from its declaration"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"declared in"];"and the plan says where its schema came from"];
    .qunit.assertEquals[{[root;d] .qt.qproc.hdbalter.dcols[root;d;`nt]}[root] each .qt.qproc.hdbalter.parts;5#enlist `time`sym`note;
        "every partition holds it"];
    .qunit.assertEquals[read1 hsym `$root,"/sym";sym_before;"and the shared sym file gained nothing"]};

.qt.qproc.hdbalter.test_fill_refuses_a_declaration_the_partitions_disagree_with:{[t]
    root:.qt.qproc.hdbalter.build[];
    decl:.qt.qproc.hdbalter.scratch[],"/decl.q";
    (hsym `$decl) 0: enlist "pb:([] time:`timestamp$(); sym:`symbol$(); amt:`float$(); n:`long$())";
    before:.qt.qproc.hdbalter.snapshot[root];
    r:.qt.qproc.hdbalter.on[root;"-fill -schema ",decl," -apply"];
    .qunit.assertEquals[r 1;1;"refused"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"differ from the template"] and .qt.qproc.hdbalter.says[r;"n:j"];"naming the declared shape"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"n:i"] and .qt.qproc.hdbalter.says[r;"2026.01.03"];"and the shape the partitions hold"];
    .qunit.assertEquals[.qt.qproc.hdbalter.snapshot[root];before;"before any write"]};

/ --- reorder, rename, attr, cast -----------------------------------------------

.qt.qproc.hdbalter.test_reorder_changes_only_the_d_of_partitions_holding_the_table:{[t]
    root:.qt.qproc.hdbalter.build[];
    before:.qt.qproc.hdbalter.snapshot[root];
    r:.qt.qproc.hdbalter.on[root;"-reorder n,amt,sym,time -apply"];
    .qunit.assertEquals[r 1;0;"a table missing from two partitions is reordered"];
    after:.qt.qproc.hdbalter.snapshot[root];
    changed:(key before) where not (value before)~'after key before;
    .qunit.assertEquals[asc changed;asc {[root;d] root,"/",string[d],"/pb/.d"}[root] each .qt.qproc.hdbalter.pb_parts;
        "exactly the three .d files changed"];
    .qunit.assertEmpty[(key after) except key before;"nothing was added to the HDB: the journal is beside it"];
    q:.qt.qproc.hdbalter.select[root;"cols select from pb where date within 2026.01.03 2026.01.05"];
    .qunit.assertEquals[q 0;0;"a cross-date select over the partitions holding it runs"];
    .qunit.assertEquals[`$.j.k last q 1;`date`n`amt`sym`time;"in the new order"];
    .qunit.assertEquals[.qt.qproc.hdbalter.status[root];"completed";"and the journal records a completed run"]};

/ Apply `op`, then undo it from its journal: (before; after apply; after undo; outputs).
.qt.qproc.hdbalter.round_trip:{[op]
    root:.qt.qproc.hdbalter.build[];
    before:.qt.qproc.hdbalter.snapshot[root];
    a:.qt.qproc.hdbalter.on[root;op," -apply"];
    mid:.qt.qproc.hdbalter.snapshot[root];
    u:.qt.qproc.hdbalter.run["-hdb ",root," -undo ",.qt.qproc.hdbalter.journal[root]," -apply"];
    (before;mid;.qt.qproc.hdbalter.snapshot[root];a;u;.qt.qproc.hdbalter.status[root])}

/ The common assertions of a round trip.
.qt.qproc.hdbalter.assert_round_trip:{[res;what]
    .qunit.assertEquals[(res[3] 1;res[4] 1);0 0;what," and its undo both exit clean"];
    .qunit.assertTrue[not res[0]~res 1;what," changed the HDB"];
    .qunit.assertEquals[res 2;res 0;"its undo restores every file's bytes"];
    .qunit.assertEquals[res 5;"undone";"and the journal says so"]}

.qt.qproc.hdbalter.test_rename_round_trips:{[t]
    res:.qt.qproc.hdbalter.round_trip["-rename amt=amount"];
    .qt.qproc.hdbalter.assert_round_trip[res;"-rename"];
    .qunit.assertTrue[any (key res 1) like "*/2026.01.05/pb/amount";"the column file was renamed"]};

.qt.qproc.hdbalter.test_attr_round_trips:{[t]
    res:.qt.qproc.hdbalter.round_trip["-attr sym:g"];
    .qt.qproc.hdbalter.assert_round_trip[res;"-attr"]};

.qt.qproc.hdbalter.test_cast_round_trips_and_keeps_no_original_once_clean:{[t]
    res:.qt.qproc.hdbalter.round_trip["-cast n:long"];
    .qt.qproc.hdbalter.assert_round_trip[res;"-cast"];
    .qunit.assertFalse[any (key res 1) like "*.uqf-*";"a clean cast leaves no .uqf-new or .uqf-orig behind"]};

.qt.qproc.hdbalter.test_an_attribute_the_data_breaks_is_refused_before_any_write:{[t]
    root:.qt.qproc.hdbalter.build[];
    before:.qt.qproc.hdbalter.snapshot[root];
    r:.qt.qproc.hdbalter.on[root;"-attr sym:s -apply"];
    .qunit.assertEquals[r 1;1;"`x`a is not sorted"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"does not satisfy `s#"];"the refusal says why"];
    .qunit.assertEquals[.qt.qproc.hdbalter.snapshot[root];before;"and nothing was written"]};

/ --- interruption ----------------------------------------------------------------

.qt.qproc.hdbalter.test_an_interrupted_run_is_undone_or_completed:{[t]
    {[op]
        root:.qt.qproc.hdbalter.build[];
        before:.qt.qproc.hdbalter.snapshot[root];
        k:.qt.qproc.hdbalter.on[root;op," -apply -test_interrupt_after 2"];
        .qunit.assertEquals[k 1;3;op," was stopped after two of three partitions"];
        .qunit.assertEquals[.qt.qproc.hdbalter.status[root];"started";"leaving a journal of a started run"];
        o:.qt.qproc.hdbalter.on[root;"-reorder time,sym,n,amt"];
        .qunit.assertTrue[(1=o 1) and .qt.qproc.hdbalter.says[o;"interrupted run"];"another operation is refused meanwhile"];
        u:.qt.qproc.hdbalter.run["-hdb ",root," -undo ",.qt.qproc.hdbalter.journal[root]," -apply"];
        .qunit.assertTrue[(0=u 1) and .qt.qproc.hdbalter.says[u;"final check clean"];"-undo ends with a clean final check"];
        .qunit.assertEquals[.qt.qproc.hdbalter.snapshot[root];before;"and restores every file"];
        .qt.qproc.hdbalter.on[root;op," -apply -test_interrupt_after 1"];
        a:.qt.qproc.hdbalter.on[root;op," -apply"];
        .qunit.assertTrue[(0=a 1) and .qt.qproc.hdbalter.says[a;"final check clean"];"running it again completes it, cleanly"];
        .qunit.assertTrue[.qt.qproc.hdbalter.says[a;"already done: 2026.01.03"];"skipping the partition already changed"];
        .qunit.assertEquals[.qt.qproc.hdbalter.status[root];"completed";"and the journal it resumed is completed"]
        } each ("-reorder n,amt,sym,time";"-rename amt=amount";"-cast n:long";"-attr sym:g")};

/ --- refusals ----------------------------------------------------------------------

.qt.qproc.hdbalter.test_partitions_that_disagree_are_refused_before_any_write_naming_each_shape:{[t]
    root:.qt.qproc.hdbalter.build[];
    / one partition holds pb with its columns in another order
    @[hsym `$root,"/2026.01.04/pb";`.d;:;`time`sym`n`amt];
    before:.qt.qproc.hdbalter.snapshot[root];
    r:.qt.qproc.hdbalter.on[root;"-cast n:long -apply"];
    .qunit.assertEquals[r 1;1;"refused"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"disagree"];"saying the partitions disagree"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"time:p, sym:s, amt:f, n:i  <- 2026.01.03, 2026.01.05"];"naming one shape and its partitions"];
    .qunit.assertTrue[.qt.qproc.hdbalter.says[r;"time:p, sym:s, n:i, amt:f  <- 2026.01.04"];"and the other"];
    .qunit.assertEquals[.qt.qproc.hdbalter.snapshot[root];before;"before any write"]};

.qt.qproc.hdbalter.test_the_operations_own_mistakes_are_refused:{[t]
    root:.qt.qproc.hdbalter.build[];
    cases:(("-reorder n,amt";"exactly the columns");
           ("-cast sym:long";"a symbol column");
           ("-cast n:symbol";"cannot cast to symbol");
           ("-rename amt=sym";"already a column");
           ("-rename nope=x";"no column nope");
           ("-reorder time -rename a=b";"one operation per run"));
    got:{[root;c] r:.qt.qproc.hdbalter.on[root;c 0]; (r 1;.qt.qproc.hdbalter.says[r;c 1])}[root] each cases;
    .qunit.assertEquals[got;(count cases)#enlist (1;1b);"each is refused, saying why"]};

/ --- a segmented database ---------------------------------------------------------

.qt.qproc.hdbalter.test_a_segmented_database_is_followed_through_par_txt:{[t]
    base:.qt.qproc.hdbalter.scratch[];
    root:base,"/hdb";
    segs:base,/:("/seg1";"/seg2");
    system "mkdir -p ",root," "," " sv segs;
    (hsym `$root,"/par.txt") 0: segs;
    r:hsym `$root;
    {[r;s;d] (` sv (hsym `$s),(`$string d),`pb`) set .Q.en[r] ([] time:2#d+0D; sym:`x`y; n:1 2i)}[r]'[segs;2026.01.01 2026.01.02];
    res:.qt.qproc.hdbalter.on[root;"-reorder n,sym,time -apply"];
    .qunit.assertEquals[res 1;0;"the reorder exits clean"];
    got:{[s;d] get ` sv (hsym `$s),(`$string d),`pb`.d}'[segs;2026.01.01 2026.01.02];
    .qunit.assertEquals[got;2#enlist `n`sym`time;"both segments' partitions were reordered"]};
