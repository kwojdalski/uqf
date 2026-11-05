/ torq_compress.q - a thin uqf wrapper over TorQ's own HDB compression, `.cmp` (#2146).
/ .
/ REUSE DECISION. uqf does not implement compression. TorQ's .cmp
/ (lib/torq/code/common/compress.q, run by its `compression` process) already
/ reads the age-based per-table, per-column compressionconfig.csv with defaults,
/ walks partitioned, splayed and par.txt HDBs, leaves .d, *.q, *.k and the
/ hash files alone, compresses each file to <file>_kdbtempzip with -19!, checks
/ `get` of the two for equality before renaming, moves a nested column's #
/ files, skips what is already in the target state, decompresses with algo 0
/ and keeps .cmp.statstab. All of that runs here unchanged; nothing in
/ lib/torq is edited and .cmp's checkcsv limits (algos 0 to 3, block sizes 12
/ to 19) stand. Its sibling in KX's whitepaper (https://code.kx.com/q/wp/compress/),
/ simongarland/compress, was investigated and cannot be vendored: it carries no licence.
/ .
/ WHAT THIS ADDS, and only this:
/ .
/   plan    .cmp.showcomp's table, with each file's current state and the action
/           TorQ would take on it (compress, decompress or skip). Read-only.
/   apply   .cmp.compressmaxage, then a check TorQ does not make. TorQ LOGS a file
/           whose read-back did not match and keeps the original; it neither
/           raises nor says so in a return value, so a run that failed looked
/           the same as one that worked. apply reads -21! on every file it
/           planned to change, and on its # hash files, and refuses with a named
/           error listing each one that is not in its target state.
/   run     apply, then TorQ's HDB reload (.qtorq.reload_hdb, which already
/           exists in torq_pipeline.q: the (`reload;date) message to every hdb
/           found through .servers) only if something changed.
/ .
/ WHAT STAYS TORQ'S, and is worth knowing: .cmp swaps file by file as each one
/ verifies, so a refused run may have converted some files before the one that
/ failed. Every file is valid at every moment (same values), and a second run
/ continues from where this stopped. A file already compressed with another
/ algorithm is skipped, not recompressed; to change algorithm, decompress (algo
/ 0) and compress again. TorQ ages partitions from today's date (.z.D), so plan
/ takes no as-of.
/ .
/ WHERE IT RUNS. .cmp lives in the process TorQ's compression.q starts (or any
/ process that loads common/compress.q); this file is loaded after it there, as
/ torq_pipeline.q is loaded after TorQ's own code, and needs .cmp,
/ .lg and (for run) .qtorq.reload_hdb. Nothing under src/ calls it. The conf is the
/ one .cmp.inputcsv names; #2152 generates it.

\d .

/ Private: refuse to run where TorQ's compression code is not loaded.
/ @private
.qtorq.compress.require_cmp:{[]
    if[not all `showcomp`compressmaxage in key `.cmp;
        '"qtorq.compress: TorQ's .cmp is not loaded - load lib/torq/code/common/compress.q (TorQ's compression process does) first"]}

/ Private: a file's bytes on disk - the compressed length when it is compressed.
/ @private
.qtorq.compress.disk_bytes:{[f]
    z:-21!f;
    $[count z; z`compressedLength; hcount f]}

/ Private: is a file compressed, as -21! says.
/ @private
.qtorq.compress.is_compressed:{[f] 0<count -21!f}

/ What TorQ's age-based config would do to every file under an HDB, as data. Reads
/ directory listings and file headers and writes nothing (.cmp.showcomp's own
/ side effect is loading the csv into .cmp.compressioncsv).
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path (.cmp.inputcsv's file)
/ @param maxage the oldest partition to consider, in days (0W for all)
/ @return table fullpath, table, partition, column, age, calgo, cblocksize, clevel,
/   compressage, currentsize (all from .cmp.showcomp), plus compressed (the file's state
/   now) and action: compress, decompress, or skip where it is already in the target state
/ @throws error when .cmp is not loaded, or its checkcsv refuses the config
/ @eg .qtorq.compress.plan[`:/data/hdb;`:/data/config/compressionconfig.csv;365]
.qtorq.compress.plan:{[hdb;conf;maxage]
    .qtorq.compress.require_cmp[];
    t:.cmp.showcomp[hdb;conf;maxage];
    / Nothing old enough, or a config that holds every table out of the age pass (a
    / `write table, #2151): the ?[] below is 'type on zero rows, so the answer is
    / given with its two columns typed.
    if[0=count t; :update compressed:`boolean$(), action:`symbol$() from t];
    t:update compressed:.qtorq.compress.is_compressed each fullpath from t;
    update action:?[calgo=0; ?[compressed;`decompress;`skip]; ?[compressed;`skip;`compress]] from t}

/ Private: the hash files (col#, col##) a column has, as a list of file symbols.
/ @private
.qtorq.compress.companions:{[f]
    c:`$string[f],/:("#";"##");
    c where not ()~/:key each c}

/ Bring an HDB to the state TorQ's config asks for, with TorQ's own code, then check what
/ TorQ only logs. Files already in their target state are not touched. Nothing is
/ called when there is nothing to change.
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config, a file symbol or string path
/ @param maxage the oldest partition to consider, in days (0W for all)
/ @return table fullpath, table, partition, column, action, bytes_before, bytes_after - one row
/   per file changed; empty when the HDB was already as configured
/ @throws "qtorq.compress: n file(s) were not brought to their target state ..." listing them,
/   with each such original kept by TorQ; also what .cmp throws
/ @eg .qtorq.compress.apply[`:/data/hdb;`:/data/config/compressionconfig.csv;365]
.qtorq.compress.apply:{[hdb;conf;maxage]
    p:.qtorq.compress.plan[hdb;conf;maxage];
    todo:select from p where action<>`skip;
    out:{[t] select fullpath, table, partition, column, action from t};
    if[0=count todo; :update bytes_before:`long$(), bytes_after:`long$() from out todo];
    hashes:(todo`fullpath)!{[f] read1 each .qtorq.compress.companions f} each todo`fullpath;
    before:.qtorq.compress.disk_bytes each todo`fullpath;
    .cmp.compressmaxage[hdb;conf;maxage];
    after:.qtorq.compress.disk_bytes each todo`fullpath;
    ok:(.qtorq.compress.is_compressed each todo`fullpath)=todo[`calgo]>0;
    hashes_ok:{[f;h] (read1 each .qtorq.compress.companions f)~h}'[todo`fullpath;value hashes];
    bad:todo[`fullpath] where not ok & hashes_ok;
    leftover:`$string[todo`fullpath],\:"_kdbtempzip";
    left:leftover where not ()~/:key each leftover;
    if[count bad,left;
        '"qtorq.compress: ",string[count bad]," file(s) were not brought to their target state (TorQ logged the failed read-back and kept the original): ",
            (", " sv string bad),$[count left;"; temp file(s) left behind: ",", " sv string left;""],
            "; files converted before them are valid, and a second run continues"];
    update bytes_before:before, bytes_after:after from out todo}

/ apply, then ask every running HDB to reload (.qtorq.reload_hdb - TorQ's own
/ (`reload;date) message found through .servers, which logs a refusal and does not
/ raise) if, and only if, a file changed.
/ @param hdb the HDB root, a file symbol
/ @param conf the compression config
/ @param maxage the oldest partition to consider, in days
/ @return what apply returns; the reload's own counts are in .qtorq.reload_hdb's log line
/ @throws what apply or reload throws
/ @eg .qtorq.compress.run[`:/data/hdb;`:/data/config/compressionconfig.csv;365]
.qtorq.compress.run:{[hdb;conf;maxage]
    r:.qtorq.compress.apply[hdb;conf;maxage];
    if[count r; .qtorq.reload_hdb[]];
    r}

\d .
