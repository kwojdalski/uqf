/ torq_compress_survey.q - what `uqs data compress` asks of TorQ's .cmp (#2147, #2148).
/ .
/ No compression here. Every file is planned by TorQ's .cmp.showcomp
/ (.qtorq.compress.plan), compressed, verified and swapped by .cmp.compressfromtable,
/ and checked after by .qtorq.compress.apply_plan (torq_compress.q). What this adds is
/ the SELECTION of one run - which rows of .cmp's plan it takes - so that the plan a
/ person reads and the run that follows are the same rows:
/ .
/   --table T       every table that is not T gets a minage no partition reaches, in
/                   the config .cmp plans from
/   --older-than D  every row's minage is at least D days, likewise
/   --date / --from / --to / --last
/                   the partitions within those days, planned with the policy's
/                   minage set aside (to D, else 0): naming a day compresses it on
/                   demand, and each row says when that is younger than the policy
/   --algo / --blocksize / --level
/                   the rows' calgo, cblocksize and clevel - the columns of .cmp's
/                   own plan table that compressfromtable reads - after .cmp.checkcsv
/                   has accepted them
/   being written   a partition the coverage ledger says is still being written
/                   (.qetl.coverage.unreleased: rows written that its readers have
/                   not been released to see) is held, and never run
/ .
/ A table without a `default` column row of its own - one that takes the stack's
/ `default` row, for all its columns or for those it does not name - is given a copy
/ of that row under its own name before a scope shuts the default, so it is not shut
/ with it. .cmp reads the two the same way.
/ .
/ survey adds the estimate: the newest selected partition of each table, its first
/ `n` rows of each column copied (uncompressed) into a temporary directory, and the
/ same rows, pointed at the copy, run through .qtorq.compress.apply_plan - so the
/ ratio is the one .cmp itself reaches, not one predicted here.
/ .
/ WHERE IT RUNS. housekeeping1 - TorQ's own HDB-maintenance process, which loads
/ .cmp with the rest of TorQ's common code - loads it after torq_compress.q
/ (VENDORED_LOAD_OVERLAY in python/uqs/src/uqs/stack/procs.py), and `uqs data
/ compress` calls it over IPC. It needs .qetl.coverage (src/etl/core/
/ materialisation.q and what it loads) for the hold.

\d .

/ A minage no partition reaches: the age of a partition 2,700 years old.
.qtorq.compress.never:1000000i

/ Private: TorQ's config, read and checked by .cmp itself (.cmp.loadcsv).
/ @private
.qtorq.compress.config:{[conf]
    .qtorq.compress.require_cmp[];
    .cmp.loadcsv $[10h=type conf; hsym `$conf; hsym conf];
    .cmp.compressioncsv}

/ Private: a partition named as text (a date or a month, q's or ISO's spelling) as
/ .cmp.hdbstructure types it.
/ @private
.qtorq.compress.partition_of:{[s]
    s:$[10h=type s; s; string s];
    if[not null d:"D"$s; :d];
    if[not null m:"M"$s; :m];
    '"qtorq.compress: ",s," is not a date or month partition"}

/ TorQ's config rows scoped to one run: only `only`'s tables (all when empty), only
/ partitions at least `older` days old - on top of each row's own minage, unless the
/ run names its days (`explicit`), when `older` replaces it.
/ @param c TorQ's config rows (.qtorq.compress.config)
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param explicit 1b when the run names its days, which set the policy's minage aside
/ @return the config rows, in .cmp's columns
.qtorq.compress.scope:{[c;only;older;explicit]
    only:(),only;
    own:exec distinct table from c where table<>`default, column=`default;
    d:select from c where table=`default, column=`default;
    if[count add:(only except own) except `default;
        c:c,raze {[d;t] update table:t from d}[d] each add];
    if[count only; c:update minage:.qtorq.compress.never from c where not table in only];
    $[explicit;
        update minage:`int$older from c where minage<.qtorq.compress.never;
        update minage:minage|`int$older from c]}

