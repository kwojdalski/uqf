/ upstream_trades.q - a trade table in ANOTHER kdb+ process (.qfeed.upstream_trades).
/ .
/ The other two sources in this directory are analogues of external
/ relational systems, and their live path has never run here because no such
/ system exists on a developer machine. This one is different: its live path
/ is a plain q process loading the vendored starter pack's HDB - the smallest
/ kdb+ instance that can exist, with real data in it - and it is what
/ tests/q/run_two_instances.q stands up. So this is the source through which
/ the framework's live-connection code (.qbw.connect, a source's `query`,
/ .qsrc.validate_live) is actually exercised, rather than merely declared.
/ .
/ The shape is the starter pack's `trade`, which this tree did not design and
/ does not own: date-partitioned, sym `p#, an `int` size where every table of
/ ours uses `long`, a `char` exchange code. That is the point. A source
/ declaration describes what the OTHER side has, and the transform is where
/ it becomes ours.

\d .qfeed.upstream_trades

source_name:`upstream_trades

/ The columns read, and their q types AS THE UPSTREAM HAS THEM. `size` is
/ int (i), not long, and `ex` is a single char - validate_live checks this
/ against the real process and would refuse the declaration if it lied.
fields:`time`sym`ex`price`size`side
types:"pscfis"

/ Where the rows land locally - a name that says they came from elsewhere.
target:`imported_trades

time_field:`time

/ THE WHOLE ROW, because nothing smaller identifies one. Measured on the
/ real data: of 196,519 trades on 2015.01.07, only 183,660 (time;sym) pairs
/ are distinct, 189,988 (time;sym;ex), and 196,509 even with price and size
/ - but no two rows are identical. The starter pack is a synthetic tick feed
/ with no trade id, and a key that pretended otherwise would silently merge
/ distinct trades on restatement (D-11). Declaring the truth costs nothing
/ here and is exactly what the row_key exists to make explicit.
row_key:`time`sym`ex`price`size`side

/ The starter pack's timestamps are wall-clock New York with no zone recorded
/ anywhere. This declaration says UTC because the DATA says nothing, and a
/ zone claimed with no evidence is worse than a zone stated as unknown - the
/ two days of sample data are used as intervals, never as instants that must
/ line up with anything else.
tz:`UTC

/ ------------------------------------------------------------- THE QUERY

/ A parameterised functional select on the remote side (ETL-08), never a
/ string. The bounds are ARGUMENTS.
/ .
/ The upstream is date-partitioned, so the partition column is constrained
/ FIRST: without `date within`, a one-hour window would scan every
/ partition on disk before the time filter saw a row.
/ .
/ `trade WITH A BACKTICK, never bare. A lambda carries its defining
/ namespace across the wire, and this one is defined under \d .qfeed.upstream_trades - so on
/ the remote, a bare `trade` resolves as `.qfeed.upstream_trades.trade`, which does not exist
/ there, and the query throws 'trade. The symbol form is resolved by the
/ remote's own select at ITS root, which is where the table is. Found the
/ first time any source in this tree ran live; both older sources have the
/ same latent fault, and test_source_contract.q now reads every source's
/ query block and refuses the bare form.
/ @param h an open handle to the upstream process
/ @param range_from inclusive lower bound, in the source's own clock
/ @param range_to exclusive upper bound
/ @return the upstream's rows in the window, in its own shape
query:{[h;range_from;range_to]
    h({[from_ts;to_ts]
        select time, sym, ex, price, size, side from `trade
            where date within `date$(from_ts;to_ts), time>=from_ts, time<to_ts
      };range_from;range_to)}

/ ------------------------------------------------------------ THE FIXTURE

/ Ten rows CUT FROM THE SAME DATA the live process serves - the earliest
/ trade of each symbol on 2015.01.07, copied verbatim - so the fixture and
/ the live path are the same table twice, and validate_fixture checks the
/ shape validate_live will. Two things about it are the upstream's, not
/ ours: `side` is `buy or `side (its vocabulary, whatever it means by the
/ second), and `ex` is a one-character exchange code. Sorted by time, as
/ the window logic requires.
/ @return ten real upstream rows, sorted by time
/ @eg .qfeed.upstream_trades.fixture[]
fixture:{[]
    `time xasc ([]
        time:2015.01.07D00:00:02.038247000 2015.01.07D00:00:02.838234000 2015.01.07D00:00:01.638238000
             2015.01.07D00:00:01.438268000 2015.01.07D00:00:01.638238000 2015.01.07D00:00:01.838143000
             2015.01.07D00:00:00.638306000 2015.01.07D00:00:01.438268000 2015.01.07D00:00:01.238273000
             2015.01.07D00:00:03.838217000;
        sym:`AAPL`AIG`AMD`DELL`DOW`GOOG`HPQ`IBM`INTC`MSFT;
        ex:"NNONOONNNN";
        price:28.73 31.79 43.79 14.88 35.8 71.34 53.53 23.67 50.9 20.12;
        size:41 19 1 2 41 47 9 32 7 42i;
        side:`side`buy`side`side`buy`side`buy`buy`buy`buy)}

.qsrc.register[source_name;
    `source`table`target`time_field`row_key`fields`types`query`fixture`tz!
    (source_name;`trade;target;time_field;row_key;fields;types;query;fixture;tz)];

\d .
