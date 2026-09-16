/ demo_events.q - a generic analogue of a venue order/trade event tape
/ (.qsevt).
/ .
/ The ingestion half of issue #46. Shape and rationale in docs/architecture/event-tape.md;
/ this file is the ETL-12 source declaration for it.
/ .
/ SYNTHETIC BY DESIGN (A-04)
/ .
/ Every column is one any venue's tape would carry and every value is
/ invented. Nothing about a real venue's schema, a bank's data, or any
/ business logic is recoverable from it. That is the same standing as
/ demo_deals.q and for the same reason.
/ .
/ WHY THIS IS THE MICROSTRUCTURE TAPE AND NOT AN OMS TAPE
/ .
/ The seven ROADMAP features blocked on #46 need `action` in {add,cancel,
/ trade}, an AGGRESSOR side, and a per-event size - flow-toxicity inputs.
/ An OMS lifecycle tape (new/ack/fill/cancel/reject over our own orders)
/ unblocks execution-quality metrics instead, which is a different set and
/ largely already served by hit_ratio_by and markout_at_horizons. See
/ docs/architecture/event-tape.md for the comparison.

\d .qsevt

source_name:`demo_events

/ The columns this adapter reads, and their q types.
/ p=timestamp, s=symbol, j=long, f=float. Note `j` for a long, not `l`:
/ that is what `meta` reports, and validate compares against meta - the
/ first draft wrote "l" and was refused at registration, correctly.
fields:`time`sym`action`side`size`price`order_id`pip_factor
types:"pssjffjj"

target:`event_tape

/ The window is taken on event time.
time_field:`time

/ An event is identified by its order and its action: one order_id produces
/ an add and then exactly one terminal event (a cancel or a trade), so the
/ pair is unique where order_id alone is not.
/ .
/ Worth stating because it is the first COMPOSITE key in this tree, and it
/ is what D-11's restatement path will key on if a venue ever corrects an
/ event.
row_key:`order_id`action

time_zone:`UTC

/ ------------------------------------------------------------- THE QUERY

/ Parameterised, never concatenated (ETL-08 / FE-14). Half-open
/ [range_from;range_to) per ETL-08 - >= on the lower bound and < on the
/ upper, so a boundary event is published exactly once.
query:{[h;range_from;range_to]
    h({[from_ts;to_ts]
        select time, sym, action, side, size, price, order_id, pip_factor
            from event_tape
            where time>=from_ts, time<to_ts
      };range_from;range_to)}

/ ------------------------------------------------------------ THE FIXTURE

/ A deterministic synthetic tape, ordered by time (the sortedness contract
/ in docs/architecture/event-tape.md).
/ .
/ Deliberately shaped so the two implemented features have something to
/ measure rather than degenerate:
/ .
/   - both sides trade, with a buy-side skew, so signed_trade_flow is
/     nonzero and signed
/   - cancels outnumber trades, as they do on any real venue, so
/     cancel_to_trade_ratio is > 1 rather than a coin flip
/   - one order_id adds then cancels, another adds then trades, so the
/     composite row_key has both terminal shapes present
fixture:{[]
    ([] time:2026.09.11D09:00:00.000000000+1000000000*til 10;
        sym:10#`EURUSD;
        action:`add`add`trade`cancel`add`cancel`add`trade`add`cancel;
        side:1 -1 1 -1 1 1 -1 -1 1 1;
        size:1000000 2000000 1000000 2000000 500000 500000 1500000 1500000 750000 750000f;
        price:1.0842 1.0840 1.0842 1.0840 1.0843 1.0843 1.0839 1.0839 1.0844 1.0844;
        order_id:1 2 1 2 3 3 4 4 5 5j;
        pip_factor:10#10000j)}

/ Register on load, so the declaration and the implementation cannot drift.
.qsrc.register[source_name;
    `source`table`target`time_field`row_key`fields`types`query`fixture`time_zone!
    (source_name;`event_tape;target;time_field;row_key;fields;types;query;fixture;time_zone)];

\d .
