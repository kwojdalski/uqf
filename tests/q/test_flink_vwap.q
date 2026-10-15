/ test_flink_vwap.q - the flink_vwap streaming job (.qt.qpipe.job.flink_vwap).
/ .
/ Every case is about which windows survive: a repeat admitted shows the
/ desk a window twice, a fresh one dropped never existed, and neither throws.


.qt.qpipe.job.flink_vwap.published:()
.qt.qpipe.job.flink_vwap.recorder:{[t;x] `.qt.qpipe.job.flink_vwap.published set .qt.qpipe.job.flink_vwap.published,enlist (t;x); count x}
.qt.qpipe.job.flink_vwap.all_rows:{[] $[0=count .qt.qpipe.job.flink_vwap.published; (); (,/) last each .qt.qpipe.job.flink_vwap.published]}

/ Empty the marks and wire publish to the recorder before driving the job.
.qt.qpipe.job.flink_vwap.drive_ready:{[]
    .qetl.job.stream.reset `flink_vwap;
    `.qt.qpipe.job.flink_vwap.published set ();
    .qetl.job.stream.wire[`flink_vwap;.qt.qpipe.job.flink_vwap.recorder];
    }

.qt.qpipe.job.flink_vwap.beforeNamespace_load:{[] `.qt.qpipe.job.flink_vwap.saved set .qpipe.job.flink_vwap.high_water;}
.qt.qpipe.job.flink_vwap.afterNamespace_restore:{[] `.qpipe.job.flink_vwap.high_water set .qt.qpipe.job.flink_vwap.saved;}

.qt.qpipe.job.flink_vwap.t0:2026.10.09D09:00:00.000000000

/ A batch as it arrives from the plant, `time` included: window k of each sym
/ ends at t0 + 5s*k.
.qt.qpipe.job.flink_vwap.batch:{[syms;ks]
    n:count syms;
    ([] time:n#.qt.qpipe.job.flink_vwap.t0;
        sym:syms;
        window_end:.qt.qpipe.job.flink_vwap.t0+0D00:00:05*ks;
        vwap:1.1+0.001*til n;
        volume:n#1000000f;
        n:n#10j)}

.qt.qpipe.job.flink_vwap.push:{[syms;ks] .qpipe.job.flink_vwap.on_batch[`flink_vwap_raw;.qt.qpipe.job.flink_vwap.batch[syms;ks]]}

.qt.qpipe.job.flink_vwap.test_fresh_windows_are_published_without_time:{[t]
    .qt.qpipe.job.flink_vwap.drive_ready[];
    .qt.qpipe.job.flink_vwap.push[`EURUSD`GBPUSD;1 1];
    out:.qt.qpipe.job.flink_vwap.all_rows[];
    .qunit.assertEquals[count out;2;"both windows published"];
    .qunit.assertEquals[cols out;`sym`window_end`vwap`volume`n;"time is dropped; .u.upd stamps it"];
    .qunit.assertEquals[first each .qt.qpipe.job.flink_vwap.published;enlist `flink_vwap;"one publish, to flink_vwap"]}

.qt.qpipe.job.flink_vwap.test_a_replayed_window_is_dropped:{[t]
    .qt.qpipe.job.flink_vwap.drive_ready[];
    .qt.qpipe.job.flink_vwap.push[`EURUSD`EURUSD;1 2];
    .qt.qpipe.job.flink_vwap.push[`EURUSD`EURUSD`EURUSD;1 2 3];
    .qunit.assertEquals[exec window_end from .qt.qpipe.job.flink_vwap.all_rows[];.qt.qpipe.job.flink_vwap.t0+0D00:00:05*1 2 3;"windows 1 and 2 once each, then 3"]}

.qt.qpipe.job.flink_vwap.test_marks_are_per_sym:{[t]
    .qt.qpipe.job.flink_vwap.drive_ready[];
    .qt.qpipe.job.flink_vwap.push[enlist `EURUSD;enlist 5];
    .qt.qpipe.job.flink_vwap.push[enlist `GBPUSD;enlist 1];
    .qunit.assertEquals[count .qt.qpipe.job.flink_vwap.all_rows[];2;"EURUSD's mark does not hold back GBPUSD's earlier window"]}

.qt.qpipe.job.flink_vwap.test_a_window_repeated_within_a_batch_is_kept_once:{[t]
    .qt.qpipe.job.flink_vwap.drive_ready[];
    .qt.qpipe.job.flink_vwap.push[`EURUSD`EURUSD;1 1];
    out:.qt.qpipe.job.flink_vwap.all_rows[];
    .qunit.assertEquals[count out;1;"one row per (sym;window_end)"];
    .qunit.assertEquals[first out`vwap;1.1;"the first is kept"]}

.qt.qpipe.job.flink_vwap.test_an_all_repeat_batch_publishes_nothing:{[t]
    .qt.qpipe.job.flink_vwap.drive_ready[];
    .qt.qpipe.job.flink_vwap.push[enlist `EURUSD;enlist 2];
    .qt.qpipe.job.flink_vwap.push[enlist `EURUSD;enlist 1];
    .qunit.assertEquals[count .qt.qpipe.job.flink_vwap.published;1;"no empty publish for a batch of repeats"]}

/ What test_job_output_contracts.q drives flink_vwap with.
.qt.qpipe.job.flink_vwap.contract_driver:{[]
    .qetl.job.stream.reset `flink_vwap;
    .qpipe.job.flink_vwap.on_batch[`flink_vwap_raw;.qt.qpipe.job.flink_vwap.batch[`EURUSD`GBPUSD;1 1]];
    }
