/ mapped_books.q - top-of-book reads against memory-mapped market_data (.qetl.mapped, #2288).
/ .
/ THE RULE. Market data can run to tens of terabytes. A markout run must never copy
/ it into a process heap: the as-of join runs against the MEMORY-MAPPED HDB partition
/ and touches only the pages it needs. Executions are small, so they are the side that
/ lives in memory. What a markout's memory grows with is the number of executions,
/ never the size of the market.
/ .
/ WHAT KEEPS A COLUMN MAPPED. `select cols from market_data where date=d` - the date
/ and nothing else in the where clause - hands back the columns as memory-mapped
/ vectors. ANY other where clause (a sym, a time range) materialises a copy of every
/ column it names. So nothing here, and nothing a markout calls, selects market_data
/ with a where beyond the date (scripts/gates/policy/markout_mapped.py refuses
/ one), and none touches a ladder: bid_prices and the other depth columns are never
/ read by a markout. The level-0 top is stored FLAT (bid, ask), beside a row number
/ (rid) that lets a consumer that does want a depth read only the matched rows' pages.
/ .
/ THE LAYOUT IT RELIES ON. A partition is sorted by sym, source, source_time with p#
/ on sym (src/etl/plant_tables.q, `sorting`). aj keyed on those three binary-searches
/ inside the parted sym's range. This module never sorts or copies a partition: the
/ storage layer's job is to write it in that order, and `require_arranged` refuses a
/ table that is not.
/ .
/ THE TOP-OF-BOOK TABLE (the markout core's input): sym, source, source_time, bid, ask,
/ clock. A (sym, source) is on one clock - the clock is a property of the feed that
/ writes the source - so the clock of the matched book is the clock of the series.
/ .
/ WHERE THINGS RUN. `tob_at` is run by the process that MAPS the partitions (the HDB, over
/ IPC, or this process when it loaded one): it returns one row per distinct book the
/ probes matched - memory proportional to the probes, whatever the market's size. It
/ calls only this namespace, which the HDB process loads (VENDORED_LOAD_OVERLAY).

\d .

/ The order a top-of-book table must be in: first column parted, the rest ascending within it.
.qetl.mapped.order:`sym`source`source_time

/ The columns of a top-of-book table, and of its input contract.
.qetl.mapped.columns:`sym`source`source_time`bid`ask`clock

/ A table up to this many rows has its order checked in full; a larger one (a mapped
/ partition) is checked by its attribute and by sampled neighbours, because reading every
/ row to prove an order is the scan this module exists to avoid.
.qetl.mapped.full_check_rows:1000000

/ How many neighbouring pairs are sampled in a table past full_check_rows.
.qetl.mapped.sample_pairs:2048

/ A column value as plain symbols/values: an enumeration (a mapped partition's sym) is decoded by
/ `value`; anything else is returned as it is. `value` of a plain SYMBOL would evaluate a variable,
/ which is why this checks the type first.
/ @param x a vector or an atom
/ @return x with its enumeration, if any, resolved
/ @eg .qetl.mapped.plain `a`b  ->  `a`b
.qetl.mapped.plain:{[x] $[(abs type x) within 20 76h; value x; x]}

/ Why a top-of-book table is not in the order the aj needs, or "" when it is.
/ .
/ Checks the first sort column carries p#, and that the rows are ascending by sym, source,
/ source_time: all of them for a small table, and a sample of neighbouring pairs for a mapped
/ one (past full_check_rows). The attribute is O(1) either way.
/ @param tob a top-of-book table (it needs sym, source and source_time)
/ @return "" when arranged, else the reason
/ @eg .qetl.mapped.arranged[0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$())]  ->  ""
.qetl.mapped.arranged:{[tob]
    if[not all .qetl.mapped.order in cols tob; :"lacks ",", " sv string .qetl.mapped.order except cols tob];
    n:count tob;
    if[n<2; :""];
    if[not `p=attr tob`sym; :"sym carries no p# attribute"];
    ix:$[n<=.qetl.mapped.full_check_rows; til n-1;
        asc .qetl.mapped.sample_pairs?n-1];
    cs:.qetl.mapped.order;
    / both rows of each pair, read by index: a mapped column pays only those pages
    pairs:{[tob;ix;c] ((tob c) ix; (tob c) ix+1)}[tob;ix] each cs;
    before:pairs[;0];
    after:pairs[;1];
    lt:after<'before;
    gt:after>'before;
    undecided:count[ix]#1b;
    bad:count[ix]#0b;
    i:0;
    while[i<count cs;
        bad:bad | undecided & lt i;
        undecided:undecided & not (lt i) | gt i;
        i+:1];
    $[any bad; "not ascending by ",", " sv string cs; ""]}

/ Refuse a top-of-book table that is not in the order the aj needs. The core calls this on its
/ input instead of sorting it: sorting is the storage layer's job, and a sort here would copy a
/ mapped partition into the heap.
/ @param who the caller, for the message
/ @param tob the top-of-book table
/ @return tob, unchanged
/ @throws error naming the caller and what is wrong
/ @eg .qetl.mapped.require_arranged[`eg;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$())]  ->  0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$())
.qetl.mapped.require_arranged:{[who;tob]
    why:.qetl.mapped.arranged tob;
    if[count why; '(string who),": top-of-book input is not arranged for an as-of join (",why,") - sort it by sym, source, source_time and put p# on sym where it is written; the core never sorts or copies it"];
    tob}

/ Sort a SMALL in-memory top-of-book table into the order the core requires and put p# on sym:
/ for the live buffer and for fixtures, whose rows arrive in time order. Never for a mapped
/ partition, which is written in order and must not be copied.
/ @param tob a top-of-book table
/ @return the same rows, sorted by sym, source, source_time, with p# on sym
/ @eg attr (.qetl.mapped.arrange ([] sym:`b`a; source:`x`x; source_time:2#2026.09.17D10:00:00))`sym  ->  `p
.qetl.mapped.arrange:{[tob]
    if[0=count tob; :update `p#sym from tob];
    update `p#sym from .qetl.mapped.order xasc tob}

/ The index of the first element of s's sorted run in a sorted column, and one past its last: the
/ symbol-keyed binary searches below read O(log n) elements of a (possibly mapped) column.
/ @param col a column ascending within [lo;hi)
/ @param v the value
/ @param lo the first index to consider
/ @param hi one past the last
/ @return the first index in [lo;hi) whose element is greater than v
/ @private
.qetl.mapped.upper:{[col;v;lo;hi]
    while[lo<hi;
        mid:(lo+hi) div 2;
        $[v<.qetl.mapped.plain col mid; hi:mid; lo:mid+1]];
    lo}

/ The runs of a sorted, p#-attributed top-of-book table: every (sym; source) is one contiguous run of rows, found by
/ galloping - the first source of a sym's run, then the end of that source's run by binary search, and so on. Reads
/ O(runs * log rows) elements, never a column.
/ @param tob a top-of-book table, arranged
/ @param syms the syms to look for, or ` for every sym
/ @return table sym, source, lo, hi: the rows [lo;hi) of each run
/ @eg .qetl.mapped.runs[.qetl.mapped.arrange ([] sym:`a`a`b; source:`x`y`x; source_time:3#2026.09.17D10:00:00);`]`hi  ->  1 2 3
.qetl.mapped.runs:{[tob;syms]
    empty:([] sym:`symbol$(); source:`symbol$(); lo:`long$(); hi:`long$());
    if[0=count tob; :empty];
    all_syms:.qetl.mapped.plain distinct tob`sym;
    / a sym's run: from where it first appears to where the next sym first appears
    starts:(tob`sym)?all_syms;
    ends:(1_starts),count tob;
    keep:$[` ~ syms; til count all_syms; where all_syms in syms];
    rows:raze {[tob;sy;lo;hi]
        out:();
        pos:lo;
        while[pos<hi;
            s:.qetl.mapped.plain (tob`source) pos;
            end:.qetl.mapped.upper[tob`source;s;pos;hi];
            out,:enlist (sy;s;pos;end);
            pos:end];
        out}[tob]'[all_syms keep;starts keep;ends keep];
    $[count rows; ([] sym:`symbol$rows[;0]; source:`symbol$rows[;1]; lo:`long$rows[;2]; hi:`long$rows[;3]); empty]}

/ The sources a sym has in an arranged top-of-book table.
/ @param tob a top-of-book table, arranged
/ @param syms the syms to look for
/ @return table sym, source: one row per source the sym has in the table
/ @eg .qetl.mapped.sources_for[.qetl.mapped.arrange ([] sym:`a`a`b; source:`x`y`x; source_time:3#2026.09.17D10:00:00);enlist `a]`source  ->  `x`y
.qetl.mapped.sources_for:{[tob;syms]
    select sym, source from .qetl.mapped.runs[tob;distinct syms]}

/ The as-of join of probes against ONE top-of-book table, mapped or not: for each probe (sym; source; source_time),
/ the latest book of that (sym; source) at or before the time. No where clause, no copy of a column. The matched
/ book's own time comes back as `quoted`, as aj's does not (aj returns the probe's).
/ .
/ WHY NOT aj. aj is this join, and keyed on three columns against a table sorted and parted as ours it returns the
/ same books - but it first groups the right table by (sym; source) inside each parted sym, and does that by copying
/ the sym's columns into the heap for the call: at 100 million rows a day the heap's high-water mark jumped from 64MB
/ to 192MB around one aj of 200 probes (tests/q/run_mapped_heap.q), and it grows with the sym's rows - the market. A
/ two-key aj (sym, time) does not copy, but is wrong for several sources. So the search is done here, where it can be
/ held to touching only what it needs: the (sym; source) runs are found by galloping (`runs`), and every probe's
/ binary search over its run's source_time runs in lockstep with all the others - about log2(longest run) rounds of
/ one gather each, reading O(log n) elements per probe and nothing else. tests/q/test_mapped_books.q holds it equal to
/ aj over the whole table.
/ @param tob a top-of-book table (arranged); rid and w (a promoted variant's stored weight, variant_run.q), when it has them, come back too
/ @param probes table sym, source, source_time: the instants to price each (sym; source) at
/ @return the probes with quoted, bid, ask, clock (and rid) of the latest book at or before each; null where none
/ @eg .qetl.mapped.asof[.qetl.mapped.arrange ([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:00; bid:enlist 1f; ask:enlist 2f; clock:enlist `venue);([] sym:enlist `a; source:enlist `x; source_time:enlist 2026.09.17D10:00:05)]`bid  ->  enlist 1f
.qetl.mapped.asof:{[tob;probes]
    n:count probes;
    carry:`bid`ask`clock,(`rid`w) inter cols tob;
    none:probes,'flip (`quoted,carry)!(enlist n#0Np),{[tob;n;c] n#first 0#tob c}[tob;n] each carry;
    if[(0=n) or 0=count tob; :none];
    r:.qetl.mapped.runs[tob;distinct probes`sym];
    / each probe's run: [lo;hi) of its (sym; source), empty when the table has none
    run:(`sym`source xkey r) ([] sym:probes`sym; source:probes`source);
    lo:0^run`lo;
    hi:0^run`hi;
    t:probes`source_time;
    col:tob`source_time;
    l:lo;
    h:hi;
    / lockstep bisection: the first index in [lo;hi) whose time is after the probe's
    while[any l<h;
        act:where l<h;
        mid:((l act)+h act) div 2;
        go:(col mid)<=t act;
        l[act]:?[go;mid+1;l act];
        h[act]:?[go;h act;mid]];
    idx:l-1;
    / a null time sorts first: a run whose latest time at or before the probe is null has no book
    ok:(idx>=lo) & hi>lo;
    at:where ok;
    ok[at]:not null col idx at;
    at:where ok;
    plain:{[x] $[(abs type x) within 20 76h; value x; x]};
    got:{[tob;n;at;idx;c] @[n#first 0#tob c;at;:;(tob c) idx at]}[tob;n;at;idx];
    quoted:@[n#0Np;at;:;col idx at];
    probes,'flip (`quoted,carry)!enlist[quoted],{[plain;got;c] plain got c}[plain;got] each carry}

/ The books a set of probes is priced against, read from the partitions of the global `market_data`
/ one date at a time, each by a DATE-ONLY select so every column stays memory-mapped.
/ .
/ Run in the process that maps market_data (the HDB, or this one). For each probe (sym; time) and each
/ source the sym has, the latest book at or before the time - from the partitions within `skew` days of
/ the time's date (the partition is the plant's receipt date, the time the source's stamp), else from
/ the earlier partitions back to `read_from`, one date-only read each, never a scan of a day. Returns the
/ DISTINCT books matched, so the memory it needs is the probes' and the answer's, not the market's.
/ .
/ arg is a dictionary:
/   probes     table sym, time - the instants to price (any order, duplicates welcome)
/   read_from  the oldest source_time a book may have
/   skew       how many days either side of a time's date its books may be stored
/   venues     `all, or the sources that count
/ @param arg the dictionary above
/ @return a top-of-book table: sym, source, source_time, bid, ask, clock, rid, date - arranged
/ @throws error when the partitions lack the flat columns (run `uqs data check --apply`)
/ @eg count .qetl.mapped.tob_at[`probes`read_from`skew`venues!(0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;`all)]  ->  0
.qetl.mapped.tob_at:{[arg]
    p:distinct arg`probes;
    empty:0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$(); rid:`long$(); date:`date$());
    if[0=count p; :update `p#sym from empty];
    skew:arg`skew;
    lo:(`date$arg`read_from)-skew;
    hi:(`date$max p`time)+skew;
    / `market_data, quoted: the coverage tool re-evaluates a function in its own namespace, where a bare name is not the global
    have:asc exec distinct date from select date from `market_data where date within (lo;hi);
    if[0=count have; :update `p#sym from empty];
    part:{select sym,source,source_time,quoted:source_time,bid,ask,clock,rid from `market_data where date=x};
    pairs:distinct raze {[part;syms;d] .qetl.mapped.sources_for[part d;syms]}[part;distinct p`sym] each have;
    if[not `all~arg`venues; pairs:select from pairs where source in arg`venues];
    sp:exec source by sym from pairs;
    f:update pid:i from ungroup update source:sp sym from update source_time:time from p;
    if[0=count f; :update `p#sym from empty];
    dt:`date$f`time;
    one:{[part;f;d;rows] j:.qetl.mapped.asof[part d;f rows]; update date:d from select pid, quoted, bid, ask, clock, rid from j where not null quoted}[part;f];
    near:raze {[one;dt;skew;d] one[d;where (abs dt-d)<=skew]}[one;dt;skew] each have;
    got:select last quoted, last bid, last ask, last clock, last rid, last date by pid from `pid`quoted`date xasc near;
    left:(exec pid from f) except exec pid from got;
    back:desc have;
    while[count[left] and count back;
        d:first back;
        back:1_back;
        rows:left where ((dt left)-d)>skew;
        if[count rows;
            m:one[d;rows];
            got:got,select last quoted, last bid, last ask, last clock, last rid, last date by pid from m;
            left:left except exec pid from m]];
    books:select distinct sym, source, source_time:quoted, bid, ask, clock, rid, date from (f lj got) where not null quoted, quoted>=arg`read_from;
    .qetl.mapped.arrange books}

/ The latest top of book of every (sym; source) strictly before an instant, with its market: what a job that
/ restarts needs to know of the market as it stood at the close of the previous day, without reading the market.
/ .
/ Run in the process that maps market_data, like tob_at. The partitions are walked newest first from the one
/ holding `before`, each by a DATE-ONLY select (every column stays memory-mapped), the (sym; source) runs found by
/ galloping and priced by `asof` at the instant: a few elements per run, never a scan of a day. A (sym; source)
/ already found in a newer partition is not looked for in an older one, so the walk back over a weekend or a
/ holiday reads only what is still missing, down to `read_from`.
/ .
/ The latest BOOK is returned, whether or not both its sides are quoted: the caller decides what a one-sided
/ book is worth (a mid needs both).
/ @param arg a dictionary: before (the exclusive instant), read_from (the oldest source_time a book may have)
/ @return table sym, source, market, source_time, bid, ask, clock: one row per (sym; source) that has a book in range
/ @eg count .qetl.mapped.last_tops[`before`read_from!(2026.09.17D00:00:00;2026.09.10D00:00:00)]
.qetl.mapped.last_tops:{[arg]
    before:arg`before;
    empty:0#([] sym:`symbol$(); source:`symbol$(); market:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());
    / `market_data, quoted: as in tob_at, a bare name is not the global under the coverage tool
    have:desc exec distinct date from select date from `market_data where date within (`date$arg`read_from;`date$before);
    got:empty;
    while[count have;
        d:first have;
        have:1_have;
        part:select sym,source,market,source_time,bid,ask,clock from `market_data where date=d;
        r:.qetl.mapped.runs[part;`];
        if[count r;
            r:update market:`symbol$.qetl.mapped.plain (part`market) lo from r;
            / only the (sym; source) pairs a newer partition has not answered
            r:r where not ((r`sym),'(r`source)) in (got`sym),'(got`source);
            if[count r;
                j:.qetl.mapped.asof[part;select sym, source, source_time:before-1 from r];
                j:select sym, source, source_time:quoted, bid, ask, clock from j where not null quoted, quoted>=arg`read_from;
                got,:select sym, source, market, source_time, bid, ask, clock from (j lj `sym`source xkey select sym, source, market from r)]]];
    got}

/ The newest source_time among the books of one market in the newest partition of the global `market_data` that holds
/ any, read from the END of each (sym; source) run - the run is ascending, so its last row is its newest - and the
/ market from the run's first. No where clause, no scan of a column: a few elements per run.
/ .
/ Partitions are walked newest first and the walk stops at the first holding the market: the newest day can hold only
/ another market's books (a Saturday holds crypto alone), and reading it alone answered -0Wp for FX (#2571).
/ .
/ Run in the process that maps market_data, like tob_at.
/ @param arg a dictionary: market (a symbol, or ` for every market)
/ @return the newest source_time, or -0Wp when no partition holds the market
/ @eg .qetl.mapped.newest[enlist[`market]!enlist `crypto]
.qetl.mapped.newest:{[arg]
    ds:desc exec date from select distinct date from `market_data;
    n:-0Wp;
    while[(n=-0Wp) and count ds; n:.qetl.mapped.newest_on[arg`market;first ds]; ds:1_ds];
    n}

/ Private: newest's read of one partition, -0Wp when it holds no book of the market.
/ @private
.qetl.mapped.newest_on:{[mkt;d]
    t:$[null mkt; select sym,source,source_time from `market_data where date=d; select sym,source,source_time,market from `market_data where date=d];
    r:.qetl.mapped.runs[t;`];
    if[0=count r; :-0Wp];
    if[not null mkt; r:r where (.qetl.mapped.plain (t`market) r`lo)=mkt];
    $[count r; max .qetl.mapped.plain (t`source_time) (r`hi)-1; -0Wp]}

/ The instants a markout asks the market about, one per (sym; time): for each execution, each of its legs at the
/ execution's own instant and at every horizon from it. The ONLY thing a twin's source needs to know about
/ scoring to read the books that answer it; the core asks for exactly these (sym; instant) pairs and no others.
/ @param legs the market_data syms each execution is priced from, a symbol list per execution
/ @param t0 each execution's instant
/ @param horizons the spec's horizons, signed timespans
/ @return table sym, time, distinct
/ @eg .qetl.mapped.probes[enlist `EURUSD`USDJPY;enlist 2026.09.17D10:00:00;enlist 0D00:00:10]`time  ->  2026.09.17D10:00:00 2026.09.17D10:00:10 2026.09.17D10:00:00 2026.09.17D10:00:10
.qetl.mapped.probes:{[legs;t0;horizons]
    per:ungroup ([] sym:legs; t0:t0);
    if[0=count per; :([] sym:`symbol$(); time:`timestamp$())];
    steps:(enlist 0D),horizons;
    distinct ungroup `sym`time#update time:{[t;h] t+\:h}[t0;steps] from per}

/ The books a window's probes are priced against, read where the market is mapped (the HDB, over `h`) by tob_at, and
/ returned as the top-of-book table the core takes: sym, source, source_time, bid, ask and clock, arranged. rid
/ and date, which tob_at also returns, stay behind: a markout reads no depth.
/ @param h the handle to the process that maps market_data
/ @param probes table sym, time (probes)
/ @param read_from the oldest source_time a book may have
/ @param skew days either side of a time's date its books may be stored
/ @param venues `all, or the sources that count
/ @return the top-of-book table, memory proportional to the probes
/ @eg count .qetl.mapped.read_books[{value x};0#([] sym:`symbol$(); time:`timestamp$());2026.09.17D00:00:00;1;`all]  ->  0
.qetl.mapped.read_books:{[h;probes;read_from;skew;venues]
    arg:`probes`read_from`skew`venues!(probes;read_from;skew;venues);
    b:.qetl.source.ipc_call[h;{[a] .qetl.mapped.tob_at a};enlist arg];
    .qetl.mapped.columns#b}
