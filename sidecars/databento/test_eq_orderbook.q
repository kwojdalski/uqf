/ test_eq_orderbook.q - the databento bundle's live fold, and the backfill twin it
/ shares a transform with (.qt.qpipe.job.eq_orderbook).
/ .
/ The job under test republishes rows folded by the SAME .qetl.transform transform the
/ ODBC backfill applies. That sharing is the point of the job existing, so
/ the cases below check the wiring and the two things the job itself
/ decides - which table it listens to, and that the venue clock survives -
/ rather than re-testing the fold, which test_transform.q already runs
/ against its own declared examples.
/ .
/ These cases lived in the tree's test_stream_job.q, test_twins.q and
/ test_dqchecks.q while the tree held the job. They moved with it (#1775).

.qt.qpipe.job.eq_orderbook.published:()

.qt.qpipe.job.eq_orderbook.recorder:{[t;x] `.qt.qpipe.job.eq_orderbook.published set .qt.qpipe.job.eq_orderbook.published,enlist (t;x); count x}

/ The last publication onto one table, as rows.
.qt.qpipe.job.eq_orderbook.last_on:{[tbl] last last .qt.qpipe.job.eq_orderbook.published where tbl=first each .qt.qpipe.job.eq_orderbook.published}

/ The last eq_orderbook publication's rows.
.qt.qpipe.job.eq_orderbook.last_rows:{[] .qt.qpipe.job.eq_orderbook.last_on[`eq_orderbook]}

