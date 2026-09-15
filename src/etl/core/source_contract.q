/ source_contract.q - the centralised external-source contract (.qsrc).
/ .
/ Implements requirement E-12: "register every external source table, target
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
/   E-04 (answered via A-04 + F-22/F-23) - the external driver is NOT a hard
/     dependency. A public single-host demo cannot require a licensed ODBC
/     driver, or the whole backfill path is undemonstrable. Every source
/     declares a fixture, so the path is exercisable with no driver at all.
/ .
/   E-07 (answered via A-04 + the F-20 precedent) - credentials come from the
/     ENVIRONMENT only. Nothing secret lives in this tree, and the YAML layer
/     must never carry one. `require_credentials` enforces that rather than
/     documenting it.
/ .
/   E-08 (answered via F-14) - source queries are PARAMETERISED q lambdas,
/     never built by string concatenation. F-14's guarantee is that no caller
/     input reaches query text; uqf_frontend/queries.py already honours it,
/     and a source adapter is the same problem with a less friendly input.
/ .
/   E-09 (answered via A-04) - bank-internal business logic is not
/     reimplementable here. What gets built is a generic ANALOGUE with the
/     same shape and none of the logic. So this contract describes shapes, and
/     deliberately carries no business semantics.
/ .
/ TIME AND TIMEZONE (issue #80: L-03, L-05, L-06)
/ .
/   L-06 - "do external sources return local times, and who converts?"
/     WHAT THE BANK'S SOURCES ACTUALLY RETURN IS NOT KNOWABLE FROM HERE. The
/     canonical tree is unreachable and A-04 forbids its schemas appearing
/     here, so nobody in this repository can answer that half. What IS
/     decidable is the policy, and the policy is what changes the code:
/ .
/       - internally, everything is UTC. That is not new: python/uqf_frontend
/         already enforces it at the HTTP edge, where queries.coerce rejects
/         a naive datetime outright. It cites that rule as "E-08/R9.1" in the
/         FRONTEND's numbering - a different E-08 from this file's, which is
/         the parameterised-query decision above. Same rule, two numbering
/         schemes; this file's timezone handling is the q-side half of it.
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
/   L-03 - the `z->p` cast bug class. THE CANONICAL BUG ITSELF IS NOT
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
/     documentation: (1) register below requires the time_field to be one of
/     the declared fields AND to be declared `p`, so a z time column is a
/     registration failure; (2) validate/validate_live compare declared type
/     characters against `meta`, so a source that silently changes a column
/     from p to z fails on both the fixture and the live path; (3)
/     scripts/check_q_traps.py forbids the q datetime type in src/ outright -
/     the cast direction is not statically decidable, but the type's
/     PRESENCE is, and this tree has no legitimate use for it.
/ .
/   L-05 - DST in windowed backfills. Windows are cut in UTC by
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
/ `time_zone` is REQUIRED, with no default (L-06). A defaulted zone is the
/ bug: it reads as a decision downstream while nobody ever made one. Stating
/ `UTC` costs one symbol and makes "this source hands over UTC" a claim
/ somebody wrote, which validate_live can then be run against.
required_declarations:`source`table`target`time_field`fields`types`query`fixture`time_zone

/ source -> its declaration dict.
sources:(`symbol$())!();

