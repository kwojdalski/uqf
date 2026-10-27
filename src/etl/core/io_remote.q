/ io_remote.q - a bounded worker that extracts on one machine and an HDB
/ writer that owns the destination on another (#1650): .qetl.io.remote.
/ .
/ WHY. A backfill's source (an ODBC driver, its credentials) and its
/ destination (a TorQ stack's HDB) need not live on one host. Until this file,
/ a worker wrote through .qetl.io.hdb or .qetl.io.hdb_scoped, both of which
/ write the files themselves, so the HDB root had to exist on the machine
/ running the worker. Here the two halves are split along the manager
/ contract io_manager.q already defines:
/ .
/   CLIENT   .qetl.io.remote.manager is an ordinary io manager on the
/            extraction machine. Its write_keyed sends each window's validated
/            batch, with the window and conflict options the shell already
/            builds, as one synchronous message; flush, finish and recover are
/            forwarded the same way. It writes nothing locally.
/   RECEIVER .qetl.io.remote.serve, in a process on the destination stack,
/            holds the REAL manager - an .qetl.io.hdb_scoped over the verified
/            attachment's root, so the lock, the date bounds, the end-of-day
/            fence, staging and recovery are exactly the ones a direct write
/            gets - and admits each batch before handing it over.
/ .
/ No second backfill engine: windows, retries, coverage and the checkpoint
/ stay the bounded shell's. Coverage is recorded after publish returns, and
/ publish returns only when the receiver has COMMITTED the batch through its
/ manager, so a window is never covered for rows that are not durably written.
/ .
/ WHAT THE RECEIVER ADMITS. A request is data only: attachment, dataset,
/ target, the window's options and the rows - never a path, never q code,
/ never a secret. Before anything is written it must name a granted
/ (attachment; dataset), come from a user that grant names, write the grant's
/ target in the grant's schema, fit max_rows, and lie inside its window.
/ Where the HDB is comes from the grant the receiver's operator made, so a
/ client cannot redirect a write.
/ .
/ RETRIES DO NOT APPEND TWICE. A batch's identity is an md5 of its
/ destination, window options and rows, computed by the receiver (a client's
/ claimed id must agree). The receiver keeps a journal, persisted to a file
/ of its own, of every batch it has taken:
/ .
/   committed with this id     the retry is acknowledged as `duplicate and
/                              nothing is written - the lost-acknowledgement
/                              case
/   pending with this id       a write that died mid-way. Under upsert,
/                              replace or ignore, writing it again is
/                              harmless, so it is. Under append or fail it is
/                              refused as in doubt: appending again could
/                              double rows, and the receiver cannot tell
/   this window committed by   under append or fail, refused: a re-fetch that
/   ANOTHER id                 returned different rows for a committed window
/                              would otherwise append them beside the first.
/                              Keyed strategies resolve it themselves
/ .
/ The window is (attachment; dataset; partition; source_version; range), so
/ a restatement under a new source_version is a new window, as coverage says.
/ .
/ DURABLE IS NOT QUERYABLE. finish and flush report what the manager
/ finished separately from whether the readers reloaded: the receiver calls
/ the manager's on_ready itself and records its outcome, where
/ .qetl.io.ready would log and swallow it. A failed reload never undoes or
/ hides a durable write.
/ .
/ WHAT THIS FILE DOES NOT DO. It knows no TorQ (scripts/gates/
/ check_etl_layering.py): connecting, authenticating a handle and gating
/ .z.pg belong to the deployment. .qetl.io.remote.permitted is the pure
/ check such a gate calls. A batch is one message; chunked transfer with
/ backpressure is a later slice of #1650, and max_rows bounds a message until
/ then.

\d .

/ ------------------------------------------------------------ IDENTITY

/ The options a request may carry: what .qetl.job.bounded.runtime.write_opts
/ builds. Anything else is refused, so a request cannot smuggle a setting the
/ receiver would act on.
.qetl.io.remote.opt_keys:`on_conflict`row_key`time_column`range_from`range_to`partition_column`partition`source_version

/ The keys a request carries, exactly.
.qetl.io.remote.request_keys:`attachment`dataset`target`batch_id`opts`batch

/ Strategies under which writing the same batch twice leaves the same rows.
.qetl.io.remote.idempotent:`upsert`replace`ignore

/ A batch's identity: the md5 of its destination, options and rows.
/ @param attachment the attachment, a symbol
/ @param dataset the dataset, a symbol
/ @param target the target table, a symbol
/ @param opts the window and conflict options
/ @param batch the rows
/ @return a 32-character hex symbol
/ @eg count string .qetl.io.remote.batch_id[`uat;`deals;`deals;`on_conflict`row_key!(`upsert;`id);([] id:1 2)]  ->  32
.qetl.io.remote.batch_id:{[attachment;dataset;target;opts;batch]
    `$raze string md5 "c"$-8!(attachment;dataset;target;opts;batch)}

/ ------------------------------------------------------------ RECEIVER

/ Grants, by .qetl.io.remote.grant_key: a generic list of dictionaries, so q
/ does not fold them into a table and refuse a differently-shaped one later.
.qetl.io.remote.grants:(enlist `)!enlist (::)

/ Every batch the receiver has taken, and how far it got.
.qetl.io.remote.empty_journal:{[]
    ([] batch_id:`symbol$(); attachment:`symbol$(); dataset:`symbol$(); partition:`symbol$();
        source_version:`symbol$(); range_from:`timestamp$(); range_to:`timestamp$();
        target:`symbol$(); strategy:`symbol$(); state:`symbol$(); rows:`long$(); at:`timestamp$())}
.qetl.io.remote.journal:.qetl.io.remote.empty_journal[]

/ Where the journal persists; null keeps it in memory only (tests).
.qetl.io.remote.journal_file:`

/ Every reader-reload outcome, apart from the durable writes it follows.
.qetl.io.remote.reloads:([] attachment:`symbol$(); dataset:`symbol$(); at:`timestamp$(); final:`boolean$();
    finished:`long$(); outcome:`symbol$(); error:())

/ Private: the key a grant is filed under.
/ @private
.qetl.io.remote.grant_key:{[attachment;dataset] `$string[attachment],"|",string dataset}

/ Open the receiver's journal: load what an earlier receiver persisted, so a
/ restarted receiver still knows which batches it committed.
/ @param path a file symbol, or ` for an in-memory journal
/ @return how many journal rows were loaded
/ @eg .qetl.io.remote.open `  ->  0
.qetl.io.remote.open:{[path]
    `.qetl.io.remote.journal_file set path;
    `.qetl.io.remote.journal set $[(not null path) and not ()~key path; get path; .qetl.io.remote.empty_journal[]];
    count .qetl.io.remote.journal}

/ Private: persist the journal, when it has a file.
/ @private
.qetl.io.remote.persist:{[]
    if[not null .qetl.io.remote.journal_file; .qetl.io.remote.journal_file set .qetl.io.remote.journal];
    }

/ Grant one (attachment; dataset) to remote writers.
/ .
/ Made by the receiver's operator, from the verified attachment: the manager
/ is what the batches are written through (an .qetl.io.hdb_scoped over its
/ root), so where the rows land is decided here and never by a request.
/ @param attachment the attachment, a symbol
/ @param dataset the dataset, a symbol
/ @param g dict: mgr (an io manager with write_keyed), target (the table),
/   schema (an empty table: the columns and types a batch must have),
/   max_rows (the most rows one batch may carry) and users (who may write;
/   empty admits only calls from inside this process); optionally contract,
/   a receiver contract (io_remote_contract.q) every batch's options must
/   keep to
/ @return the grant's key
/ @throws error when a key is missing, the manager cannot take keyed writes, or the contract is for another dataset
/ @eg .qetl.io.remote.serve[`uat;`deals;`mgr`target`schema`max_rows`users!(.qetl.io.memory;`deals;([] id:`long$());1000;`symbol$())]
.qetl.io.remote.serve:{[attachment;dataset;g]
    need:`mgr`target`schema`max_rows`users;
    if[count missing:need where not need in key g;
        '"remote serve: a grant needs ",", " sv string missing];
    .qetl.io.require_manager g`mgr;
    if[not `write_keyed in key g`mgr; '"remote serve: the manager must take keyed writes"];
    if[not 98h=type g`schema; '"remote serve: schema must be an empty table"];
    if[`contract in key g;
        if[not dataset=g[`contract]`dataset;
            '"remote serve: the contract is for ",string[g[`contract]`dataset],", not ",string dataset]];
    k:.qetl.io.remote.grant_key[attachment;dataset];
    .qetl.io.remote.grants[k]:(need,(enlist `contract) inter key g)#g;
    k}

/ Withdraw a grant: later requests for it are refused.
/ @param attachment the attachment
/ @param dataset the dataset
/ @return 1b when there was one
/ @eg .qetl.io.remote.withdraw[`no;`such]  ->  0b
.qetl.io.remote.withdraw:{[attachment;dataset]
    k:.qetl.io.remote.grant_key[attachment;dataset];
    had:k in key .qetl.io.remote.grants;
    .qetl.io.remote.grants:(enlist k)_.qetl.io.remote.grants;
    had}

/ Private: is v plain data - no function and no file symbol anywhere in it?
/ @private
.qetl.io.remote.plain:{[v]
    t:type v;
    $[t within 100 112h; 0b;
      t in -11 11h; not any ":"=first each string (),v;
      0h=t; all .z.s each v;
      99h=t; (.z.s key v) and .z.s value v;
      1b]}

/ Private: refuse a malformed request, naming what is wrong.
/ @private
.qetl.io.remote.require_request:{[req]
    if[not 99h=type req; '"remote: a request is a dictionary"];
    if[not (asc key req)~asc .qetl.io.remote.request_keys;
        '"remote: a request carries exactly ",", " sv string .qetl.io.remote.request_keys];
    if[not all -11h=type each req`attachment`dataset`target;
        '"remote: attachment, dataset and target are symbols"];
    if[not 98h=type req`batch; '"remote: the batch is a table"];
    o:req`opts;
    if[not 99h=type o; '"remote: opts is a dictionary"];
    if[count extra:(key o) except .qetl.io.remote.opt_keys;
        '"remote: opts may not carry ",", " sv string extra];
    if[not .qetl.io.remote.plain o; '"remote: opts carry data only - no function and no path"];
    if[not all `on_conflict`range_from`range_to in key o; '"remote: opts must name on_conflict and the window"];
    .qetl.io.require_strategy o`on_conflict;
    if[not (-12h=type o`range_from) and -12h=type o`range_to;
        '"remote: the window bounds are timestamps"];
    if[(any null o`range_from`range_to) or not o[`range_from]<o`range_to;
        '"remote: the window must run forwards, from range_from to range_to"];
    }

/ Private: refuse a batch the grant does not admit.
/ @private
.qetl.io.remote.require_admitted:{[g;req;user]
    if[(not null user) and not user in g`users;
        '"remote: ",string[user]," may not write ",string[req`dataset]," of attachment ",string req`attachment];
    if[not req[`target]=g`target;
        '"remote: ",string[req`dataset]," is written to ",string[g`target],", not ",string req`target];
    b:req`batch; s:g`schema;
    if[not (cols b)~cols s;
        '"remote: ",string[g`target]," takes columns ",(" " sv string cols s),", not ",(" " sv string cols b)];
    want:$[`contract in key g; g[`contract]`types; exec t from meta s];
    if[(count b) and not (exec t from meta b)~want;
        '"remote: ",string[g`target],"'s batch types differ from its schema"];
    if[`contract in key g; .qetl.io.remote.contract.require_opts[g`contract;req`opts]];
    if[count[b]>g`max_rows;
        '"remote: a batch of ",string[count b]," rows is over ",string[g`target],"'s limit of ",string g`max_rows];
    o:req`opts;
    tc:$[(`time_column in key o) and not null o`time_column; o`time_column; `time];
    if[(count b) and tc in cols b;
        ts:`timestamp$b tc;
        if[not all (ts>=o`range_from) and ts<o`range_to;
            '"remote: rows of ",string[g`target]," lie outside the window ",
             string[o`range_from]," - ",string o`range_to]];
    }

/ Private: an opts value, or the null symbol when absent.
/ @private
.qetl.io.remote.opt:{[o;k] $[k in key o; o k; `]}

/ Private: record a batch's state in the journal, and persist it.
/ @private
.qetl.io.remote.record:{[id;req;st;n]
    o:req`opts;
    r:`batch_id`attachment`dataset`partition`source_version`range_from`range_to`target`strategy`state`rows`at!
        (id;req`attachment;req`dataset;`$string .qetl.io.remote.opt[o;`partition];
         `$string .qetl.io.remote.opt[o;`source_version];o`range_from;o`range_to;req`target;
         o`on_conflict;st;`long$n;.z.p);
    `.qetl.io.remote.journal set (delete from .qetl.io.remote.journal where batch_id=id),enlist r;
    .qetl.io.remote.persist[];
    }

/ Private: whether the journal already settles this batch - `duplicate when
/ it was committed - refusing what could double rows (RETRIES above).
/ @private
.qetl.io.remote.require_fresh:{[g;req;id]
    o:req`opts;
    held:select from .qetl.io.remote.journal where batch_id=id;
    if[count held;
        if[`committed=first held`state; :`duplicate];
        if[not o[`on_conflict] in .qetl.io.remote.idempotent;
            '"remote: batch ",string[id]," was pending when the receiver stopped; under ",
             string[o`on_conflict]," writing it again could double its rows - check ",string[g`target],
             " and withdraw the batch with .qetl.io.remote.forget"]];
    ver:`$string .qetl.io.remote.opt[o;`source_version];
    pt:`$string .qetl.io.remote.opt[o;`partition];
    other:select from .qetl.io.remote.journal where attachment=req`attachment, dataset=req`dataset,
        partition=pt, source_version=ver, range_from=o`range_from, range_to=o`range_to,
        state=`committed, not batch_id=id;
    if[(count other) and not o[`on_conflict] in .qetl.io.remote.idempotent;
        '"remote: window ",string[o`range_from]," - ",string[o`range_to]," of ",string[req`dataset],
         " was committed by batch ",string[first other`batch_id],"; under ",string[o`on_conflict],
         " a different batch for it would add rows beside the first"];
    `fresh}

/ Take one batch: admit it, write it through the grant's manager, and say
/ whether it is committed - what a client's publish waits on.
/ .
/ The user is the connection's, given by the deployment's gate; ` means a
/ call from inside this process, which every grant admits.
/ @param req the request (request_keys)
/ @param user the remote user, or ` for an in-process call
/ @return dict status (`committed or `duplicate), batch_id and rows
/ @throws error, writing nothing, for anything not admitted or in doubt
.qetl.io.remote.accept:{[req;user]
    .qetl.io.remote.require_request req;
    k:.qetl.io.remote.grant_key[req`attachment;req`dataset];
    if[not k in key .qetl.io.remote.grants;
        '"remote: nothing is granted for ",string[req`dataset]," of attachment ",string req`attachment];
    g:.qetl.io.remote.grants k;
    .qetl.io.remote.require_admitted[g;req;user];
    o:req`opts;
    id:.qetl.io.remote.batch_id[req`attachment;req`dataset;req`target;o;req`batch];
    if[(not null req`batch_id) and not id=req`batch_id;
        '"remote: the batch does not match its id - altered in transit"];
    if[`duplicate~.qetl.io.remote.require_fresh[g;req;id]; :`status`batch_id`rows!(`duplicate;id;first exec rows from .qetl.io.remote.journal where batch_id=id)];
    .qetl.io.remote.record[id;req;`pending;count req`batch];
    n:.qetl.io.write_keyed[g`mgr;g`target;req`batch;o];
    .qetl.io.remote.record[id;req;`committed;n];
    `status`batch_id`rows!(`committed;id;n)}

/ The entry point a client's message names: accept, as this connection's user.
/ @param req the request
/ @return as accept
.qetl.io.remote.submit:{[req] .qetl.io.remote.accept[req;$[0=.z.w; `; .z.u]]}

/ Forget a batch the journal holds, so it may be sent again - the operator's
/ way out of an in-doubt append, once they have checked the target.
/ @param id the batch id
/ @return 1b when it was there
/ @eg .qetl.io.remote.forget `nosuch  ->  0b
.qetl.io.remote.forget:{[id]
    had:id in .qetl.io.remote.journal`batch_id;
    `.qetl.io.remote.journal set delete from .qetl.io.remote.journal where batch_id=id;
    .qetl.io.remote.persist[];
    had}

/ Private: the grant a forwarded call is for, refused when there is none.
/ @private
.qetl.io.remote.grant_for:{[attachment;dataset]
    k:.qetl.io.remote.grant_key[attachment;dataset];
    if[not k in key .qetl.io.remote.grants;
        '"remote: nothing is granted for ",string[dataset]," of attachment ",string attachment];
    .qetl.io.remote.grants k}

/ Private: tell the readers, and record how that went apart from the write.
/ @private
.qetl.io.remote.reload:{[attachment;dataset;mgr;status]
    / `reloaded only when on_ready says it asked: one that declined (nothing due yet) has
    / shown the readers nothing, and a client must not take its silence for a reload
    r:$[not `on_ready in key mgr; (`none;"");
        @[{[f;s] $[1b~f s; (`reloaded;""); (`none;"")]}[mgr`on_ready];status;{[e] (`failed;e)}]];
    `.qetl.io.remote.reloads insert ([] attachment:enlist attachment; dataset:enlist dataset; at:enlist .z.p;
        final:enlist status`final; finished:enlist `long$status`finished; outcome:enlist first r; error:enlist last r);
    `outcome`error!r}

