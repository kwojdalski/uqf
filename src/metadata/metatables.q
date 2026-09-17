/ Declarative metatables for partitioned eFX data. Pure bounded queries and
/ partition replacement; TorQ DQE owns scheduling, transport and persistence.
/ Definitions are trusted q code, not a query language for untrusted clients.
/ .
\d .qmeta

/ Construct a metatable definition; an empty aggregate dictionary means row counts.
/ @param tab source table name
/ @param partition_col physical or logical partition column
/ @param group_cols symbol vector of additional grouping columns
/ @param aggregates dictionary of output names to functional qSQL aggregate expressions
/ @return definition dictionary
/ @throws invalid names, duplicate columns or reserved output names
/ @eg .qmeta.definition[`trade;`date;`sym`venue;()!()]
definition:{[tab;partition_col;group_cols;aggregates]
    if[not -11h=type tab;'"metatables: table must be a symbol atom"];
    if[null tab;'"metatables: table must be named"];
    if[not -11h=type partition_col;'"metatables: partition column must be a symbol atom"];
    if[null partition_col;'"metatables: partition column must be named"];
    if[not 11h=type group_cols;'"metatables: group columns must be a symbol vector"];
    if[(any null group_cols) or not group_cols~distinct group_cols;
        '"metatables: group columns must be named and unique"];
    if[partition_col in group_cols;'"metatables: partition column is already included"];
    if[not 99h=type aggregates;'"metatables: aggregates must be a dictionary"];
    if[0=count aggregates;aggregates:enlist[`rows]!enlist(count;`i)];
    names:key aggregates;
    if[not 11h=type names;'"metatables: aggregate names must be symbols"];
    if[(any null names) or not names~distinct names;
        '"metatables: aggregate names must be named and unique"];
    if[any names in partition_col,group_cols;
        '"metatables: aggregate names collide with grouping columns"];
    if[`meta_observed_at in partition_col,group_cols,names;
        '"metatables: meta_observed_at is reserved"];
    `table`partition_col`group_cols`aggregates!(tab;partition_col;group_cols;aggregates)};

/ Private: validate a definition against current source metadata and normalize partitions.
require_request:{[spec;partitions]
    if[not 99h=type spec;'"metatables: definition must be a dictionary"];
    fields:`table`partition_col`group_cols`aggregates;
    if[not fields~key spec;'"metatables: use definition to construct the specification"];
    definition . spec fields;
    if[not (type partitions) in 6 7 11 13 14h;
        '"metatables: partitions must be an int, long, symbol, month or date vector"];
    if[0=count partitions;'"metatables: explicit nonempty partitions required"];
    if[any null partitions;'"metatables: null partitions are not allowed"];
    source_meta:meta spec`table;
    required:(spec`partition_col),spec`group_cols;
    if[not all required in exec c from source_meta;
        '"metatables: source is missing a partition or grouping column"];
    expected:first exec t from source_meta where c=spec`partition_col;
    if[not expected~.Q.t type partitions;
        '"metatables: partition type does not match source"];
    distinct partitions};

/ Private: query a single partition; preserve typed empty grouped results.
collect_partition:{[spec;part;observed_at]
    grouping:spec`group_cols;
    by_expr:$[count grouping;grouping!grouping;0b];
    predicate:enlist(=;spec`partition_col;$[-11h=type part;enlist part;part]);
    result:0!?[spec`table;predicate;by_expr;spec`aggregates];
    if[(0=count grouping) and 1<>count result;
        '"metatables: ungrouped aggregates must produce one row"];
    / An aggregate must produce a scalar per group, not a nested raw column.
    if[any 0h=type each flip result;
        '"metatables: aggregates must produce scalar columns"];
    result:flip ((enlist spec`partition_col)!enlist(count result)#part),flip result;
    result:flip (flip result),(enlist`meta_observed_at)!enlist(count result)#observed_at;
    result};

/ Collect exact measurements for explicit partitions, without changing source or stored metadata.
/ Ungrouped empty slices have rows=0; grouped empty slices have no groups.
/ A zero count is not proof that a physical partition exists or is complete.
/ @param spec definition returned by definition
/ @param partitions nonempty typed vector; duplicate partitions are measured once
/ @return unkeyed table: partition, grouping columns, aggregates, UTC meta_observed_at
/ @throws malformed request, missing source columns, query or aggregate errors
/ @eg .qmeta.collect[.qmeta.definition[`trade;`date;`symbol$();()!()];enlist 2026.09.01]
collect:{[spec;partitions]
    partitions:require_request[spec;partitions];
    raze collect_partition[spec;;.z.p]each partitions};

/ Replace complete requested slices of a metatable, including groups which disappeared.
/ Returns a new table only after every query succeeds. Assign or persist it at the caller.
/ @param current prior unkeyed result of collect using this same definition
/ @param spec unchanged definition (use a new metatable when changing definitions)
/ @param partitions explicit slices to recompute
/ @return replacement metatable; other partitions retain their original observations
/ @throws source/query failure or incompatible stored schema, leaving current untouched
/ @eg .qmeta.refresh[stored;spec;enlist 2026.09.01]
refresh:{[current;spec;partitions]
    if[not 98h=type current;'"metatables: current must be an unkeyed table"];
    replacement:collect[spec;partitions];
    if[not (0#current)~0#replacement;
        '"metatables: stored schema differs; rebuild after definition changes"];
    retained:?[current;enlist(not;(in;spec`partition_col;enlist partitions));0b;()];
    retained,replacement};

/ Private: temporal bounds preserve typed nulls for empty/all-null slices.
time_bound:{[direction;values]
    if[not (type values) in 12 13 14 15 16 17 18 19h;
        '"metatables: time range columns must be temporal vectors"];
    present:values where not null values;
    $[count present;direction present;first 0#values]};

/ Count rows matching a quality violation expression; true means a bad row.
/ @param violations boolean vector, one value per source row
/ @param row_count number of rows in the source group
/ @return long violation count
/ @throws non-boolean, scalar or wrong-length rule result
/ @eg .qmeta.count_bad[101b;3] -> 2
count_bad:{[violations;row_count]
    if[not 1h=type violations;'"metatables: quality rules must return boolean vectors"];
    if[row_count<>count violations;
        '"metatables: quality rules must return one boolean per source row"];
    `long$sum violations};

/ Build profiling aggregates usable by definition and the TorQ DQE adapter.
/ @param time_cols temporal columns to measure with min_ and max_ outputs
/ @param null_cols columns to measure with null_ counts using q null semantics
/ @param rules dictionary of rule names to boolean violation expressions (true means bad)
/ @return aggregate dictionary: rows, temporal bounds, null counts, bad_ rule counts
/ @throws unnamed or duplicate column/rule names, malformed lists or dictionary
/ @eg .qmeta.profile[enlist`time;`bid`ask;enlist[`crossed]!enlist(>;`bid;`ask)]
profile:{[time_cols;null_cols;rules]
    if[not all 11h=type each (time_cols;null_cols);
        '"metatables: profile columns must be symbol vectors"];
    if[(any null time_cols,null_cols) or not (time_cols;null_cols)~distinct each (time_cols;null_cols);
        '"metatables: profile columns must be named and unique within each list"];
    if[not 99h=type rules;'"metatables: rules must be a dictionary"];
    rule_names:key rules;
    if[count rules;
        if[not 11h=type rule_names;'"metatables: rule names must be symbols"];
        if[(any null rule_names) or not rule_names~distinct rule_names;
            '"metatables: rule names must be named and unique"]];
    metrics:enlist[`rows]!enlist(count;`i);
    idx:0;
    while[idx<count time_cols;
        col:time_cols idx;
        metrics,:(enlist `$"min_",string col)!enlist(.qmeta.time_bound;min;col);
        metrics,:(enlist `$"max_",string col)!enlist(.qmeta.time_bound;max;col);
        idx+:1];
    idx:0;
    while[idx<count null_cols;
        col:null_cols idx;
        metrics,:(enlist `$"null_",string col)!enlist($;enlist`long;(sum;(null;col)));
        idx+:1];
    idx:0;
    while[idx<count rules;
        rule_name:rule_names idx;
        metrics,:(enlist `$"bad_",string rule_name)!enlist(.qmeta.count_bad;rules rule_name;(count;`i));
        idx+:1];
    metrics};

\d .
