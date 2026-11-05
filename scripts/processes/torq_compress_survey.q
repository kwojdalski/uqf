/ torq_compress_survey.q - what `uqs data compress` asks of TorQ's .cmp (#2147, #2148).
/ .
/ No compression here. Every file is planned, compressed, checked and reloaded by
/ .qtorq.compress (torq_compress.q), which is TorQ's .cmp unchanged plus a read-back
/ check. What this adds is the SCOPE of one run, and it says it in .cmp's own
/ language - the rows of a compressionconfig.csv - so that the plan a person reads
/ and the run that follows it are the same .cmp call over the same file:
/ .
/   --table T       every table that is not T gets a minage no partition reaches
/   --older-than D  every row's minage is at least D days
/   being written   a table the coverage ledger says is still being written
/                   (.qetl.coverage.unreleased: rows written that its readers have
/                   not been released to see) is held below its oldest such day -
/                   that day and every newer one are left for a later run
/   one partition   minage and maxage both its age, which .cmp's
/                   `age within (minage;maxage)` reads as that partition alone
/ .
/ A table without a `default` column row of its own - one that takes the stack's
/ `default` row, for all its columns or for those it does not name - is given a copy
/ of that row under its own name before any of that, so the default can be shut
/ without shutting the table. .cmp reads the two the same way.
/ .
/ survey adds the estimate: the newest planned partition of each table, its first
/ `n` rows of each column copied (uncompressed) into a temporary directory, and
/ .qtorq.compress.apply run over that copy with the same scoped config - so the
/ ratio is the one .cmp itself reaches, not one predicted here.
/ .
/ WHERE IT RUNS. housekeeping1 - TorQ's own HDB-maintenance process, which loads
/ .cmp with the rest of TorQ's common code - loads it after torq_compress.q
/ (VENDORED_LOAD_OVERLAY in python/uqs/src/uqs/stack/procs.py), and `uqs data
/ compress` calls it over IPC. It needs .qetl.coverage (src/etl/core/
/ materialisation.q and what it loads) for the hold.

\d .

/ The day .cmp ages partitions from: its hdbstructure takes .z.D, the local date, so
/ a scope that is to agree with .cmp's plan takes the same one.
.qtorq.compress.today:{[] .z.D}

/ A minage no partition reaches: the age of a partition 2,700 years old.
.qtorq.compress.never:1000000i

/ Private: TorQ's config, read and checked by .cmp itself (.cmp.loadcsv).
/ @private
.qtorq.compress.config:{[conf]
    .qtorq.compress.require_cmp[];
    .cmp.loadcsv $[10h=type conf; hsym `$conf; hsym conf];
    .cmp.compressioncsv}

/ Private: a partition's age as .cmp.hdbstructure reckons it - days for a date,
/ months for a month.
/ @private
.qtorq.compress.age_of:{[part]
    s:$[10h=type part; part; string part];
    if[not null d:"D"$s; :.qtorq.compress.today[]-d];
    if[not null m:"M"$s; :(`month$.qtorq.compress.today[])-m];
    '"qtorq.compress: ",s," is not a date or month partition"}

/ The days each table is still being written to, oldest first: the coverage
/ ledger's unreleased claims (.qetl.coverage.unreleased).
/ @param at the as-of
/ @return dict table -> the oldest date still being written
.qtorq.compress.held:{[at]
    u:.qetl.coverage.unreleased[at];
    exec min date by dataset from u}

/ TorQ's config rows scoped to one run: only `tables` (all when empty), only
/ partitions at least `older` days old, and each held table only below its
/ oldest day still being written.
/ @param c TorQ's config rows (.qtorq.compress.config)
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param held dict table -> the oldest date still being written
/ @return the config rows, in .cmp's columns
.qtorq.compress.scope:{[c;only;older;held]
    only:(),only;
    named:distinct only,key held;
    own:exec distinct table from c where table<>`default, column=`default;
    d:select from c where table=`default, column=`default;
    if[count add:(named except own) except `default;
        c:c,raze {[d;t] update table:t from d}[d] each add];
    if[count only; c:update minage:.qtorq.compress.never from c where not table in only];
    c:update minage:minage|`int$older from c;
    if[count held;
        below:`int$1+.qtorq.compress.today[]-value held;
        c:update minage:minage|((key held)!below) table from c where table in key held];
    c}

/ Private: write config rows where .cmp reads them, in a directory of their own.
/ @private
.qtorq.compress.write_conf:{[dir;c]
    f:` sv dir,`compressionconfig.csv;
    f 0: csv 0: `table`minage`column`calgo`cblocksize`clevel#c;
    f}

/ Private: a temporary directory, as a file symbol.
/ @private
.qtorq.compress.tmpdir:{[] hsym `$first system "mktemp -d"}

/ Private: remove a temporary directory made by tmpdir.
/ @private
.qtorq.compress.rmdir:{[dir] system "rm -rf '",(1_string dir),"'"; }

/ Private: f[dir] in a fresh temporary directory, removed whether or not f throws.
/ @private
.qtorq.compress.in_tmp:{[f]
    dir:.qtorq.compress.tmpdir[];
    r:@[{[f;dir] (`ok;f dir)}[f];dir;{[e] (`err;e)}];
    .qtorq.compress.rmdir[dir];
    if[`err~first r; 'last r];
    last r}

/ Private: copy the first n rows of one table partition's planned columns into
/ root, uncompressed whatever .z.zd says, with a .d naming them.
/ @private
.qtorq.compress.copy_sample:{[root;n;rows]
    d:` sv root,(`$string first rows`partition),first rows`table;
    {[d;n;f;c] ((` sv d,c);17;0;0) set n sublist get f}[d;n]'[rows`fullpath;rows`column];
    (` sv d,`.d) set rows`column;
    n&count get first rows`fullpath}

