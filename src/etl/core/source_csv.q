/ source_csv.q - the csv transport: a source that is a delimited text file.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q. The transport's row is
/ registered in source_transports.q; this file holds the operations it points at.
/ .
/ WHAT THE HANDLE IS. The credential is the path of the file, and the handle is
/ that path as a file symbol: a file has nothing to connect to, so `open` only
/ proves the path is a file (a missing one fails at connect, not at the first
/ window) and `close` has nothing to release. A source's query reads the whole
/ file for each window and cuts the window itself; a fills drop is small, and a
/ file that is not is the case for the odbc transport, not for a bigger reader.
/ .
/ WHAT THE FILE MAY CONTAIN. A header line, then one row per line, comma
/ separated, every field read as TEXT. Quoted fields are NOT supported: q's own
/ reader splits on every comma, so a quoted comma would silently shift every
/ column to its right. A row with the wrong number of fields is refused for that
/ reason (csv_rows), not read short. Typing the text is the source's adapter's,
/ through .qetl.coerce, because a per-source policy (case, a decimal comma,
/ which zone) is the adapter's to own (source_contract.q).

\d .

/ A csv source's file, from its credential.
/ @param cred the path of the file, a string
/ @return the path as a file symbol
/ @throws error when the credential is not the path of a file that exists
.qetl.source.csv_path:{[cred]
    if[0=count cred; '"csv: the credential is the path of a csv file"];
    p:hsym `$cred;
    / key of a file is the file itself; of a directory its entries; of a missing path, empty
    if[not p~key p; '"csv: ",cred," is not a file that exists"];
    p}

/ The column names a file's lines open with.
/ @param h the file, as a file symbol, for the message
/ @param lines the file's lines
/ @return a symbol vector, one name per header field
/ @throws error when there are no lines
/ @private
.qetl.source.csv_names:{[h;lines]
    if[0=count lines; '"csv: ",(1_string h)," is empty - a csv source needs a header line"];
    `$trim each "," vs (first .qetl.source.unbom lines) except "\r"}

/ A file's lines without the UTF-8 byte order mark Excel's "CSV UTF-8" opens with:
/ left on, it is part of the first column's name, and that column reads as absent (#2550).
/ @param lines a file's lines, as read0 returns them
/ @return the lines, the first without a leading BOM
/ @eg .qetl.source.unbom ("\357\273\277a,b";"1,2")  ->  ("a,b";"1,2")
.qetl.source.unbom:{[lines]
    if[0=count lines; :lines];
    $["\357\273\277"~3#first lines; @[lines;0;3_]; lines]}

/ The first line of a file, split into its column names.
/ @param h the file, as a file symbol
/ @return a symbol vector, one name per header field
/ @throws error when the file is empty
.qetl.source.csv_header:{[h] .qetl.source.csv_names[h;read0 h]}

/ A csv file's columns for the live check: each header field, typed general.
/ @param h the file, as a file symbol
/ @return ([] c:symbols; t:chars), one row per header field
.qetl.source.csv_meta:{[h]
    c:.qetl.source.csv_header[h];
    ([] c:c; t:(count c)#" ")}

/ Every row of a csv file as text.
/ @param h the file, as a file symbol
/ @return a table of strings, one column per header field, one row per data line
/ @throws error naming the first line whose field count differs from the header's
.qetl.source.csv_rows:{[h]
    all_lines:read0 h;
    c:.qetl.source.csv_names[h;all_lines];
    lines:1_ all_lines;
    / each kept line's number in the file, so a blank line skipped above a bad row
    / does not shift the line it is named by (#2404)
    kept:where 0<count each trim each lines;
    lines:lines kept;
    n:count c;
    width:1+sum each lines="," ;
    bad:where width<>n;
    if[count bad;
        '"csv: ",(1_string h)," line ",string[2+kept first bad]," has ",string[width first bad]," fields, the header has ",string[n],
         " (a quoted comma is not supported)"];
    flip c!flip {x except "\r"} each/: "," vs/: lines}

/ Send a csv source's query: read the file's rows and hand them to `f`, logged at TRACE like
/ .qetl.source.local, so `uqs job backfill --trace` shows what a csv source is asked.
/ @param h the file, as a file symbol
/ @param f a function taking (rows; range_from; range_to), rows as .qetl.source.csv_rows returns them
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return what `f` returns
.qetl.source.csv:{[h;f;range_from;range_to]
    rows:.qetl.source.csv_rows[h];
    if[not .qetl.log.enabled`TRACE; :f[rows;range_from;range_to]];
    t0:.z.p;
    req:`transport`request!(`csv;.qetl.log.next_request[]);
    .[{.qetl.log.ch.trc[x;y;z]};(`source;"query sent";
        (enlist[`call]!enlist .qetl.source.call_text f),req,`range_from`range_to!(range_from;range_to));::];
    r:.[f;(rows;range_from;range_to);{[req;t0;e]
        .[{.qetl.log.ch.trc[x;y;z]};(`source;"query failed";
            req,`error`ms!(e;`long$(.z.p-t0)%1000000));::];
        'e}[req;t0]];
    .[{.qetl.log.ch.trc[x;y;z]};(`source;"query returned";
        req,`rows`ms!(count r;`long$(.z.p-t0)%1000000));::];
    r}

\d .
