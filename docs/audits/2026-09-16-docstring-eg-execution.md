# Docstring example baseline: execution

Date: 2026-09-16 (Europe/London; execution began 2026-09-15 UTC).
Branch: `kwojdalski/issue-110-audit-baselines`.
Audited source: `8fc80a51369062bdf47dda65deecd02641c02a78`.
Issue: [#110](https://github.com/kwojdalski/uqf/issues/110).
This is a bounded baseline, not a whole-repository clearance.

## Inventory and method

A fresh recursive inventory of `src/**/*.q` found **157 `@eg` lines**, **109 containing `->`**. An arrow is not necessarily an executable expected value. Six lines belong to excluded `src/integrations/data.q`; the remaining inventory is 151. The issue/agent's historical 131/92 count is not current.

This run classified all **13 lines in `src/execution/execution.q`**: 10 contain an arrow, eight carry executable expected values, two have prose expectations, and three have no assertion. It executed all eight concrete examples on KDB-X 5 (`.z.K = 5f`). Floats use relative tolerance `1e-6` at documented display precision; dictionary keys and each value are compared separately. The full existing suite passed: 669 tests, 0 failures/errors.

## Full result table

Line numbers below refer to `src/execution/execution.q`. Calls and expected values are quoted verbatim from the corresponding `@eg` payload.

| Line | Documented example | Expected | Computed | KDB-X | Verdict / test |
|---|---|---|---|---|---|
| 23 | `.qexec.markout[1;1.1000;1.1010;10000]` | `10f` | `9.9999999999988987` | PASS | ALREADY-COVERED: `test_execution.q:14` |
| 50 | `.qexec.markout_at_horizons[trades;quotes;0D00:00:01 0D00:00:10]` | None | Not evaluated | N/A | NO-ASSERTION; external fixture variables |
| 83 | `.qexec.eff_spread[1;1.1002;1.1000;10000]` | `4f` | `3.9999999999995595` | PASS | ALREADY-COVERED: `test_execution.q:91` |
| 93 | `.qexec.slippage[1;1.1000;1.1003;10000]` | `3f` | `2.9999999999996696` | PASS | ALREADY-COVERED: `test_execution.q:96` |
| 100 | `.qexec.fill_ratio[73;100]` | `0.73` | `0.72999999999999998` | PASS | ALREADY-COVERED: `test_execution.q:101` |
| 107 | `.qexec.reject_ratio[4;100]` | `0.04` | `0.040000000000000001` | PASS | ALREADY-COVERED: `test_execution.q:105` |
| 136 | `` .qexec.hit_ratio_by[requests;start_ts;end_ts;0D01:00:00;enlist `sym;`amount] `` | None | Not evaluated | N/A | NO-ASSERTION; external fixture variables |
| 137 | `` .qexec.hit_ratio_by[requests;start_ts;end_ts;0Nn;`symbol$();`count] `` | one overall count-mode ratio, no time-bucketing or grouping | Not evaluated | N/A | NON-EVALUABLE; prose and external variables |
| 183 | `` .qexec.reject_ratio_by[requests;start_ts;end_ts;0D01:00:00;enlist `sym;`amount] `` | None | Not evaluated | N/A | NO-ASSERTION; external fixture variables |
| 184 | `` .qexec.reject_ratio_by[requests;start_ts;end_ts;0Nn;`symbol$();`count] `` | one overall count-mode ratio | Not evaluated | N/A | NON-EVALUABLE; prose and external variables |
| 207 | `.qexec.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000]` | `1.100625` | `1.100625` | PASS | ALREADY-COVERED: `test_execution.q:182` |
| 226 | `.qexec.vwap_expanding[1.1000 1.1010 1.1005;1000000 2000000 1000000]` | `1.1 1.100667 1.100625` | `1.1000000000000001 1.1006666666666667 1.100625` | PASS | TEST-CANDIDATE: full documented prefix vector |
| 248 | `.qexec.sweep_price[1.1000 1.1002 1.1005;1000000 1000000 2000000;3000000]` | `` `avg_price`worst_price`filled_size`fully_filled!(1.100233;1.1005;3000000;1b) `` | `avg_price=1.1002333333333334`, `worst_price=1.1005`, `filled_size=3000000`, `fully_filled=1b`; keys match | PASS (all keys) | ALREADY-COVERED: `test_execution.q:189-198` |

Verdict split: **0 STALE, 0 THROWS, 1 TEST-CANDIDATE, 2 NON-EVALUABLE, 3 NO-ASSERTION, 7 ALREADY-COVERED**. ALREADY-COVERED extends the agent's categories because inspection found exact examples already in tests. Calling those untested would be false. No source correction is proposed.

## Proposed test

Inside the existing `.executiontest` namespace in `tests/q/test_execution.q`:

```q
test_vwap_expanding_documented_prefixes:{[t]
    actual:.qexec.vwap_expanding[1.1000 1.1010 1.1005;1000000 2000000 1000000];
    .testutil.assertApprox[actual;1.1 1.100667 1.100625;1e-6;"documented expanding VWAP at all three fills"]};
```

The existing tests at :251-272 check endpoints, alignment and future-fill invariance, but do not assert every element of this documented weighted prefix vector. The proposed test is deliberately only a report suggestion; no test/source file was changed.

## Reproduction

Save as a scratch file and run from the repository root with `QHOME="$HOME/.kx" "$HOME/.kx/bin/q" /path/to/docstrings.q`. All 12 emitted comparisons passed (seven scalar/vector examples plus dictionary keys and four values).

```q
system "l src/init.q";
system "P 17";
show .z.K;
audit_close:{[expected;actual] $[(type expected) in -9 9h; all 1e-6>abs(actual-expected)%1|abs expected; expected~actual]};
audit_check:{[label;expected;actual] -1 label," ",$[audit_close[expected;actual];"PASS";"FAIL"]," expected=",.Q.s1[expected]," actual=",.Q.s1 actual;};
audit_check["execution.q:23";10f;.qexec.markout[1;1.1000;1.1010;10000]];
audit_check["execution.q:83";4f;.qexec.eff_spread[1;1.1002;1.1000;10000]];
audit_check["execution.q:93";3f;.qexec.slippage[1;1.1000;1.1003;10000]];
audit_check["execution.q:100";0.73;.qexec.fill_ratio[73;100]];
audit_check["execution.q:107";0.04;.qexec.reject_ratio[4;100]];
audit_check["execution.q:207";1.100625;.qexec.vwap[1.1000 1.1010 1.1005;1000000 2000000 1000000]];
audit_check["execution.q:226";1.1 1.100667 1.100625;.qexec.vwap_expanding[1.1000 1.1010 1.1005;1000000 2000000 1000000]];
audit_expected:`avg_price`worst_price`filled_size`fully_filled!(1.100233;1.1005;3000000;1b);
audit_actual:.qexec.sweep_price[1.1000 1.1002 1.1005;1000000 1000000 2000000;3000000];
audit_check["sweep keys";key audit_expected;key audit_actual];
{audit_check["sweep ",string x;audit_expected x;audit_actual x]} each key audit_expected;
exit 0;
```

## Cleared

All eight executable assertions in `execution.q` match the implementation at display precision. No STALE or THROWS result in this set. No module is claimed fully executable/clean: five execution examples could not be checked as standalone value assertions.

## Not checked

The 138 in-scope examples outside execution: foundation (stats, ccy, daycount, rates), pricing (forwards, options), portfolio (risk, positions), market_data (book, microstructure, dqchecks), and ETL (backfill_state, coercion, coverage, worker_config, worker_runtime). Their inventory was counted, not executed. `src/integrations/data.q` and its six examples were explicitly excluded. No whole-repository claim about example test coverage is made.
