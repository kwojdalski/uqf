// test_positions.q - tests for src/portfolio/positions.q. Load src/portfolio/risk.q,
// src/portfolio/positions.q, tests/lib/qunit.q and tests/lib/testutil.q before this
// file.

\d .positionstest

/ A two-sym trades table: one buy, one sell, a millisecond apart.
/ .
/ Shared with the @eg examples for apply_fills and reconcile_trades, which
/ bind it as `trades`, so the documented calls run against the same rows the
/ reconciliation test proves balance.
mk_trades:{[]
    ([] sym:`EURUSD`GBPUSD; size:1000000 500000f; trade_price:1.1000 1.2500; side:1 -1;
        time:2026.01.01D09:00:00.000000000+0D 0D00:00:00.001)}

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

test_apply_fills_folds_a_trades_table_in_time_order:{[t]
    / A SUPERSET of the four columns apply_fills needs, carrying trade_id
    / and order_id it has no use for - the point being that it ignores
    / them. Deliberately out of time order here, to check apply_fills sorts
    / before folding rather than trusting row order.
    trades:([]
        trade_id:1 2 3; order_id:1 2 3;
        time:2026.01.01D09:00:00.200 2026.01.01D09:00:00.000 2026.01.01D09:00:00.100;
        sym:`EURUSD`EURUSD`EURUSD;
        side:-1 1 1;
        trade_price:1.1050 1.1000 1.1020;
        size:400000 1000000 200000f;
        pip_factor:10000 10000 10000);
    b:.qpos.apply_fills[.qpos.empty_book[];trades];
    row:b`EURUSD;
    / applied in time order: buy 1mm@1.1000, buy 200k@1.1020 (avg weighted),
    / sell 400k@1.1050 (partial reduce) - same arithmetic as apply_fill's
    / own weighted-average/partial-reduce tests, just via the folded path.
    expected:.qpos.apply_fill[.qpos.apply_fill[.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];`EURUSD;200000;1.1020;1];`EURUSD;400000;1.1050;-1];
    exp_row:expected`EURUSD;
    .testutil.assertApprox[row`qty;exp_row`qty;1e-9;"apply_fills' final qty matches folding apply_fill by hand, in time order"];
    .testutil.assertApprox[row`avg_price;exp_row`avg_price;1e-9;"apply_fills' final avg_price matches the hand-folded sequence"];
    .testutil.assertApprox[row`realized_pnl;exp_row`realized_pnl;1e-6;"apply_fills' final realized_pnl matches the hand-folded sequence"]};

test_apply_fills_on_an_empty_trades_table_is_a_noop:{[t]
    trades:0#([] sym:`symbol$(); side:`long$(); trade_price:`float$(); size:`float$(); time:`timestamp$());
    b:.qpos.apply_fills[.qpos.empty_book[];trades];
    .qunit.assertEmpty[b;"no trades -> book stays empty"]};

test_reconcile_trades_flags_no_breaks_when_book_matches_trades:{[t]
    trades:mk_trades[];
    reference:.qpos.apply_fills[.qpos.empty_book[];trades];
    r:.qpos.reconcile_trades[reference;trades;0f;1e-9];
    .qunit.assertEquals[exec status from r;`match`match;"reference book built from the same trades matches exactly, no breaks"]};

