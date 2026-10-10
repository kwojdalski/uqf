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
browse_value:{[v]
    $[10h=type v; "G"$v;
      0h<>type v; v;
      not count v; v;
      all 10h=type each v; "G"$v;
      v]}

/ Private: one browser condition as a getdata filter pair.
browse_pair:{[o;v]
    op:browse_ops o;
    v:browse_value v;
    $[`ne=o; (op 0;op 1;enlist v); `in=o; (op;(),v); (op;v)]}

/ The time window a read covers: the browser's own bounds on `time` where it
/ gave them, else the latest window the caller's policy allows.
/ @param fc the filter columns
/ @param fo their operators
/ @param fv their values
/ @param span how far back an open window reaches
/ @param now the window's end when none is given
/ @return (starttime;endtime)
/ @eg .uqf.browse_window[`time`sym;`ge`eq;(2026.01.01D10:00;`EURUSD);1D;2026.01.02D00:00]
browse_window:{[fc;fo;fv;span;now]
    t:where fc=`time;
    lo:t where fo[t] in `ge`gt`eq;
    hi:t where fo[t] in `le`lt`eq;
    e:$[count hi; min `timestamp$fv hi; now];
    s:$[count lo; max `timestamp$fv lo; e-span];
    (s;e)}

/ Read a browsable table as the caller, under its query policy.
/ @param t the table
/ @param fc filter columns, symbols
/ @param fo filter operators, symbols - eq ne lt le gt ge in
/ @param fv filter values, one per column; a guid as a char vector
/ @param lim the most rows to return - and never more than the caller's
/   policy allows: the policy is the one authority on size (#928), so a read
/   asking for more is truncated to it rather than refused
/ @param procs the tiers to read, e.g. `rdb`hdb
/ @return the rows, at most lim
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
    .dataaccess.getdata req}

\d .
