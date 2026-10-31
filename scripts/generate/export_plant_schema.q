/ export_plant_schema.q - print, as JSON, every plant table's declared schema (#1781).
/ .
/ uqs reads which tables the plant carries, their columns in order, each
/ column's q type and attribute, and each nested column's element type. It
/ used to find them by matching `name:([]...)` in plant_tables.q's TEXT, so
/ `name: ([]...)` - which q loads the same - silently lost the table. Here q
/ evaluates the definitions and reports what it holds; uqs.model.plant_metadata
/ runs this, writes src/etl/generated/plant_schema.json from it and reads that.
/ .
/ It loads src/etl/plant_tables.q and nothing else: no init.q, no feed, no
/ worker, no port. That file, in turn, reads the vendored starter pack's
/ database.q ($TORQAPPHOME's when set) - trusted tree code, both of them.
/ A bundle's tables.q is evaluated here only once it is INSTALLED into
/ plant_tables.q; one being inspected before install is never run (see
/ stack/bundles.py, which validates it as text).
/ .
/ Run from the tree's root: q <this file> -q

\l src/etl/plant_tables.q

/ The plant's own keyed registries, which are not tables a process holds.
.qschema.registries:`elements`sort_rules;

/ Keyed tables bound in .qetl.plant other than the registries. A tickerplant
/ table is unkeyed, so one of these is refused rather than dropped in silence.
.qschema.keyed:{[]
    n:(key `.qetl.plant) except .qschema.registries;
    n where {[x] v:get ` sv `.qetl.plant,x; $[99h=type v; 98h=type key v; 0b]} each n}

/ One table's declared schema: ordered columns with type char and attribute,
/ the nested columns' declared element types, and the declared sort rule.
.qschema.table:{[t]
    m:0!meta .qetl.plant.schema t;
    nested:exec column!element from .qetl.plant.elements where table=t;
    sorting:exec flip (string column; string att) from .qetl.plant.sort_rules where table=t;
    `name`vendored`columns`nested`sorting!(
        string t;
        t in .qetl.plant.vendored;
        {[c;ty;a] `name`type`attribute!(string c; enlist ty; string a)}'[m`c; m`t; m`a];
        (key nested)!enlist each value nested;
        sorting)}

if[count k:.qschema.keyed[];
    -2 "export_plant_schema: keyed table(s) ",(", " sv string k)," in .qetl.plant - a plant table is unkeyed; the schema metadata does not carry keyed tables";
    exit 1];

/ Every other name plant_tables.q binds in .qetl.plant - its API and its
/ registries - which a table may not take (#1597): `uqs job new` refuses them.
.qschema.bindings:{[] string asc ((key `.qetl.plant) except `) except .qetl.plant.names[]}

-1 .j.j `tables`bindings!(.qschema.table each .qetl.plant.names[]; .qschema.bindings[]);
exit 0