/ Register an external source table (E-12).
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
/   time_field - the column the window is taken on, as a symbol
/   fields  - the columns this adapter READS, as a symbol vector
/   types   - the expected q type characters, one per field, as a string
/   query   - a parameterised lambda taking (handle;range_from;range_to)
/   fixture - a niladic lambda returning a synthetic table of the same shape
/   time_zone - the zone the source's time_field is expressed in, as a
/     symbol: `UTC, or a tz-database name such as `$"Europe/London" (L-06)
/ @return the source name
/ @throws error naming every missing or malformed declaration at once
register:{[source;decl]
    missing:required_declarations where not required_declarations in key decl;
    if[count missing;
        '"register: ",string[source]," is missing declaration(s): ",", " sv string missing];
    if[not 11h=abs type decl`fields;
        '"register: ",string[source],"'s fields must be a symbol vector"];
    if[not 10h=abs type decl`types;
        '"register: ",string[source],"'s types must be a string of q type characters, one per field"];
    if[(count decl`fields)<>count decl`types;
        '"register: ",string[source]," declares ",string[count decl`fields],
         " field(s) but ",string[count decl`types]," type(s) - E-12 requires a type per required field"];
    if[not 100h=type decl`query;
        '"register: ",string[source],"'s query must be a lambda (E-08: parameterised, never concatenated)"];
    if[not 100h=type decl`fixture;
        '"register: ",string[source],"'s fixture must be a niladic lambda (E-04: the path must be exercisable with no driver)"];
    if[not -11h=type decl`time_zone;
        '"register: ",string[source],"'s time_zone must be a single symbol - `UTC, or a tz-database name such as `$\"Europe/London\" (L-06)"];
    / The window is taken on time_field, so time_field must be a field this
    / adapter actually READS. Without this check a typo registers happily and
    / surfaces two layers down: validate never checks the column (it is not
    / in `fields`), the live query filters on something the declaration never
    / described, and only window_fixture notices - at fetch time, mid-run.
    if[not (decl`time_field) in decl`fields;
        '"register: ",string[source],"'s time_field ",string[decl`time_field],
         " is not one of its declared fields (",(", " sv string decl`fields),
         ") - the window is taken on that column, so it must be one the contract describes"];
    / L-03, enforced rather than hoped for: the window column must be a
    / TIMESTAMP. q's datetime (`z`) is a float count of days, so z->p is a
    / rounding that loses sub-second precision silently - 999 of 1000
    / nanosecond-spaced instants do not survive it, and whole seconds do,
    / which is why it passes every hand-check. A window cut on such a column
    / produces plausible numbers and misplaced rows rather than an error.
    time_idx:(decl`fields)?decl`time_field;
    time_char:(decl`types)[time_idx];
    if[not "p"=time_char;
        / Short by necessity: q truncates a thrown string at 255 bytes, so the
        / long form lives in the comment above rather than in the message.
        '"register: ",string[source],"'s time_field ",string[decl`time_field],
         " is type \"",time_char,"\", not \"p\" - the window column must be a timestamp; a datetime rounds sub-second values silently (L-03)"];
    sources[source]:decl;
    source}

registered:{[] key sources}

declaration:{[source]
    if[not source in key sources;
        '"declaration: ",string[source]," is not a registered source - E-12 requires central registration, so an unregistered source is a wiring bug rather than a lookup miss"];
    sources source}

/ ------------------------------------------------------------ VALIDATION

/ Private: the q type character for each column of a table.
/ .
/ `meta`'s `t` column is exactly this, and using it rather than `type each`
/ means an empty table validates the same way a populated one does - which
/ matters because a fixture may legitimately be empty, and a source may
/ legitimately return no rows for a window.
type_chars:{[t] exec t from 0!meta t}

column_names:{[t] exec c from 0!meta t}

/ Validate a table against a source's declaration (E-12).
/ .
/ This is the single function both the fixture check and the live metadata
/ check go through, which is what makes "that same contract" true rather
/ than aspirational.
/ .
/ Extra columns are ALLOWED, declared fields are not optional. A source
/ growing a column is routine and breaking on it would make every upstream
/ addition an outage; a source LOSING a declared column, or changing its
/ type, is precisely the silent breakage worth failing on - a missing column
/ reads as a null in most q code rather than as an error.
/ @param source a registered source name
/ @param t a table to check
/ @return 1b when the table satisfies the declaration
/ @throws error naming every missing field and every type mismatch at once
validate:{[source;t]
    decl:declaration source;
    present:column_names t;
    chars:type_chars t;
    missing:decl[`fields] where not decl[`fields] in present;
    / Report missing fields AND type mismatches together. Reporting only the
    / first class means fixing the columns, re-running, and only then
    / learning the types are wrong too.
    checkable:decl[`fields] where decl[`fields] in present;
    expected:decl[`types] decl[`fields]?checkable;
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
    1b}

/ Validate a source's own fixture (E-12's "generated fixtures").
/ .
/ Runs in the deterministic suite, with no connection anywhere. A fixture
/ that does not satisfy the contract is a broken test double, and finding
/ that out from a failing worker test is a much longer path.
validate_fixture:{[source] validate[source;(declaration[source]`fixture)[]]}

/ Validate LIVE external metadata against the same declaration (E-12).
/ .
/ Separate from validate_fixture only in where the table comes from - the
/ contract and the checking are identical, which is the requirement.
/ .
/ Belongs to the `smoke` lane, not the deterministic suite (E-20): the
/ deterministic suite proves local behaviour, not that a configured external
/ service is reachable or compatible.
/ @param source a registered source name
/ @param h an open handle to the external source
validate_live:{[source;h]
    decl:declaration source;
    m:@[{[handle;tbl] handle({0!meta x};tbl)}[h];decl`table;
        {'"validate_live: cannot read metadata for ",string[decl`table]," (",x,")"}];
    present:exec c from m;
    chars:exec t from m;
    missing:decl[`fields] where not decl[`fields] in present;
    if[count missing;
        '"validate_live: ",string[decl`table]," is missing ",(", " sv string missing),
         " - the external schema has changed, or this declaration was always wrong"];
    checkable:decl`fields;
    expected:decl`types;
    actual:chars present?checkable;
    wrong:checkable where not expected=actual;
    if[count wrong;
        '"validate_live: ",string[decl`table]," type mismatch on ",", " sv string wrong];
    1b}

/ ---------------------------------------------------------- CREDENTIALS

/ The environment variable holding a source's credential (E-07).
/ .
/ Mechanical from the source name, so an operator can guess it. Deliberately
/ a separate prefix from .qwcfg's UQF_: a credential is not configuration,
/ and keeping the namespaces apart means a credential can never arrive
/ through the YAML or overrides layer by accident.
credential_var:{[source] "UQF_SOURCE_CRED_",upper string source}

/ Read a source's credential, or refuse (E-07).
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
    if[0=count v;
        '"require_credentials: ",string[source]," has no credential - set ",env_var,
         " in the environment. There is deliberately no file or vault fallback: ",
         "nothing secret lives in this repository (E-07)"];
    v}

/ Is a credential available? For deciding between the live and fixture paths
/ without throwing.
has_credentials:{[source] 0<count getenv `$credential_var source}

/ ---------------------------------------------------------------- ZONES

/ The zone table: timezoneID, gmtDateTime, adjustment (L-06).
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

/ Load a zone table from a serialised q table (L-06).
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

/ Refuse to convert without a table, or for a zone it does not know (L-06).
/ .
/ There is deliberately no fallback to a fixed offset. A fixed offset is
/ correct for part of the year and an hour wrong for the rest, which puts
/ rows in the wrong window without ever failing - and a coverage ledger then
/ records both windows as complete.
require_zone_table:{[zone]
    if[0=count zone_table;
        / Consequence first, then the fix: the 255-byte truncation would
        / otherwise cut the reason, which is the part that matters.
        '"require_zone_table: no zone table loaded, and ",string[zone],
         " needs one - a fixed-offset fallback would be an hour wrong for half the year and never error. See load_zone_table"];
    if[not zone in zone_names;
        '"require_zone_table: zone ",string[zone]," is not in the loaded zone table (",
         string[count zone_names]," zones) - check the tz-database spelling, e.g. `$\"Europe/London\""];
    zone}

/ Private: the offsets this zone has ever used. At most 8 in tzdata, so the
/ candidate search in local_to_utc is cheap and fully vectorised.
offsets_for:{[zone] distinct exec adjustment from zone_table where timezoneID=zone}

/ Private: the offset in effect at each UTC instant. Unambiguous by
/ construction - every UTC instant has exactly one offset.
offset_at:{[zone;ts]
    exec adjustment from aj[`timezoneID`gmtDateTime;
        ([] timezoneID:(count ts)#zone; gmtDateTime:ts);
        zone_table]}

/ UTC -> the source's local wall clock (L-06).
/ .
/ Shape-preserving: an atom in, an atom out, so a caller converting window
/ bounds does not have to enlist and unwrap.
/ @throws error when an instant predates the zone table's coverage - a null
/   offset would otherwise propagate as a null timestamp, which reads as "no
/   data" rather than as "the lookup missed"
utc_to_local:{[zone;ts]
    tsv:(),ts;
    if[0=count tsv; :ts];
    a:offset_at[zone;tsv];
    if[any null a;
        '"utc_to_local: ",string[zone]," has no rule covering ",
         (-3!min tsv where null a)," - it predates the zone table's coverage"];
    $[0>type ts; first; ::] tsv+a}

/ The source's local wall clock -> UTC (L-06), and the answer to L-05.
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
/ Throwing on both is the deliberate pick L-05 asks for, and it is the only
/ one that cannot lie. Picking either candidate silently assigns the row a
/ UTC instant that may be an hour off, which moves it into a neighbouring
/ backfill window - and since the ledger records windows rather than rows,
/ both windows then read as complete while one holds an hour of the other's
/ data. A hard failure at fetch, naming the row, is recoverable; a
/ misplaced hour discovered months later is not. The operator's fix is to
/ have the source hand over UTC, which is why `UTC is the recommended
/ declaration and the only one needing no table at all.
/ @param zone a zone in the loaded table
/ @param ts local wall-clock timestamp(s)
/ @return the corresponding UTC timestamp(s), same shape as ts
/ @throws error on a nonexistent or an ambiguous local time
local_to_utc:{[zone;ts]
    tsv:(),ts;
    if[0=count tsv; :ts];
    cs:local_candidates[zone;tsv];
    nvalid:count each cs;
    if[any 0=nvalid;
        '"local_to_utc: ",nonexistent_message[zone;first tsv where 0=nvalid]];
    if[any 1<nvalid;
        pos:first where 1<nvalid;
        '"local_to_utc: ",ambiguous_message[zone;tsv pos;cs pos]];
    $[0>type ts; first; ::] first each cs}

/ Private: every UTC instant a local reading could denote, per row.
/ .
/ Candidate-and-verify: every offset the zone has ever used gives one
/ candidate, kept only if converting it back reproduces the reading. One
/ vectorised round trip per offset (at most 8 in tzdata), not one lookup per
/ row - a backfill window can hold millions.
/ @return a list, one ragged entry per input row: 1 instant normally, 0 in a
/   spring-forward gap, 2 in an autumn repeated hour
local_candidates:{[zone;tsv]
    offs:offsets_for zone;
    if[0=count offs;
        '"local_candidates: no offsets for zone ",string zone];
    cands:tsv -\: offs;
    ok:flip {[zone;tsv;o] tsv = utc_to_local[zone;tsv-o]}[zone;tsv] each offs;
    cands @' where each ok}

/ Private: the two error texts, shared by local_to_utc and narrow_to_utc so
/ the two paths cannot explain the same failure differently.
nonexistent_message:{[zone;bad]
    "local time ",(-3!bad)," does not exist in ",string[zone],
    " - it falls in a spring-forward gap, so no UTC instant maps to it. Either the ",
    "declared time_zone is wrong for this source, or the source is emitting ",
    "wall-clock readings its own calendar never had (L-05)"}

ambiguous_message:{[zone;bad;cs]
    "local time ",(-3!bad)," is ambiguous in ",string[zone],
    " - it occurs twice on an autumn transition, at ",(" and " sv -3!'asc cs),
    " UTC. Refusing to pick: either choice can move the row into a neighbouring ",
    "backfill window, which the ledger would still record as complete. Have the ",
    "source hand over UTC (L-05)"}

/ ------------------------------------------------------------- FETCHING

/ Fetch one window, from the live source or from the fixture (E-04).
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
/ The fixture is WINDOWED here, on the declared time_field, using the same
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
    decl:declaration source;
    bounds:source_bounds[decl;range_from;range_to];
    page:$[null h;
        (`fixture;window_fixture[decl;bounds 0;bounds 1]);
        (`live;(decl`query)[h;bounds 0;bounds 1])];
    (page 0;narrow_to_utc[decl;page 1;range_from;range_to])}

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
    zone:decl`time_zone;
    if[`UTC~zone; :(range_from;range_to)];
    require_zone_table zone;
    (utc_to_local[zone;range_from-bound_padding];
     utc_to_local[zone;range_to+bound_padding])}

/ Private: convert a fetched page's time_field to UTC and narrow it to the
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
narrow_to_utc:{[decl;t;range_from;range_to]
    zone:decl`time_zone;
    if[`UTC~zone; :t];
    f:decl`time_field;
    local_ts:t f;
    if[0=count local_ts; :t];
    cs:local_candidates[zone;local_ts];
    nvalid:count each cs;
    if[any 0=nvalid;
        '"narrow_to_utc: ",nonexistent_message[zone;first local_ts where 0=nvalid]];
    in_range:{[lo;hi;c] any (c>=lo) and c<hi}[range_from;range_to] each cs;
    if[any in_range and 1<nvalid;
        pos:first where in_range and 1<nvalid;
        '"narrow_to_utc: ",ambiguous_message[zone;local_ts pos;cs pos]];
    / Every surviving row now has exactly one reading, so the conversion is
    / unambiguous and the half-open bound is applied in UTC - the direction
    / where the arithmetic cannot double-count or skip.
    kept:t where in_range;
    ![kept;();0b;(enlist f)!enlist enlist first each cs where in_range]}

/ Private: apply the window to a fixture, on its declared time_field.
/ .
/ Functional select (`?[t;where;0b;()]`) rather than qSQL, because the column
/ name is a variable: `select from t where time_field>=from_ts` would compare
/ the literal symbol, not the column it names.
window_fixture:{[decl;range_from;range_to]
    t:(decl`fixture)[];
    f:decl`time_field;
    if[not f in column_names t;
        '"window_fixture: ",string[decl`source],"'s fixture has no ",string[f],
         " column, so the window cannot be applied - it would return every row for every window and triplicate the data"];
    ?[t;((>=;f;range_from);(<;f;range_to));0b;()]}

\d .
