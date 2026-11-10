/ stream_defaults.q - the values a streaming job's optional declaration keys take when
/ absent (#2657), split out of stream_job.q to keep it under its line budget.
/ .
/ Loads after stream_job.q: `define` calls with_defaults at run time, never at load.

\d .

/ The value each optional key means when it is ABSENT, for the keys where absence
/ is exactly that value (#2657). `define` fills these before it stores the
/ declaration, so a reader indexes `d`replay` rather than re-deciding the default.
/ .
/ Deliberately not here, because their absence is not a value: `carry`, `check`,
/ `period`, `on_batch`, `on_timer`, `poll`, `state`, `ephemeral`, `on_start`,
/ `on_endofday`, `on_replayed` (each is "this job has none of it", tested by
/ presence), and `start_with_all`/`note` (read only by the Python registry).
/ `procname` is resolved by `define` itself (claim_procname).
.qetl.job.stream.optional_defaults:`replay`restore_from`on_fail`transform!(0b;`symbol$();`;`)

/ Fill the optional keys a declaration left out with their defaults.
/ @param decl a validated declaration dict
/ @return decl with every key of optional_defaults present
/ @eg .qetl.job.stream.with_defaults[enlist[`transform]!enlist `bars]`replay  ->  0b
.qetl.job.stream.with_defaults:{[decl] decl,(k!.qetl.job.stream.optional_defaults k:(key .qetl.job.stream.optional_defaults) except key decl)}
