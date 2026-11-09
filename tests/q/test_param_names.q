// test_param_names.q - one name per meaning for a parameter (#2117, #2118).
// Load src/init.q, tests/lib/qunit.q and tests/lib/support.q before this file.
//
// `side` is the trade direction, 1 or -1. A parameter that holds a book side,
// `bid or `ask, is book_side. In the *_by family "bucket" is the time width
// (bucket_size) and the grouping columns are group_cols. The parameter lists
// are read off the loaded lambdas, so a new function that reuses an old
// spelling fails here.

.qt.qf.util.param_names.domains:`px`mkt`exec`port`math`util`demo

/ Every public function of the .qf domains: its full name and parameter names.
.qt.qf.util.param_names.functions:{[]
    names:raze {[d] dom:`$".qf.",string d; {[dom;k] ` sv dom,k}[dom] each (key dom) except `} each .qt.qf.util.param_names.domains;
    names:names where {[n] 100h=type value n} each names;
    ([] fn:names; params:{[n] (value value n)1} each names)}

/ Functions whose `side` is a trade direction (1 buy, -1 sell), not a book side.
.qt.qf.util.param_names.direction_functions:`.qf.exec.markout`.qf.exec.eff_spread`.qf.exec.slippage`.qf.exec.cross_markout_at_horizons`.qf.exec.cross_impact_at_horizons`.qf.exec.cross_markout_attribution`.qf.exec.cross_markout_attribution_with`.qf.port.pnl`.qf.port.apply_fill`.qf.px.leg_pv

.qt.qf.util.param_names.test_the_scan_reads_signatures:{[t]
    f:.qt.qf.util.param_names.functions[];
    .qunit.assertTrue[`book_side in first exec params from f where fn=`.qf.mkt.cross_size_at_price;"book_side is read off a signature"];
    .qunit.assertTrue[`group_cols in first exec params from f where fn=`.qf.exec.fill_probability_by;"group_cols is read off a signature"]};

.qt.qf.util.param_names.test_no_book_side_parameter_is_named_side:{[t]
    f:.qt.qf.util.param_names.functions[];
    named_side:exec fn from f where {`side in x} each params;
    unexpected:named_side except .qt.qf.util.param_names.direction_functions;
    .qunit.assertEquals[unexpected;`symbol$();"a side parameter outside the trade-direction functions (use book_side for `bid/`ask): ",.Q.s1 unexpected]};

.qt.qf.util.param_names.test_no_by_function_names_a_parameter_bucket_cols:{[t]
    f:.qt.qf.util.param_names.functions[];
    bad:exec fn from f where fn like "*_by", {`bucket_cols in x} each params;
    .qunit.assertEquals[bad;`symbol$();"a *_by function with a bucket_cols parameter (use group_cols): ",.Q.s1 bad]};
