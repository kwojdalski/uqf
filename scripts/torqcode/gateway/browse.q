/ browse.q - the browser's one way to read a table: through getdata, under
/ the caller's query policy (.uqf.browse, #889).
/ .
/ The frontend used to send a select to the tiers as `admin`, a trusted
/ role, so querypolicy.csv never applied to the browser - the ordinary
/ reader's path ran every read unbounded and truncated afterwards. It now
/ logs in as the `browser` role, which may run this function and
/ .checkinputs.querypolicyfor and nothing else, and this turns the browser's
/ filters into a .dataaccess.getdata request. getdata runs under the
/ caller's login, so querypolicy.q holds it to the table's policy - or, for
/ a table the catalog lets the browser read with no policy of its own, to a
/ row cap (.checkinputs.unboundedpolicies).
/ .
/ WHY A FUNCTION HERE, not getdata from the frontend: getdata's filters carry
/ q functions as operators - (=;`EURUSD) - which a Python client cannot
/ send. The frontend sends operator NAMES, as it always has, and they are
/ looked up here; an unknown one is refused.
/ .
/ Loaded on every gateway with querypolicy.q (KDBSERVCODE/gateway/).

\d .uqf

/ The browser's operators, as getdata's filters spell them. "Not equal" is
/ `not in`: TorQ allows `not` only before in, within and like.
browse_ops:`eq`ne`lt`le`gt`ge`in!((=);(not;in);(<);(<=);(>);(>=);(in))

/ Private: a browser value as the q type its column holds. A guid arrives as
/ a char vector - kola cannot encode a Python uuid, and a str would arrive as
/ a symbol, which a guid column refuses with 'type (#1042) - so a char vector,
/ or a list of them for `in`, is parsed with "G"$. No other column type is
/ sent as chars: the frontend sends symbols as str, which kola makes symbols.
/ A timespan arrives as a one-item timespan vector - the only form kola gives
/ a value of nanoseconds, where a Python timedelta stops at the microsecond
/ (#1329) - so it, or a list of them for `in`, is unwrapped to the atom or
/ the vector the column compares with. A timestamp arrives the same way, for
/ the same reason: a Python datetime stops at the microsecond (#1371).
browse_value:{[v]
    $[10h=type v; "G"$v;
      (type v) in 12 16h; first v;
      0h<>type v; v;
      not count v; v;
      all 10h=type each v; "G"$v;
      all (type each v) in 12 16h; raze v;
      v]}

/ Private: one browser condition as a getdata filter pair.
browse_pair:{[o;v]
    op:browse_ops o;
    v:browse_value[v];
    $[`ne=o; (op 0;op 1;enlist v); `in=o; (op;(),v); (op;v)]}

/ The time window a read covers: the browser's own bounds on `time` where it
/ gave them, else the latest window the caller's policy allows.
/ @param fc the filter columns
/ @param fo their operators
/ @param fv their values; a timestamp as an atom or a one-item vector, the
/   form the frontend sends one in (#1371)
/ @param span how far back an open window reaches
/ @param now the window's end when none is given
/ @return (starttime;endtime)
/ @eg .uqf.browse_window[`time`sym;`ge`eq;(2026.01.01D10:00;`EURUSD);1D;2026.01.02D00:00]
browse_window:{[fc;fo;fv;span;now]
    t:where fc=`time;
    lo:t where fo[t] in `ge`gt`eq;
    hi:t where fo[t] in `le`lt`eq;
    e:$[count hi; min raze `timestamp$fv hi; now];
    s:$[count lo; max raze `timestamp$fv lo; e-span];
    (s;e)}

/ Private: an integral column with an infinity in it, as floats. kola turns
/ 0W and -0W into null on the way to Python, where they would reach the
/ browser as a missing value; 0w and -0w survive it, as inf (#1330). Only a
/ column that holds one is converted, and a long past 2^53 loses nothing the
/ browser's own numbers would have kept.
browse_infinity:{[x]
    if[not (k:abs type x) in 5 6 7h; :x];
    w:("hij"!(0Wh;0Wi;0W)) .Q.t k;
    if[not any w=abs x; :x];
    f:"f"$x;
    f[where x=w]:0w;
    f[where x=neg w]:-0w;
    f}

/ Private: browse_infinity over every column of a table.
browse_infinite:{[r] $[98h=type r; flip browse_infinity each flip r; r]}

/ Read a browsable table as the caller, under its query policy.
/ @param t the table
/ @param fc filter columns, symbols
/ @param fo filter operators, symbols - eq ne lt le gt ge in
/ @param fv filter values, one per column; a guid as a char vector
/ @param lim the most rows to return - and never more than the caller's
/   policy allows: the policy is the one authority on size (#928), so a read
/   asking for more is truncated to it rather than refused
/ @param procs the tiers to read, e.g. `rdb`hdb
/ @return dict: rows, at most cap, an integral column holding 0W or -0W as
/   floats so the infinity survives kola (browse_infinite); cap, the row cap the read ran under -
/   lim, or the policy's maxrows where that is lower - so the caller can tell
/   a result cut by the policy from one that is simply complete (#1255)
/ @throws error naming an unknown operator, or the policy a request breaks
browse:{[t;fc;fo;fv;lim;procs]
    if[count bad:fo except key browse_ops;
        '"uqf.browse: unknown operator ",", " sv string bad];
    pol:.checkinputs.querypolicyfor t;
    span:$[99h=type pol; pol`maxrange; .checkinputs.policyceiling`maxrange];
    lim:$[99h=type pol; lim&pol`maxrows; lim];
    w:browse_window[fc;fo;fv;span;.z.p];
    pairs:browse_pair'[fo;fv];
    req:`tablename`starttime`endtime`sublist`procs!(t;w 0;w 1;lim;(),procs);
    if[count fc; req[`filters]:{[fc;pairs;c] pairs where fc=c}[fc;pairs] each (distinct fc)!distinct fc];
    `rows`cap!(browse_infinite[.dataaccess.getdata req];lim)}

\d .