/ The minage the policy gives one table's column: its own row, else its table's, else
/ the stack's `default` - the order .cmp.showcomp joins them in.
/ @param c TorQ's config rows
/ @param t the table
/ @param col the column
/ @return the minage, in days; null when no row reaches it
.qtorq.compress.policy_minage:{[c;t;col]
    r:select from c where table=t, column=col;
    if[0=count r; r:select from c where table=t, column=`default];
    if[0=count r; r:select from c where table=`default, column=`default];
    first r`minage}

/ Private: refuse an algorithm, block size or level .cmp would refuse in its config,
/ by .cmp.checkcsv itself, before it reaches a plan row.
/ @private
.qtorq.compress.check_change:{[change]
    if[not all (key change) in `calgo`cblocksize`clevel;
        '"qtorq.compress: a change sets calgo, cblocksize or clevel, not ",", " sv string (key change) except `calgo`cblocksize`clevel];
    r:`table`minage`column`calgo`cblocksize`clevel!(`one_off;0i;`default;2i;17i;6i);
    r[key change]:`int$value change;
    if[0=r`calgo; r[`cblocksize`clevel]:0 0i];
    .cmp.checkcsv enlist r;
    }

/ The rows of .cmp's plan one run selects: planned over every partition (maxage 0W)
/ with the scoped config, narrowed to `days`, given `change`, each row with the
/ minage its policy gives it.
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path (.cmp.inputcsv)
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param days (first;last) partitions, inclusive, either null for open; () for the policy's
/ @param change dict of calgo, cblocksize, clevel overriding the policy's, or ()!()
/ @return the plan's rows, with policy_minage
/ @throws error when .cmp is not loaded, or checkcsv refuses the config or the change
.qtorq.compress.selected:{[hdb;conf;only;older;days;change]
    c:.qtorq.compress.config[conf];
    explicit:0<count days;
    sc:.qtorq.compress.scope[c;only;older;explicit];
    p:.qtorq.compress.in_tmp {[hdb;sc;dir] .qtorq.compress.plan[hdb;.qtorq.compress.write_conf[dir;sc];0W]}[hdb;sc];
    if[explicit; if[not null days 0; p:select from p where partition>=days 0]];
    if[explicit; if[not null days 1; p:select from p where partition<=days 1]];
    if[count change;
        .qtorq.compress.check_change[change];
        p:![p;();0b;key[change]!{[v] $[-6h=type v; v; `int$v]} each value change]];
    p:update action:?[calgo=0; ?[compressed;`decompress;`skip]; ?[compressed;`skip;`compress]] from p;
    update policy_minage:.qtorq.compress.policy_minage[c]'[table;column] from p}

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
/ root, uncompressed whatever .z.zd says, with a .d naming them; the rows, pointed
/ at the copies.
/ @private
.qtorq.compress.copy_sample:{[root;n;rows]
    d:` sv root,(`$string first rows`partition),first rows`table;
    to:{[d;c] ` sv d,c}[d] each rows`column;
    {[n;f;t] (t;17;0;0) set n sublist get f}[n]'[rows`fullpath;to];
    (` sv d,`.d) set rows`column;
    update fullpath:to, currentsize:hcount each to from rows}

/ Private: the compression .cmp reaches on a sample of each table: its newest
/ selected partition with a column to compress, n rows of each, the same rows run
/ through .qtorq.compress.apply_plan against the copy.
/ @private
.qtorq.compress.estimate:{[todo;n]
    empty:([] table:`symbol$(); column:`symbol$(); bytes_before:`long$(); bytes_after:`long$());
    todo:select from todo where action=`compress;
    if[0=count todo; :(empty;([] table:`symbol$(); partition:(); rows:`long$()))];
    newest:select from todo where age=(min;age) fby table;
    .qtorq.compress.in_tmp {[n;newest;dir]
        root:` sv dir,`hdb;
        parts:0!select by table, partition from newest;
        rows:raze {[root;n;newest;t;p] .qtorq.compress.copy_sample[root;n;select from newest where table=t, partition=p]}[root;n;newest]'[parts`table;parts`partition];
        r:.qtorq.compress.apply_plan rows;
        per:select bytes_before:sum bytes_before, bytes_after:sum bytes_after by table, column from r;
        counts:exec n&count get first fullpath by table, partition from rows;
        (0!per;([] table:parts`table; partition:parts`partition; rows:"j"$value counts))
        }[n;newest]}

/ Private: a file's bytes once uncompressed, from its -21! header.
/ @private
.qtorq.compress.plain_bytes:{[f] h:-21!f; $[count h; h`uncompressedLength; hcount f]}

/ Everything `uqs data compress` shows before it changes anything: TorQ's config,
/ what the coverage ledger holds back, and per table partition the columns .cmp
/ would compress, decompress or leave, with today's bytes and the estimate.
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path (.cmp.inputcsv)
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param days (first;last) partitions, inclusive, either null for open; () for the policy's
/ @param change dict of calgo, cblocksize, clevel overriding the policy's, or ()!()
/ @param n the rows of each column the estimate compresses
/ @return dict: config (TorQ's rows), held (dataset, date still being written),
/   partitions (table, partition, age, status - `act, or `held while being written -
/   policy_minage, the youngest age the policy compresses at, early: 1b when the
/   run reaches it younger than that, dir, compress, decompress, skip, compressed:
/   column lists, files, bytes_now, bytes_after: today's and the estimate,
/   bytes_written: the estimate of the files .cmp writes anew), and
/   sample (table, partition, rows) and ratios (table, column, bytes_before,
/   bytes_after) the estimate came from
/ @throws error when .cmp is not loaded or refuses the config, or the ledger cannot be read
.qtorq.compress.survey:{[hdb;conf;only;older;days;change;n]
    p:.qtorq.compress.selected[hdb;conf;only;older;days;change];
    u:.qetl.coverage.unreleased[.z.p];
    p:update status:?[([] dataset:table; date:partition) in u;`held;`act] from p;
    est:.qtorq.compress.estimate[select from p where status=`act;n];
    p:p lj `table`column xkey select table, column, r:bytes_after%bytes_before from est 0;
    p:p lj select tr:(sum bytes_after)%sum bytes_before by table from est 0;
    p:update after:currentsize from p;
    p:update after:"j"$currentsize*1f^tr^r from p where action=`compress;
    p:update after:.qtorq.compress.plain_bytes each fullpath from p where action=`decompress;
    parts:0!select policy_minage:min policy_minage, dir:{"/" sv -1_"/" vs 1_string first x} fullpath,
        compress:column where action=`compress, decompress:column where action=`decompress,
        skip:column where action=`skip, compressed:column where compressed,
        files:count i, bytes_now:sum currentsize, bytes_after:sum after,
        bytes_written:sum after where action<>`skip
        by table, partition, age, status from p;
    parts:update early:age<policy_minage from parts;
    `config`held`partitions`sample`ratios!(.cmp.compressioncsv;u;`partition xasc parts;est 1;est 0)}

/ Run one partition of a selection: its rows of .qtorq.compress.selected, less any
/ table whose partition is still being written, through .qtorq.compress.apply_plan
/ (.cmp.compressfromtable and the read-back check). The HDB is not reloaded here: the
/ caller reloads once, after the last partition (.qtorq.reload_hdb).
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path
/ @param only the tables to reach, or () for all
/ @param older the least age, in days
/ @param days as survey's
/ @param change as survey's
/ @param part the partition, a date or month as text
/ @return what .qtorq.compress.apply_plan returns: one row per file changed
/ @throws what .qtorq.compress.apply_plan throws - a named error when a file did not reach its target state
.qtorq.compress.apply_partition:{[hdb;conf;only;older;days;change;part]
    pt:.qtorq.compress.partition_of[part];
    p:select from .qtorq.compress.selected[hdb;conf;only;older;days;change] where partition=pt;
    u:.qetl.coverage.unreleased[.z.p];
    p:select from p where not ([] dataset:table; date:partition) in u;
    .qtorq.compress.apply_plan p}

\d .
