/ limits.q - risk limits, breach detection and alert throttling (.qlimit).
/ Depends on nothing else here: a limit is a number compared with another
/ number, and keeping it that way is what lets the same file police an
/ exposure, a P&L drawdown or a fill count.
/ .
/ WHAT A LIMIT IS HERE. A row in a table, not a registered function. The
/ registry pattern the rest of this tree uses (.qetl.source, .qetl.io, .qetl.transform, .qalloc)
/ is right when the thing being declared is BEHAVIOUR - a way of fetching,
/ writing, transforming, matching. A limit is data: a scope, a metric and
/ a cap. Desks change them daily, and they arrive from a spreadsheet or a
/ risk system rather than from a q file, so they have to be a table that
/ can be loaded, amended and diffed - not code that has to be edited.
/ .
/ THE SHAPE, and it is deliberately long-form:
/ .
/   measured   the scope columns, plus `metric (a symbol) and `observed
/   limits     the same scope columns, plus `metric and `cap
/ .
/ Melting a book into (metric; observed) rather than checking named columns
/ is what makes one evaluate call cover every metric a desk has. The
/ alternative - a limits table with a column per metric - needs a schema
/ change and a code change every time risk invents a new one, which is
/ the kind of coupling that ends with limits maintained in a spreadsheet
/ nobody loads.
/ .
/ SCOPE IS WHATEVER BOTH TABLES AGREE ON. A limits table keyed on
/ (sym, book) polices each pair on each book; one keyed on (book) alone
/ polices the desk. Nothing here decides which - the caller rolls its book
/ up to the level its limits are written at, and that is a .qdesk.rollup
/ call. So one limit mechanism serves every level without a notion of
/ hierarchy, and a desk-wide limit is not a special case.
/ .
/ ABSOLUTE VALUES. A cap is on |value|: a desk short 20mm past a 15mm
/ limit is as far over as one long 20mm, and a signed comparison would
/ police only one direction. A one-sided limit is expressed as two rows
/ with different metrics, not by making the common case a trap.

\d .qlimit

/ The columns a limits table must carry, on top of its scope columns.
required_limit_cols:`metric`cap

/ The columns a measured table must carry, on top of its scope columns.
required_measured_cols:`metric`observed

/ Columns that are NEVER scope - so the scope is whatever is left, and a
/ desk adding a column to its limits file widens the scope without telling
/ this file about it.
/ .
/ Every column a measurement or a breach acquires on its way through this
/ file is listed, not just the ones a limits table carries. Leaving
/ `observed` and `utilisation` off this list is a bug with a specific and
/ nasty shape: they would become part of a breach's identity, so a
/ breached position that MOVED would look like a new breach and alert
/ again on every tick - which is exactly what throttling exists to stop.
/ .
/ `severity is optional and carried through to the breach untouched, so a
/ desk can route warnings and hard breaches differently without this file
/ knowing what its severities mean.
non_scope_cols:`metric`cap`severity`observed`utilisation

