/ react_replay.q - a bounded worker re-fires the reactions an earlier run never
/ completed (.qetl.job.bounded). Split from react.q (#1100) to keep that file
/ under the module line budget; loaded straight after it.
/ .

/ A bounded worker's re-firing of reactions an earlier run never completed,
/ moved here from bounded_worker.q (#618) unchanged and in the same
/ namespace: it is reaction logic, and .qetl.reaction.pending and
/ notify_published, which it drives, are in react.q. It stays in
/ .qetl.job.bounded because it reads a worker through that namespace's own
/ def, spec, own and transform_batch, so run_body calls it as it always has.

\d .qetl.job.bounded

/ Private: fire again the reactions an earlier run never completed.
/ .
/ A reaction runs after its window's coverage is recorded, so a run killed in
/ between - or a reaction that threw - left a covered window with no derived
/ output, and every later run found the window covered and did nothing about
/ it. .qetl.reaction.pending finds those windows over this run's range, and
/ each is fetched and transformed again and its publication announced again.
/ Every reaction for the dataset runs, not only the one owed: a reaction
/ replaces its own output per window (.qetl.reaction.write), so running one
/ that already succeeded rewrites the same rows.
/ .
/ Before this run's own windows, which are not covered yet and notify as
/ they publish. Not on a dry run, which fires no reaction at all.
/ @param worker the worker's name
/ @return how many windows were announced again
/ @private
replay_reactions:{[worker]
    owed:owed_reactions[worker];
    ws:distinct select range_from, range_to from owed;
    if[0=count ws; :0];
    .qetl.log.warn[worker;"re-firing reactions for windows covered without a successful reaction";
        `windows`reactions!(count ws;distinct owed`name)];
    sum replay_window[worker] each ws}

/ The reactions this run's range still owes: (name; range_from; range_to) for
/ every covered window whose reaction has no successful outcome since it was
/ covered - .qetl.reaction.pending over the run's dataset, partition, version
/ and range. Shared by replay_reactions, which re-fires them at the start of
/ a run, and run_body, which ends a run `partial while any remain (#632).
/ None on a dry run, which fires no reaction at all.
/ .
/ When the ledgers cannot be read, this THROWS rather than answering "none
/ owed". It used to trap every error into the empty table - so an unreadable
/ etl_reactions replayed nothing and ended the run `idle, the stale derived
/ dataset #632 exists to report, now reported as success (#808). Not knowing
/ what is owed is not knowing that nothing is: the run fails, naming the
/ ledger, and the lock is released by `run` as on any other failure.
/ @param worker the worker's name
/ @return a table of name, range_from, range_to; empty when nothing is owed
/ @throws error naming the worker and why the reaction ledgers could not be read
owed_reactions:{[worker]
    none:([] name:`symbol$(); range_from:`timestamp$(); range_to:`timestamp$());
    if[not .qetl.job.bounded.runtime.allows`notify_reactions; :none];
    cfg:def worker;
    s:spec worker;
    r:@[{[a] (1b;.qetl.reaction.pending . a)};
        (cfg`dataset;cfg`partition;s`source_version;s`range_from;s`range_to);
        {[e] (0b;e)}];
    if[first r; :last r];
    .qetl.log.err[worker;"cannot tell which reactions are owed - the reaction or coverage ledger is unreadable";
        `dataset`error!(cfg`dataset;last r)];
    '"owed_reactions: cannot tell which reactions ",string[worker]," owes - ",last r}

/ Private: the error a run that leaves reactions owed ends `partial with.
/ @param owed owed_reactions' table, not empty
/ @return the message, naming the reactions and how many windows they owe
/ @private
owed_error:{[owed]
    string[count owed]," reaction(s) owed over ",string[count distinct select range_from, range_to from owed],
    " window(s) (",(", " sv string distinct owed`name),
    ") - a dataset derived from this one is stale; the next run re-fires them first"}

/ Private: announce one covered window's publication again, from a fresh
/ fetch and transform. A window that cannot be fetched or transformed is
/ logged and left owed, for the next run.
/ @return 1 when announced, else 0
/ @private
replay_window:{[worker;w]
    cfg:def worker;
    f:own[worker;`fetch][w`range_from;w`range_to];
    if[`failed~f`state;
        .qetl.log.err[worker;"could not re-fetch a window to re-fire its reactions";
            `range_from`range_to`error!(w`range_from;w`range_to;f`error)];
        :0];
    out:@[transform_batch[worker;];f`result;{[e] (`transform_failed;e)}];
    if[(0h=type out) and `transform_failed~first out;
        .qetl.log.err[worker;"could not transform a window to re-fire its reactions";
            `range_from`range_to`error!(w`range_from;w`range_to;last out)];
        :0];
    @[{[a] .qetl.reaction.notify_published_for . a};
      (cfg`dataset;cfg`partition;(spec worker)`source_version;w`range_from;w`range_to;out;.qetl.io.for_cfg cfg);
      {[e] (::)}];
    1}

\d .
