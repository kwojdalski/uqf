/ once.q - the --once contract every job kind shares (.qetl.once, #2798).
/ .
/ An operator needs one way to run ONE unit of a job's real processing and
/ then have the process leave, whatever the job is. What a unit is depends on
/ the kind, and is written down once, here, in `kinds`:
/ .
/   poll        one poll of a polling feed
/   timer       one cycle of a timer-driven job
/   subscriber  one input batch and its processing
/   bounded     one planned window of a bounded worker
/   reaction    one invocation, under its parent's run
/ .
/ WHO ENDS THE PROCESS. The framework's runner for the job's kind, one
/ implementation per kind, and never the job: .qetl.job.stream.once (poll,
/ timer, subscriber) and .qetl.job.bounded.once (bounded, and so reaction). A
/ job file declares what it always did and gets --once because of which runner
/ runs it, so a job a scaffold writes has it the moment it exists, and a job
/ cannot ship a one-shot loop of its own that would drift from this contract.
/ .
/ A UNIT MAY PUBLISH NOTHING. A timer cycle that found nothing to emit is a
/ cycle that ran: it is `done` with rows 0. Nothing here fabricates a row, and
/ nothing flushes pending event-time state to make a unit finish - a
/ subscriber that sees no batch inside its timeout ends `no_work`, with whatever
/ it was holding still held.
/ .
/ THE OUTCOMES, and their exit codes, which are the cross-process contract
/ python/uqs reads through uqs.generated.q_facts:
/ .
/   done      0  the unit ran to its end
/   failed    1  it did not: the reason says why
/   stopped   4  a bounded run an operator stopped before its window (bounded_stop.q)
/   no_work   6  there was nothing to do: a poll that found no page, a range already
/                covered, a subscriber that saw no input before its timeout
/ .
/ 2 is a usage error, 3 rows written but not yet queryable and 5 an unknown
/ outcome in the CLI's own vocabulary, so no_work takes 6.
/ .
/ THE INPUT WAIT. A subscriber is waiting on a plant that may be quiet, so its
/ wait is bounded: default_timeout, at most max_timeout, set per run with
/ -once_timeout SECONDS. Reaching it is `no_work` with reason `input_timeout`,
/ and says nothing about a query: a remote query that is cancelled is an error
/ in its own right, and ends the unit `failed`.
/ .
/ THE OUTCOME IS A FILE as well as an exit code, because the launcher
/ (`uqs start --once`) cannot read the exit status of a process torq.sh
/ detached. <status_dir>/once_<instance>.json holds one JSON object, written
/ atomically, as the status file is.

\d .

/ The outcomes a unit ends in, and the exit code of each. `stopped` is a bounded run's own (an
/ operator's stop file), carried over so one table holds every code a --once run exits with.
.qetl.once.outcomes:`done`failed`stopped`no_work
.qetl.once.codes:.qetl.once.outcomes!0 1 4 6i

/ The default and the longest wait for input, as timespans. A subscriber waits for exactly one
/ live batch; a quiet plant must not hold the process for ever, nor a typo hold it for a week.
.qetl.once.default_timeout:0D00:01:00
.qetl.once.max_timeout:0D01:00:00

/ What a job kind's unit is, and who ends the process: `owner` is the function that owns
/ termination for the kind, `standalone` whether the kind is launched as a process of its own
/ (a reaction is not: it runs inside its parent's run), `waits` whether the unit waits for input
/ and so takes the timeout.
.qetl.once.kinds:([kind:`poll`timer`subscriber`bounded`reaction]
    unit:("one poll";"one processing cycle";"one input batch and its processing";"one planned window";"one invocation under its parent's run");
    owner:`.qetl.job.stream.once.run`.qetl.job.stream.once.run`.qetl.job.stream.once.run`.qetl.job.bounded.once.limit`.qetl.job.bounded.once.limit;
    standalone:11110b;
    waits:00100b)

/ The keys of an outcome file, in order. Named here so the launcher takes them from q (the
/ q-facts generator exports them) rather than guessing.
.qetl.once.fields:`instance`job`kind`outcome`code`reason`units`rows`detail`started_at`ended_at`pid`host

/ An outcome file is <file_prefix><instance><file_suffix> in the status directory.
.qetl.once.file_prefix:"once_"
.qetl.once.file_suffix:".json"

/ Where an instance's outcome file is.
/ @param instance the process instance, e.g. `crypto_markout1
/ @return the path, a string
/ @eg .qetl.once.path[`demo_deals_feed1] like "*/once_demo_deals_feed1.json"  ->  1b
.qetl.once.path:{[instance] (.qetl.status.status_dir[]),"/",.qetl.once.file_prefix,string[instance],.qetl.once.file_suffix}

/ Whether a command line asks for a one-unit run.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return 1b when -once is present
/ @eg .qetl.once.requested .Q.opt ("-once";"-trace")  ->  1b
.qetl.once.requested:{[opts] `once in key opts}

/ How long a subscriber waits for its one batch: -once_timeout SECONDS, else the default.
/ @param opts the parsed command line, as .Q.opt returns it
/ @return the wait, a timespan
/ @throws error when the value is not a positive number of seconds, or is past max_timeout
/ @eg .qetl.once.timeout_from_flags .Q.opt ("-once_timeout";"90")  ->  0D00:01:30.000000000
.qetl.once.timeout_from_flags:{[opts]
    if[not `once_timeout in key opts; :.qetl.once.default_timeout];
    raw:$[count opts`once_timeout; first opts`once_timeout; ""];
    secs:"F"$raw;
    if[null secs; '"once: -once_timeout must be a number of seconds, got \"",raw,"\""];
    if[secs<=0f; '"once: -once_timeout must be positive, got ",raw];
    t:`timespan$`long$secs*1e9;
    if[t>.qetl.once.max_timeout;
        '"once: -once_timeout ",raw," is past the longest wait, ",string[`long$.qetl.once.max_timeout%1000000000]," seconds"];
    t}

/ Which kind a job is, and so which runner ends it.
/ @param job the job's name, a streaming job, a bounded worker or a registered reaction
/ @return `poll, `timer, `subscriber, `bounded or `reaction
/ @throws error when nothing by that name is declared
/ @eg .qetl.once.kind_of `upstream_trades_backfill  ->  `bounded
.qetl.once.kind_of:{[job]
    if[job in .qetl.job.bounded.defined[]; :`bounded];
    if[job in .qetl.job.stream.defined[];
        d:.qetl.job.stream.def job;
        :$[0<count .qetl.job.stream.subscriptions job; `subscriber; `poll in key d; `poll; `timer]];
    if[job in distinct raze {x`name} each value .qetl.reaction.reactions;
        :`reaction];
    '"once: ",string[job]," is not a declared streaming job, bounded worker or reaction"}

/ Every job the loaded tree declares, with its kind: what the contract tests, and the verify,
/ walk, so a kind that is declared and has no runner cannot go unnoticed.
/ @return table of job and kind
.qetl.once.declared:{[]
    jobs:asc .qetl.job.bounded.defined[],.qetl.job.stream.defined[];
    ([] job:jobs; kind:.qetl.once.kind_of each jobs)}

/ The kinds whose runner does not exist: every row of `kinds` names the function that owns its
/ termination, and that function must be defined.
/ @return the kinds that name a function that is not defined, as a symbol list - empty is healthy
.qetl.once.unowned:{[]
    exec kind from .qetl.once.kinds where not {[f] @[{get x; 1b};f;{[e] 0b}]} each owner}

/ The process this run is, for the outcome: its TorQ name when it has one, else the process a
/ streaming job declares, else the job's own name.
/ @param job the job's name
/ @return the instance, a symbol
.qetl.once.instance:{[job] p:.qetl.run.proc_name[]; $[not null p; p; job in .qetl.job.stream.defined[]; (.qetl.job.stream.def job)`procname; job]}

/ What a run knows about itself from its start, for the outcome it will write.
/ @param instance the process instance
/ @param job the job's name
/ @param kind the job's kind
/ @return the context dictionary
.qetl.once.context:{[instance;job;kind] `instance`job`kind`started_at!(instance;job;kind;.z.p)}

/ The outcome of one unit, as the document the launcher reads.
/ @param ctx a context (instance, job, kind, started_at)
/ @param outcome one of outcomes
/ @param reason a short reason, "" for done
/ @param units how many units ran (0 or 1)
/ @param rows how many rows the unit published
/ @param detail free text: the table, the window, the cursor
/ @return the dictionary written to the outcome file
/ @throws error when the outcome is not one of outcomes
.qetl.once.document:{[ctx;outcome;reason;units;rows;detail]
    if[not outcome in .qetl.once.outcomes;
        '"once: unknown outcome ",string[outcome]," - expected one of ",", " sv string .qetl.once.outcomes];
    .qetl.once.fields!(ctx`instance;ctx`job;ctx`kind;outcome;.qetl.once.codes outcome;reason;units;rows;detail;ctx`started_at;.z.p;.z.i;string .z.h)}

/ Write an outcome file, atomically: a launcher polling for it reads the old file or the new
/ one, never half of one.
/ @param doc a document
/ @return the path written
.qetl.once.write:{[doc]
    dir:.qetl.status.status_dir[];
    .qetl.job.bounded.state.ensure_dir dir;
    target:.qetl.once.path[doc`instance];
    tmp:target,".tmp";
    (hsym `$tmp) 0: enlist .j.j doc;
    system"mv ",.qetl.job.bounded.state.shell_word[tmp]," ",.qetl.job.bounded.state.shell_word target;
    target}

/ How the process leaves. A global so a test can put a recorder here and read what a runner
/ would have exited with, instead of losing the q process that is running the test.
/ @param code the exit code
.qetl.once.exit:{[code] exit code}

/ End a unit: write its outcome, log it, and leave with the outcome's code. The one exit every
/ --once runner goes through, so the file, the log line and the status cannot disagree.
/ .
/ A failure to WRITE the file is logged and does not change the exit code: the code is still
/ the truth, and a launcher that finds no file reports the process as gone without an outcome.
/ @param ctx a context
/ @param outcome one of outcomes
/ @param reason a short reason, "" for done
/ @param units how many units ran
/ @param rows how many rows the unit published
/ @param detail free text
/ @return the document
.qetl.once.conclude:{[ctx;outcome;reason;units;rows;detail]
    doc:.qetl.once.document[ctx;outcome;reason;units;rows;detail];
    .[.qetl.once.write;enlist doc;{[e] .qetl.log.ch.err[`lifecycle;"could not write the --once outcome file";enlist[`error]!enlist e]}];
    $[outcome in `done`no_work; .qetl.log.ch.info; .qetl.log.ch.err][`lifecycle;"once: ",string[outcome];
        (`job`kind`outcome`code`reason`units`rows`detail#doc)];
    .qetl.once.exit[doc`code];
    doc}

\d .
