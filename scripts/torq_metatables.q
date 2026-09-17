/ Optional TorQ DQE adapter. Load after src/metadata/metatables.q on the
/ DQE process and each source HDB. DQE transports this lambda to the source.
/ No timers, handlers, connections or storage are installed by this file.
\d .dqe

/ Collect one named metatable in DQE's advanced-result dictionary format.
/ @param name resultkeys identifier, unique per definition
/ @param tab source table name (also used by DQE's parameter introspection)
/ @param partition_col explicit partition column
/ @param partitions explicit typed vector of slices
/ @param group_cols additional eFX dimensions, e.g. sym and venue
/ @param aggregates functional qSQL aggregates; empty dictionary defaults to count
/ @return name mapped to an unkeyed metatable, for DQE advancedres
uqf_metatable:{[name;tab;partition_col;partitions;group_cols;aggregates]
    if[not -11h=type name;'"metatables: result name must be a symbol atom"];
    if[null name;'"metatables: result name must be nonempty"];
    spec:.qmeta.definition[tab;partition_col;group_cols;aggregates];
    enlist[name]!enlist .qmeta.collect[spec;partitions]};

\d .

/ Plain q remains supported; TorQ advertises the adapter when its API registry exists.
if[`api in key `.;
    .api.add[`.dqe.uqf_metatable;1b;"Collect a bounded eFX partition metatable";
        "name;tab;partition_col;partitions;group_cols;aggregates";"dictionary of name to metatable"]];