/ Private: the compression .cmp reaches on a sample of each table: its newest
/ partition with a column to compress, n rows of each, through .qtorq.compress.apply.
/ @private
.qtorq.compress.estimate:{[c;todo;n]
    empty:([] table:`symbol$(); column:`symbol$(); bytes_before:`long$(); bytes_after:`long$());
    todo:select from todo where action=`compress;
    if[0=count todo; :(empty;([] table:`symbol$(); partition:(); rows:`long$()))];
    newest:select from todo where age=(min;age) fby table;
    .qtorq.compress.in_tmp {[c;n;newest;dir]
        root:` sv dir,`hdb;
        parts:0!select fullpath, column by table, partition from newest;
        rows:{[root;n;r] .qtorq.compress.copy_sample[root;n;flip `fullpath`column`table`partition!(r`fullpath;r`column;count[r`column]#r`table;count[r`column]#enlist r`partition)]}[root;n] each parts;
        conf:.qtorq.compress.write_conf[dir;c];
        r:.qtorq.compress.apply[root;conf;0W];
        per:select bytes_before:sum bytes_before, bytes_after:sum bytes_after by table, column from r;
        (0!per;([] table:parts`table; partition:parts`partition; rows:"j"$rows))
        }[c;n;newest]}

/ Private: a file's bytes once uncompressed, from its -21! header.
/ @private
.qtorq.compress.plain_bytes:{[f] h:-21!f; $[count h; h`uncompressedLength; hcount f]}

/ Everything `uqs data compress` shows before it changes anything: TorQ's config,
/ what the coverage ledger holds back, and per table partition the columns .cmp
/ would compress, decompress or leave, with today's bytes and the estimate.
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path (.cmp.inputcsv)
/ @param maxage the oldest partition to consider, in days (0W for all)
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param n the rows of each column the estimate compresses
/ @return dict: config (TorQ's rows), held (dataset, date still being written),
/   partitions (table, partition, age, dir, status - `act, or `held or `deferred
/   behind a day still being written - compress, decompress, skip, compressed:
/   column lists, files, bytes_now, bytes_after: today's and the estimate,
/   bytes_written: the estimate of the files .cmp writes anew), and
/   sample (table, partition, rows) and ratios (table, column, bytes_before,
/   bytes_after) the estimate came from
/ @throws error when .cmp is not loaded or refuses the config, or the ledger cannot be read
.qtorq.compress.survey:{[hdb;conf;maxage;only;older;n]
    c:.qtorq.compress.config[conf];
    u:.qetl.coverage.unreleased[.z.p];
    h:exec min date by dataset from u;
    open:.qtorq.compress.scope[c;only;older;()!()];
    p:.qtorq.compress.in_tmp {[hdb;maxage;open;dir] .qtorq.compress.plan[hdb;.qtorq.compress.write_conf[dir;open];maxage]}[hdb;maxage;open];
    below:(key h)!.qtorq.compress.today[]-value h;
    p:update status:`act from p;
    p:update status:?[([] dataset:table; date:partition) in u;`held;`deferred] from p where table in key below, age<=below table;
    est:.qtorq.compress.estimate[.qtorq.compress.scope[c;only;older;h];select from p where status=`act;n];
    p:p lj `table`column xkey select table, column, r:bytes_after%bytes_before from est 0;
    p:p lj select tr:(sum bytes_after)%sum bytes_before by table from est 0;
    p:update after:currentsize from p;
    p:update after:"j"$currentsize*1f^tr^r from p where action=`compress;
    p:update after:.qtorq.compress.plain_bytes each fullpath from p where action=`decompress;
    parts:0!select dir:{"/" sv -1_"/" vs 1_string first x} fullpath,
        compress:column where action=`compress, decompress:column where action=`decompress,
        skip:column where action=`skip, compressed:column where compressed,
        files:count i, bytes_now:sum currentsize, bytes_after:sum after,
        bytes_written:sum after where action<>`skip
        by table, partition, age, status from p;
    `config`held`partitions`sample`ratios!(c;u;`partition xasc parts;est 1;est 0)}

/ Run one partition of the scoped config: .qtorq.compress.run (TorQ's .cmp, the
/ check, then the HDB reload) over exactly that partition - every row's minage and
/ the maxage its age. A day still being written is never reached.
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param part the partition, a date or month as a string
/ @return what .qtorq.compress.run returns: one row per file changed
/ @throws what .qtorq.compress.run throws - a named error when a file did not reach its target state
.qtorq.compress.run_partition:{[hdb;conf;only;older;part]
    a:.qtorq.compress.age_of[part];
    c:.qtorq.compress.scope[.qtorq.compress.config[conf];only;older;.qtorq.compress.held[.z.p]];
    c:update minage:minage|`int$a from c;
    .qtorq.compress.in_tmp {[hdb;a;c;dir] .qtorq.compress.run[hdb;.qtorq.compress.write_conf[dir;c];a]}[hdb;a;c]}

\d .
