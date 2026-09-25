/ source_contract.q - the centralised external-source contract (.qsrc).
/ .
/ The source contract: "register every external source table, target
/ mapping, required field and required type in the centralised source
/ contract. Validate both generated fixtures and live external metadata
/ against that same contract."
/ .
/ The phrase doing the work is "that SAME contract". One declaration validates
/ both a local fixture and a live source, so a fixture cannot drift from the
/ thing it stands in for. Two separate declarations would pass a suite while
/ the real source had changed - and a fixture that no longer resembles its
/ source is worse than no fixture, because it manufactures confidence.
/ .
/ Four ETL decisions are recorded here rather than left implicit, and the
/ time-and-timezone block after them likewise. They follow from answers
/ already given, and are stated so nobody has to re-derive them:
/ .
/   the question bank (answered by the maintainer) - the external driver is NOT a hard
/     dependency. A public single-host demo cannot require a licensed ODBC
/     driver, or the whole backfill path is undemonstrable. Every source
/     declares a fixture, so the path is exercisable with no driver at all.
/ .
/   the question bank (answered by the maintainer) - credentials come from the
/     ENVIRONMENT only. Nothing secret lives in this tree, and the YAML layer
/     must never carry one. `require_credentials` enforces that rather than
/     documenting it.
/ .
/   the question bank - source queries are PARAMETERISED q lambdas,
/     never built by string concatenation. The frontend's guarantee is that no
/     caller input reaches query text; uqf_frontend/queries.py already honours it,
/     and a source adapter is the same problem with a less friendly input.
/ .
/   the question bank - bank-internal business logic is not
/     reimplementable here. What gets built is a generic ANALOGUE with the
/     same shape and none of the logic. So this contract describes shapes, and
/     deliberately carries no business semantics.
/ .
/ TIME AND TIMEZONE (issue #80)
/ .
/   Do external sources return local times, and who converts?"
/     WHAT THE BANK'S SOURCES ACTUALLY RETURN IS NOT KNOWABLE FROM HERE. The
/     canonical tree is unreachable and this repository being public forbids
/     its schemas appearing
/     here, so nobody in this repository can answer that half. What IS
/     decidable is the policy, and the policy is what changes the code:
/ .
/       - internally, everything is UTC. That is not new: python/uqf_frontend
/         already enforces it at the HTTP edge, where queries.coerce rejects
/         a naive datetime outright. Same rule; this file's timezone handling
/         is the q-side half of it.
/       - the zone is therefore a PER-SOURCE property, because only the
/         source knows it, and it is DECLARED rather than defaulted. An
/         omitted zone is the exact shape of the bug: it reads as "UTC" to
/         every later reader while the source was handing over wall-clock
/         local time all along, which is silent and off by one offset.
/       - the FRAMEWORK converts, once, at fetch, in fetch_window below.
/         Not the worker (each would do it slightly differently, and a
/         worker that forgot would be indistinguishable from a UTC source),
/         and not the consumer (by then the zone is gone).
/ .
/   The `z->p` cast bug class. THE CANONICAL BUG ITSELF IS NOT
/     RECOVERABLE from this tree; what follows is the class, measured here
/     under KDB-X, and the guards that stop it recurring. q's `datetime`
/     (type 15h, `z`) is a FLOAT count of days; `timestamp` (12h, `p`) is a
/     long count of nanoseconds. Going z->p is therefore a float-to-long
/     rounding, and it is quiet:
/ .
/       - measured: of 1000 timestamps one nanosecond apart, 999 do not
/         survive a p->z->p round trip; the largest error seen was 629ns,
/         and up to 447ns of it BACKWARDS, i.e. to an earlier instant.
/       - whole seconds DO survive (0 error across a full day of them), so
/         the bug passes every hand-check built from round numbers and only
/         shows up on real trade timestamps.
/       - `=` says a z and a p at the same instant are equal (1b) while `~`
/         says they do not match (0b), and `distinct` keeps a value and its
/         own round trip as TWO values - so a dedupe on (key;time) silently
/         stops recognising rows it has already published.
/       - filtering a z column with p window bounds does not even warn: q
/         promotes, the window comes back plausible, and the wrongness is
/         carried in the data rather than raised.
/ .
/     Three things prevent recurrence, all of them enforcement rather than
/     documentation: (1) register below requires the time_column to be one of
/     the declared columns AND to be declared `p`, so a z time column is a
/     registration failure; (2) validate/validate_live compare declared type
/     characters against `meta`, so a source that silently changes a column
/     from p to z fails on both the fixture and the live path; (3)
/     scripts/gates/check_q_traps.py forbids the q datetime type in src/ outright -
/     the cast direction is not statically decidable, but the type's
/     PRESENCE is, and this tree has no legitimate use for it.
/ .
/   DST in windowed backfills. Windows are cut in UTC by
/     .qwrt.windows, so a "daily" window is always exactly 24h of elapsed
/     time: never short, never long, and the coverage ledger keeps tiling
/     exactly across a transition. The variable thing is the LOCAL span, and
/     that is handled here rather than by warping window widths - see
/     source_bounds and local_to_utc for the two traps that produces.

\d .qsrc

/ ---------------------------------------------------------------- SCHEMA

/ What every registered source must declare. Named as data so a test can
/ assert the set rather than trusting a code review.
/ .
/ `tz` is REQUIRED, with no default. A defaulted zone is the
/ bug: it reads as a decision downstream while nobody ever made one. Stating
/ `UTC` costs one symbol and makes "this source hands over UTC" a claim
/ somebody wrote, which validate_live can then be run against.
required_declarations:`source`table_name`target`time_column`row_key`columns`types`query`fixture`tz

/ How a source is reached - the one OPTIONAL declaration.
/ .
/   ipc   a q process: the credential is host:port, opened with hopen, and
/         the query callback calls the handle with a lambda. The default,
/         because every source before ODBC was one.
/   odbc  anything with an ODBC driver: the credential is the connection
/         string, opened with .qodbc.open, and the query callback builds SQL
/         through .qodbc's one escape function.
/ .
/ A source's transport decides how .qbw.connect opens a handle and how
/ cleanup closes one, so it belongs to the source rather than the worker: two
/ workers over one source cannot disagree about how to reach it.
transports:`ipc`odbc
default_transport:`ipc

/ source -> its declaration dict.
sources:(`symbol$())!();

