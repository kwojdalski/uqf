/ schema.q - the one refusal for a table that lacks the columns a function
/ reads (.qschema).
/ .
/ Why a foundation module for one function: the check was hand-written at
/ fourteen sites and worded five ways - "is missing required column(s)",
/ "is missing column(s)", "no such column(s)", "the book has no column(s)",
/ "missing columns" - so no grep found them all and no caller could match on
/ one shape. Only two helpers existed and both were tied to one table:
/ .qfwd.require_quotes_cols and .qmicro.require_tape. #418.
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

\d .qschema

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
/ @eg .qschema.require_cols[`markout;`trades;([] sym:enlist `EURUSD);enlist `sym]  -> ::
/ @eg .qschema.require_cols[`markout;`trades;([] sym:enlist `EURUSD);`sym`time]  -> throws
require_cols:{[fn_name;table_name;tbl;req]
    r:(),req;
    missing:r where not r in cols tbl;
    if[count missing;
        '(string fn_name),": ",(string table_name)," is missing required column(s) ",", " sv string missing];
    }
\d .
