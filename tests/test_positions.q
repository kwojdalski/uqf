// test_positions.q - tests for src/positions.q. Load src/risk.q,
// src/positions.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .positionstest

test_empty_book_is_empty:{[t] .qunit.assertEmpty[.qpos.empty_book[];"empty_book starts with no rows"]};

test_apply_fill_opens_from_flat:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;1000000f;1e-9;"opening buy sets qty"];
    .testutil.assertApprox[row`avg_price;1.1000;1e-9;"opening buy sets avg_price to the fill price"];
    .testutil.assertApprox[row`realized_pnl;0f;1e-9;"opening a position realizes nothing"]};

test_apply_fill_opens_short:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;-1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;-1000000f;1e-9;"opening sell sets negative qty"];
    .testutil.assertApprox[row`avg_price;1.1000;1e-9;"avg_price is still the fill price"]};

test_apply_fill_adds_same_direction_weighted_average:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;1000000;1.2000;1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;2000000f;1e-9;"two same-direction buys add up"];
    .testutil.assertApprox[row`avg_price;1.1500;1e-9;"equal-size fills average to the midpoint"];
    .testutil.assertApprox[row`realized_pnl;0f;1e-9;"adding to a position realizes nothing"]};

test_apply_fill_partial_reduce_realizes_pnl_avg_price_unchanged:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;400000;1.1050;-1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;600000f;1e-9;"partial sell reduces qty, same direction"];
    .testutil.assertApprox[row`avg_price;1.1000;1e-9;"avg_price of the remaining position is unchanged"];
    .testutil.assertApprox[row`realized_pnl;2000f;1e-6;"400000 closed at a 50-pip gain -> 2000"]};

test_apply_fill_exact_close_realizes_full_pnl_and_flattens:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;1000000;1.0950;-1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;0f;1e-9;"fully closed -> flat"];
    .testutil.assertApprox[row`avg_price;0f;1e-9;"avg_price resets to 0 once flat"];
    .testutil.assertApprox[row`realized_pnl;-5000f;1e-6;"1,000,000 closed at a 50-pip loss -> -5000"]};

test_apply_fill_flip_realizes_old_and_reopens_at_fill_price:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;1500000;1.1050;-1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;-500000f;1e-9;"oversized sell flips long to short"];
    .testutil.assertApprox[row`avg_price;1.1050;1e-9;"the new (short) side opens at the flipping fill's price"];
    .testutil.assertApprox[row`realized_pnl;5000f;1e-6;"the whole old 1,000,000 long realizes at the flip price: 50 pips * 1mm"]};

test_apply_fill_zero_qty_fill_is_noop:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;0;1.5000;1];
    row:b`EURUSD;
    .testutil.assertApprox[row`qty;1000000f;1e-9;"zero-size fill leaves qty unchanged"];
    .testutil.assertApprox[row`avg_price;1.1000;1e-9;"zero-size fill leaves avg_price unchanged"]};

test_apply_fill_tracks_multiple_syms_independently:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`GBPUSD;500000;1.2500;-1];
    .testutil.assertApprox[(b`EURUSD)`qty;1000000f;1e-9;"EURUSD row unaffected by the GBPUSD fill"];
    .testutil.assertApprox[(b`GBPUSD)`qty;-500000f;1e-9;"GBPUSD row set independently"]};

test_unrealized_pnl_long_position:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    .testutil.assertApprox[.qpos.unrealized_pnl[b;`EURUSD;1.1100];10000f;1e-6;"long 1mm, price up 100 pips -> +10000"]};

test_unrealized_pnl_short_position:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;-1];
    .testutil.assertApprox[.qpos.unrealized_pnl[b;`EURUSD;1.1050];-5000f;1e-6;"short 1mm, price up 50 pips -> -5000"]};

test_unrealized_pnl_zero_for_flat_sym:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;1000000;1.1050;-1];
    .testutil.assertApprox[.qpos.unrealized_pnl[b;`EURUSD;1.5000];0f;1e-9;"flat position has no unrealized P&L regardless of market price"]};

