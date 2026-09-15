/ coercion.q - the shared text-to-type layer for external sources (.qcoer).
/ .
/ Implements requirement E-05, answered on issue #73: sources return text
/ that q must coerce, and there IS one shared coercion layer rather than a
/ per-source cast. Three trap classes were named as having actually bitten
/ the real sources, and each is handled explicitly below rather than left to
/ q's own casts:
/ .
/   floats      - decimal separators, exponents, and EMPTY-AS-ZERO
/   timestamps  - format, and a date where a timestamp was expected
/   symbols     - trailing whitespace and case
/ .
/ WHY A SHARED LAYER AND NOT A CAST AT EACH SITE
/ .
/ Every one of these traps returns a plausible value rather than an error, so
/ a per-source cast is a per-source opportunity to get it wrong quietly. The
/ requirements' own phrasing - "is there one shared coercion layer" - is the
/ answer: one place, tested once, named in the source contract.
/ .
/ WHAT I MEASURED, INCLUDING WHERE I WAS WRONG
/ .
/ Two things I expected to be traps are NOT, on this KDB-X build, and the
/ comments below say so rather than repeating folklore:
/ .
/   "F"$"" is already 0n, not 0f. So q does not silently turn a missing
/   number into a zero, and the empty-to-null mapping here agrees with q
/   rather than correcting it.
/ .
/   `$"EURUSD " TRIMS: the resulting symbol is 6 characters and matches
/   `EURUSD under both = and ~, and a join on it returns its row. So
/   trailing whitespace on a symbol is not a q-level trap at all. The trim
/   below is kept because it is free and a source may pad in ways `$ does
/   not handle, but it is not fixing a q behaviour.
/ .
/ Three traps ARE real and measured, and they are why this file exists:
/ .
/   "P"$"2026.09.15" silently yields midnight. A date where a timestamp was
/   expected collapses every row in a day onto 00:00, destroying intraday
/   ordering - so every markout at a positive horizon reads the wrong
/   quote. Nothing errors; the numbers are simply wrong. THIS IS THE WORST
/   ONE.
/ .
/   "F"$"1,0842" silently yields 0n. A European decimal comma does not
/   truncate, it DELETES the value - and a null rate then propagates as
/   "no data" rather than as a parse failure anyone would investigate.
/ .
/   `$"eurusd" does not match `EURUSD. Unlike whitespace, case is not
/   folded by `$, so a lower-case source symbol misses every join and
/   returns an empty table that reads as "no data for that pair".

\d .qcoer

/ ------------------------------------------------------------------ FLOAT

/ Characters that are legitimately part of a number, once a decimal comma
/ has been normalised. Anything else means the field is not a number at all
/ rather than a number needing cleanup.
numeric_chars:".-+eE0123456789"

/ Coerce text to a float, normalising a European decimal comma.
/ .
/ The comma is the real trap, measured: "F"$"1,0842" is 0n, so the value is
/ not truncated, it is DELETED. A null rate then reads downstream as "no
/ data for this row" rather than as a parse failure someone would
/ investigate - and for an FX rate, quietly having no price is materially
/ different from having a wrong one.
/ .
/ Empty also maps to null, which AGREES with q ("F"$"" is already 0n) rather
/ than correcting it. It is stated explicitly here anyway, because a reader
/ should not have to know that to trust the function, and because a future
/ build changing it would otherwise change this function's meaning silently.
/ @param s the text to coerce
/ @return the float, or 0n when the text is empty or not numeric
/ @eg .qcoer.to_float["1.0842"]  ->  1.0842
/ @eg .qcoer.to_float["1,0842"]  ->  1.0842
/ @eg .qcoer.to_float[""]        ->  0n
to_float:{[s]
    t:trim s;
    if[0=count t; :0n];
    / a decimal comma, only when there is no dot - "1,234.56" is a thousands
    / separator and this layer deliberately does NOT guess at those, because
    / "1,234" is ambiguous between 1234 and 1.234 and no rule is safe.
    normalised:$[(any t=",") and not any t="."; ssr[t;",";"."]; t];
    if[any t=","; if[any t="."; :0n]];
    if[not all normalised in numeric_chars; :0n];
    v:"F"$normalised;
    / "F"$ on garbage also gives 0n, so this is belt-and-braces rather than
    / the primary check - but it costs nothing and covers a form the
    / character test admits, e.g. "1.2.3".
    v}

/ Coerce text to a long, mapping empty to null rather than zero.
to_long:{[s]
    t:trim s;
    if[0=count t; :0Nj];
    if[not all t in numeric_chars; :0Nj];
    "J"$t}

/ ------------------------------------------------------------- TIMESTAMP