/ Register an external source table.
/ .
/ Registration VALIDATES immediately, unlike .qbfstate.register which
/ deliberately defers. The asymmetry is deliberate and worth stating: a
/ bounded worker's methods appear as its file loads, so validating early
/ would force declaration order. A source declaration is a single literal
/ with no such ordering problem, so the earliest possible failure is the best
/ one.
/ @param source a name for this source, e.g. `deals_analogue
/ @param decl a dict carrying every name in required_declarations:
/   table   - the external table name, as a symbol
/   target  - the local table it lands in, as a symbol
/   time_column - the column the window is taken on, as a symbol
/   row_key - the column(s) identifying a row uniquely, as a symbol vector
/   columns  - the columns this adapter READS, as a symbol vector
/   types   - the expected q type characters, one per field, as a string
/   query   - a parameterised lambda taking (handle;range_from;range_to)
/   fixture - a niladic lambda returning a synthetic table of the same shape
/   tz - the zone the source's time_column is expressed in, as a
/     symbol: `UTC, or a tz-database name such as `$"Europe/London"
/ and optionally:
/   transport - `ipc (the default) or `odbc, see `transports`
/ @return the source name
/ @throws error naming every missing or malformed declaration at once
define:{[source;decl]
    missing:required_declarations where not required_declarations in key decl;
    if[count missing;
        '"define: ",string[source]," is missing declaration(s): ",", " sv string missing];
    if[not 11h=abs type decl`columns;
        '"define: ",string[source],"'s columns must be a symbol vector"];
    if[not 10h=abs type decl`types;
        '"define: ",string[source],"'s types must be a string of q type characters, one per field"];
    if[(count decl`columns)<>count decl`types;
        '"define: ",string[source]," declares ",string[count decl`columns],
         " field(s) but ",string[count decl`types]," type(s) - the source contract requires a type per required field"];
    if[not 100h=type decl`query;
        '"define: ",string[source],"'s query must be a lambda (parameterised, never concatenated)"];
    if[not 100h=type decl`fixture;
        '"define: ",string[source],"'s fixture must be a niladic lambda (the path must be exercisable with no driver)"];
    if[not -11h=type decl`tz;
        '"define: ",string[source],"'s tz must be a single symbol - `UTC, or a tz-database name such as `$\"Europe/London\""];
    / The window is taken on time_column, so time_column must be a field this
    / adapter actually READS. Without this check a typo registers happily and
    / surfaces two layers down: validate never checks the column (it is not
    / in `columns`), the live query filters on something the declaration never
    / described, and only window_fixture notices - at fetch time, mid-run.
    / row_key: declared and VALIDATED, deliberately not yet used.
    / .
    / The mechanism lands ahead of the semantics on purpose. docs/restatement-
    / design.md sets out why: answering "rows can be superseded in
    / place" changes what is_covered MEANS, and sixteen files rest on the
    / current meaning - so the shape of that change needs agreeing before
    / any of it is built. A declared key is the one piece that is a
    / prerequisite either way and has zero blast radius on its own.
    / .
    / It also earns its place independently of restatements. A failed
    / window was
    / answered "leave the published rows, record no coverage, re-run redoes
    / the window", and that duplicates rows unless the publish path can
    / dedupe - the framework promises retry-SAFE publication, which is explicitly
    / weaker than exactly-once. A row key is exactly what makes that dedupe
    / possible, so this is worth having even if supersession is deferred.
    / `11h=abs type`, not `-11h=abs type`: abs is always positive, so the
    / latter can never be true and rejected every row_key including a
    / correct one. 11h=abs accepts both a symbol atom (-11h) and a symbol
    / vector (11h), which is the point - a single-column key should not have
    / to be enlisted at the call site.
    if[not 11h=abs type decl`row_key;
        '"define: ",string[source],"'s row_key must be a symbol or symbol vector naming the column(s) that identify a row uniquely"];
    key_cols:(),decl`row_key;
    key_absent:key_cols where not key_cols in decl`columns;
    if[count key_absent;
        '"define: ",string[source],"'s row_key names ",(", " sv string key_absent),
         " which is not among its declared columns - a key this contract cannot see cannot identify a row"];

    if[not (decl`time_column) in decl`columns;
        '"define: ",string[source],"'s time_column ",string[decl`time_column],
         " is not one of its declared columns (",(", " sv string decl`columns),
         ") - the window is taken on that column, so it must be one the contract describes"];
    / Enforced rather than hoped for: the window column must be a
    / TIMESTAMP. q's datetime (`z`) is a float count of days, so z->p is a
    / rounding that loses sub-second precision silently - 999 of 1000
    / nanosecond-spaced instants do not survive it, and whole seconds do,
    / which is why it passes every hand-check. A window cut on such a column
    / produces plausible numbers and misplaced rows rather than an error.
    time_idx:(decl`columns)?decl`time_column;
    time_char:(decl`types)[time_idx];
    if[not "p"=time_char;
        / Short by necessity: q truncates a thrown string at 255 bytes, so the
        / long form lives in the comment above rather than in the message.
        '"define: ",string[source],"'s time_column ",string[decl`time_column],
         " is type \"",time_char,"\", not \"p\" - the window column must be a timestamp; a datetime rounds sub-second values silently"];
    tr:$[`transport in key decl; decl`transport; default_transport];
    if[not tr in transports;
        '"define: ",string[source],"'s transport must be one of ",(", " sv string transports)];
    / Stored on EVERY declaration, declared or not: `sources` holds dicts, and
    / a key present on one and absent on another stops later assignments
    / fitting - the shape .qbw's optional_cfg normalisation exists for.
    decl[`transport]:tr;
    / Store row_key NORMALISED to a vector, always.
    / .
    / Two reasons, and the second is not obvious. Semantically it means no
    / consumer has to decide whether a single-column key needs enlisting.
    / Mechanically it is required: `sources[source]:decl` throws `type` when
    / one stored declaration's row_key is an atom and another's is a vector,
    / because the dict's value list has already settled on a shape. Storing
    / one shape keeps every declaration mutually assignable.
    sources[source]:@[decl;`row_key;:;key_cols];
    .[{.qlog.dbg[x;y;z]};(source;"source registered";
        `columns`time_column`tz`transport`row_key!(decl`columns;decl`time_column;decl`tz;tr;key_cols));::];
    source}