test_reconcile_trades_flags_a_qty_break:{[t]
    trades:([] sym:enlist `EURUSD; size:enlist 1000000f; trade_price:enlist 1.1000; side:enlist 1; time:enlist 2026.01.01D09:00:00.000000000);
    / broker shows 900000, not the 1,000,000 trades imply - a missed fill or booking error
    reference:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;900000;1.1000;1];
    r:.qpos.reconcile_trades[reference;trades;0f;1e-9];
    row:first r;
    .qunit.assertTrue[row`qty_break;"900000 (reference) vs 1000000 (computed from trades) exceeds qty_tol -> break"];
    .testutil.assertApprox[row`qty_diff;100000f;1e-6;"qty_diff is computed - reference"];
    .qunit.assertEquals[row`status;`break;"a qty break marks the row `break"]};

test_reconcile_trades_qty_tol_absorbs_small_diffs:{[t]
    trades:([] sym:enlist `EURUSD; size:enlist 1000000f; trade_price:enlist 1.1000; side:enlist 1; time:enlist 2026.01.01D09:00:00.000000000);
    reference:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;999999.5;1.1000;1];
    r:.qpos.reconcile_trades[reference;trades;1;1e-9];
    .qunit.assertEquals[first exec status from r;`match;"a 0.5-unit qty diff is within qty_tol=1 -> not a break"]};

test_reconcile_trades_flags_an_avg_price_break_when_both_sides_open:{[t]
    trades:([] sym:enlist `EURUSD; size:enlist 1000000f; trade_price:enlist 1.1000; side:enlist 1; time:enlist 2026.01.01D09:00:00.000000000);
    / same qty, but the broker's avg_price disagrees with what these trades imply
    reference:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1010;1];
    r:.qpos.reconcile_trades[reference;trades;0f;1e-6];
    row:first r;
    .qunit.assertTrue[not row`qty_break;"qty matches exactly -> no qty break"];
    .qunit.assertTrue[row`avg_price_break;"avg_price disagrees beyond price_tol while both sides are open -> break"];
    .qunit.assertEquals[row`status;`break;"an avg_price break alone still marks the row `break"]};

test_reconcile_trades_does_not_flag_avg_price_when_one_side_is_flat:{[t]
    / trades fully close the EURUSD position (flat, avg_price resets to 0
    / by convention) but the broker's reference book still shows it open -
    / that's a real qty break, but avg_price comparison would be
    / meaningless (0 vs a real rate) and must not double-count as its own break.
    trades:([]
        sym:`EURUSD`EURUSD;
        size:1000000 1000000f;
        trade_price:1.1000 1.1050;
        side:1 -1;
        time:2026.01.01D09:00:00.000000000+0D 0D00:00:00.001);
    reference:.qpos.apply_fill[.qpos.empty_book[];`EURUSD;1000000;1.1000;1];
    r:.qpos.reconcile_trades[reference;trades;0f;1e-9];
    row:first r;
    .qunit.assertTrue[row`qty_break;"computed is flat (0) vs reference's still-open 1000000 -> a real qty break"];
    .qunit.assertTrue[not row`avg_price_break;"one side flat -> avg_price isn't compared, no separate break"]};

test_reconcile_trades_includes_a_sym_missing_from_either_side:{[t]
    / GBPUSD only exists in trades (reference never booked it) - and
    / AUDUSD only exists in reference (no trades at all, e.g. a manual
    / adjustment) - both must still show up as rows, not get silently dropped.
    trades:([] sym:enlist `GBPUSD; size:enlist 500000f; trade_price:enlist 1.2500; side:enlist 1; time:enlist 2026.01.01D09:00:00.000000000);
    reference:.qpos.apply_fill[.qpos.empty_book[];`AUDUSD;200000;0.6500;1];
    r:.qpos.reconcile_trades[reference;trades;0f;1e-9];
    syms:exec sym from r;
    .qunit.assertTrue[`GBPUSD in syms;"a sym only in trades still appears in the reconciliation"];
    .qunit.assertTrue[`AUDUSD in syms;"a sym only in the reference book still appears in the reconciliation"];
    gbpusd_row:first select from r where sym=`GBPUSD;
    .testutil.assertApprox[gbpusd_row`reference_qty;0f;1e-9;"GBPUSD has no reference qty -> defaults to 0"];
    audusd_row:first select from r where sym=`AUDUSD;
    .testutil.assertApprox[audusd_row`computed_qty;0f;1e-9;"AUDUSD has no trades -> computed qty defaults to 0"]};

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
