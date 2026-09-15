# Causality baseline: execution and rolling OFI

Date: 2026-09-16 (Europe/London; execution began 2026-09-15 UTC).
Branch: `kwojdalski/issue-110-audit-baselines`.
Audited source: `8fc80a51369062bdf47dda65deecd02641c02a78`.
Issue: [#110](https://github.com/kwojdalski/uqf/issues/110).
This is a bounded baseline, not a whole-repository clearance.

## Findings

| Severity | Where | Defect | Evidence | Reachable from | Fix |
|---|---|---|---|---|---|
| None | Audited scope below | No confirmed causality defect | Ten KDB-X assertions passed, plus VWAP caller inspection | Execution API and one rolling primitive | None proposed |

Severity split: 0 CRITICAL, 0 HIGH, 0 MEDIUM, 0 LOW.

## Checks and cleared scope

| Check | Scope and evidence | Conclusion |
|---|---|---|
| 1. VWAP benchmark | `execution.q:203-230`; `rg -n 'vwap' src tests scripts python` found the production flat-VWAP call in `microstructure.q:149`, using levels from one snapshot. Tests use fill aggregates or that same snapshot comparison. The warning at `execution.q:216-222` explains hindsight and offers `vwap_expanding`. | No forward-window benchmark caller found in these trees. Expanding values match the hand-calculated prefixes; a later extreme fill leaves prior values unchanged. The warning lives with the expanding function, not the flat function's own qDoc block; a cross-link would improve discovery, but the issue's old claim that no distinction exists is no longer true. |
| 2. As-of ordering | `execution.q:59,69` sorts before `aj`. Deliberately shuffled quotes at +10s, 0s, +1s; +10s carries extreme price 9. Targets 0s, +0.5s and +1s return 1.1, 1.1 and 1.101. | Exact and between-quote boundaries are correct; future quote excluded on this path. |
| 3. Sign convention | Header at `execution.q:4-10`; functions at :24, :84, :94. Buy/sell favourable markouts both +10 pips; adverse execution effective spreads both +4; slippage costs both +3. | All three signed metrics conform on both sides. Ratios and sweep output do not claim a signed cost convention. |
| 4. Rolling causality | `microstructure.q:389`: input 1 -1 2 0 -3, window 2 returns 1 0 1 2 -3; appending 999 preserves that prefix. | `rolling_ofi` is trailing on this fixture. |
| 5. Missing horizon data | Missing symbol and a target before the first quote both yield null reference prices and null markouts. | Nulls propagate through `markout_at_horizons`; no zero substitution within this function. |

## Executed evidence

KDB-X at `~/.kx/bin/q`, `QHOME=~/.kx`; `.z.K` reports `5f`.
Save the following as a scratch file outside the repository and run from the repository root:

```sh
QHOME="$HOME/.kx" "$HOME/.kx/bin/q" /path/to/causality.q
```

```q
system "l src/init.q";
system "P 17";
audit_assert:{[label;ok] -1 label," ",$[ok;"PASS";"FAIL"]; if[not ok;exit 1]};
audit_near:{[a;b] all 1e-10>abs a-b};
audit_assert["markout buy/sell favourable";audit_near[10 10f;.qexec.markout[1 -1;1.1 1.1;1.101 1.099;10000]]];
audit_assert["effective spread buy/sell cost";audit_near[4 4f;.qexec.eff_spread[1 -1;1.1002 1.0998;1.1 1.1;10000]]];
audit_assert["slippage buy/sell cost";audit_near[3 3f;.qexec.slippage[1 -1;1.1 1.1;1.1003 1.0997;10000]]];
audit_prices:1.1 1.101 1.1005;
audit_sizes:1000000 2000000 1000000;
audit_prefix:.qexec.vwap_expanding[audit_prices;audit_sizes];
audit_assert["expanding prefix values";audit_near[audit_prefix;1.1 1.1006666666666667 1.100625]];
audit_later:.qexec.vwap_expanding[audit_prices,9f;audit_sizes,1000000000];
audit_assert["expanding unchanged by later fill";audit_near[audit_prefix;3#audit_later]];
audit_t:2026.09.15D10:00:00.000000000;
audit_trades:([] sym:`EURUSD`MISSING;time:2#audit_t;side:1 1;trade_price:1.1 1.1;pip_factor:10000 10000);
audit_quotes:([] sym:3#`EURUSD;time:audit_t+0D00:00:10 0D00:00:00 0D00:00:01;mid:9 1.1 1.101);
audit_result:.qexec.markout_at_horizons[audit_trades;audit_quotes;0D00:00:00 0D00:00:00.500 0D00:00:01];
show audit_result;
audit_assert["unsorted input: exact and between quotes";audit_near[1.1 1.1 1.101;3#audit_result`ref_price]];
audit_assert["missing symbol: null reference and markout";(all null 3_audit_result`ref_price)&all null 3_audit_result`markout_pips];
audit_early:.qexec.markout_at_horizons[1#audit_trades;audit_quotes;neg 0D00:00:01];
audit_assert["before first quote: null reference and markout";(all null audit_early`ref_price)&all null audit_early`markout_pips];
audit_rolling:.qmicro.rolling_ofi[1 -1 2 0 -3;2];
audit_assert["rolling OFI trailing values";audit_rolling~1 0 1 2 -3];
audit_assert["rolling OFI unchanged by future value";audit_rolling~5#.qmicro.rolling_ofi[1 -1 2 0 -3 999;2]];
exit 0;
```

Observed assertion output (the display-only table is omitted):

```text
markout buy/sell favourable PASS
effective spread buy/sell cost PASS
slippage buy/sell cost PASS
expanding prefix values PASS
expanding unchanged by later fill PASS
unsorted input: exact and between quotes PASS
missing symbol: null reference and markout PASS
before first quote: null reference and markout PASS
rolling OFI trailing values PASS
rolling OFI unchanged by future value PASS
```

## Not checked

- The `leg_book_as_of` callers in forwards, chaining, cross-markout, cross-impact, scripts and TorQ: no path-complete ordering clearance.
- Other microstructure rolling functions: OFI, multilevel OFI, autocorrelation, velocity/acceleration, queue depletion and spread ratio.
- Null handling in downstream consumers, dashboards or aggregate reports.
- General malformed-input/zero-size behaviour; outside this information-causality pass.
- `src/integrations/data.q` is explicitly excluded. ETL, portfolio, foundation and options were not audited.

The full existing KDB-X suite also passed: 669 tests, 0 failures, 0 errors. That result does not extend this audit's cleared scope.
