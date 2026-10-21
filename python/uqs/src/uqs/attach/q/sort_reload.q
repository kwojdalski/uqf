/ sort_reload.q - reload the process's TorQ sort rules from its own sort.csv (#1517).
/ .
/ Sent over IPC as one lambda by uqs.attach.sorting. TorQ reads sort.csv into
/ .sort.params once, at start; `.sort.getsortcsv` is its own loader, and calling it
/ again is the supported reload. It is handed the path the process is CONFIGURED
/ with (.wdb.sortcsv, .rdb.sortcsv, else .sort.defaultfile) - never a path from the
/ caller, so a reload cannot point the process at another file. getsortcsv parses
/ and validates the whole file before it replaces .sort.params, so a failed reload
/ leaves the rules the process had.
/ .
/ @param req JSON {"table": "name"} - the table whose rows the answer reports
/ @return JSON {"status": "reloaded"|"failed"|"unsupported", "detail", "path",
/   "rows": [{"att", "col", "sort"}, ...] for the table, "count": all rows}
{[req]
 r:.j.k $[10h=type req;req;string req];
 t:`$r`table;
 g:{@[value;x;y]};
 if[not @[{`getsortcsv in key x};`.sort;0b];
  :.j.j `status`detail`path`rows`count!("unsupported";"this process has no .sort.getsortcsv";"";();0)];
 f:g[`.wdb.sortcsv;g[`.rdb.sortcsv;g[`.sort.defaultfile;`]]];
 if[null f;
  :.j.j `status`detail`path`rows`count!("unsupported";"this process names no sort.csv";"";();0)];
 res:@[{.sort.getsortcsv x;`ok};f;{x}];
 sp:g[`.sort.params;([]tabname:`$();att:`$();column:`$();sort:`boolean$())];
 own:select att:string att,col:string column,sort from sp where tabname=t;
 .j.j `status`detail`path`rows`count!($[`ok~res;"reloaded";"failed"];$[`ok~res;"";res];string f;own;count sp)}
