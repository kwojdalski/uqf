/ hdb_compression_report.q - what `uqs data compress --report` prints (#2149).
/ .
/ Run as:  q scripts/gates/hdb_compression_report.q <hdb root> <sort.csv> <top n> <torq home>
/ .
/ Loads TorQ's own .cmp (common/compress.q) and its sort.csv reader
/ (common/dbwriteutils.q, .sort.getsortcsv) against stand-ins for the .lg and
/ .proc.getconfigfile a TorQ process would give them, then uqf's wrapper, and
/ prints .qtorq.compress.report as one line - report|<json> - then DONE. q
/ exits 0 whatever happens while a script runs, so the caller believes DONE,
/ never the exit code. Reads only: nothing under the HDB is written.
/ .
/ The HDB's enumeration domains (its root sym file, and any other symbol list
/ at the root) are loaded first, as \l would, so a partition sorted by sym is
/ judged in the order the wdb sorted it.
args:.z.x;
if[4>count args; '"hdb_compression_report: usage - <hdb root> <sort.csv> <top n> <torq home>"];
hdb_root:hsym `$args 0;
sort_csv:hsym `$args 1;
top_n:"J"$args 2;
torq_home:args 3;
if[()~key hdb_root; '"hdb_compression_report: no HDB at ",1_string hdb_root];
if[()~key sort_csv; '"hdb_compression_report: no sort.csv at ",1_string sort_csv];

if[not `o in key `.lg; .lg.o:{[a;b]}];
if[not `e in key `.lg; .lg.e:{[a;b]}];
if[not `getconfigfile in key `.proc; .proc.getconfigfile:{[n] enlist `:none.csv}];
system"l ",torq_home,"/code/common/compress.q";
system"l ",torq_home,"/code/common/dbwriteutils.q";
system"l scripts/processes/torq_compress.q";
.sort.getsortcsv sort_csv;

/ A file at the root holding a symbol list is an enumeration domain.
domains:{[root]
    fs:` sv/:root,/:k where not (k:key root) like "*.*";
    fs where 11h={@[{type get x};x;0h]} each fs}
{[f] (last ` vs f) set get f} each domains hdb_root;

-1 "report|",.j.j .qtorq.compress.report[hdb_root;.sort.params;top_n];
-1 "DONE";
exit 0
