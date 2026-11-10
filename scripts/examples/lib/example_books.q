/ example_books.q - the synthetic order-book plumbing the worked examples under
/ scripts/examples/ share: a 10-level book, per-level wide columns, and
/ jittered event timestamps. Each example used to carry its own copy.
/ .
/ Here, not under src/: nothing in the library uses it, and it narrates through
/ .qetl.log, which no library module reads. Not an example itself, so it sits in
/ lib/, which the worked-example runner's `scripts/examples/*.q` does not reach.
/ An example loads it after src/init.q (which brings
/ src/examples/example_defaults.q, whose .qf.demo constants these read) and
/ src/etl/core/log.q:
/   \l scripts/examples/lib/example_books.q
/ What differs between examples - the timestamp cadence, how many rows - is an
/ argument, so each example's call still shows the numbers it teaches with.

/ A single 10-level top-of-book: bid/ask start .qf.demo.pip_size apart at spot
/ and walk out one pip per level; sizes grow by .qf.demo.level_size_unit per
/ level, ask offset a tenth of a level below bid so bid/ask sizes stay visually
/ distinct. .qf.demo.level_cols applies the same pattern to a whole wide table.
/ @param spot the mid the ladder starts from
/ @return dict bid_prices, bid_sizes, ask_prices, ask_sizes - ten each
.qf.demo.book:{[spot]
    levels:til 10;
    bid_prices:spot-.qf.demo.pip_size*levels;
    ask_prices:(spot+.qf.demo.pip_size)+.qf.demo.pip_size*levels;
    bid_sizes:.qf.demo.level_size_unit*1+levels;
    ask_sizes:(.qf.demo.level_size_unit-.qf.demo.level_size_unit%10)+.qf.demo.level_size_unit*levels;
    `bid_prices`bid_sizes`ask_prices`ask_sizes!(bid_prices;bid_sizes;ask_prices;ask_sizes)}

/ One prefix's worth of zero-padded, per-level columns as a single dict
/ col_name!column_vector, e.g. bid_px_00!... bid_px_09!... .
/ @param prefix the column name prefix, a string such as "bid_px_"
/ @param n_levels how many levels (columns)
/ @param base level 0's value
/ @param step the change from one level to the next
/ @param row_step the per-row drift (a sub-pip one for prices, a small size jitter for sizes)
/ @param n_rows how many rows each column has
/ @return dict of symbol column names to float vectors
.qf.demo.level_cols:{[prefix;n_levels;base;step;row_step;n_rows]
    names:{[prefix;i] `$prefix,$[i<10;"0",string i;string i]}[prefix] each til n_levels;
    names!{[base;step;row_step;n_rows;i] (base+step*i)+row_step*til n_rows}[base;step;row_step;n_rows] each til n_levels}

/ Synthetic event timestamps: each lands a jittered gap after the previous one,
/ the gap drawn from Normal(mean, std) via this repo's own .qf.math.inv_ncdf
/ (inverse normal CDF) - the irregular arrival cadence of a real feed. Floored
/ at `min` so timestamps stay strictly increasing (an unclamped Normal draw
/ can go negative, which a real feed would not).
/ @param example the example's name, for the narration line's `example` field
/ @param n how many timestamps
/ @param start_ts the instant the first gap is added to
/ @param gaps dict mean, std, min - timespans
/ @return n strictly increasing timestamps
.qf.demo.timestamps:{[example;n;start_ts;gaps]
    p:1e-9+(1-2e-9)*n?1.0;
    .qetl.log.ch.dbg[`lifecycle;"running: .qf.math.inv_ncdf p";enlist[`example]!enlist example];
    shock:.qf.math.inv_ncdf p;
    start_ts+sums gaps[`min]|gaps[`mean]+gaps[`std]*shock}
