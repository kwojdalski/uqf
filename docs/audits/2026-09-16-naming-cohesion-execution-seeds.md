# Naming and cohesion baseline: execution and prior rename seeds

Date: 2026-09-16 (Europe/London; execution began 2026-09-15 UTC).
Branch: `kwojdalski/issue-110-audit-baselines`.
Audited source: `8fc80a51369062bdf47dda65deecd02641c02a78`.
Issue: [#110](https://github.com/kwojdalski/uqf/issues/110).
This is a bounded baseline, not a whole-repository clearance.

## Findings

| Surface | Impact | Finding | Outlier → majority |
|---|---|---|---|
| Bounded surfaces below | None | No verified naming defect in the reviewed set | No rename proposed |

Severity split: 0 HIGH, 0 MED, 0 LOW. This is not clearance of all microstructure, forwards or repository naming. No unverified finding is promoted to the table.

## Scope and cleared evidence

| Surface | What was inspected | Result |
|---|---|---|
| Execution definitions | All 11 function definitions and qDoc blocks in `src/execution/execution.q:14-263` | Names are snake_case, none starts `get_`. No top-level function returns a boolean predicate; `sweep_price` returns a dictionary containing `fully_filled`, so it does not need an `is_` prefix. |
| Signed metric arguments | `markout:24`, `eff_spread:84`, `slippage:94` | `side` first and `pip_factor` last; the middle names distinguish trade/reference, trade/mid, arrival/execution roles. Slippage's different middle argument order describes a different calculation, not a naming defect. |
| Ratio families | `fill_ratio:101`, `reject_ratio:108`; `hit_ratio_by:138`, `reject_ratio_by:185` | Scalar numerator/denominator names match the counted event. Both grouped functions share the exact argument list `requests;start_ts;end_ts;bucket_size;group_cols;mode` and return the appropriately named ratio column (:155-157, :199-201). |
| VWAP and sweep | `vwap:208`, `vwap_expanding:227`, `sweep_price:249` | `prices;sizes` agrees; sweep alone adds `target_size`. Body :228-230 computes prefixes, matching “expanding”; body :257-263 computes the documented price/size/fill dictionary. |
| Execution/microstructure placement and tests | `src/execution/execution.q:1-12`, `src/market_data/microstructure.q:1-17`; `tests/q/test_execution.q:4`, `tests/q/test_microstructure.q:4`; `src/init.q:43,45`; `tests/run_tests.q:32,35` | Both modules fit their areas, have matching test filenames and test namespaces ending `test`, and are registered. `execution/execution.q` repeats a word, but this limited pair provides no majority-based actionable rename, so no stylistic finding is filed. |
| Fixed namespace collision | `src/examples/example_defaults.q:12` is `.qexdef`; `src/execution/execution.q:12` is `.qexec` | Neither namespace prefixes the other. Example defaults describe synthetic constants (:1-9), consistent with their area. No full test-pairing audit of example defaults is claimed. |
| Fixed convexity argument order | `src/market_data/microstructure.q:197,214,219` | Both helper and public wrapper now take `prices;side`; wrapper passes them in that order. Helper computes one row, public wrapper loops over rows, matching the `_one` distinction. |
| Fixed cross-family timestamp naming/order | `src/pricing/forwards.q:545,586,609` | `cross_price_ok_at_size`, `cross_size_at_price`, `cross_ref_price_at` all start `quotes;sym;at_time`. The old fourth-position `t` seed is already fixed. Only these signatures were compared, not their complete bodies/call graph. |
| Documented exceptions | `.claude/skills/kdb-q-conventions/SKILL.md`; `src/pricing/options.q:28-30,64-66` | `d1v`/`d2v` remain documented domain/scoping exceptions; no rename suggested. The `book_crossed`, qUnit hook-prefix and integrations exclusions remain exclusions, not repository-wide verified-clean claims. |
| Init ordering | `src/init.q:10-32` | The source explicitly documents flat namespaces, a pricing/execution cycle and call-time name resolution. A simple topological ordering rule cannot be imposed on this graph. Both selected modules loaded successfully under KDB-X. |

The three historical seeds (namespace collision, convexity order, cross timestamp order) are cleared on this exact source revision. They must not be filed again from the stale examples embedded in the auditor definition.

## Method

Read the execution module body end to end; compare related argument lists and return expressions. Read the selected microstructure and forwards seed definitions and the example-defaults header. Inspect loader/test registration and recorded exceptions. Commands included:

```sh
git branch --show-current
git rev-parse HEAD
rg -n '^[a-z][a-z0-9_]*:\{' src/execution/execution.q
rg -n 'book_convexity' src/market_data/microstructure.q
rg -n 'cross_ref_price_at:|cross_price_ok_at_size:|cross_size_at_price:' src/pricing/forwards.q
rg -n '^\\d' src/execution/execution.q src/market_data/microstructure.q src/examples/example_defaults.q
rg -n 'execution.q|microstructure.q' src/init.q tests/run_tests.q
```

No rename finding was made, so proposed-name/call-site-count fields are not applicable. The existing KDB-X test suite passed all 669 tests; this confirms loading and tests on this tree, not semantic naming correctness beyond the reading above.

## Not checked

- Whole-tree module inventory, missing-test detection, namespace prefix collisions and every load path (including dynamically loaded ETL workers).
- Full body/argument audit of forwards and microstructure beyond the selected historical seeds; other foundation, pricing, portfolio and ETL modules.
- `src/integrations/data.q` convention pass (explicitly excluded); its filename/layout pass was also not reached.
- Repository-wide short-name overloads, private-helper conventions or boolean prefixes.
- The documented daycount/options `d1`/`d2` collision beyond the options-local exception above.

## Unverified

None retained as a candidate finding in this bounded run. The unreviewed surfaces above remain unreviewed.
