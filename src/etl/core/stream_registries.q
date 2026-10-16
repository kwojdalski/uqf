/ stream_registries.q - the list of every registry a stream job's definition writes
/ (.qetl.job.stream.registries, #1386).
/ .
/ WHY A LIST. A streaming job is written into more than one place: the base
/ registries below, and one `registry` per job kind built on it (normalizer,
/ horizons, rolling, bars). Anything that has to put those back - the test
/ helper that brackets a suite (.qt.support.save_stream_jobs, #1375) - needs
/ all of them, and a hand-kept copy of the list fails silently when a new kind
/ is added: its registry leaks between suites and the order they ran in
/ decides what the next one sees.
/ .
/ A kind therefore REGISTERS its own registry's name, in its own file, with
/ `.qetl.job.stream.registries:distinct .qetl.job.stream.registries,`.qetl.job.stream.<kind>.registry`.
/ This file loads after stream_job.q and before every kind, so the list exists
/ when they append to it; tests/q/test_job_output_contracts.q fails on any
/ `registry` under .qetl.job.stream.* a kind forgot to name.

\d .

/ The registries stream_job.q itself keeps; the kinds add theirs.
.qetl.job.stream.registries:`.qetl.job.stream.jobs`.qetl.job.stream.procnames`.qetl.job.stream.initial;
