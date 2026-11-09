/ stream_outputs.q - the one declaration key a streaming job names what it
/ produces under: `outputs` (#2102).
/ .
/ A bounded worker declares the dataset it fills and a streaming job the
/ tables it publishes; both are `outputs`, a symbol list, because both are the
/ datasets the job produces. The job TYPE still decides what that means: a
/ worker's one output is what coverage and replacement are keyed on, a streaming
/ job's are what it publishes onto the tickerplant (and nothing records them as
/ complete). There is no alias for the old spelling, `publishes`: a declaration
/ that still carries it is refused with the rename, by define and by each job
/ kind (bars, horizon, rolling), so a stale file is named rather than read as a
/ job that produces nothing.

\d .

/ Refuse the retired `publishes` key.
/ @param who the prefix the message opens with, e.g. "define: crypto_markout"
/ @param decl the declaration dictionary
/ @throws error with the migration when decl still carries publishes
/ @private
.qetl.job.stream.refuse_publishes:{[who;decl]
    if[`publishes in key decl;
        'who," declares publishes, which is now outputs - rename the key; the value is unchanged (the symbol list of tables the job publishes onto the tickerplant)"];}
