// test_catalog.q - the desk catalog's authored half in
// scripts/processes/uqs_catalog.q (.qt.qcat).
//
// This is the gate that moved. It used to be the last test in
// python/uqf_frontend/tests/test_catalog_drift.py, where it read the q file
// as text from Python and said: a published table must be in the catalog, or
// in _NOT_IN_CATALOG with a reason, and "we forgot" is not one of the two.
//
// The rest of that file policed a 203-row copy of every column and type. The
// copy is gone - `meta` on a live process answers it now - so the drift test
// went with it. This part did not: it is the only thing standing between
// "deliberately not exposed" and "nobody got round to describing it", which
// look identical from outside because both are a table the frontend will not
// browse.


/ Both files, loaded here rather than in run_tests.q's list: nothing else
/ needs .qcat in scope, and plant_tables.q defines twenty-odd top-level tables
/ that no other suite wants in the root namespace. Same reasoning as
/ test_stack_tables.q, which loads the same table file for the same reason.
.qt.qcat.beforeNamespace_load:{[]
    system"l scripts/processes/uqs_catalog.q";
    }

/ The tables plant_tables.q declares, read back out of the file as text.
/ .
/ Lifted from test_stack_tables.q's `declared`, including the reason its
/ pattern looks the way it does: q's `like` treats "[...]" as a CHARACTER
/ CLASS, so the obvious "*:([]*" is an empty class and THROWS rather than
/ matching the literal text it appears to match.
/ .
/ Read as text rather than loaded: this suite does not need the tables
/ themselves, only their names, and loading them would put every one in the
/ root namespace for every suite that runs after this.
.qt.qcat.published:{[]
    ls:read0 `$":src/etl/plant_tables.q";
    / `name:([]` exactly, as uqs and .qetl.plant.adopt_vendored read it: a
    / looser "*:(*" also caught the keyed `elements:([table:...]...)` lookup.
    ls:ls where (not ls like "/*") and {[l] (0<count l) and "([]"~3#(1+l?":")_l} each ls;
    / The flattened copy the PeachQ lane runs writes each name qualified
    / (`.qetl.plant.arbitrage:([]`, as uqs reads it too), so the prefix is dropped.
    asc `$ {n:x til x?":"; $[n like ".qetl.plant.*"; 12_n; n]} each ls}

.qt.qcat.test_every_published_table_is_described_or_hidden:{[t]
    / The gate. A table the tickerplant carries that nobody has described is
    / invisible to a desk, and invisible for no stated reason - which is how
    / a capability ships and nobody can find it.
    .qunit.assertEquals[
        .qt.qcat.published[] except (key .qcat.describe),key .qcat.hidden;
        `symbol$();
        "every table in plant_tables.q is either described or explicitly hidden"]};

.qt.qcat.test_no_table_is_both_described_and_hidden:{[t]
    / Both lists would be a contradiction the surface resolves silently in
    / hidden's favour. Better to refuse it than to pick.
    .qunit.assertEquals[(key .qcat.describe) inter key .qcat.hidden;
        `symbol$();
        "a table is described or hidden, never both"]};

/ The datasets the tree's bounded workers write (#1363). Read from the
/ registry, intersected with the workers declared in a FILE: tests register
/ fixture workers at run time (see test_namespaces.q), and a fixture's dataset
/ is not something a desk may browse. A worker can fill a table that is not in
/ plant_tables.q - imported_trades, demo_deals, event_tape - and the desk reads
/ it all the same, so it is as much a part of the browsable set as a plant
/ table is.
.qt.qcat.datasets:{[]
    asc exec distinct dataset from .qetl.job.bounded.worker_cfg
        where name in .qt.support.etl_declaration_names["src/etl/workers"]}

/ Described tables that are neither in plant_tables.q nor a worker's dataset,
/ and the file that owns each. One today: the coverage ledger, declared by the
/ framework. It is not in any tier's tables - the process keeps it in a lock
/ directory file (src/etl/core/materialisation.q's ledger_path) - so there is
/ nothing for getdata to serve and no tableproperties.csv row, which would make
/ an HDB that lacks the table refuse to start.
/ .
/ The other two tables this list used to carry, demo_deals and event_tape, are
/ worker datasets and now come from `datasets`; the hand list was the second
/ home of a fact the workers declare.
.qt.qcat.ledgers:(enlist `etl_coverage)!enlist "src/etl/core/materialisation.q"

/ Every table a desk may browse, before the catalog's decision: the plant
/ tables, the workers' datasets and the ledger.
.qt.qcat.browsable:{[] asc distinct .qt.qcat.published[],.qt.qcat.datasets[],key .qt.qcat.ledgers}

.qt.qcat.test_every_worker_dataset_is_described_or_hidden:{[t]
    / The same gate as for a plant table, for the tables only a worker writes.
    / imported_trades was in no list at all, which is what "nobody described
    / it" looks like when the list of exemptions is kept by hand.
    .qunit.assertEquals[
        .qt.qcat.datasets[] except (key .qcat.describe),key .qcat.hidden;
        `symbol$();
        "every dataset a bounded worker writes is either described or explicitly hidden"]};

.qt.qcat.test_the_catalog_describes_no_table_that_does_not_exist:{[t]
    / The other direction. An entry for a table nothing declares exposes
    / nothing - the frontend browses the INTERSECTION of this and the live
    / meta - but it is either a typo or a table somebody forgot to publish,
    / and both are worth one line of thought.
    .qunit.assertEquals[
        (key .qcat.describe) except .qt.qcat.browsable[];
        `symbol$();
        "every described table is a plant table, a worker's dataset or the ledger"]};

.qt.qcat.test_each_ledger_is_declared_where_it_claims:{[t]
    / Without this, `ledgers` is an exemption list that grows whenever the
    / check above is inconvenient - which is the shape this repository keeps
    / finding and deleting. Reading the named file means a table that MOVED
    / fails here rather than sitting exempt from both directions forever.
    missing:(key .qt.qcat.ledgers) where not
        {[nm;path] any (read0 hsym `$path) like "*",(string nm),"*"}'
            [key .qt.qcat.ledgers; value .qt.qcat.ledgers];
    .qunit.assertEquals[missing;`symbol$();
        "each table in `ledgers` is named by the file said to declare it"]};

/ The tables scripts/torqconfig/dataaccess/tableproperties.csv lists: its
/ tablename column, read as text.
.qt.qcat.listed:{[]
    ls:1_read0 `$":scripts/torqconfig/dataaccess/tableproperties.csv";
    distinct `$ {(","vs x) 1} each ls where 0<count each ls}

/ The tables an installed sidecar bundle added (src/etl/installed_bundles.json,
/ empty in a checkout, filled in the PeachQ lane's bundle-installed copy). They
/ are plant tables of the installed tree, so they are browsable - but the
/ committed tableproperties.csv describes the BASE tree, and the gateway
/ derives a row for a held plant table the file omits at bootstrap
/ (gateway_access.table_properties_lines), which is how an installed bundle's
/ tables reach getdata. So the two checks below hold the file to the base tree.
.qt.qcat.bundled:{[]
    f:`$":src/etl/installed_bundles.json";
    if[()~key f; :`symbol$()];
    b:.j.k "c"$read1 f;
    distinct `symbol$raze {`$x`tables} each value b}

.qt.qcat.test_every_browsable_table_has_a_getdata_row:{[t]
    / TorQ's checktablename refuses a table the file does not list, and
    / .uqf.browse reads through getdata - so a table the catalog describes and
    / the file omits is offered to the desk and then errors on every read. The
    / ledger is the one exemption, for the reason above.
    .qunit.assertEquals[
        ((.qt.qcat.browsable[] except key .qt.qcat.ledgers) except .qt.qcat.bundled[]) except .qt.qcat.listed[];
        `symbol$();
        "each browsable table has a tableproperties.csv row (python/uqs/.../gateway_access.py derives them)"]};

/ --- TorQ's own refusal, run against the shipped file (#1363) -------------
/ .
/ The two consequences above are TorQ's, not ours, so this runs TorQ's
/ checktablename itself: lib/torq/code/dataaccess/checkinputs.q is loaded as
/ it is and given the rows readtableproperties reads from the shipped file,
/ and its metadata join is stood in for - the check under test is only
/ "is this table listed". What TorQ needs around it (a logger, the process
/ type, the error messages) is put in place for the run and put back.

.qt.qcat.torqnames:`.proc.proctype`.checkinputs.tablepropertiesconfig`.checkinputs.jointableproperties`.schema.errors`.lg.o

/ A refused request's message, or `accepted.
.qt.qcat.checked:{[tbl]
    old:{@[get;x;{::}]} each .qt.qcat.torqnames;
    .proc.proctype:`hdb;
    if[not 100h=type @[get;`.lg.o;{::}]; .lg.o:{[a;b]}];
    .eodtime.datatimezone:`$"Europe/London";.eodtime.rolltimeoffset:0D;.eodtime.rolltimezone:`$"Europe/London";
    system"l lib/torq/code/dataaccess/dataaccessutils.q";
    system"l lib/torq/code/dataaccess/checkinputs.q";
    .checkinputs.tablepropertiesconfig:.checkinputs.readtableproperties `:scripts/torqconfig/dataaccess/tableproperties.csv;
    .schema.errors:1!.checkinputs.readcsv[`:lib/torq/config/dataaccess/errormessages.csv;"s*"];
    .checkinputs.jointableproperties:{[d] d};
    d:`tablename`starttime`endtime`metainfo!(tbl;2020.01.01D;2020.01.02D;()!());
    r:@[{.dataaccess.checktablename x;`accepted};d;{[e] e}];
    .qt.qcat.restore'[.qt.qcat.torqnames;old];
    r}

/ Put a name back as it was: set to its old value, or removed if it had none.
.qt.qcat.restore:{[n;old]
    if[not (::)~old; n set old; :(::)];
    parts:"." vs string n;
    ![`$"." sv -1_ parts;();0b;enlist `$last parts]}

.qt.qcat.test_getdata_refuses_a_table_the_file_does_not_list:{[t]
    .qunit.assertEquals[.qt.qcat.checked`not_a_table;"Table:`not_a_table doesn't exist";
        "TorQ's checktablename refuses an unlisted table - the consequence the file's rows prevent"]};

.qt.qcat.test_getdata_accepts_every_browsable_table:{[t]
    / The positive half, over the whole derived set: every table a desk may
    / browse - including a worker dataset no plant table declares - gets past
    / checktablename, which it did not when the file held five hand rows.
    t:(.qt.qcat.browsable[] except key[.qt.qcat.ledgers],.qt.qcat.bundled[]);
    .qunit.assertEquals[t where not `accepted~/:.qt.qcat.checked each t;`symbol$();
        "checktablename accepts each browsable table"]};

.qt.qcat.test_every_hidden_table_states_a_reason:{[t]
    / The reason is DATA rather than a comment precisely so this can require
    / one. A hidden table with an empty reason is "we forgot" wearing a
    / decision's clothes.
    .qunit.assertEquals[
        (key .qcat.hidden) where 0=count each value .qcat.hidden;
        `symbol$();
        "a hidden table says why it is hidden"]};

.qt.qcat.test_no_description_is_empty:{[t]
    .qunit.assertEquals[
        (key .qcat.describe) where 0=count each value .qcat.describe;
        `symbol$();
        "every described table has prose, not an empty string"]};

.qt.qcat.test_surface_excludes_every_hidden_table:{[t]
    / What the frontend actually receives.
    .qunit.assertEquals[
        (exec table from .qcat.surface[]) inter key .qcat.hidden;
        `symbol$();
        "surface[] never offers a hidden table"]};

.qt.qcat.test_surface_carries_the_columns_the_frontend_reads:{[t]
    .qunit.assertEquals[cols .qcat.surface[];`table`description;
        "surface[] is (table; description), which is what catalog.py unpacks"]};

/ --- every browsable table is bounded, or says why not (#889) -------------

/ The tables querypolicy.csv bounds: its tablename column, read as text.
.qt.qcat.policied:{[]
    ls:1_read0 `$":scripts/torqconfig/dataaccess/querypolicy.csv";
    distinct `$first each (enlist ",") vs/: ls where 0<count each ls}

.qt.qcat.test_every_browsable_table_has_a_policy_or_says_why_not:{[t]
    / The browser reads a whole select and only then truncates to max_rows;
    / the policy file's range, filters and byte limit bind gateway logins
    / only. A browsable table in neither place is read unbounded by omission.
    .qunit.assertEquals[
        (exec table from .qcat.surface[]) except .qt.qcat.policied[],key .qcat.unbounded;
        `symbol$();
        "each browsable table has a querypolicy.csv row or a .qcat.unbounded reason"]};

.qt.qcat.test_no_table_is_both_bounded_and_said_to_be_unbounded:{[t]
    / Assessing one moves it: a policy row added and its unbounded entry left
    / behind would leave a stale "not yet assessed" claim in the catalog.
    .qunit.assertEquals[.qt.qcat.policied[] inter key .qcat.unbounded;`symbol$();
        "a table with a policy row is not also in .qcat.unbounded"]};

.qt.qcat.test_every_unbounded_entry_is_browsable_and_states_a_reason:{[t]
    u:.qcat.unbounded;
    .qunit.assertEquals[(key u) except exec table from .qcat.surface[];`symbol$();
        ".qcat.unbounded names only browsable tables"];
    .qunit.assertEquals[(key u) where 0=count each value u;`symbol$();
        "each unbounded table says why"]};
