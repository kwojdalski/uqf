// reference_worker.q - a minimal bounded worker (.qt.fixture.reference_worker) that satisfies the
// .qetl.job.bounded.state contract, for driving the lifecycle tests.
//
// This is TEST INFRASTRUCTURE, not a production worker. It deliberately does
// not live in src/etl/workers/: what a real worker looks like is a design
// question this file must not pre-empt by accident, and a reference
// implementation in src/ would become the de-facto answer.
//
// Its source is synthetic and in-memory, so every lifecycle assertion is
// deterministic. The adapters it calls go through .qt.fixture.etl, so a test can
// make fetch fail on the third window, or count how many times publish ran,
// without a live connection anywhere.


/ --- the contract's required globals ------------------------------

.qt.fixture.reference_worker.source_version:`;
.qt.fixture.reference_worker.range_from:0Np;
.qt.fixture.reference_worker.range_to:0Np;

/ How wide a window this worker takes. Not part of the contract - a worker's
/ own business - but it is what makes "window boundaries" observable.
.qt.fixture.reference_worker.width:1D;

/ --- the contract's required methods ------------------------------------

/ The current run specification, in the shape save_checkpoint and
/ write_status both expect.
.qt.fixture.reference_worker.spec:{[] `source_version`range_from`range_to!(.qt.fixture.reference_worker.source_version;.qt.fixture.reference_worker.range_from;.qt.fixture.reference_worker.range_to)}

.qt.fixture.reference_worker.init:{[run_spec]
    .qt.fixture.reference_worker.source_version::run_spec`source_version;
    .qt.fixture.reference_worker.range_from::run_spec`range_from;
    .qt.fixture.reference_worker.range_to::run_spec`range_to;
    .qetl.job.bounded.state.require_contract `reference;
    .qt.fixture.reference_worker.spec[]}

/ Turn a cursor into the windows still to do.
/ .
/ A null cursor means start at range_from; a cursor means resume from it.
/ That is the whole of "cursor advancement" from the caller's side, and it is
/ why load_checkpoint returning 0Np for a foreign specification is safe
/ rather than merely conservative - it resumes from the start of the range
/ rather than from someone else's position.
.qt.fixture.reference_worker.plan:{[cursor]
    start:$[null cursor; .qt.fixture.reference_worker.range_from; cursor];
    if[not start<.qt.fixture.reference_worker.range_to; :([] range_from:`timestamp$(); range_to:`timestamp$())];
    .qetl.job.bounded.runtime.windows[start;.qt.fixture.reference_worker.range_to;.qt.fixture.reference_worker.width]}

.qt.fixture.reference_worker.fetch:{[from_ts;to_ts] .qt.fixture.etl.call[`fetch;(from_ts;to_ts)]}

.qt.fixture.reference_worker.publish:{[batch] .qt.fixture.etl.call[`publish;enlist batch]}

.qt.fixture.reference_worker.checkpoint:{[cursor] .qt.fixture.etl.call[`checkpoint;enlist cursor]}
