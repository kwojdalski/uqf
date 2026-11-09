/ test_mapped_books.q - top-of-book reads against mapped market_data (src/etl/core/mapped_books.q, .qetl.mapped, #2288).
/ .
/ A markout's as-of join runs against the memory-mapped partitions and never copies them. This file holds the module
/ to what that needs and no more: the order it demands of a table and refuses without, the runs it finds by
/ galloping, the aj against a table's own columns, the books `tob_at` returns for a set of probes - equal to a brute-force
/ aj over the whole market, across midnight and across sources - the row numbers a depth read goes through, and the
/ newest book of a market. The partitions are stood in for by an in-memory table with a `date` column, which is what
/ `select ... where date=d` reads; what the real mapping costs in heap is test_mapped_heap.q's.


/ market_data as an HDB would hold it: sorted by sym, source, source_time, with date and rid.
/ @param n rows
/ @param seed the random seed
.qt.qetl.mapped.market:{[n;seed]
    system "S ",string seed;
    syms:`EURUSD`GBPUSD`USDJPY;
    srcs:`a`b`c;
    t:2026.09.15D00:00:00.000000000+n?259200000000000;
    rows:([] sym:n?syms; source:n?srcs; source_time:t; market:n#`fx;
        bid:{?[x<0.1;0n;1+x]} n?1f; ask:{?[x<0.1;0n;2+x]} n?1f; clock:n#`venue);
    .qt.support.stand_in_market_data rows}

/ Run f . args with `market_data` standing in for the mapped table, and put the root back.
.qt.qetl.mapped.with_market:{[md;f;args]
    had:$[`market_data in key `.; (1b;get `market_data); (0b;::)];
    `market_data set md;
    r:.[f;args;{[e] `threw,e}];
    $[first had; `market_data set last had; ![`.;();0b;enlist `market_data]];
    if[`threw~first r; 'last r];
    r}

/ --- the order a table must be in ---------------------------------------------------------------------

.qt.qetl.mapped.test_an_arranged_table_is_accepted_and_returned_as_it_is:{[t]
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from .qt.qetl.mapped.market[200;1];
    .qunit.assertEquals[.qetl.mapped.arranged tob;"";"sorted by sym, source, source_time with p# on sym"];
    .qunit.assertTrue[tob~.qetl.mapped.require_arranged[`probe;tob];"returned unchanged: the check copies nothing"];
    .qunit.assertEquals[attr tob`sym;`p;"arrange puts p# on sym"];
    .qunit.assertEquals[.qetl.mapped.arranged 0#tob;"";"an empty table has no order to break"]};

.qt.qetl.mapped.test_a_table_out_of_order_or_without_the_attribute_is_refused_by_name:{[t]
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from .qt.qetl.mapped.market[200;2];
    .qunit.assertEquals[.qetl.mapped.arranged update `#sym from tob;"sym carries no p# attribute";"p# is part of the contract"];
    / sorted by sym alone (so p# is true of it) but with each sym's rows in reverse: source and source_time out of order
    rev:update `p#sym from `sym xasc reverse tob;
    .qunit.assertTrue[(.qetl.mapped.arranged rev) like "not ascending*";"source_time out of order inside a (sym, source)"];
    .qunit.assertEquals[.qetl.mapped.arranged select sym, source from tob;"lacks source_time";"a column the join keys on"];
    .qunit.assertThrows[{.qetl.mapped.require_arranged[`probe;x]};update `#sym from tob;"probe: top-of-book input is not arranged for an as-of join*";"and the core says so by name"];
    .qunit.assertTrue[(@[{.qetl.mapped.require_arranged[`probe;x]};update `#sym from tob;{[e] e}]) like "*sym carries no p# attribute*";"and why"]};

.qt.qetl.mapped.test_a_big_table_is_judged_by_its_attribute_and_sampled_neighbours:{[t]
    keep:.qetl.mapped.full_check_rows;
    `.qetl.mapped.full_check_rows set 10;
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from .qt.qetl.mapped.market[500;3];
    ok:.qetl.mapped.arranged tob;
    / every adjacent pair out of order: any sample finds one
    rev:update `p#sym from reverse tob;
    bad:.qetl.mapped.arranged rev;
    `.qetl.mapped.full_check_rows set keep;
    .qunit.assertEquals[ok;"";"a sorted table passes a sampled check"];
    .qunit.assertTrue[bad like "not ascending*";"and a table in the wrong order does not"]};

/ --- runs ----------------------------------------------------------------------------------------------

.qt.qetl.mapped.test_runs_are_found_by_galloping_and_hold_every_row_once:{[t]
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from .qt.qetl.mapped.market[400;4];
    r:.qetl.mapped.runs[tob;`];
    .qunit.assertEquals[sum r[`hi]-r`lo;count tob;"the runs partition the table"];
    .qunit.assertEquals[r`lo;0,-1_r`hi;"each run begins where the last ended"];
    .qunit.assertEquals[{[tob;lo;hi] 1=count distinct (tob`source) lo+til hi-lo}[tob]'[r`lo;r`hi];(count r)#1b;"one source in each, and each (sym; source) once"];
    .qunit.assertEquals[count r;count distinct select sym, source from tob;"as many runs as (sym; source) pairs"];
    s:.qetl.mapped.sources_for[tob;enlist `GBPUSD];
    .qunit.assertEquals[asc exec source from s;asc exec distinct source from tob where sym=`GBPUSD;"the sources of one sym"];
    .qunit.assertEquals[count .qetl.mapped.sources_for[tob;enlist `NOPE];0;"a sym with no book has no sources"];
    .qunit.assertEquals[count .qetl.mapped.runs[0#tob;`];0;"nor does an empty table have runs"]};

/ --- the as-of join ---------------------------------------------------------------------------------------

.qt.qetl.mapped.test_asof_is_an_aj_against_the_tables_own_columns:{[t]
    tob:.qetl.mapped.arrange select sym, source, source_time, bid, ask, clock from .qt.qetl.mapped.market[600;5];
    p:([] sym:6#`EURUSD`GBPUSD; source:6#`a`b; source_time:2026.09.16D00:00:00+0D06*til 6);
    got:.qetl.mapped.asof[tob;p];
    ref:aj[`sym`source`source_time;p;select sym, source, source_time, quoted:source_time, bid, ask, clock from tob];
    .qunit.assertEquals[got;ref;"the probe's instant is kept; the matched book's own time comes back as quoted"];
    .qunit.assertTrue[all (got`quoted)<=got`source_time;"never a book from after the instant"]};

/ --- the books a set of probes is priced against -------------------------------------------------------

/ The books by brute force: every book of the market, as-of joined in one go with the probes by (sym; source).
.qt.qetl.mapped.brute:{[md;probes;read_from]
    all_md:`sym`source`source_time xasc select sym, source, source_time, quoted:source_time, bid, ask, clock, rid, date from md;
    sp:exec distinct source by sym from all_md;
    f:ungroup update source:sp sym from update source_time:time from probes;
    j:aj[`sym`source`source_time;f;all_md];
    j:select from j where not null quoted, quoted>=read_from;
    `sym`source`source_time xasc select distinct sym, source, source_time:quoted, bid, ask, clock, rid, date from j}

.qt.qetl.mapped.test_tob_at_returns_the_books_a_brute_force_aj_finds_and_only_those:{[t]
    md:.qt.qetl.mapped.market[900;6];
    p:distinct ([] sym:60?`EURUSD`GBPUSD`USDJPY; time:2026.09.15D00:00:00+60?259200000000000);
    rf:2026.09.14D00:00:00;
    got:.qt.qetl.mapped.with_market[md;{[p;rf] .qetl.mapped.tob_at`probes`read_from`skew`venues!(p;rf;1;`all)};(p;rf)];
    ref:.qt.qetl.mapped.brute[md;p;rf];
    .qunit.assertEquals[`sym`source`source_time xasc select sym, source, source_time, bid, ask, clock, rid, date from got;ref;"the same books, row for row, as aj over the whole market"];
    .qunit.assertEquals[.qetl.mapped.arranged got;"";"and it comes back arranged, ready for the core"];
    .qunit.assertTrue[(count got)<count md;"far fewer rows than the market holds"]};

.qt.qetl.mapped.test_a_book_before_midnight_prices_an_instant_after_it:{[t]
    / EURUSD on a quotes at 23:59:50 on the 15th and nothing again until the 17th: the 16th's probe is priced by the 15th's book
    md:.qt.support.stand_in_market_data ([] sym:`EURUSD`EURUSD; source:`a`a; source_time:2026.09.15D23:59:50 2026.09.17D12:00:00; market:`fx`fx; bid:1.1 1.3; ask:1.2 1.4; clock:`venue`venue);
    p:([] sym:`EURUSD`EURUSD`EURUSD; time:2026.09.16D00:00:05 2026.09.16D12:00:00 2026.09.17D12:00:01);
    got:.qt.qetl.mapped.with_market[md;{[p] .qetl.mapped.tob_at`probes`read_from`skew`venues!(p;2026.09.15D00:00:00;1;`all)};enlist p];
    .qunit.assertEquals[got`source_time;2026.09.15D23:59:50 2026.09.17D12:00:00;"the 16th's instants take the 15th's last book; the 17th's takes its own"];
    .qunit.assertEquals[got`bid;1.1 1.3;"with their own sides"];
    none:.qt.qetl.mapped.with_market[md;{[p] .qetl.mapped.tob_at`probes`read_from`skew`venues!(p;2026.09.16D00:00:00;1;`all)};enlist p];
    .qunit.assertEquals[none`source_time;enlist 2026.09.17D12:00:00;"a book older than read_from is not a reference"]};

.qt.qetl.mapped.test_the_venue_set_narrows_the_books_and_no_probe_is_no_read:{[t]
    md:.qt.qetl.mapped.market[300;7];
    p:([] sym:enlist `EURUSD; time:enlist 2026.09.16D12:00:00);
    only:.qt.qetl.mapped.with_market[md;{[p] .qetl.mapped.tob_at`probes`read_from`skew`venues!(p;2026.09.14D00:00:00;1;enlist `b)};enlist p];
    .qunit.assertEquals[asc distinct only`source;enlist `b;"only the declared venues"];
    nothing:.qt.qetl.mapped.with_market[md;{[u] .qetl.mapped.tob_at`probes`read_from`skew`venues!(0#([] sym:`symbol$(); time:`timestamp$());2026.09.14D00:00:00;1;`all)};enlist 0b];
    .qunit.assertEquals[(count nothing;cols nothing);(0;`sym`source`source_time`bid`ask`clock`rid`date);"no probes, no books, and the shape the caller expects"]};

.qt.qetl.mapped.test_probes_are_each_leg_at_the_execution_and_every_horizon:{[t]
    p:.qetl.mapped.probes[(`EURUSD`EURPLN;enlist `EURUSD;`symbol$());2026.09.17D10:00:00 2026.09.17D10:00:00 2026.09.17D10:00:05;-0D00:00:01 0D00:00:10];
    .qunit.assertEquals[count p;6;"EURUSD and EURPLN, each at 10:00:00, 09:59:59 and 10:00:10: the second execution shares the first's instants, and the leg-less third asks for none"];
    .qunit.assertEquals[asc exec distinct time from p where sym=`EURPLN;asc 2026.09.17D09:59:59 2026.09.17D10:00:00 2026.09.17D10:00:10;"each leg is priced at the execution and at each horizon from it"];
    .qunit.assertEquals[count .qetl.mapped.probes[enlist `symbol$();enlist 2026.09.17D10:00:00;enlist 0D00:00:01];0;"an execution no route prices asks for nothing"]};

/ --- depth, through the row number --------------------------------------------------------------------------

.qt.qetl.mapped.test_depth_is_read_through_the_row_number_and_only_for_the_matched_rows:{[t]
    / market_data with ladders, the flat top being their first level, as a real partition has them
    flat:update bid:1+0.001*i, ask:2+0.001*i from .qt.qetl.mapped.market[200;8];
    md:.qt.support.stand_in_market_data update bid_prices:{x,x-0.001 0.002}each bid, ask_prices:{x,x+0.001 0.002}each ask from flat;
    p:([] sym:`EURUSD`GBPUSD; time:2026.09.16D12:00:00 2026.09.17D12:00:00);
    r:.qt.qetl.mapped.with_market[md;{[p]
        b:.qetl.mapped.tob_at`probes`read_from`skew`venues!(p;2026.09.14D00:00:00;1;`all);
        .qetl.mapped.depth_at`books`cols!(b;`bid_prices`ask_prices)};enlist p];
    .qunit.assertTrue[0<count r;"books matched"];
    .qunit.assertEquals[first each r`bid_prices;r`bid;"the first level of the depth read at the rid is the flat bid the aj returned"];
    .qunit.assertEquals[count each r`ask_prices;(count r)#3;"all three levels, of those rows only"];
    .qunit.assertTrue[(count r)<count md;"and not the day's"];
    wrong:.qt.qetl.mapped.with_market[md;{[p]
        b:.qetl.mapped.tob_at`probes`read_from`skew`venues!(p;2026.09.14D00:00:00;1;`all);
        @[{.qetl.mapped.depth_at`books`cols!(x;`bid_prices)};update rid:rid+1 from b;{[e] e}]};enlist p];
    .qunit.assertTrue[wrong like "*depth_at: a rid does not point at the row its flat top describes*";"a rid that is not the row's position is refused, not read"]};

/ --- the newest book --------------------------------------------------------------------------------------------

.qt.qetl.mapped.test_the_newest_book_of_a_market_is_read_from_the_ends_of_its_runs:{[t]
    rows:([] sym:`EURUSD`EURUSD`BTCUSD; source:`a`b`x; source_time:2026.09.17D10:00:30 2026.09.17D10:00:20 2026.09.17D11:00:00;
        market:`fx`fx`crypto; bid:3#1f; ask:3#2f; clock:3#`venue);
    md:.qt.support.stand_in_market_data rows,update source_time:2026.09.16D23:00:00 from 1#rows;
    fx:.qt.qetl.mapped.with_market[md;{[m] .qetl.mapped.newest enlist[`market]!enlist m};enlist `fx];
    cr:.qt.qetl.mapped.with_market[md;{[m] .qetl.mapped.newest enlist[`market]!enlist m};enlist `crypto];
    any_:.qt.qetl.mapped.with_market[md;{[m] .qetl.mapped.newest enlist[`market]!enlist m};enlist `];
    .qunit.assertEquals[(fx;cr;any_);(2026.09.17D10:00:30;2026.09.17D11:00:00;2026.09.17D11:00:00);"each market's newest, from the newest partition only; ` for any"];
    none:.qt.qetl.mapped.with_market[md;{[m] .qetl.mapped.newest enlist[`market]!enlist m};enlist `equity];
    .qunit.assertEquals[none;-0Wp;"a market with no book there vouches for nothing"]};

/ #2571: a Saturday partition holds crypto alone; FX's newest is Friday's, not -0Wp.
.qt.qetl.mapped.test_the_newest_book_of_a_market_absent_from_the_newest_partition_is_an_earlier_days:{[t]
    rows:([] sym:`EURUSD`BTCUSDT; source:`a`x; source_time:2026.11.06D21:59:00 2026.11.07D10:00:00;
        market:`fx`crypto; bid:2#1f; ask:2#2f; clock:2#`venue);
    md:.qt.support.stand_in_market_data rows;
    fx:.qt.qetl.mapped.with_market[md;{[m] .qetl.mapped.newest enlist[`market]!enlist m};enlist `fx];
    .qunit.assertEquals[fx;2026.11.06D21:59:00;"Friday's FX book, the newest partition holding one"];
    none:.qt.qetl.mapped.with_market[md;{[m] .qetl.mapped.newest enlist[`market]!enlist m};enlist `equity];
    .qunit.assertEquals[none;-0Wp;"a market no partition holds vouches for nothing"]};