/ Coerce text to a timestamp, accepting both ISO and q spellings.
/ .
/ A source emitting "2026-09-15T09:30:00" is not malformed; q's own "P"$
/ handles several forms, but the DATE-ONLY case is the trap. "2026-09-15"
/ coerces happily to 2026.09.15D00:00:00.000000000 - so every row in a day
/ collapses onto midnight, every intraday ordering is destroyed, and every
/ markout at a positive horizon reads from the wrong quote. Nothing errors;
/ the numbers are simply wrong.
/ .
/ So a date-only value is REFUSED here rather than widened. A caller that
/ genuinely wants midnight can say so with `to_date_as_midnight`, which is
/ explicit and greppable.
/ @return the timestamp, or 0Np when empty, malformed, or date-only
/ @eg .qcoer.to_timestamp["2026-09-15T09:30:00"]  ->  2026.09.15D09:30:00
/ @eg .qcoer.to_timestamp["2026-09-15"]           ->  0Np
to_timestamp:{[s]
    t:trim s;
    if[0=count t; :0Np];
    if[is_date_only t; :0Np];
    / ISO uses "-" between date parts and "T" before the time; q uses "." and
    / "D". Normalise the separators rather than branching on format, so a
    / mixed source does not need two code paths.
    normalised:ssr[ssr[t;"T";"D"];"-";"."];
    @[{"P"$x};normalised;{0Np}]}

/ Is this text a date with no time part?
/ .
/ Exported because "did the source give me a date where I asked for a
/ timestamp" is a question worth answering explicitly, and because
/ to_timestamp's refusal is otherwise indistinguishable from a parse
/ failure.
is_date_only:{[s]
    t:trim s;
    if[0=count t; :0b];
    / no time separator at all, and short enough to be just a date
    (not any t in "TD ") and 10>=count t}

/ Widen a date-only value to midnight, DELIBERATELY and visibly.
/ .
/ Separate from to_timestamp so that "this column is a date and midnight is
/ correct for it" is a statement in the source declaration rather than an
/ accident of q's casting rules. If intraday ordering matters for the
/ dataset, this is the wrong function and the source needs a real timestamp.
to_date_as_midnight:{[s]
    t:trim s;
    if[0=count t; :0Np];
    d:@[{"D"$x};ssr[t;"-";"."];{0Nd}];
    $[null d; 0Np; `timestamp$d]}

/ ---------------------------------------------------------------- SYMBOL

/ Coerce text to a symbol, trimming and upper-casing.
/ .
/ CASE is the real trap: `$"eurusd" does not match `EURUSD, and in q that is
/ not an error - every join returns zero rows and the result is an empty
/ table that reads as "no data for that pair" rather than as a data bug.
/ This library's convention is upper-case six-letter pairs throughout, and
/ .qccy.normalize_ccy_pair names the same trap for user input.
/ .
/ The TRIM is defensive rather than corrective, and it is worth being honest
/ about which: measured on this build, `$"EURUSD " already yields a 6-
/ character symbol that matches `EURUSD and joins correctly, so q handles
/ trailing whitespace itself. The trim stays because it costs nothing and a
/ source may pad with characters `$ does not strip, but it is not fixing a
/ q behaviour and should not be cited as doing so.
/ @eg .qcoer.to_symbol["EURUSD "]  ->  `EURUSD
/ @eg .qcoer.to_symbol[" eurusd"]  ->  `EURUSD
to_symbol:{[s]
    t:trim s;
    if[0=count t; :` ];
    `$upper t}

/ Coerce text to a symbol WITHOUT case folding, for identifiers where case
/ is meaningful - an exchange order id, say. Still trims, because trailing
/ whitespace is never meaningful and always silent.
to_symbol_cased:{[s]
    t:trim s;
    if[0=count t; :` ];
    `$t}

/ --------------------------------------------------------------- REQUIRE

/ Refuse a null where a value is mandatory.
/ .
/ The coercions above map bad input to null rather than throwing, so a single
/ bad row does not abort a whole window. But some columns cannot be null and
/ still mean anything - a trade with no rate, a row with no timestamp - and
/ for those, publishing the null is worse than failing the window: it puts a
/ hole in the data that every downstream consumer must then remember to
/ check for.
/ @param field the column name, for the message
/ @param v the coerced value
/ @throws error when v is null
require_present:{[field;v]
    if[null v;
        '"require_present: ",string[field]," coerced to null - the source text was empty or malformed, and this column cannot be null (E-05)"];
    v}

/ Coerce a whole column and report how many values failed.
/ .
/ Returns the count rather than throwing, so a caller can decide: a handful
/ of bad rows in a million might be tolerable and worth logging, while a
/ column that failed entirely means the format changed and the window should
/ not be published at all. That judgement belongs to the worker, not here.
/ @param f one of the to_* functions
/ @param col a list of text values
/ @return dict of values and the failure count
/ `"j"$` because `sum` of a boolean vector is an INT, and 2i does not match
/ 2j under ~ - which is what assertEquals uses. The failure then reads like a
/ miscount rather than a type mismatch. Second time this exact trap has bitten
/ in this session, after .qetldbl.call_count; it needs types to detect
/ statically, so it stays a reviewer-attention item rather than a checker rule.
coerce_column:{[f;col]
    vals:f each col;
    `values`failed!(vals;"j"$sum null vals)}

\d .
