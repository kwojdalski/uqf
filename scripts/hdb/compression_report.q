/ compression_report.q - what `uqs data compress --report` prints (#2149).
/ .
/ Run as:  q scripts/hdb/compression_report.q <hdb root> <sort.csv> <top n> <torq home>
/ .
/ <sort.csv> is `-` when there is none to judge by (#2219): an HDB named by --hdb
/ on a target has no sort.csv this side can know, and the local runtime's is not
/ that HDB's. Sorting is then reported as not checked (sort_checked 0b), never as
/ found valid. Run from the directory holding scripts/processes/torq_compress.q:
/ the repository here, the helper's temporary copy on a target
/ (python/uqs/src/uqs/stack/hdb_compression.py).
/ .
/ Loads TorQ's own .cmp (common/compress.q) and its sort.csv reader
/ (common/dbwriteutils.q, .sort.getsortcsv) against stand-ins for the .lg and
/ .proc.getconfigfile a TorQ process would give them, then uqf's wrapper, and
/ prints .qtorq.compress.report as one line - report|<json> - then DONE. q
/ exits 0 whatever happens while a script runs, so the caller believes DONE,
/ never the exit code. A refusal - no HDB, a file or directory q may not read
/ ("cannot read <file>: <q's error>", the file found by unreadable) - is one line, error|<why>,
/ so the caller can say what to fix. Reads only: nothing under the HDB is written.
/ .
/ The HDB's enumeration domains (its root sym file, and any other symbol list
/ at the root) are loaded first, as \l would, so a partition sorted by sym is
/ judged in the order the wdb sorted it.
fail:{[why] -1 "error|",why; exit 1};
args:.z.x;
if[4>count args; fail "usage - <hdb root> <sort.csv or -> <top n> <torq home>"];
hdb_root:hsym `$args 0;
sort_checked:not (enlist "-")~args 1;
sort_csv:hsym `$args 1;
top_n:"J"$args 2;
torq_home:args 3;
if[()~listing:@[key;hdb_root;{[e] fail "cannot read ",(1_string hdb_root),": ",e}]; fail "no HDB at ",1_string hdb_root];
if[-11h=type listing; fail (1_string hdb_root)," is a file, not an HDB directory"];
if[sort_checked; if[()~key sort_csv; fail "no sort.csv at ",1_string sort_csv]];

if[not `o in key `.lg; .lg.o:{[a;b]}];
if[not `e in key `.lg; .lg.e:{[a;b]}];
if[not `getconfigfile in key `.proc; .proc.getconfigfile:{[n] enlist `:none.csv}];
system"l ",torq_home,"/code/common/compress.q";
system"l ",torq_home,"/code/common/dbwriteutils.q";
system"l scripts/processes/torq_compress.q";
if[sort_checked; @[.sort.getsortcsv;sort_csv;{[e] fail "cannot read sort.csv: ",e}]];

/ A file at the root holding a symbol list is an enumeration domain.
domains:{[root]
    fs:` sv/:root,/:k where not (k:key root) like "*.*";
    fs where 11h={@[{type get x};x;0h]} each fs}
@[{[r] {[f] (last ` vs f) set get f} each domains r};hdb_root;{[e] fail "cannot read the HDB's sym file: ",e}];

/ The first path under p that q cannot list or read, or ` for none. Asked only
/ once the report has failed, to name the file: KDB-X's error does ("<file>. OS
/ reports: Permission denied"), PeachQ's ('io) does not (#2281).
unreadable:{[p]
    k:@[key;p;{[e] e}];
    if[10h=type k; :p];
    if[-11h=type k; :$[@[{read1 (x;0;1); 1b};p;{[e] 0b}];`;p]];
    r:`; i:0;
    while[(null r) and i<count k; r:unreadable ` sv p,k i; i+:1];
    r}
refuse:{[e] f:unreadable hdb_root; fail $[null f; e; "cannot read ",(1_string f),": ",e]};
r:@[{[x] .qtorq.compress.report[hdb_root;.sort.params;top_n]};::;refuse];
-1 "report|",.j.j r,`sort_checked`sort_csv!(sort_checked;$[sort_checked;1_string sort_csv;::]);
-1 "DONE";
exit 0
