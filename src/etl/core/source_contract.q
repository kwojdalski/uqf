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
/ Four decisions recorded here rather than left implicit. They follow from
/ answers already given, and are stated so nobody has to re-derive them:
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

\d .qsrc

/ ---------------------------------------------------------------- SCHEMA

/ What every registered source must declare. Named as data so a test can
/ assert the set rather than trusting a code review.
required_declarations:`source`table`target`time_field`fields`types`query`fixture

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
fetch_window:{[source;h;range_from;range_to]
    decl:declaration source;
    $[null h;
        (`fixture;window_fixture[decl;range_from;range_to]);
        (`live;(decl`query)[h;range_from;range_to])]}

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