test_unrealized_pnl_zero_for_unknown_sym:{[t]
    .testutil.assertApprox[.qpos.unrealized_pnl[.qpos.empty_book[];`USDJPY;150.00];0f;1e-9;"a sym never traded has no unrealized P&L"]};

test_total_pnl_combines_realized_and_unrealized:{[t]
    b:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    b:.qpos.apply_fill[b;`EURUSD;400000;1.1050;-1];  / realizes 2000, 600000 left open @ 1.1000
    expected:2000f+.qrisk.pnl[600000;1.1000;1.1100;1];
    .testutil.assertApprox[.qpos.total_pnl[b;`EURUSD;1.1100];expected;1e-6;"total = realized + mark-to-market on what's left open"]};

test_ccy_legs_splits_pair_into_base_and_quote:{[t]
    legs:.qpos.ccy_legs[`EURAUD;1000000;1.6000];
    .qunit.assertEquals[exec ccy from legs;`EUR`AUD;"legs are base then quote"];
    amounts:exec amount from legs;
    .testutil.assertApprox[amounts[0];1000000f;1e-9;"base currency leg is +qty"];
    .testutil.assertApprox[amounts[1];-1600000f;1e-9;"quote currency leg is -(qty*avg_price)"]};

test_ccy_exposure_nets_across_pairs_sharing_a_currency:{[t]
    / EURAUD short-AUD leg and AUDUSD long-AUD leg both touch AUD - a real
    / book nets them into one number rather than reporting two disconnected
    / per-pair legs.
    b:.qpos.apply_fill[.qpos.empty_book[];`EURAUD;1000000;1.6000;1];  / +EUR 1mm, -AUD 1.6mm
    b:.qpos.apply_fill[b;`AUDUSD;500000;0.6500;1];  / +AUD 500k, -USD 325k
    exposure:.qpos.ccy_exposure[b];
    .testutil.assertApprox[first exec amount from exposure where ccy=`AUD;-1100000f;1e-6;"AUD nets the EURAUD short leg and the AUDUSD long leg: -1,600,000+500,000"];
    .testutil.assertApprox[first exec amount from exposure where ccy=`EUR;1000000f;1e-9;"EUR leg from the EURAUD position"];
    .testutil.assertApprox[first exec amount from exposure where ccy=`USD;-325000f;1e-6;"USD leg from the AUDUSD position"]};

/ Shared 3-pair quotes table (AUDUSD, EURUSD, EURPLN), mirroring
/ test_forwards.q's mk_quotes_table fixture - PLN is only quoted against
/ EUR here, so converting a PLN exposure into USD forces
/ ccy_exposure_in to chain through EUR, exactly the multi-leg bridging
/ (e.g. AUDPLN needing AUD->USD->EUR->PLN) cross_book_at itself already
/ handles for pricing a single pair.
mk_quotes_table:{[dummy]
    mk_book:{[spot]
        `bid_prices`bid_sizes`ask_prices`ask_sizes!(
            spot-0 0.0001;1000000 2000000;spot+0.0001 0.0002;1000000 2000000)};
    ts:2026.01.01D00:00:00.000000000+0D 0D00:00:00.001 0D00:00:00.002;
    unsorted:([] ts;sym:`AUDUSD`EURUSD`EURPLN),'(mk_book each 0.6550 1.0850 4.2500);
    `sym`ts xasc unsorted};

test_ccy_exposure_in_direct_pair_converts_at_the_chains_own_mid:{[t]
    quotes:mk_quotes_table[::];
    b:.qpos.apply_fill[.qpos.empty_book[];`AUDUSD;1000000;0.6550;1];
    at:2026.01.02D00:00:00.000000000;
    r:.qpos.ccy_exposure_in[b;quotes;`USD;at];
    aud_amount:first exec amount from r where ccy=`AUD;
    aud_reporting:first exec reporting_amount from r where ccy=`AUD;
    expected_mid:first exec mid from .qfwd.cross_book_at[quotes;`AUDUSD;at;enlist aud_amount;enlist `mid];
    .testutil.assertApprox[aud_reporting;aud_amount*expected_mid;1e-6;"AUD leg converts to USD at AUDUSD's own chain mid"]};

test_ccy_exposure_in_reporting_currency_converts_at_one:{[t]
    quotes:mk_quotes_table[::];
    b:.qpos.apply_fill[.qpos.empty_book[];`AUDUSD;1000000;0.6550;1];
    at:2026.01.02D00:00:00.000000000;
    r:.qpos.ccy_exposure_in[b;quotes;`USD;at];
    usd_amount:first exec amount from r where ccy=`USD;
    usd_reporting:first exec reporting_amount from r where ccy=`USD;
    .testutil.assertApprox[usd_reporting;usd_amount;1e-9;"reporting_ccy's own row converts at 1 (unchanged)"]};

test_ccy_exposure_in_chains_through_a_bridge_currency:{[t]
    / PLN has no direct USD quote in this fixture (only EURPLN and EURUSD
    / are quoted) - the same 3-pair chaining
    / test_cross_book_at_chains_through_available_pairs exercises for
    / pricing a single AUDPLN pair must kick in here too.
    quotes:mk_quotes_table[::];
    b:.qpos.apply_fill[.qpos.empty_book[];`EURPLN;500000;4.2500;1];
    at:2026.01.02D00:00:00.000000000;
    r:.qpos.ccy_exposure_in[b;quotes;`USD;at];
    pln_amount:first exec amount from r where ccy=`PLN;
    pln_reporting:first exec reporting_amount from r where ccy=`PLN;
    expected_mid:first exec mid from .qfwd.cross_book_at[quotes;`PLNUSD;at;enlist abs pln_amount;enlist `mid];
    .testutil.assertApprox[pln_amount;-2125000f;1e-6;"PLN leg from the EURPLN position, at cost"];
    .testutil.assertApprox[pln_reporting;pln_amount*expected_mid;1e-6;"PLN leg converts to USD by chaining through the EUR bridge"]};

\d .
