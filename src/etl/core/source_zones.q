/ source_zones.q - the time-zone table and UTC/local conversion.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

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
/ @private
offsets_for:{[tz] distinct exec adjustment from zone_table where timezoneID=tz}

/ Private: the offset in effect at each UTC instant. Unambiguous by
/ construction - every UTC instant has exactly one offset.
/ @private
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
/ @private
local_candidates:{[tz;tsv]
    offs:offsets_for[tz];
    if[0=count offs;
        '"local_candidates: no offsets for zone ",string tz];
    cands:tsv -\: offs;
    ok:flip {[tz;tsv;o] tsv = utc_to_local[tz;tsv-o]}[tz;tsv] each offs;
    cands @' where each ok}

/ Private: the two error texts, shared by local_to_utc and narrow_to_utc so
/ the two paths cannot explain the same failure differently.
/ @private
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

\d .