/ Finish what the receiver's manager wrote for a grant, then ask the readers
/ to reload. finished is durable; reload says whether readers see it.
/ @param attachment the attachment
/ @param dataset the dataset
/ @return dict finished, reload (`reloaded, `failed or `none) and error
.qetl.io.remote.finish:{[attachment;dataset]
    g:.qetl.io.remote.grant_for[attachment;dataset];
    m:g`mgr;
    n:$[`finish in key m; (m`finish)[]; 0];
    r:.qetl.io.remote.reload[attachment;dataset;m;`finished`pending`final!(n;0;1b)];
    `finished`reload`error!(n;r`outcome;r`error)}

/ Finish what lies wholly before `upto`, then ask the readers to reload when
/ nothing is left open.
/ @param attachment the attachment
/ @param dataset the dataset
/ @param upto a timestamp
/ @return dict finished, pending, reload and error
.qetl.io.remote.flush:{[attachment;dataset;upto]
    g:.qetl.io.remote.grant_for[attachment;dataset];
    m:g`mgr;
    f:$[`flush in key m; (m`flush)[upto]; `finished`pending!0 0];
    r:$[(0<f`finished) and 0=f`pending;
        .qetl.io.remote.reload[attachment;dataset;m;f,enlist[`final]!enlist 0b];
        `outcome`error!(`none;"")];
    `finished`pending`reload`error!(f`finished;f`pending;r`outcome;r`error)}

/ Queue what an interrupted run left unfinished in the grant's target.
/ @param attachment the attachment
/ @param dataset the dataset
/ @param range_from inclusive lower bound
/ @param range_to exclusive upper bound
/ @return how many parts were queued
.qetl.io.remote.recover:{[attachment;dataset;range_from;range_to]
    g:.qetl.io.remote.grant_for[attachment;dataset];
    .qetl.io.recover[g`mgr;g`target;range_from;range_to]}

/ What the receiver holds, by attachment, dataset and state - pending,
/ committed - beside the last reader reload of each.
/ @return dict journal and reloads
/ @eg key .qetl.io.remote.status[]  ->  `journal`reloads
.qetl.io.remote.status:{[]
    `journal`reloads!(
        select batches:count i, rows:sum rows by attachment, dataset, state from .qetl.io.remote.journal;
        select last at, last final, last outcome, last error by attachment, dataset from .qetl.io.remote.reloads)}

/ The receiver's entry points, and how many arguments each takes.
.qetl.io.remote.entry_points:`.qetl.io.remote.submit`.qetl.io.remote.finish`.qetl.io.remote.flush`.qetl.io.remote.recover!1 2 3 4

/ Should a gate let this message through? Only a call of an entry point by
/ name, with its arguments, all of them plain data.
/ @param msg the message a client sent
/ @return 1b to admit it
/ @eg .qetl.io.remote.permitted (`.qetl.io.remote.finish;`uat;`deals)  ->  1b
/ @eg .qetl.io.remote.permitted "system \"rm -rf /\""  ->  0b
.qetl.io.remote.permitted:{[msg]
    / a call whose arguments are all symbols is a symbol list, not a general one
    if[not (type msg) in 0 11h; :0b];
    if[not -11h=type first msg; :0b];
    f:first msg;
    if[not f in key .qetl.io.remote.entry_points; :0b];
    if[not (count[msg]-1)=.qetl.io.remote.entry_points f; :0b];
    .qetl.io.remote.plain[1_msg]}