/ Empty the job's state and wire publish to the recorder.
.qt.qpipe.job.eq_orderbook.drive_ready:{[]
    .qetl.job.stream.reset `eq_orderbook;
    `.qt.qpipe.job.eq_orderbook.published set ();
    .qetl.job.stream.wire[`eq_orderbook;.qt.qpipe.job.eq_orderbook.recorder];
    }

/ A live batch, shaped as it arrives off the tickerplant - the source's own
/ fixture with a `time` prepended, which is what .u.upd does to every message
/ on receipt, and its columns named by the plant table.
.qt.qpipe.job.eq_orderbook.mbp10_batch:{[] .qt.qpipe.job.eq_orderbook.plant_shaped .qpipe.source.databento_mbp10.fixture[]}

/ Source-contract rows as the tickerplant delivers them: its `time` first and
/ the instrument named `sym`, as the plant table databento_mbp10 names it.
.qt.qpipe.job.eq_orderbook.plant_shaped:{[f] `time xcols update time:.z.p from @[cols f;where `symbol=cols f;:;`sym] xcol f}

.qt.qpipe.job.eq_orderbook.test_a_plant_shaped_batch_is_folded_not_refused:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    live:.qt.qpipe.job.eq_orderbook.mbp10_batch[];
    .qunit.assertEquals[3#cols live;`time`ts_event`sym;"the batch is the plant table's shape"];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;live];
    .qunit.assertEquals[count .qt.qpipe.job.eq_orderbook.last_rows[];count live;"a live batch is folded and published (#2600)"]};

.qt.qpipe.job.eq_orderbook.test_databento_folds_a_live_batch_and_republishes_it:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    out:.qt.qpipe.job.eq_orderbook.last_rows[];
    .qunit.assertEquals[
        (first first .qt.qpipe.job.eq_orderbook.published;count out;count out[0;`bid_prices]);
        (`eq_orderbook;4;10);
        "four MBP-10 records fold into four book rows of ten levels a side"]};

.qt.qpipe.job.eq_orderbook.test_databento_keeps_the_venue_clock_as_its_own_column:{[t]
    / THE POINT OF ts_event. .u.upd stamps `time` on receipt, so a book
    / carrying only `time` would say when it ARRIVED and nothing about when
    / it happened - and a feed stuck an hour behind would look current. A
    / Binance book in the cryptorust recorder was stamped 1973 for weeks
    / because the only clock came from the venue and nothing could
    / cross-check it.
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    out:.qt.qpipe.job.eq_orderbook.last_rows[];
    .qunit.assertEquals[`ts_event in cols out;1b;"the venue clock is carried through"];
    .qunit.assertEquals[out[0;`ts_event];(.qpipe.source.databento_mbp10.fixture[])[0;`ts_event];
        "and it is Databento's value, not the time the row was folded"]};

.qt.qpipe.job.eq_orderbook.test_databento_does_not_republish_the_tickerplants_time:{[t]
    / One column too wide is a live `length error at the tickerplant, not a
    / warning - .qtorq.publish drops `time`, and this job must not hand it
    / one to drop in the first place.
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    .qunit.assertEquals[`time in cols .qt.qpipe.job.eq_orderbook.last_rows[];0b;
        "the receipt stamp is the tickerplant's to add, not this job's to send"]};

.qt.qpipe.job.eq_orderbook.test_databento_ignores_a_table_it_did_not_subscribe_to:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`fx_orderbook;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    .qunit.assertEquals[count .qt.qpipe.job.eq_orderbook.published;0;"a batch from another table is ignored"]};

.qt.qpipe.job.eq_orderbook.test_databento_publishes_nothing_for_an_empty_batch:{[t]
    / An empty batch is ordinary on a quiet symbol. Publishing a zero-row
    / message would put an empty write on the tickerplant every tick.
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;0#.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    .qunit.assertEquals[count .qt.qpipe.job.eq_orderbook.published;0;"an empty batch publishes nothing"]};

.qt.qpipe.job.eq_orderbook.test_databento_keeps_no_state:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    .qunit.assertEquals[count .qt.qpipe.job.eq_orderbook.published;4;"each batch stands alone: a book and its market_data per batch"]};

/ --- market_data: the same books, scoreable (#1939) ---------------------------

.qt.qpipe.job.eq_orderbook.test_a_folded_book_is_also_published_as_market_data:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    md:.qt.qpipe.job.eq_orderbook.last_on[`market_data];
    book:.qt.qpipe.job.eq_orderbook.last_rows[];
    .qunit.assertEquals[first each .qt.qpipe.job.eq_orderbook.published;`eq_orderbook`market_data;"the book, then its market_data"];
    .qunit.assertEquals[count md;count book;"one snapshot per folded book"];
    .qunit.assertEquals[.qetl.plant.problems[`market_data;md];();"to the plant's market_data contract"];
    .qunit.assertEquals[distinct md`market;enlist `equity;"market is equity"];
    .qunit.assertEquals[distinct md`clock;enlist `venue;"the clock is the venue's"];
    .qunit.assertEquals[distinct md`source;enlist `XNAS;"source is the dataset"]};

.qt.qpipe.job.eq_orderbook.test_market_data_source_time_is_the_venue_stamp_not_receipt:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;update time:.z.p from .qpipe.source.databento_mbp10.fixture[]];
    .qunit.assertEquals[(.qt.qpipe.job.eq_orderbook.last_on[`market_data])`source_time;(.qt.qpipe.job.eq_orderbook.last_rows[])`ts_event;
        "source_time is ts_event, which is not a time the tickerplant stamped"]};

.qt.qpipe.job.eq_orderbook.test_market_data_ladders_are_the_books_as_floats:{[t]
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    md:.qt.qpipe.job.eq_orderbook.last_on[`market_data];
    book:.qt.qpipe.job.eq_orderbook.last_rows[];
    .qunit.assertEquals[md`bid_prices;book`bid_prices;"prices pass through"];
    .qunit.assertEquals[md`ask_sizes;`float$/:book`ask_sizes;"sizes are the book's, as floats"]};

.qt.qpipe.job.eq_orderbook.test_the_publish_seam_drops_a_crossed_market_data_book_and_keeps_the_book:{[t]
    / the wired seam sees both tables: the crossed snapshot is withheld from market_data, while
    / eq_orderbook (which the gate does not judge) keeps all four rows
    .qt.qpipe.job.eq_orderbook.drive_ready[];
    crossed:update bid_px_00:ask_px_00+1f from .qt.qpipe.job.eq_orderbook.mbp10_batch[] where i=3;
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;crossed];
    .qunit.assertEquals[count .qt.qpipe.job.eq_orderbook.last_rows[];4;"the book is published whole"];
    .qunit.assertEquals[count .qt.qpipe.job.eq_orderbook.last_on[`market_data];3;"the crossed snapshot is not market_data"]};

/ What test_job_output_contracts.q drives the job with.
.qt.qpipe.job.eq_orderbook.contract_driver:{[]
    .qetl.job.stream.reset `eq_orderbook;
    .qpipe.job.eq_orderbook.on_batch[`databento_mbp10;.qt.qpipe.job.eq_orderbook.mbp10_batch[]];
    }

