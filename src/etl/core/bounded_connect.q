/ bounded_connect.q - how a bounded worker opens its live source (.qetl.job.bounded).
/ Split from bounded_worker.q to keep that file under its line cap; loaded
/ straight before it. Same namespace, unchanged: init calls `connect` and
/ plan_only calls it too (#1201), both resolved at call time.

\d .

/ Private: open the live connection. Separate from init so the failure is
/ attributable, and so a test can exercise init without one.
/ .
/ The source's transport opens it - .qetl.source.transport's `open`: an ipc
/ credential is host:port, an odbc one a connection string, a local one an
/ HDB directory's path, validated there so a wrong path fails the run rather
/ than quietly reading the fixture.
/ @private
.qetl.job.bounded.connect:{[worker]
    source:(.qetl.job.bounded.def worker)`source;
    cred:.qetl.source.require_credentials source;
    transport:(.qetl.source.def source)`transport;
    .qetl.log.dbg[worker;"connecting to the source";`source`transport!(source;transport)];
    opener:(.qetl.source.transport_def transport)`open;
    @[opener;cred;{[source;e] '.qetl.job.bounded.connect_error[source;e]}[source]]}

/ The error connect throws when it cannot reach a source.
/ .
/ SHORT ON PURPOSE, and assembled rather than written inline, because q
/ SILENTLY truncates a thrown string at 254 bytes - measured, not the 255
/ this tree said before: a 254-byte string survives a throw intact and a
/ 255-byte one comes back 254. singlestore_odbc.q's
/ require_available already keeps its own message short for that reason - and
/ this one WRAPPED that message inside a longer one, recreating the bug a
/ layer up. The composed string ran to 286 bytes, q threw 254, and the tail
/ it dropped was "...record synthetic data as covered" - the half that says
/ WHY a backfill refuses. Measured on 2026.09.26 against a real run.
/ .
/ The driver's own text comes FIRST because it is the actionable half: it
/ names what is missing and how to install it. Ours follows, and is trimmed
/ rather than the driver's if the two together still do not fit.
/ @param source the source that could not be reached
/ @param e the opener's error text
/ @return the message to throw, never longer than q will carry
/ @eg 256>count .qetl.job.bounded.connect_error[`duckdb_deals;"driver not loaded"]  ->  1b
.qetl.job.bounded.connect_error:{[source;e]
    msg:"connect: ",string[source]," unreachable - ",e,
        ". Not falling back to the fixture: that would record synthetic data as covered";
    $[254<count msg; 254#msg; msg]}

\d .