/ Refuse a limits table that is not one, naming what is wrong.
/ .
/ Checked when limits are LOADED rather than at the first breach, because
/ a malformed limits table is most dangerous in the case where nothing
/ breaches: it would police nothing and look exactly like a quiet day.
/ @param limits the candidate table
/ @return 1b when acceptable
/ @throws error naming the missing column, the non-positive cap, or the duplicated scope
/ @eg .qlimit.require_limits[([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist 1e6)] -> 1b
require_limits:{[limits]
    if[not 98h=type limits; '"require_limits: limits must be a table"];
    .qschema.require_cols[`require_limits;`limits;limits;required_limit_cols];
    if[not 11h=abs type limits`metric;
        '"require_limits: metric must be a symbol column naming what is capped"];
    if[any null limits`cap; '"require_limits: a null cap polices nothing - remove the row instead"];
    if[any 0f>=limits`cap;
        '"require_limits: every cap must be positive - a cap is on the absolute value, so a negative one can never be met"];
    scope:scope_cols limits;
    ident:?[limits;();0b;(scope,`metric)!scope,`metric];
    if[(count distinct ident)<count ident;
        '"require_limits: two limits cover the same scope and metric - which one binds would depend on row order"];
    1b}

/ The scope columns of a limits or breach table: everything that is not a
/ metric, a cap or a severity.
/ @param limits a limits table
/ @return the scope columns, in the table's own order
/ @eg .qlimit.scope_cols[([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist 1e6)] -> enlist `sym
scope_cols:{[limits] c:cols limits; c where not c in non_scope_cols}

/ Melt a book into the long (metric; observed) form evaluate consumes.
/ .
/ One row per (scope, metric) pair. The metrics are named rather than
/ inferred, because a book carries columns that are not risk metrics -
/ a fill count is a fact, not an exposure - and policing a column by
/ accident is worse than not policing it at all.
/ .
/ THE SCOPE IS THE BOOK'S OWN KEY, and the book must therefore be keyed.
/ The obvious alternative - scope is every column that is not a metric -
/ is wrong in a way that hides: a book's other measures (a fill count, the
/ metrics not being melted this time) would join the scope, and from there
/ the breach identity, so a breached position that moved would read as a
/ new breach and alert again on every tick. Taking the key instead means
/ the scope is what the book is ABOUT, which is the thing a limit is
/ written against, and it cannot drift as columns are added.
/ @param book a KEYED table - .qdesk's books are keyed on their dimensions
/ @param metrics the columns to melt, e.g. `base_qty`quote_qty
/ @return a table of the book's key columns plus metric and observed
/ @see the column is `observed` rather than `value` because `value` is a q
/   builtin: in a `where` clause the bare name resolves as the function and
/   the column is unreachable, which qlinter's QF013 exists to catch
/ @throws error when the book is not keyed, or names a metric it does not carry
/ @eg exec metric from .qlimit.measure[([sym:enlist `EURUSD] base_qty:enlist 1e6);enlist `base_qty] -> enlist `base_qty
measure:{[book;metrics]
    if[not 99h=type book;
        '"measure: the book must be keyed on its scope columns - an unkeyed table does not say which of its columns a limit would be written against"];
    scope:keys book;
    t:0!book;
    m:(),metrics;
    .qschema.require_cols[`measure;`book;t;m];
    overlap:m where m in scope;
    if[count overlap;
        '"measure: ",(", " sv string overlap)," is part of the book's key, so it identifies a position rather than measuring one"];
    raze {[t;scope;metric]
        ?[t;();0b;scope!scope] ,' ([] metric:(count t)#metric; observed:"f"$t metric)}[t;scope] each m}

/ Compare measured values with their caps and return the breaches.
/ .
/ A LEFT JOIN from measured onto limits, so a measured scope with no limit
/ is unpoliced rather than an error - a desk does not write a limit for
/ every pair it might ever touch. The converse is reported by
/ unmatched_limits, because a limit that matches nothing is usually a typo
/ in a scope name and is otherwise invisible.
/ @param measured the long-form measurements (see measure)
/ @param limits the limits table
/ @return a table of scope, metric, observed, cap, severity, utilisation - one row per breach, worst first
/ @throws error when either table is malformed
/ @eg count .qlimit.evaluate[([] sym:enlist `EURUSD; metric:enlist `base_qty; observed:enlist 2e6);([] sym:enlist `EURUSD; metric:enlist `base_qty; cap:enlist 1e6)] -> 1
evaluate:{[measured;limits]
    if[not 98h=type measured; '"evaluate: measured must be a table"];
    .qschema.require_cols[`evaluate;`measured;measured;required_measured_cols];
    require_limits limits;
    scope:scope_cols limits;
    unknown:scope where not scope in cols measured;
    if[count unknown;
        '"evaluate: limits are scoped on ",(", " sv string unknown),", which the measurements do not carry - roll the book up to the level the limits are written at"];
    keyed:(scope,`metric) xkey $[`severity in cols limits; limits; update severity:`hard from limits];
    joined:measured lj keyed;
    breached:select from joined where not null cap, cap<abs observed;
    r:update utilisation:(abs observed)%cap from breached;
    `utilisation xdesc r}

/ Limits that matched nothing in this set of measurements.
/ .
/ Not a breach and not an error, but worth surfacing: a limit whose scope
/ is misspelled polices nothing and is indistinguishable from a limit that
/ is simply never approached. This is the only way to tell the two apart.
/ @param measured the long-form measurements
/ @param limits the limits table
/ @return the limit rows with no matching measurement
/ @eg count .qlimit.unmatched_limits[([] sym:enlist `EURUSD; metric:enlist `base_qty; observed:enlist 1f);([] sym:enlist `GBPUSD; metric:enlist `base_qty; cap:enlist 1e6)] -> 1
unmatched_limits:{[measured;limits]
    scope:scope_cols limits;
    ident:?[measured;();0b;(scope,`metric)!scope,`metric];
    limits where not (?[limits;();0b;(scope,`metric)!scope,`metric]) in ident}

/ ------------------------------------------------------------ THROTTLING

/ An empty throttle state: breach identity -> when it was last alerted.
/ .
/ A plain dictionary keyed on a SYMBOL, rather than a keyed table on the
/ scope columns, because the scope columns are not known here - they vary
/ with the limits table - and a state whose schema depends on its first
/ input cannot be constructed empty. Rendering the identity to one symbol
/ makes the state one shape whatever it polices.
/ .
/ q interns symbols and never frees them, which makes rendering an
/ identity to a symbol a real question rather than a free choice. It is
/ fine here and the bound is worth stating: the distinct identities are
/ (scopes x metrics) in the LIMITS table, which a desk writes by hand, so
/ the set is small, fixed at load, and does not grow with traffic.
/ @return an empty throttle state, to be threaded through throttle
/ @eg count .qlimit.no_alerts[] -> 0
no_alerts:{[] (`symbol$())!`timestamp$()}

/ Split a breach set into the ones worth alerting on and the ones already
/ alerted recently, and carry the state forward.
/ .
/ WHY THIS EXISTS. A breach is a STATE, not an event: a position over its
/ limit is over it on every tick until someone trades out of it, so a job
/ that alerts per evaluation sends one alert per tick and the desk stops
/ reading them. Throttling turns the state back into an event.
/ .
/ The period is per (scope, metric), not global: two different pairs
/ breaching at once are two alerts, and neither silences the other.
/ .
/ Returns the state as well as the alerts because this is a pure function
/ and the caller owns the state - the convention every stateful thing here
/ follows, and what lets a throttle be tested without a clock.
/ @param state the previous state, from no_alerts or a previous call
/ @param breaches the current breaches, from evaluate
/ @param now the time to record against anything alerted
/ @param period how long a given breach stays quiet after alerting
/ @return a dict of `state (carry it to the next call) and `alerts (the breaches to send)
/ @eg count .qlimit.throttle[.qlimit.no_alerts[];([] sym:enlist `EURUSD; metric:enlist `base_qty; observed:enlist 2e6; cap:enlist 1e6);2026.01.01D09:00:00;0D00:05]`alerts -> 1
throttle:{[state;breaches;now;period]
    if[0=count breaches; :`state`alerts!(state;breaches)];
    scope:scope_cols breaches;
    ids:identity ?[breaches;();0b;(scope,`metric)!scope,`metric];
    quiet:{[state;now;period;id] $[id in key state; now<period+state id; 0b]}[state;now;period] each ids;
    fresh:breaches where not quiet;
    `state`alerts!(state,(ids where not quiet)!(count fresh)#now; fresh)}

/ Private: render each row of a scope table to one symbol.
/ .
/ .Q.s1 per value rather than `string`, so that a symbol and a string
/ spelling of it do not collide, and so a null scope value renders as
/ something rather than as an empty string indistinguishable from another
/ column's.
identity:{[scopes] `$ {"|" sv .Q.s1 each value x} each scopes}

\d .
