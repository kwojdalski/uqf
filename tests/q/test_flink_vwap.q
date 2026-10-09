/ test_flink_vwap.q - the flink_vwap streaming job (.flink_vwaptest).
/ .
/ Every case is about which windows survive: a repeat admitted shows the
/ desk a window twice, a fresh one dropped never existed, and neither throws.

\d .flink_vwaptest

published:()
recorder:{[t;x] `.flink_vwaptest.published set .flink_vwaptest.published,enlist (t;x); count x}
all_rows:{[] $[0=count .flink_vwaptest.published; (); (,/) last each .flink_vwaptest.published]}

/ Empty the marks and wire publish to the recorder before driving the job.
drive_ready:{[]
    .qetl.job.stream.reset `flink_vwap;
    `.flink_vwaptest.published set ();
    .qetl.job.stream.wire[`flink_vwap;.flink_vwaptest.recorder];
    }

beforeNamespace_load:{[] `.flink_vwaptest.saved set .qpipe.job.flink_vwap.high_water;}
afterNamespace_restore:{[] `.qpipe.job.flink_vwap.high_water set .flink_vwaptest.saved;}

t0:2026.10.09D09:00:00.000000000

/ A batch as it arrives from the plant, `time` included: window k of each sym
/ ends at t0 + 5s*k.
batch:{[syms;ks]
    n:count syms;
    ([] time:n#.flink_vwaptest.t0;
        sym:syms;
        window_end:.flink_vwaptest.t0+0D00:00:05*ks;
        vwap:1.1+0.001*til n;
        volume:n#1000000f;
        n:n#10j)}

push:{[syms;ks] .qpipe.job.flink_vwap.on_batch[`flink_vwap_raw;.flink_vwaptest.batch[syms;ks]]}

test_fresh_windows_are_published_without_time:{[t]
    drive_ready[];
    push[`EURUSD`GBPUSD;1 1];
    out:all_rows[];
    .qunit.assertEquals[count out;2;"both windows published"];
    .qunit.assertEquals[cols out;`sym`window_end`vwap`volume`n;"time is dropped; .u.upd stamps it"];
    .qunit.assertEquals[first each .flink_vwaptest.published;enlist `flink_vwap;"one publish, to flink_vwap"]}

test_a_replayed_window_is_dropped:{[t]
    drive_ready[];
    push[`EURUSD`EURUSD;1 2];
    push[`EURUSD`EURUSD`EURUSD;1 2 3];
    .qunit.assertEquals[exec window_end from all_rows[];t0+0D00:00:05*1 2 3;"windows 1 and 2 once each, then 3"]}

test_marks_are_per_sym:{[t]
    drive_ready[];
    push[enlist `EURUSD;enlist 5];
    push[enlist `GBPUSD;enlist 1];
    .qunit.assertEquals[count all_rows[];2;"EURUSD's mark does not hold back GBPUSD's earlier window"]}

test_a_window_repeated_within_a_batch_is_kept_once:{[t]
    drive_ready[];
    push[`EURUSD`EURUSD;1 1];
    out:all_rows[];
    .qunit.assertEquals[count out;1;"one row per (sym;window_end)"];
    .qunit.assertEquals[first out`vwap;1.1;"the first is kept"]}

test_an_all_repeat_batch_publishes_nothing:{[t]
    drive_ready[];
    push[enlist `EURUSD;enlist 2];
    push[enlist `EURUSD;enlist 1];
    .qunit.assertEquals[count .flink_vwaptest.published;1;"no empty publish for a batch of repeats"]}

/ What test_job_output_contracts.q drives flink_vwap with.
contract_driver:{[]
    .qetl.job.stream.reset `flink_vwap;
    .qpipe.job.flink_vwap.on_batch[`flink_vwap_raw;.flink_vwaptest.batch[`EURUSD`GBPUSD;1 1]];
    }

\d .