/ --- the twin: one transform, shared ----------------------------------------

/ test_twins.q lists every stream job with a backfill twin, with how its transform's
/ inputs are renamed for the twin's. The twin reads the same names here, so none.
.qt.qpipe.job.eq_orderbook.twin_renames:()!()

.qt.qpipe.job.eq_orderbook.test_the_eq_orderbook_twin_applies_the_same_transform:{[t]
    .qunit.assertTrue[.qt.qpipe.twins.agrees[`eq_orderbook];"one transform, shared"]};

/ #1076: the backfill writes the fold to the HDB as it is, so the fold is
/ the plant's table - ts_event included - or historical partitions lack a
/ column the live ones carry, and `time` means event time in one and
/ receipt time in the other.
.qt.qpipe.job.eq_orderbook.test_the_eq_orderbook_backfill_writes_the_plant_shape:{[t]
    f:.qpipe.transform.eq_orderbook.to_book .qpipe.source.databento_mbp10.fixture[];
    .qunit.assertEquals[cols f;cols .qetl.plant.shape `eq_orderbook;"the plant's columns, in its order, ts_event included"];
    .qunit.assertEquals[.qetl.plant.problems[`eq_orderbook;![f;();0b;enlist `time]];();"with its types, nested columns included"];
    .qunit.assertEquals[f`time;f`ts_event;"history has no receipt time: time is the event time"]};

/ --- one rule for a crossed book: the live check and this backfill's gate -----

/ (#1176) "is this book crossed" has one rule (.qf.mkt.crossed_top), so the live check
/ and the backfill gate agree on a locked, a one-sided and a crossed book.
.qt.qpipe.job.eq_orderbook.live_quotes:{[b] ([] time:count[b]#2026.01.01D00:00:00.000000000; sym:count[b]#`X; bid_prices:b`bid_prices; bid_sizes:b`bid_sizes; ask_prices:b`ask_prices; ask_sizes:b`ask_sizes)}

/ The live check's crossed count and the gate's, for one fixture variant.
.qt.qpipe.job.eq_orderbook.gate_crossed:{[f]
    b:.qetl.transform.apply[`eq_orderbook;enlist[`batch]!enlist f];
    live:count select from .qf.mkt.check_market_data_quality[.qt.qpipe.job.eq_orderbook.live_quotes b;1000] where status=`crossed;
    gate:count select from .qpipe.job.eq_orderbook_backfill.quality_check[b] where check=`crossed_top_of_book;
    (live;gate)}

.qt.qpipe.job.eq_orderbook.test_a_locked_book_is_not_crossed_on_the_live_check_or_the_gate:{[t]
    f:update bid_px_00:ask_px_00 from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[.qt.qpipe.job.eq_orderbook.gate_crossed f;0 0;"locked (bid = ask): both pass it"]};

.qt.qpipe.job.eq_orderbook.test_a_one_sided_book_is_not_crossed_on_the_live_check_or_the_gate:{[t]
    f:update bid_px_00:0n from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[.qt.qpipe.job.eq_orderbook.gate_crossed f;0 0;"one-sided: absent, not crossed, on both"]};

.qt.qpipe.job.eq_orderbook.test_a_crossed_book_is_crossed_on_the_live_check_and_the_gate:{[t]
    f:update bid_px_00:ask_px_00+1f from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[.qt.qpipe.job.eq_orderbook.gate_crossed f;1 1;"bid above ask: flagged by both"]};

/ #1210: a level 0 at a positive price but zero size is absent on every path.
.qt.qpipe.job.eq_orderbook.test_a_zero_size_top_is_absent_not_crossed_on_the_live_check_or_the_gate:{[t]
    f:update bid_px_00:ask_px_00+1f, bid_sz_00:0 from .qpipe.source.databento_mbp10.fixture[] where i=3;
    .qunit.assertEquals[.qt.qpipe.job.eq_orderbook.gate_crossed f;0 0;"zero-size top bid: not crossed on either"];
    b:.qetl.transform.apply[`eq_orderbook;enlist[`batch]!enlist f];
    r:.qf.mkt.check_market_data_quality[.qt.qpipe.job.eq_orderbook.live_quotes b;1000];
    .qunit.assertEquals[count select from r where status=`one_sided;1;"the live check calls the zero-size top one_sided"]};
