/ test_order_outcomes.q - the order-outcome contract and its adapters
/ (.order_outcomestest, #1396).
/ .
/ Three things: the `orders` adapter (statuses, source time, identity scope, missing
/ dimensions, empty and malformed input); a SECOND, differently shaped source
/ whose several-updates-per-order lifecycles must give the same metrics as the
/ orders feed without the quality transform changing; and the policy the two
/ share. The statistic's own counting rules are test_order_quality.q's.

\d .order_outcomestest

t0:2026.10.09D10:00:00
as_of:t0+0D00:10:00

/ ---------------------------------------------------------- THE ORDERS ADAPTER

/ An orders batch: time, order_id, book, product, order_status.
orders_rows:{[tms;ids;bks;prods;sts] ([] time:tms; order_id:ids; sym:(count tms)#`EURUSD; book:bks; product:prods;
    side:(count tms)#1; size:(count tms)#1e6; price:(count tms)#1.1; order_status:sts)}

adapt:{[b] .qetl.job.stream.normalizer.normalize[`order_outcomes;`orders;b]}

contract_driver:{[]
    .qpipe.job.order_outcomes.on_batch[`orders;orders_rows[enlist t0;enlist 1;enlist `london;enlist `spot;enlist `filled]]}

test_the_adapter_output_is_the_contract:{[t]
    r:adapt[orders_rows[t0+0D00:00:01*til 3;1 2 3;`london`london`newyork;`spot`spot`fwd;`filled`rejected`cancelled]];
    .qunit.assertEquals[cols r;cols .qetl.plant.published `order_outcomes;"the plant table, column for column"];
    .qunit.assertEquals[r`outcome;`filled`rejected`cancelled;"each terminal status is its own outcome"];
    .qunit.assertEquals[r`source_status;`filled`rejected`cancelled;"the source's code is kept"]};

test_every_outcome_is_in_the_vocabulary_whatever_the_status:{[t]
    r:adapt[orders_rows[t0+0D00:00:01*til 4;1 2 3 4;4#`london;4#`spot;`filled`new`partially_filled`]];
    .qunit.assertTrue[all r[`outcome] in .qpipe.transform.order_outcome.outcomes;"nothing outside the vocabulary"];
    .qunit.assertEquals[r`outcome;`filled`unmapped`unmapped`unmapped;"an unknown or null status is unmapped, not dropped, not guessed"];
    .qunit.assertEquals[count r;4;"and the row is kept, so the exclusion is visible"]};

test_the_event_time_is_the_sources_and_the_plant_stamps_its_own:{[t]
    r:adapt[orders_rows[enlist t0;enlist 1;enlist `london;enlist `spot;enlist `filled]];
    .qunit.assertEquals[first r`source_time;t0;"source_time is the order's time"];
    .qunit.assertFalse[`time in cols r;"the adapter publishes no receipt time: the plant stamps it"]};

test_a_request_id_is_scoped_by_its_source:{[t]
    r:adapt[orders_rows[enlist t0;enlist 7;enlist `london;enlist `spot;enlist `filled]];
    .qunit.assertEquals[(first r`source;first r`request_id);(`orders;`7);"the pair is the identity"];
    / ("a:b";"c") and ("a";"b:c") cannot meet: the parts are columns, not a joined string
    .qunit.assertEquals[type first r`source;-11h;"the scope is a column of its own, not part of a joined string"]};

test_venue_is_null_because_orders_carries_none:{[t]
    r:adapt[orders_rows[t0+0D00:00:01*til 2;1 2;`london`newyork;`spot`fwd;`filled`rejected]];
    .qunit.assertTrue[all null r`venue;"never defaulted"];
    .qunit.assertEquals[(r`book;r`product);(`london`newyork;`spot`fwd);"book and product pass through"]};

test_missing_dimensions_stay_null:{[t]
    r:adapt[orders_rows[enlist t0;enlist 1;enlist `;enlist `;enlist `filled]];
    .qunit.assertTrue[(null first r`book) and null first r`product;"a null book is not made up"];
    .qunit.assertEquals[first r`outcome;`filled;"and the outcome is still read"]};

test_an_empty_batch_is_an_empty_contract_table:{[t]
    r:adapt[0#orders_rows[enlist t0;enlist 1;enlist `london;enlist `spot;enlist `filled]];
    .qunit.assertEquals[(count r;cols r);(0;cols .qetl.plant.published `order_outcomes);"no rows, the right columns"]};

test_a_row_without_an_id_is_unmapped_not_invented:{[t]
    r:adapt[orders_rows[t0+0D00:00:01*til 2;1 0N;2#`london;2#`spot;`filled`filled]];
    .qunit.assertEquals[r`outcome;`filled`unmapped;"cannot be identified, so cannot be counted"];
    .qunit.assertTrue[null last r`request_id;"and no id is fabricated"]};

test_a_batch_missing_a_column_is_refused_by_name:{[t]
    .qunit.assertThrows[{adapt[x]};([] time:enlist t0; order_id:enlist 1);
        "*orders batch for order_outcomes*";"the shell names the source and the missing column"]};

test_the_status_map_refuses_a_made_up_outcome:{[t]
    .qunit.assertThrows[{.qpipe.transform.order_outcome.status_map[`F`G;x]};`filled`done;
        "*done is not an order outcome*";"an adapter cannot invent a sixth outcome"]};

/ ----------------------------------------------- A SECOND, DIFFERENT SOURCE
/ AltOMS: another column for every concept, single-letter state codes, a venue,
/ and several messages per order. It is declared here, in the test, because it is
/ a fixture: the point is that a source is one transform and nothing else changes.

alt_events:([] msg_time:`timestamp$(); cl_ord_id:`symbol$(); ord_state:`symbol$(); desk:`symbol$();
    instr_class:`symbol$(); lp:`symbol$(); qty:`float$(); last_px:`float$())

alt_status:.qpipe.transform.order_outcome.status_map[`N`P`E`F`R`C`X;`unresolved`unresolved`unresolved`filled`rejected`cancelled`cancelled]

/ AltOMS messages as order outcomes: a rename, a status table and nothing else.
from_alt:{[b]
    out:.qpipe.transform.order_outcome.translate[.order_outcomestest.alt_status;b`ord_state];
    select source_time:msg_time, source:`alt_oms, request_id:cl_ord_id, source_status:ord_state,
        outcome:?[null cl_ord_id;`unmapped;out], book:desk, product:instr_class, venue:lp from b}

alt_example:([] msg_time:2026.10.09D10:00:00 2026.10.09D10:00:01 2026.10.09D10:00:02 2026.10.09D10:00:03;
    cl_ord_id:`a1`a1`a1`a2; ord_state:`N`P`F`Z; desk:4#`london; instr_class:4#`spot;
    lp:`lp1`lp1`lp1`lp2; qty:4#1e6; last_px:4#1.1)

ensure_alt:{[]
    if[not `order_outcomes_from_alt_oms in .qetl.transform.defined[];
        .qetl.transform.define[`order_outcomes_from_alt_oms;`inputs`output`fn`examples!(
            (enlist `alt_events)!enlist 0#alt_events;
            .qpipe.job.order_outcomes.order_outcomes;
            from_alt;
            enlist `inputs`expected!(
                (enlist `alt_events)!enlist alt_example;
                ([] source_time:alt_example`msg_time; source:4#`alt_oms; request_id:`a1`a1`a1`a2;
                    source_status:`N`P`F`Z; outcome:`unresolved`unresolved`filled`unmapped;
                    book:4#`london; product:4#`spot; venue:`lp1`lp1`lp1`lp2)))]]}

/ The same six requests, as each source reports them. ids/times/outcomes are the
/ requests' own: terminal event (seconds before as_of; outcome; book).
reqs:((50;`filled;`london);(40;`rejected;`london);(30;`cancelled;`newyork);
    (20;`filled;`london);(240;`rejected;`newyork);(1800;`filled;`london))

/ As the orders feed reports them: one row, at the final status.
feed_a:{[]
    n:count reqs;
    orders_rows[as_of-0D00:00:01*reqs[;0];1+til n;reqs[;2];n#`spot;reqs[;1]]}

/ One request's AltOMS messages as (request number; seconds before as_of; state; desk):
/ new, then a partial fill if it fills, then the terminal state. A reject has no partial.
msgs:{[i;r]
    s:r 0;
    st:$[r[1]=`filled; ((s+20;`N);(s+10;`P);(s;`F)); r[1]=`rejected; ((s+20;`N);(s;`R)); ((s+20;`N);(s;`C))];
    {[i;b;m] (i;m 0;m 1;b)}[i+1;r 2] each st}

/ As AltOMS reports them: several messages per order, an amendment on order 1, a
/ message in a state the adapter does not know on order 2, and order 4's fill
/ delivered twice.
feed_b:{[]
    m:(raze msgs'[til count reqs;reqs]),((1;65;`E;`london);(2;50;`Z;`london);(4;20;`F;`london));
    t:([] msg_time:as_of-0D00:00:01*m[;1]; cl_ord_id:`$"A",/:string m[;0]; ord_state:m[;2]; desk:m[;3]);
    update instr_class:`spot, lp:`lp1, qty:1e6, last_px:1.1 from `msg_time xasc t}

/ The quality transform's output over a source's outcomes at as_of.
metrics:{[outs]
    r:update time:source_time from outs;
    .qetl.job.stream.rolling.compute[`order_quality;(`source_time`source`request_id`outcome`book)#r;as_of;0Np]}

test_the_second_source_is_a_valid_mapping_with_a_verified_example:{[t]
    ensure_alt[];
    .qunit.assertTrue[all exec passed from .qetl.transform.verify `order_outcomes_from_alt_oms;"its worked example, and an empty batch, hold"];
    .qunit.assertEquals[count .qetl.job.stream.normalizer.check_source["alt";.qpipe.job.order_outcomes.order_outcomes;`alt_events;`order_outcomes_from_alt_oms];1;"the normalizer shell would accept it: the canonical table, column for column"]};

test_two_differently_shaped_sources_give_identical_metrics:{[t]
    ensure_alt[];
    a:metrics[adapt[feed_a[]]];
    b:metrics[from_alt[feed_b[]]];
    .qunit.assertTrue[0<count a;"the fixtures produce a series"];
    .qunit.assertEquals[a;b;"identical metrics, and the quality transform was not touched"];
    / the lifecycle really is several messages per order
    .qunit.assertTrue[(count feed_b[])>2*count feed_a[];"AltOMS reports many messages per order"]};

test_the_normalized_outcomes_of_the_two_sources_are_equivalent:{[t]
    ensure_alt[];
    a:adapt[feed_a[]];
    b:from_alt[feed_b[]];
    term:{select source_time, outcome, book from x where outcome in .qpipe.transform.order_outcome.terminal};
    .qunit.assertEquals[`source_time xasc distinct term a;`source_time xasc distinct term b;"the same terminal outcomes at the same source times"];
    .qunit.assertTrue[all null a`venue;"orders supplies no venue"];
    .qunit.assertEquals[distinct b`venue;enlist `lp1;"AltOMS does"]};

test_redelivery_and_multi_update_cannot_inflate_the_denominator:{[t]
    ensure_alt[];
    b:metrics[from_alt[feed_b[]]];
    a:metrics[adapt[feed_a[]]];
    f:{first exec n_orders from x where book=`london, window=0D01};
    .qunit.assertEquals[f b;f a;"the 1h denominator is the number of requests, not of messages"];
    .qunit.assertEquals[f b;4;"london has four requests, whatever AltOMS sends"]};

test_a_request_id_from_two_sources_does_not_collide_in_the_metrics:{[t]
    ensure_alt[];
    / the same id, `1, from the orders feed and from AltOMS: two requests
    one:adapt[orders_rows[enlist as_of-0D00:00:20;enlist 1;enlist `london;enlist `spot;enlist `filled]];
    two:from_alt[([] msg_time:enlist as_of-0D00:00:10; cl_ord_id:enlist `$"1"; ord_state:enlist `R;
        desk:enlist `london; instr_class:enlist `spot; lp:enlist `lp1; qty:enlist 1e6; last_px:enlist 1.1)];
    .qunit.assertEquals[(first one`request_id)=first two`request_id;1b;"the ids are equal"];
    m:metrics[one,two];
    .qunit.assertEquals[first exec n_orders from m where window=0D00:01;2;"two sources, one id, two requests"]};

\d .
