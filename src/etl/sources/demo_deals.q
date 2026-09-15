/ demo_deals.q - a generic analogue of an external relational deal source
/ (.qsdemo).
/ .
/ WHY THIS IS AN ANALOGUE AND NOT A PORT (E-09, answered via A-04)
/ .
/ The canonical tree has workers over `marketwarehouse_deals` and
/ `piggybank_general_ledger`. Those names, their schemas, and the business
/ logic over them are bank-internal and must not appear in a public
/ repository - that was decided in A-04 and it is not a technical
/ limitation to be worked around.
/ .
/ So this file reproduces the SHAPE of such a source and none of its
/ content: an external relational table of dealt trades, read in bounded
/ windows by a timestamp, landing in a local table. Every column here is one
/ any FX deal capture system would have, and the fixture's values are
/ synthetic. Nothing about the bank's actual schema, naming or logic is
/ recoverable from it.
/ .
/ Two consequences worth stating plainly, rather than discovering later:
/ .
/   1. This CANNOT validate the real source contract. E-12 asks that live
/      external metadata be validated against the same declaration as the
/      fixture, and the machinery for that is built - but the declaration
/      itself is a guess at a shape nobody here can see. Running
/      `.qsrc.validate_live` against the real source is the only thing that
/      would settle it, and that has to happen on the work machine.
/ .
/   2. A worker built on this is a real worker with a synthetic source, not a
/      demo of a worker. The lifecycle, coverage and retry behaviour it
/      exercises are the production ones.

\d .qsdemo

source_name:`demo_deals

/ The columns this adapter reads, and their q types. Deliberately a small
/ subset of what such a table would have: E-12 asks for the required fields,
/ not every field, and declaring columns the worker does not read would make
/ an upstream change to an unused column break the run.
fields:`deal_id`deal_time`sym`side`notional`rate
/ j=long, p=timestamp, s=symbol, s=symbol, f=float, f=float
types:"jpssff"

/ The local table this lands in.
target:`demo_deals

/ The column the bounded window is taken on. Declared rather than assumed so
/ .qsrc can window the fixture exactly as the live query windows the source.
time_field:`deal_time

/ ------------------------------------------------------------- THE QUERY

/ A PARAMETERISED lambda, never string concatenation (E-08, answered via
/ F-14).
/ .
/ The window bounds are arguments to a functional select evaluated on the
/ remote side, so no caller value is ever spliced into query text. The
/ alternative - building "select from t where time>=" ,string range_from -
/ is how a symbol name or a crafted timestamp string becomes an injection,
/ and it is also how a type coercion bug becomes a silent wrong answer
/ rather than an error.
/ .
/ Half-open [range_from;range_to) throughout, per E-08: >= on the lower
/ bound and < on the upper. Getting that wrong by one operator
/ double-publishes every boundary row, which then appears as a duplicate
/ nobody can explain.
query:{[h;range_from;range_to]
    h({[from_ts;to_ts]
        select deal_id, deal_time, sym, side, notional, rate
            from demo_deals
            where deal_time>=from_ts, deal_time<to_ts
      };range_from;range_to)}

/ ------------------------------------------------------------ THE FIXTURE

/ A synthetic table of the same shape (E-04, answered via A-04 + F-22/F-23).
/ .
/ Deterministic - fixed values, no .z.p, no random - because a fixture that
/ changes between runs makes a failing assertion impossible to attribute.
/ The one thing it must be is contract-satisfying, and
/ test_source_contract.q asserts exactly that.
fixture:{[]
    ([] deal_id:1 2 3 4 5j;
        deal_time:2026.09.11D09:00:00.000000000+1D*til 5;
        sym:`EURUSD`GBPUSD`EURUSD`USDJPY`EURUSD;
        side:`buy`sell`buy`sell`buy;
        notional:1000000 2500000 750000 3000000 1250000f;
        rate:1.0842 1.2631 1.0847 149.82 1.0851)}

/ Register on load, so the declaration and the implementation cannot drift:
/ there is no way to have one without the other.
.qsrc.register[source_name;
    `source`table`target`time_field`fields`types`query`fixture!
    (source_name;`demo_deals;target;time_field;fields;types;query;fixture)];

\d .
