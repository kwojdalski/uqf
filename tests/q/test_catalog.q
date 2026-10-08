// test_catalog.q - the desk catalog's authored half in
// scripts/processes/uqs_catalog.q (.cattest).
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

\d .cattest

/ Both files, loaded here rather than in run_tests.q's list: nothing else
/ needs .qcat in scope, and plant_tables.q defines twenty-odd top-level tables
/ that no other suite wants in the root namespace. Same reasoning as
/ test_stack_tables.q, which loads the same table file for the same reason.
beforeNamespace_load:{[]
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
published:{[]
    ls:read0 `$":src/etl/plant_tables.q";
    / `name:([]` exactly, as uqs and .qetl.plant.adopt_vendored read it: a
    / looser "*:(*" also caught the keyed `elements:([table:...]...)` lookup.
    ls:ls where (not ls like "/*") and {[l] (0<count l) and "([]"~3#(1+l?":")_l} each ls;
    asc `$ {x til x?":"} each ls}

test_every_published_table_is_described_or_hidden:{[t]
    / The gate. A table the tickerplant carries that nobody has described is
    / invisible to a desk, and invisible for no stated reason - which is how
    / a capability ships and nobody can find it.
    .qunit.assertEquals[
        .cattest.published[] except (key .qcat.describe),key .qcat.hidden;
        `symbol$();
        "every table in plant_tables.q is either described or explicitly hidden"]};

test_no_table_is_both_described_and_hidden:{[t]
    / Both lists would be a contradiction the surface resolves silently in
    / hidden's favour. Better to refuse it than to pick.
    .qunit.assertEquals[(key .qcat.describe) inter key .qcat.hidden;
        `symbol$();
        "a table is described or hidden, never both"]};

/ Described tables whose declaration lives somewhere other than
/ plant_tables.q, and the file that owns each.
/ .
/ Carried over from the drift test's _Q_OWNED, because the fact survived the
/ test that recorded it: plant_tables.q holds what the tickerplant is
/ configured to carry, and these three are declared by the ETL tree instead -
/ the coverage ledger by the framework, the other two by their source
/ contracts. A desk browses all three, so they belong in the catalog; they
/ are simply not declared where the others are.
elsewhere:`etl_coverage`demo_deals`event_tape!(
    "src/etl/core/materialisation.q";
    "src/etl/sources/demo_deals.q";
    "src/etl/sources/demo_events.q")

test_the_catalog_describes_no_table_that_does_not_exist:{[t]
    / The other direction. An entry for a table nothing declares exposes
    / nothing - the frontend browses the INTERSECTION of this and the live
    / meta - but it is either a typo or a table somebody forgot to publish,
    / and both are worth one line of thought.
    .qunit.assertEquals[
        (key .qcat.describe) except .cattest.published[],key .cattest.elsewhere;
        `symbol$();
        "every described table is declared somewhere in this tree"]};

test_each_elsewhere_table_is_declared_where_it_claims:{[t]
    / Without this, `elsewhere` is an exemption list that grows whenever the
    / check above is inconvenient - which is the shape this repository keeps
    / finding and deleting. Reading the named file means a table that MOVED
    / fails here rather than sitting exempt from both directions forever.
    missing:(key .cattest.elsewhere) where not
        {[nm;path] any (read0 hsym `$path) like "*",(string nm),"*"}'
            [key .cattest.elsewhere; value .cattest.elsewhere];
    .qunit.assertEquals[missing;`symbol$();
        "each table in `elsewhere` is named by the file said to declare it"]};

test_every_hidden_table_states_a_reason:{[t]
    / The reason is DATA rather than a comment precisely so this can require
    / one. A hidden table with an empty reason is "we forgot" wearing a
    / decision's clothes.
    .qunit.assertEquals[
        (key .qcat.hidden) where 0=count each value .qcat.hidden;
        `symbol$();
        "a hidden table says why it is hidden"]};

test_no_description_is_empty:{[t]
    .qunit.assertEquals[
        (key .qcat.describe) where 0=count each value .qcat.describe;
        `symbol$();
        "every described table has prose, not an empty string"]};

test_surface_excludes_every_hidden_table:{[t]
    / What the frontend actually receives.
    .qunit.assertEquals[
        (exec table from .qcat.surface[]) inter key .qcat.hidden;
        `symbol$();
        "surface[] never offers a hidden table"]};

test_surface_carries_the_columns_the_frontend_reads:{[t]
    .qunit.assertEquals[cols .qcat.surface[];`table`description;
        "surface[] is (table; description), which is what catalog.py unpacks"]};

/ --- every browsable table is bounded, or says why not (#889) -------------

/ The tables querypolicy.csv bounds: its tablename column, read as text.
policied:{[]
    ls:1_read0 `$":scripts/torqconfig/dataaccess/querypolicy.csv";
    distinct `$first each (enlist ",") vs/: ls where 0<count each ls}

test_every_browsable_table_has_a_policy_or_says_why_not:{[t]
    / The browser reads a whole select and only then truncates to max_rows;
    / the policy file's range, filters and byte limit bind gateway logins
    / only. A browsable table in neither place is read unbounded by omission.
    .qunit.assertEquals[
        (exec table from .qcat.surface[]) except policied[],key .qcat.unbounded;
        `symbol$();
        "each browsable table has a querypolicy.csv row or a .qcat.unbounded reason"]};

test_no_table_is_both_bounded_and_said_to_be_unbounded:{[t]
    / Assessing one moves it: a policy row added and its unbounded entry left
    / behind would leave a stale "not yet assessed" claim in the catalog.
    .qunit.assertEquals[policied[] inter key .qcat.unbounded;`symbol$();
        "a table with a policy row is not also in .qcat.unbounded"]};

test_every_unbounded_entry_is_browsable_and_states_a_reason:{[t]
    u:.qcat.unbounded;
    .qunit.assertEquals[(key u) except exec table from .qcat.surface[];`symbol$();
        ".qcat.unbounded names only browsable tables"];
    .qunit.assertEquals[(key u) where 0=count each value u;`symbol$();
        "each unbounded table says why"]};