/ -------------------------------------------------------------- CLIENT

/ Private: a write a remote manager cannot take: it has no window to admit.
/ @private
.qetl.io.remote.unkeyed:{[target;batch] / noqa: QF016 io manager write signature
    '"remote: ",string[target]," takes keyed writes from the bounded shell, with their window"}

/ Private: send a batch and return the rows the receiver committed.
/ @private
.qetl.io.remote.send:{[call;attachment;dataset;target;batch;opts]
    o:(.qetl.io.remote.opt_keys inter key opts)#opts;
    id:.qetl.io.remote.batch_id[attachment;dataset;target;o;batch];
    / a batch larger than one message goes in chunks (io_remote_chunks.q)
    r:$[count[batch]>.qetl.io.remote.chunk_rows;
        .qetl.io.remote.send_chunks[call;attachment;dataset;target;id;o;batch];
        call (`.qetl.io.remote.submit;.qetl.io.remote.request_keys!(attachment;dataset;target;id;o;batch))];
    if[not r[`status] in `committed`duplicate; '"remote: the receiver answered ",.Q.s1 r];
    if[`duplicate=r`status;
        .[{.qetl.log.info[x;y;z]};(`qetl.io.remote;"the receiver already holds this batch - not sent twice";
            `target`batch_id`rows!(target;id;r`rows));::]];
    r`rows}

/ The receiver's last answer about its readers, for a client that defers coverage
/ until they show the rows (scripts/dev/attached_backfill.q): outcome `reloaded,
/ `failed or `none, its error, and `asked, the instant before the call - what a
/ reload it reports can at most have mapped.
.qetl.io.remote.last_reload:`outcome`error`asked!(`none;"";0Np)

/ Private: note a finish or flush answer's reload outcome.
/ @private
.qetl.io.remote.noted:{[asked;r]
    `.qetl.io.remote.last_reload set `outcome`error`asked!(r`reload;r`error;asked);
    r}

/ Private: finish remotely, and say so when the readers did not reload.
/ @private
.qetl.io.remote.finish_via:{[call;attachment;dataset]
    asked:.z.p;
    r:.qetl.io.remote.noted[asked;call (`.qetl.io.remote.finish;attachment;dataset)];
    if[`failed=r`reload;
        .[{.qetl.log.warn[x;y;z]};(`qetl.io.remote;"written durably, but the readers did not reload";
            `attachment`dataset`error!(attachment;dataset;r`error));::]];
    r`finished}

/ Private: flush remotely, noting the reload outcome as finish does.
/ @private
.qetl.io.remote.flush_via:{[call;attachment;dataset;upto]
    asked:.z.p;
    `finished`pending#.qetl.io.remote.noted[asked;call (`.qetl.io.remote.flush;attachment;dataset;upto)]}

/ An io manager whose writes are committed by a receiver on the destination.
/ .
/ `call` sends one message and returns the answer, synchronously - for a q
/ handle h, {[m] h m}. A call that fails throws, and the shell's retry policy
/ sends the window again; the receiver's journal makes that safe.
/ @param call a function taking the message and returning the receiver's answer
/ @param attachment the attachment the receiver granted
/ @param dataset the dataset the receiver granted
/ @return a manager with write, write_keyed, flush, finish and recover
/ @eg .qetl.io.remote.manager[{[m] value m};`uat;`deals]
.qetl.io.remote.manager:{[call;attachment;dataset]
    `write`write_keyed`flush`finish`recover!(
        .qetl.io.remote.unkeyed;
        .qetl.io.remote.send[call;attachment;dataset];
        .qetl.io.remote.flush_via[call;attachment;dataset];
        / niladic: a projection with every argument supplied would be a call
        {[c;a;d;x] .qetl.io.remote.finish_via[c;a;d]}[call;attachment;dataset]; / noqa: QF016 niladic
        {[c;a;d;t;f;e] c (`.qetl.io.remote.recover;a;d;f;e)}[call;attachment;dataset])} / noqa: QF016 recover's target is the grant's

\d .
