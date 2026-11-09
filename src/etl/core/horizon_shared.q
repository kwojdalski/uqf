/ horizon_shared.q - jobs of one process share one reference buffer (.qetl.job.stream.horizons, #2288).
/ .
/ Every live horizon job keeps the reference rows its pending events can still be priced against. fx_markout and
/ fx_markout_wide read the same market_data, so each held its own copy of the same books - and each copy was a
/ whole market_data row, depth and all. The depth is gone (the reference is the flat top of book, see
/ plant_tables.q), and this file removes the second copy: jobs that declare `shared_reference` and read the same
/ table in one process keep ONE buffer.
/ .
/ HOW. Each member keeps its own `history` variable, and they are all the SAME value: when a batch arrives, the
/ first member to see it appends the rows ANY member keeps to its own buffer and sets the others' `history` to
/ the result, which q shares by reference - one copy in memory, however many jobs read it; the rest find the
/ batch already at the tail of their buffer and add nothing. A trim does the same. Each job still reads its own `history` (so `reset`, the state it declares and every reader of it work as
/ they did), and scores against rows it may not have kept itself: a row it has no use for is never an answer, since
/ a job prices only the syms its events name.
/ .
/ WHAT STAYS BOUNDED. The buffer is trimmed to the UNION of what each member's pending events can still need: for
/ each member the rows retain keeps, from the earliest instant its own pending events ask about, now. A row
/ only one member needs stays; a row none needs goes. A member that has not ticked yet is judged by its pending
/ events as they are now, never by a stale floor, so a late tick cannot lose it a row.
/ .
/ fx_markout_wide may be removed later: leaving the group is a `leave`, and nothing else changes, since every
/ member holds the same value.

\d .

/ The members of each shared buffer, in the order they joined.
.qetl.job.stream.horizons.shared.members:([] reference:`symbol$(); name:`symbol$())

/ Private: refuse a job whose reference rows would not fit the buffer it joins - the
/ same columns and types as the members' - without joining it. define runs this before
/ anything that can still refuse the declaration, and joins only once nothing can (#2425).
/ @private
.qetl.job.stream.horizons.shared.check:{[ref;job;schema]
    others:exec name from .qetl.job.stream.horizons.shared.members where reference=ref, not name=job;
    if[count others;
        have:0#get ` sv (.qetl.job.stream.namespace first others),`history;
        if[not (meta have)~meta schema;
            '"shared_reference: ",string[job]," reads ",string[ref]," as ",(", " sv string cols schema),", but ",string[first others]," shares that buffer as ",", " sv string cols have]];
    }

/ Join a shared buffer. The reference rows of every member must have the same columns and types.
/ @param ref the reference table the jobs read
/ @param job the job
/ @param schema the job's reference input, empty, as its transform declares it
/ @return name
/ @throws error when another member reads the same table in another shape
/ @eg .qetl.job.stream.horizons.shared.join[`eg_ref;`eg_job;([] sym:`symbol$())]  ->  `eg_job
.qetl.job.stream.horizons.shared.join:{[ref;job;schema]
    .qetl.job.stream.horizons.shared.check[ref;job;schema];
    delete from `.qetl.job.stream.horizons.shared.members where name=job;
    `.qetl.job.stream.horizons.shared.members insert (ref;job);
    job}

/ Leave a shared buffer. The buffer is every member's, so the rest lose nothing.
/ @param job the job
/ @return job
/ @eg .qetl.job.stream.horizons.shared.leave `eg_job  ->  `eg_job
.qetl.job.stream.horizons.shared.leave:{[job]
    delete from `.qetl.job.stream.horizons.shared.members where name=job;
    job}

/ The members of a buffer, in the order they joined.
/ @param ref the reference table
/ @return the job names
.qetl.job.stream.horizons.shared.names:{[ref] exec name from .qetl.job.stream.horizons.shared.members where reference=ref}

/ A member's `history` variable.
/ @private
.qetl.job.stream.horizons.shared.var:{[name] ` sv (.qetl.job.stream.namespace name),`history}

/ Set every member's `history` to one member's: one value, shared by reference.
/ @param ref the reference table
/ @param from the member whose buffer every other member takes
/ @return the number of members
.qetl.job.stream.horizons.shared.refresh:{[ref;from]
    t:get .qetl.job.stream.horizons.shared.var from;
    {[t;n] .qetl.job.stream.horizons.shared.var[n] set t}[t] each .qetl.job.stream.horizons.shared.names[ref] except from;
    count .qetl.job.stream.horizons.shared.names ref}

/ Append a batch of the reference table to the shared buffer, once, for the rows any member keeps.
/ Called by every member's on_batch with the same batch: the first to arrive appends to its own buffer and
/ hands it to the others; the rest find the batch already the tail of theirs and add nothing.
/ @param name the member whose on_batch was called
/ @param x the batch
/ @return the number of rows appended
.qetl.job.stream.horizons.shared.add:{[name;x]
    d:.qetl.job.stream.horizons.def name;
    ns:.qetl.job.stream.horizons.shared.names d`reference;
    kept:{[x;n] d:.qetl.job.stream.horizons.def n; $[`keep in key d; (d`keep) x; (count x)#1b]}[x] each ns;
    x:x where any kept;
    if[0=count x; :0];
    buffer:.qetl.job.stream.horizons.shared.var name;
    held:get buffer;
    x:(cols held)#x;
    / the batch another member already appended is the tail of the buffer they handed on
    if[(count held)>=count x; if[x~(neg count x)#held; :0]];
    buffer insert x;
    .qetl.job.stream.horizons.shared.refresh[d`reference;name];
    count x}

/ Trim the shared buffer to the union of what every member's pending events can still need.
/ @param name the member whose tick it is
/ @param now the time the trim is judged at
/ @return the rows left
.qetl.job.stream.horizons.shared.trim:{[name;now]
    ref:(.qetl.job.stream.horizons.def name)`reference;
    ns:.qetl.job.stream.horizons.shared.names ref;
    buffer:.qetl.job.stream.horizons.shared.var name;
    hist:get buffer;
    if[0=count hist; :0];
    marked:update rowix__:i from hist;
    kept:raze {[marked;now;n]
        d:.qetl.job.stream.horizons.def n;
        left:(get ` sv (.qetl.job.stream.namespace n),`pending) d`event_time;
        earliest:.qetl.job.stream.horizons.earliest_need[d;left;now];
        (.qetl.job.stream.horizons.retain[d;marked;earliest-d`lookback])`rowix__}[marked;now] each ns;
    buffer set hist asc distinct kept;
    .qetl.job.stream.horizons.shared.refresh[ref;name];
    count get buffer}

\d .