/ Every registered source's name.
/ .
/ The registry IS the list - there is no second declaration of which sources
/ exist, which is what makes the question bank true: adding a source is a file plus a
/ registration, with no core change.
/ @return a symbol vector, empty when nothing has registered yet
/ @eg .qsrc.defined[]
defined:{[] key sources}

/ The column(s) identifying a row uniquely, always as a vector.
/ .
/ Exported so a future dedupe or restatement path has one place to ask,
/ rather than each caller reaching into the declaration and deciding for
/ itself whether a single symbol needs enlisting.
/ Stored normalised by `define`, so this is already a vector - the `(),`
/ is belt-and-braces for a declaration written directly into `sources` by a
/ test rather than through register.
row_key:{[source] (),(declaration source)`row_key}

/ One source's full declaration, or a refusal naming it.
/ .
/ The single read path every other module uses to reach a source's contract -
/ five files call it - so it throws rather than returning a null: a caller
/ that got an empty dict back would fail later, somewhere else, on a missing
/ key.
/ @param source the registered source's name, as a symbol
/ @return the declaration dict (source, table, target, time_column, row_key,
/   columns, types, query, fixture, tz)
/ @throws error naming the source when it was never registered
/ @eg .qsrc.declaration `demo_deals
declaration:{[source]
    if[not source in key sources;
        '"declaration: ",string[source]," is not a registered source - sources register centrally, so an unregistered source is a wiring bug rather than a lookup miss"];
    sources source}

/ ------------------------------------------------------------ VALIDATION

/ Private: the q type character for each column of a table.
/ .
/ `meta`'s `t` column is exactly this, and using it rather than `type each`
/ means an empty table validates the same way a populated one does - which
/ matters because a fixture may legitimately be empty, and a source may
/ legitimately return no rows for a window.
type_chars:{[tbl] exec t from 0!meta tbl}

column_names:{[tbl] exec c from 0!meta tbl}

/ Validate a table against a source's declaration.
/ .
/ This is the single function both the fixture check and the live metadata
/ check go through, which is what makes "that same contract" true rather
/ than aspirational.
/ .
/ Extra columns are ALLOWED, declared columns are not optional. A source
/ growing a column is routine and breaking on it would make every upstream
/ addition an outage; a source LOSING a declared column, or changing its
/ type, is precisely the silent breakage worth failing on - a missing column
/ reads as a null in most q code rather than as an error.
/ @param source a registered source name
/ @param tbl a table to check
/ @return 1b when the table satisfies the declaration
/ @throws error naming every missing field and every type mismatch at once
validate:{[source;tbl]
    decl:declaration source;
    present:column_names tbl;
    chars:type_chars tbl;
    missing:decl[`columns] where not decl[`columns] in present;
    / Report missing columns AND type mismatches together. Reporting only the
    / first class means fixing the columns, re-running, and only then
    / learning the types are wrong too.
    checkable:decl[`columns] where decl[`columns] in present;
    expected:decl[`types] decl[`columns]?checkable;
    actual:chars present?checkable;
    wrong:checkable where not expected=actual;
    if[count missing,wrong;
        '"validate: ",string[source]," does not satisfy its contract",
         $[count missing; " - missing field(s): ",", " sv string missing; ""],
         $[count wrong;
            " - type mismatch on ",", " sv {x[0]," (expected ",x[1],", got ",x[2],")"} each
                flip (string wrong;enlist each expected where not expected=actual;
                      enlist each actual where not expected=actual);
            ""]];
    .[{.qlog.dbg[x;y;z]};(source;"contract satisfied";`rows`columns!(count tbl;count decl`columns));::];
    1b}

/ Validate a source's own fixture against the same contract as live data.
/ .
/ Runs in the deterministic suite, with no connection anywhere. A fixture
/ that does not satisfy the contract is a broken test double, and finding
/ that out from a failing worker test is a much longer path.
validate_fixture:{[source] validate[source;(declaration[source]`fixture)[]]}

/ Validate LIVE external metadata against the same declaration.
/ .
/ Separate from validate_fixture only in where the table comes from - the
/ contract and the checking are identical, which is the requirement.
/ .
/ Belongs to the `smoke` lane, not the deterministic suite: the
/ deterministic suite proves local behaviour, not that a configured external
/ service is reachable or compatible.
/ @param source a registered source name
/ @param h an open handle to the external source
validate_live:{[source;h]
    decl:declaration source;
    m:@[{[handle;table_name] handle({0!meta x};table_name)}[h];decl`table_name;
        {[table_name;err] '"validate_live: cannot read metadata for ",string[table_name]," (",err,")"}[decl`table_name;]];
    present:exec c from m;
    chars:exec t from m;
    missing:decl[`columns] where not decl[`columns] in present;
    if[count missing;
        '"validate_live: ",string[decl`table_name]," is missing ",(", " sv string missing),
         " - the external schema has changed, or this declaration was always wrong"];
    checkable:decl`columns;
    expected:decl`types;
    actual:chars present?checkable;
    wrong:checkable where not expected=actual;
    if[count wrong;
        '"validate_live: ",string[decl`table_name]," type mismatch on ",", " sv string wrong];
    1b}

/ ---------------------------------------------------------- CREDENTIALS

/ The environment variable holding a source's credential.
/ .
/ Mechanical from the source name, so an operator can guess it. Deliberately
/ a separate prefix from .qwcfg's UQF_: a credential is not configuration,
/ and keeping the namespaces apart means a credential can never arrive
/ through the YAML or overrides layer by accident.
credential_var:{[source] "UQF_SOURCE_CRED_",upper string source}

/ Read a source's credential, or refuse.
/ .
/ Environment ONLY. There is no file fallback and no vault, on purpose:
/ nothing secret can live in this tree, and a file fallback is how a
/ credential ends up committed. The error names the variable, since "which
/ environment variable" is the only thing the operator needs to know.
/ @throws error when the variable is unset
/ `env_var`, not `var`: var is a q BUILTIN (variance), so assigning it as a
/ lambda LOCAL throws `assign at load time and aborts the rest of the file.
/ Sixth reserved-name collision here, and the first as a local rather than a
/ parameter - check_q_traps.py now covers both.
require_credentials:{[source]
    declaration source;
    env_var:credential_var source;
    v:getenv `$env_var;
    / The variable's NAME only - never its value, which is a credential.
    .[{.qlog.dbg[x;y;z]};(source;"credential lookup";`var`present!(env_var;0<count v));::];
    if[0=count v;
        '"require_credentials: ",string[source]," has no credential - set ",env_var,
         " in the environment. There is deliberately no file or vault fallback: ",
         "nothing secret lives in this repository"];
    v}

/ Is a credential available? For deciding between the live and fixture paths
/ without throwing.
has_credentials:{[source] 0<count getenv `$credential_var source}

/ ---------------------------------------------------------------- ZONES

/ The zone table: timezoneID, gmtDateTime, adjustment.
/ .
/ Empty until an operator loads one, and that is deliberate. q has no
/ built-in tz database - `ltime`/`gtime` only ever speak the PROCESS's own
/ TZ, which is a property of whoever started the process and therefore the
/ least trustworthy input available. So conversion needs a real table, and a
/ source that declares a non-UTC zone without one is refused rather than
/ approximated (see require_zone_table).
/ .
/ Not vendored a second time: TorQ already ships a tzdata-derived table at
/ lib/torq/config/tzinfo (631 zones, 70381 transitions), in exactly this
/ shape. src/ deliberately does not reach into lib/, so the PATH is the
/ caller's to supply - tests and processes pass it in.
zone_table:0#([] timezoneID:`symbol$(); gmtDateTime:`timestamp$(); adjustment:`timespan$())

/ Cached so require_zone_table is not a 70k-row scan per fetch.
zone_names:`symbol$()

/ Load a zone table from a serialised q table.
/ @param path a file path, e.g. "lib/torq/config/tzinfo"
/ @return the number of transitions loaded
/ @throws error when the file does not hold a table of the expected shape
load_zone_table:{[path]
    zt:@[get;hsym `$path;{'"load_zone_table: cannot read a zone table from ",x," (",y,")"}[path]];
    if[not .Q.qt zt;
        '"load_zone_table: ",path," does not hold a table"];
    needed:`timezoneID`gmtDateTime`adjustment;
    absent:needed where not needed in column_names zt;
    if[count absent;
        '"load_zone_table: ",path," is missing ",(", " sv string absent),
         " - a zone table needs a zone name, the UTC instant a rule takes effect, and the offset it applies"];
    trimmed:?[zt;();0b;needed!needed];
    / The sort is load-bearing: the lookup in offset_at is an `aj`, and on an
    / unsorted right-hand table `aj` returns the wrong row SILENTLY rather
    / than erroring. Sort FIRST and group after - `xasc` reorders rows, so a
    / `g` attribute applied beforehand would be dropped, or worse, stale.
    sorted:`gmtDateTime xasc trimmed;
    zone_table::update `g#timezoneID from sorted;
    zone_names::exec distinct timezoneID from zone_table;
    count zone_table}

/ Refuse to convert without a table, or for a zone it does not know.
/ .
/ There is deliberately no fallback to a fixed offset. A fixed offset is
/ correct for part of the year and an hour wrong for the rest, which puts
/ rows in the wrong window without ever failing - and a coverage ledger then
/ records both windows as complete.
require_zone_table:{[tz]
    if[0=count zone_table;
        / Consequence first, then the fix: the 255-byte truncation would
        / otherwise cut the reason, which is the part that matters.
        '"require_zone_table: no zone table loaded, and ",string[tz],
         " needs one - a fixed-offset fallback would be an hour wrong for half the year and never error. See load_zone_table"];
    if[not tz in zone_names;
        '"require_zone_table: zone ",string[tz]," is not in the loaded zone table (",
         string[count zone_names]," zones) - check the tz-database spelling, e.g. `$\"Europe/London\""];
    tz}

/ Private: the offsets this zone has ever used. At most 8 in tzdata, so the
/ candidate search in local_to_utc is cheap and fully vectorised.
offsets_for:{[tz] distinct exec adjustment from zone_table where timezoneID=tz}

/ Private: the offset in effect at each UTC instant. Unambiguous by
/ construction - every UTC instant has exactly one offset.
offset_at:{[tz;ts]
    exec adjustment from aj[`timezoneID`gmtDateTime;
        ([] timezoneID:(count ts)#tz; gmtDateTime:ts);
        zone_table]}

/ UTC -> the source's local wall clock.
/ .
/ Shape-preserving: an atom in, an atom out, so a caller converting window
/ bounds does not have to enlist and unwrap.
/ @throws error when an instant predates the zone table's coverage - a null
/   offset would otherwise propagate as a null timestamp, which reads as "no
/   data" rather than as "the lookup missed"
utc_to_local:{[tz;ts]
    tsv:(),ts;
    if[0=count tsv; :ts];
    a:offset_at[tz;tsv];
    if[any null a;
        '"utc_to_local: ",string[tz]," has no rule covering ",
         (-3!min tsv where null a)," - it predates the zone table's coverage"];
    $[0>type ts; first; ::] tsv+a}

/ The source's local wall clock -> UTC.
/ .
/ This direction is the hard one, and it is the whole reason a fixed offset
/ will not do. A local wall-clock reading is not a unique instant:
/ .
/   - on a spring-forward day an hour of local times NEVER HAPPENED
/     (Europe/London 2026.03.29, local 01:00-01:59).
/   - on an autumn day an hour of local times happens TWICE (Europe/London
/     2026.10.25, local 01:00-01:59 - once as BST, once as GMT).
/ .
/ So the method is candidate-and-verify rather than a lookup: every offset
/ the zone has ever used gives one candidate UTC instant, and a candidate is
/ real only if converting it back (the unambiguous direction) reproduces the
/ local reading. The count of survivors is the answer:
/ .
/   1 survivor - the normal case, converted.
/   0 survivors - the local time never existed. THROW.
/   2 survivors - the local time is ambiguous. THROW.
/ .
/ Throwing on both is the deliberate pick, and it is the only
/ one that cannot lie. Picking either candidate silently assigns the row a
/ UTC instant that may be an hour off, which moves it into a neighbouring
/ backfill window - and since the ledger records windows rather than rows,
/ both windows then read as complete while one holds an hour of the other's
/ data. A hard failure at fetch, naming the row, is recoverable; a
/ misplaced hour discovered months later is not. The operator's fix is to
/ have the source hand over UTC, which is why `UTC is the recommended
/ declaration and the only one needing no table at all.
/ @param tz a zone in the loaded table
/ @param ts local wall-clock timestamp(s)
/ @return the corresponding UTC timestamp(s), same shape as ts
/ @throws error on a nonexistent or an ambiguous local time
local_to_utc:{[tz;ts]
    tsv:(),ts;
    if[0=count tsv; :ts];
    cs:local_candidates[tz;tsv];
    nvalid:count each cs;
    if[any 0=nvalid;
        '"local_to_utc: ",nonexistent_message[tz;first tsv where 0=nvalid]];
    if[any 1<nvalid;
        pos:first where 1<nvalid;
        '"local_to_utc: ",ambiguous_message[tz;tsv pos;cs pos]];
    $[0>type ts; first; ::] first each cs}

/ Private: every UTC instant a local reading could denote, per row.
/ .
/ Candidate-and-verify: every offset the zone has ever used gives one
/ candidate, kept only if converting it back reproduces the reading. One
/ vectorised round trip per offset (at most 8 in tzdata), not one lookup per
/ row - a backfill window can hold millions.
/ @return a list, one ragged entry per input row: 1 instant normally, 0 in a
/   spring-forward gap, 2 in an autumn repeated hour
local_candidates:{[tz;tsv]
    offs:offsets_for tz;
    if[0=count offs;
        '"local_candidates: no offsets for zone ",string tz];
    cands:tsv -\: offs;
    ok:flip {[tz;tsv;o] tsv = utc_to_local[tz;tsv-o]}[tz;tsv] each offs;
    cands @' where each ok}

/ Private: the two error texts, shared by local_to_utc and narrow_to_utc so
/ the two paths cannot explain the same failure differently.
nonexistent_message:{[tz;bad]
    "local time ",(-3!bad)," does not exist in ",string[tz],
    " - it falls in a spring-forward gap, so no UTC instant maps to it. Either the ",
    "declared tz is wrong for this source, or the source is emitting ",
    "wall-clock readings its own calendar never had"}

ambiguous_message:{[tz;bad;cs]
    "local time ",(-3!bad)," is ambiguous in ",string[tz],
    " - it occurs twice on an autumn transition, at ",(" and " sv -3!'asc cs),
    " UTC. Refusing to pick: either choice can move the row into a neighbouring ",
    "backfill window, which the ledger would still record as complete. Have the ",
    "source hand over UTC"}

/ ------------------------------------------------------------- COERCION

/ The coercion function for each declared q type character.
/ .
/ Declared here rather than left to each adapter, because the coercion question was
/ "is there ONE shared coercion layer" and the answer is only true if every
/ source reaches it by default. A source that casts its own text is a source
/ that can get the decimal comma or the date-only timestamp wrong privately.
/ .
/ `p` deliberately maps to to_timestamp, which REFUSES a date-only value
/ rather than widening it to midnight. A source whose column really is a
/ date, and for which midnight is correct, has to say so by coercing with
/ .qcoer.to_date_as_midnight explicitly - which is greppable, unlike an
/ accident of q's casting rules.
/ A KNOWN LIMITATION, recorded rather than shipped silently: this maps every
/ `s` column to to_symbol, which UPPER-CASES. That is right for currency
/ pairs, which is this library's convention throughout - but it is a guess
/ for enum-like columns. A text source returning "buy" yields `BUY, while
/ demo_deals' own fixture uses `buy, so the two paths disagree on case for
/ that one column.
/ .
/ It does not bite today: the fixture returns typed data, so `coerce` is
/ never applied to it. It WOULD bite the first time a real text source lands
/ a side or venue column, and the fix is a per-field coercer override in the
/ declaration rather than a cleverer default - there is no case rule that is
/ right for both `EURUSD and `buy. Left undone deliberately because
/ inventing the override mechanism before a source needs it would be
/ guessing at its shape.
coercers:(!). flip (
    ("f";.qcoer.to_float);
    ("j";.qcoer.to_long);
    ("p";.qcoer.to_timestamp);
    ("s";.qcoer.to_symbol))

/ Coerce a table of TEXT columns into the declared types.
/ .
/ For a source that returns text - which is what coercion is about - this is the
/ step between fetch and validate. Returns the coerced table plus a per-
/ column failure count, so the worker can decide: a few bad rows in a
/ million might be tolerable and worth logging, while a column that failed
/ entirely means the format changed and the window must not be published.
/ That judgement is the worker's, not this layer's.
/ @param source a registered source name
/ @param tbl a table whose declared columns hold text
/ @return dict of `table (coerced) and `failures (field -> count)
/ @throws error when a declared type has no coercer
coerce:{[source;tbl]
    decl:declaration source;
    present:column_names tbl;
    columns:decl[`columns] where decl[`columns] in present;
    chars:(decl`types) (decl`columns)?columns;
    unknown:distinct chars where not chars in key coercers;
    if[count unknown;
        '"coerce: no coercer for declared type(s) \"",unknown,"\" in ",string[source],
         " - add one to .qsrc.coercers deliberately rather than casting privately"];
    results:{[tb;f;c] .qcoer.coerce_column[coercers c;tb f]}[tbl;;] .' flip (columns;chars);
    coerced:tbl;
    coerced:{[tb;f;r] @[tb;f;:;r`values]}/[coerced;columns;results];
    failures:columns!results[;`failed];
    .[{.qlog.dbg[x;y;z]};(source;"coerced";`rows`columns`failures!(count tbl;columns;failures));::];
    `table`failures!(coerced;failures)}

/ ------------------------------------------------------------- FETCHING

/ Fetch one window, from the live source or from the fixture.
/ .
/ The fixture is not a fallback for a FAILED connection - that would turn an
/ outage into silently synthetic data, which is the worst possible outcome
/ for a coverage ledger that then records the window as complete. It is the
/ path taken when no credential is configured at all, which is an explicit
/ statement that this is a demo.
/ .
/ So: credential present means live, and a failure there is a failure.
/ Credential absent means fixture, and that is announced in the return value
/ rather than inferred.
/ @param source a registered source name
/ @param h an open handle, or 0Ni when running on the fixture
/ @param range_from window start
/ @param range_to window end, exclusive
/ The fixture is WINDOWED here, on the declared time_column, using the same
/ half-open [range_from;range_to) bounds the live query uses. Without that
/ the fixture returns every row for every window, so a three-window run
/ publishes the fixture three times - triplicating the data while coverage
/ records each window as correctly complete. Nothing errors, the row counts
/ merely lie, and the target table quietly holds three copies.
/ .
/ It is done here rather than in each fixture so a fixture author cannot
/ forget it, and so the windowing is provably the same on both paths - the
/ live query filters `>=from, <to` and so does this.
/ @return (`live or `fixture; the table)
/ .
/ For a non-UTC source the window is translated in BOTH directions here -
/ bounds out, timestamps back - so neither the query nor the fixture author
/ has to know about zones. See source_bounds and narrow_to_utc.
fetch_window:{[source;h;range_from;range_to]
    t0:.z.p;
    decl:declaration source;
    bounds:source_bounds[decl;range_from;range_to];
    .[{.qlog.dbg[x;y;z]};(source;"fetching";
        `path`range_from`range_to`source_from`source_to`tz!
            ($[null h;`fixture;`live];range_from;range_to;bounds 0;bounds 1;decl`tz));::];
    page:$[null h;
        (`fixture;window_fixture[decl;bounds 0;bounds 1]);
        (`live;(decl`query)[h;bounds 0;bounds 1])];
    out:narrow_to_utc[decl;page 1;range_from;range_to];
    / fetched vs kept differ only for a zoned source, whose bounds are padded:
    / the difference is the neighbouring windows' rows, dropped on purpose.
    .[{.qlog.dbg[x;y;z]};(source;"fetched";
        `path`fetched`kept`ms!(page 0;count page 1;count out;`long$(.z.p-t0)%1000000));::];
    (page 0;out)}

/ How far to widen a non-UTC source's window, in its own clock.
/ .
/ One day, which is far more than any offset change in tzdata (the largest
/ is the date-line jump, 24h). It buys correctness at the cost of
/ over-fetching, and the exact narrowing in narrow_to_utc throws the excess
/ away - so the only cost is bandwidth on sources that are not UTC.
bound_padding:1D

/ Private: the window bounds to hand the source, in the SOURCE's clock.
/ .
/ For `UTC this is the identity, and that is the path every source in this
/ tree takes today.
/ .
/ For a zoned source the bounds are deliberately WIDE rather than exact, and
/ this is the trap worth stating because the obvious implementation is
/ silently wrong. Converting each bound with the offset in effect AT THAT
/ BOUND is not monotonic across an autumn transition: measured for
/ Europe/London, the UTC window [2026.10.25D00:30; 2026.10.25D01:30)
/ converts to local [01:30; 01:30) - an EMPTY range. The query returns no
/ rows, nothing errors, and the coverage ledger records an hour of missing
/ trades as a complete window.
/ .
/ So: pad the bounds, fetch a superset, and narrow exactly in UTC afterwards
/ where the arithmetic is unambiguous.
source_bounds:{[decl;range_from;range_to]
    tz:decl`tz;
    if[`UTC~tz; :(range_from;range_to)];
    require_zone_table tz;
    (utc_to_local[tz;range_from-bound_padding];
     utc_to_local[tz;range_to+bound_padding])}

/ Private: convert a fetched page's time_column to UTC and narrow it to the
/ requested half-open range.
/ .
/ For `UTC this is the identity: the query (or window_fixture) has already
/ applied [range_from;range_to) and re-filtering would be dead code that
/ could only ever disagree.
/ .
/ For a zoned source the narrowing is NOT optional - source_bounds
/ deliberately over-fetched, so without this every window would publish up
/ to a day of its neighbours' rows while coverage recorded the narrow range.
/ .
/ It does NOT simply call local_to_utc on the column, and the reason is the
/ padding above. local_to_utc refuses an ambiguous reading outright, which is
/ right when a caller asks about one instant - but here the over-fetch has
/ pulled in up to a day of a NEIGHBOUR's rows, and failing this window
/ because of an ambiguous row that belongs to the next one would make every
/ window on an autumn transition day unbackfillable, not just the affected
/ hour. So the rule is range-scoped:
/ .
/   - an ambiguous row is refused only if one of its candidate instants
/     actually lands in [range_from;range_to). Otherwise it is dropped: it is
/     not this window's row, and the window that owns it will refuse it.
/   - a NONEXISTENT reading is refused unconditionally, range or no range.
/     There is no instant to compare against a range, and a source emitting a
/     wall-clock time its own calendar never had means the declared zone is
/     wrong - which is a contract breach, not a windowing question.
narrow_to_utc:{[decl;tbl;range_from;range_to]
    tz:decl`tz;
    if[`UTC~tz; :tbl];
    f:decl`time_column;
    local_ts:tbl f;
    if[0=count local_ts; :tbl];
    cs:local_candidates[tz;local_ts];
    nvalid:count each cs;
    if[any 0=nvalid;
        '"narrow_to_utc: ",nonexistent_message[tz;first local_ts where 0=nvalid]];
    in_range:{[lo;hi;c] any (c>=lo) and c<hi}[range_from;range_to] each cs;
    if[any in_range and 1<nvalid;
        pos:first where in_range and 1<nvalid;
        '"narrow_to_utc: ",ambiguous_message[tz;local_ts pos;cs pos]];
    / Every surviving row now has exactly one reading, so the conversion is
    / unambiguous and the half-open bound is applied in UTC - the direction
    / where the arithmetic cannot double-count or skip.
    kept:tbl where in_range;
    ![kept;();0b;(enlist f)!enlist enlist first each cs where in_range]}

/ Private: apply the window to a fixture, on its declared time_column.
/ .
/ Functional select (`?[t;where;0b;()]`) rather than qSQL, because the column
/ name is a variable: `select from t where time_column>=from_ts` would compare
/ the literal symbol, not the column it names.
window_fixture:{[decl;range_from;range_to]
    t:(decl`fixture)[];
    f:decl`time_column;
    if[not f in column_names t;
        '"window_fixture: ",string[decl`source],"'s fixture has no ",string[f],
         " column, so the window cannot be applied - it would return every row for every window and triplicate the data"];
    ?[t;((>=;f;range_from);(<;f;range_to));0b;()]}

\d .
