/ schema.q - the one refusal for a table that lacks the columns a function
/ reads (.qf.util).
/ .
/ Why a foundation module for one function: the check was hand-written at
/ fourteen sites and worded five ways - "is missing required column(s)",
/ "is missing column(s)", "no such column(s)", "the book has no column(s)",
/ "missing columns" - so no grep found them all and no caller could match on
/ one shape. Only two helpers existed and both were tied to one table:
/ .qf.util.require_depth_quotes and .qf.mkt.require_tape. #418.
/ .
/ It lives in foundation because every layer above loads foundation first
/ (src/init.q), the ETL tree included - src/etl/init.q assumes src/init.q has
/ already run. A check this ordinary should not be a reason for one module to
/ depend on another.
/ .
/ WHY THE FUNCTION NAME IS A PARAMETER. The message names the function the
/ caller actually called, not this one, because that is the name a reader has
/ in front of them when the error lands. q gives a lambda no way to ask its
/ own name, so the caller passes it.

\d .

/ Refuse a table that lacks any of the columns a function reads.
/ .
/ Checks membership only - a column of the wrong TYPE passes here, because
/ the sites this replaced checked membership only and widening the contract
/ silently is how a refusal starts firing on data that used to work.
/ @param fn_name symbol naming the CALLING function, for the message
/ @param table_name symbol naming the table's role in that call, e.g. `trades
/ @param tbl the table to check
/ @param req symbol vector (or one symbol) of the columns the caller reads
/ @return generic null when every required column is present
/ @throws error naming the caller, the table and every missing column
/ @eg .qf.util.require_cols[`markout;`trades;([] sym:enlist `EURUSD);enlist `sym]  -> ::
/ @eg .qf.util.require_cols[`markout;`trades;([] sym:enlist `EURUSD);`sym`time]  -> throws
.qf.util.require_cols:{[fn_name;table_name;tbl;req]
    r:(),req;
    missing:r where not r in cols tbl;
    if[count missing;
        '(string fn_name),": ",(string table_name)," is missing required column(s) ",", " sv string missing];
    }
/ Resolve an options dictionary against its defaults, refusing a key the
/ function does not know. The one refusal for every `opts` parameter in the
/ library (#2113): a misspelt option used to be dropped in pricing and
/ gave a plausible number computed with the default - `bump_pip for
/ `bump_pips made a PV01 five times too small, with no error.
/ .
/ opts holds only what has a default. A value the caller must state (a pip
/ factor, an as_of, horizons) is a positional argument of the function.
/ @param fn_name symbol naming the CALLING function, for the message
/ @param opts (::) for the defaults, or a dictionary of any of defaults' keys
/ @param defaults dict of every option the function takes -> its default
/ @return defaults with opts laid over them
/ @throws error naming every unknown key, or a non-dictionary opts
/ @eg .qf.util.resolve_opts[`f;enlist[`b]!enlist 2;`a`b!1 1]  -> `a`b!1 2
/ @eg .qf.util.resolve_opts[`f;enlist[`c]!enlist 2;`a`b!1 1]  -> throws
.qf.util.resolve_opts:{[fn_name;opts;defaults]
    if[(::)~opts; :defaults];
    known:", " sv string key defaults;
    if[not 99h=type opts;
        '(string fn_name),": opts must be (::) for the defaults, or a dictionary of ",known];
    unknown:(key opts) except key defaults;
    if[count unknown;
        '(string fn_name),": unknown option(s) ",(", " sv string unknown)," - known: ",known];
    defaults,opts}
/ Refuse a depth-quotes table that lacks a column a depth function reads:
/ time, sym and the four level vectors. The one shape every function that
/ prices from a depth book takes - synthetic cross pricing (.qf.mkt), cross
/ markouts (.qf.exec), microstructure and the data-quality checks - so it lives
/ here rather than in any of them (#626).
/ @param fn_name symbol naming the CALLING function, for the message
/ @param quotes the depth-quotes table
/ @return generic null when every column is present
/ @throws error naming the caller and every missing column
/ @eg .qf.util.require_depth_quotes[`f;([] time:`timestamp$(); sym:`symbol$())]  -> throws
.qf.util.depth_quote_cols:`time`sym`bid_prices`bid_sizes`ask_prices`ask_sizes
.qf.util.require_depth_quotes:{[fn_name;quotes] .qf.util.require_cols[fn_name;`quotes;quotes;.qf.util.depth_quote_cols]}

\d .
