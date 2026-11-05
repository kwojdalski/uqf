// test_options_contract.q - every function that takes an options dictionary
// refuses a key it does not know (#2113). Load src/init.q, tests/lib/qunit.q
// and tests/lib/support.q before this file.
//
// The functions are FOUND, not listed: every public function in the seven
// .qf domains whose parameter list names `opts`. A new one fails
// test_every_opts_function_has_a_probe until a probe call is added below, so
// it cannot skip the check. The parameter is `opts` everywhere (a `config` or
// `options` parameter is refused here too), so the scan cannot miss one by
// its spelling.

.qt.qf.util.options_contract.domains:`px`mkt`exec`port`math`util`demo

/ Every public function of the .qf domains as a table: its full name and
/ its parameter names. A non-lambda (a constant, a projection) has none.
.qt.qf.util.options_contract.functions:{[]
    names:raze {[d] dom:`$".qf.",string d; {[dom;k] ` sv dom,k}[dom] each (key dom) except `} each .qt.qf.util.options_contract.domains;
    names:names where {[n] 100h=type value n} each names;
    ([] fn:names; params:{[n] (value value n)1} each names)}

/ Public lambdas that take an options dict, by name.
.qt.qf.util.options_contract.opts_functions:{[]
    t:.qt.qf.util.options_contract.functions[];
    / resolve_opts is the refusal itself, tested directly below
    (exec fn from t where {`opts in x} each params) except `.qf.util.resolve_opts}

/ Each function's probe: a call that is valid in every argument but opts,
/ which is the unknown key. Keyed by name; the value is a unary function from
/ the opts to try, returning the call's result.
.qt.qf.util.options_contract.probes:()!()
.qt.qf.util.options_contract.probes[`.qf.px.forward_at_date]:{[o] .qf.px.forward_at_date[1.10;.qf.px.mock_fwd_curve;2026.11.02;10000;o]}
.qt.qf.util.options_contract.probes[`.qf.px.swap_value]:{[o] .qf.px.swap_value[.qf.px.mock_swap;.qf.px.mock_swap_market;2026.09.18;10000;o]}
.qt.qf.util.options_contract.probes[`.qf.exec.fill_probability_by]:{[o] .qt.qf.exec.execution.fill_call @[.qt.qf.exec.execution.fill_args[];4;:;o]}
.qt.qf.util.options_contract.probes[`.qf.exec.venue_quality]:{[o]
    .qf.exec.venue_quality[.qt.qf.exec.execution.mk_vq_quotes[];.qt.qf.exec.execution.mk_vq_requests[];
        .qt.qf.exec.execution.mk_vq_trades[];0D01:00:00;2026.09.15D12:00:00.000000000;0D00:00:01;o]}
.qt.qf.util.options_contract.probes[`.qf.mkt.stream_init]:{[o] .qf.mkt.stream_init[`ofi;o]}
.qt.qf.util.options_contract.probes[`.qf.mkt.stream_update]:{[o]
    .qf.mkt.stream_update[.qf.mkt.stream_init[`ofi;::];(enlist `quotes)!enlist ([] time:`timestamp$(); sym:`symbol$();
        bid_prices:(); bid_sizes:(); ask_prices:(); ask_sizes:());o]}
/ The private helpers that resolve opts take it too, and are probed directly.
.qt.qf.util.options_contract.probes[`.qf.mkt.stream_config]:{[o] .qf.mkt.stream_config o}
.qt.qf.util.options_contract.probes[`.qf.exec.normalised_opts]:{[o] .qf.exec.normalised_opts o}
.qt.qf.util.options_contract.probes[`.qf.exec.fill_probability_opts]:{[o] .qf.exec.fill_probability_opts o}
.qt.qf.util.options_contract.probes[`.qf.exec.venue_quality_opts]:{[o] .qf.exec.venue_quality_opts[2026.09.15D12:00:00.000000000;0D00:00:01;o]}
.qt.qf.util.options_contract.probes[`.qf.exec.run]:{[o] .qf.exec.run[.qt.qf.exec.allocation.mk_two_lots[];o]}
.qt.qf.util.options_contract.probes[`.qf.exec.allocate]:{[o] .qf.exec.allocate[.qt.qf.exec.allocation.mk_two_lots[];o]}
.qt.qf.util.options_contract.probes[`.qf.exec.residual]:{[o] .qf.exec.residual[.qt.qf.exec.allocation.mk_two_lots[];o]}
.qt.qf.util.options_contract.probes[`.qf.exec.position_at]:{[o]
    .qf.exec.position_at[.qt.qf.exec.allocation.mk_two_lots[];o;2026.01.02D00:00:00.000000000]}

.qt.qf.util.options_contract.test_the_scan_finds_the_options_functions:{[t]
    f:.qt.qf.util.options_contract.opts_functions[];
    .qunit.assertTrue[(count f)>=10;"the scan sees the library's options functions, not an empty list"];
    .qunit.assertTrue[`.qf.px.swap_value in f;"including the pricing ones that used to ignore a typo"]};

.qt.qf.util.options_contract.test_every_opts_function_has_a_probe:{[t]
    f:.qt.qf.util.options_contract.opts_functions[];
    missing:f except key .qt.qf.util.options_contract.probes;
    .qunit.assertEquals[missing;`symbol$();"a function taking opts needs a probe call above: ",", " sv string missing];
    stale:(key .qt.qf.util.options_contract.probes) except f;
    .qunit.assertEquals[stale;`symbol$();"a probe for a function that no longer takes opts"]};

.qt.qf.util.options_contract.test_every_opts_function_refuses_an_unknown_key:{[t]
    bad:(enlist `zz_typo)!enlist 1;
    {[bad;n]
        p:.qt.qf.util.options_contract.probes n;
        .qunit.assertThrows[p;bad;"*unknown option(s) zz_typo - known: *";
            (string n)," must refuse an unknown option, naming the key"]}[bad] each .qt.qf.util.options_contract.opts_functions[]};

.qt.qf.util.options_contract.test_the_dictionary_is_always_called_opts:{[t]
    t2:.qt.qf.util.options_contract.functions[];
    odd:exec fn from t2 where {any x in `config`options`opt} each params;
    .qunit.assertEquals[odd;`symbol$();"one name for the options dictionary: opts"]};

.qt.qf.util.options_contract.test_resolve_opts_lays_opts_over_the_defaults:{[t]
    d:`a`b!1 1;
    .qunit.assertEquals[.qf.util.resolve_opts[`f;::;d];d;"(::) is the defaults"];
    .qunit.assertEquals[.qf.util.resolve_opts[`f;enlist[`b]!enlist 2;d];`a`b!1 2;"a given key wins"];
    .qunit.assertThrows[.qf.util.resolve_opts[`f;;d];5;"f: opts must be (::) for the defaults, or a dictionary of a, b";"not a dictionary"];
    .qunit.assertThrows[.qf.util.resolve_opts[`f;;d];`c`d!1 2;"f: unknown option(s) c, d - known: a, b";"every unknown key is named"]};
