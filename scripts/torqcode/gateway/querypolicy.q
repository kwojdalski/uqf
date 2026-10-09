/ querypolicy.q - per-table query policies on the gateway's data-access API.
/ .
/ A request to .dataaccess.getdata on the gateway goes
/ .
/   client -> getdata -> .checkinputs.checkinputs (TorQ's checks, then the
/   table's policy) -> routing -> rdb/hdb getdata -> autojoin (merge, then
/   the response-size check) -> client
/ .
/ Nothing here is a new process or namespace. TorQ's own two steps are
/ wrapped where getdata already calls them: .checkinputs.checkinputs, which
/ getdata runs before it routes, and .dataaccess.autojoin, whose function
/ the gateway applies to the backends' results before it replies. Both are
/ called by name, so redefining them here is what getdata picks up.
/ .
/ WHERE IT LOADS. uqs points KDBSERVCODE at scripts/torqcode, and torq.q
/ loads KDBSERVCODE/<proctype>/ straight after KDBCODE/<proctype>/ - so this
/ file runs on every gateway, after TorQ's gateway/dataaccess.q has defined
/ autojoin and after common/checkinputs.q has defined checkinputs.
/ .
/ WHO IT APPLIES TO. Every caller of getdata whose .pm roles do not include
/ one of .checkinputs.trustedroles. Trusted roles can run raw q on the
/ gateway anyway, so holding their getdata calls to a policy would protect
/ nothing. With .pm off nobody has a role, so the policy applies to everyone.
/ .
/ POLICIES are scripts/torqconfig/dataaccess/querypolicy.csv: one row per
/ table (role blank) plus a row per role exception. A separate file, not
/ extra columns on tableproperties.csv: TorQ reads that one with a fixed
/ type string (readtableproperties, "ssssstsss"), so a column it does not
/ know is not a column it keeps.

\d .checkinputs

/ The policy file, the ceilings no row or exception can exceed, and the
/ roles that are not held to a policy. scripts/torqconfig/settings/gateway.q
/ sets all three; these are what a gateway gets without that file.
querypolicypath:@[value;`querypolicypath;{hsym`$getenv[`KDBSERVCONFIG],"/dataaccess/querypolicy.csv"}]
policyceiling:@[value;`policyceiling;`maxrange`maxrows`maxbytes`timeout!(31D;5000000;128000000;0D00:05)]
trustedroles:@[value;`trustedroles;`admin`administrator]

/ Request parameters an ordinary caller may not send, with why. Each runs
/ code or a query the policy cannot see into, so allowing any one of them is
/ the bypass the policy exists to close.
prohibitedparams:`sqlquery`freeformwhere`freeformby`freeformcolumn`postprocessing`postback`join!(
    "a SQL string skips TorQ's own parameter checks";
    "a free-form where clause is q the policy cannot read - use filters";
    "a free-form by clause is q the policy cannot read - use grouping or timebar";
    "a free-form select clause is q the policy cannot read - use columns or aggregations";
    "a postprocessing lambda runs arbitrary q on the gateway";
    "a postback lambda runs arbitrary q on the gateway";
    "a join runs a second query the policy has not checked")

/ The operations a policy row can allow.
policyoperations:`raw`aggregate

/ Private: "a|b" as `a`b, and "" as an empty list rather than one null.
policylist:{[s] {x where not null x}`$"|"vs s}

/ The policies in a policy file, keyed by table and role, refusing a file
/ that would quietly allow more than it says.
/ @param path the file, as a file symbol
/ @return tablename,role-keyed table: maxrange, requiredfilters, operations, functions, maxrows, maxbytes, timeout, basis
/ @eg .checkinputs.readquerypolicy `:scripts/torqconfig/dataaccess/querypolicy.csv
readquerypolicy:{[path]
    t:("SSN***JJN*";enlist",")0:path;
    / Qualified: inside qSQL a bare name resolves at the root, not in this
    / namespace, so `policylist` alone was 'policylist at load.
    t:update requiredfilters:.checkinputs.policylist each requiredfilters,
        operations:.checkinputs.policylist each operations,
        functions:.checkinputs.policylist each functions from t;
    bad:{[t;c;f] exec tablename from t where not f each t c};
    if[count b:bad[t;`operations;{(count x)&all x in .checkinputs.policyoperations}];
        '"querypolicy: ",(", "sv string b)," - operations must be one or more of ","|"sv string policyoperations];
    if[count b:bad[t;`maxrange;{x>0}],bad[t;`maxrows;{x>0}],bad[t;`maxbytes;{x>0}],bad[t;`timeout;{x>0}];
        '"querypolicy: ",(", "sv string distinct b)," - maxrange, maxrows, maxbytes and timeout must all be set and positive"];
    if[count d:exec tablename from select from t where 1<(count;i) fby ([]tablename;role);
        '"querypolicy: ",(", "sv string distinct d)," - one row per table and role"];
    `tablename`role xkey t}

/ The policy a request runs under: the table's own row, or - when the caller
/ holds a role with an exception for the table - the most permissive of
/ those exceptions, field by field. Either way capped by the ceiling, which
/ is what keeps an exception from becoming unlimited.
/ @param policies what readquerypolicy returned
/ @param caps a maxrange, maxrows, maxbytes and timeout no policy may exceed
/ @param table the table asked for
/ @param roles the caller's roles
/ @return dict: maxrange, requiredfilters, operations, functions, maxrows, maxbytes, timeout, basis
/ @eg .checkinputs.resolvepolicy[.checkinputs.readquerypolicy `:scripts/torqconfig/dataaccess/querypolicy.csv;.checkinputs.policyceiling;`mkt_orderbook;`quant]
resolvepolicy:{[policies;caps;table;roles]
    rows:0!select from policies where tablename=table;
    exceptions:select from rows where role in roles;
    p:$[count exceptions;
        `maxrange`requiredfilters`operations`functions`maxrows`maxbytes`timeout`basis!(
            max exceptions`maxrange;
            (inter/)exceptions`requiredfilters;
            distinct raze exceptions`operations;
            $[any 0=count each exceptions`functions;`symbol$();distinct raze exceptions`functions];
            max exceptions`maxrows;max exceptions`maxbytes;max exceptions`timeout;
            first exceptions`basis);
        count d:select from rows where null role;
        `tablename`role _ first d;
        '"querypolicy: ",string[table]," has no query policy",$[count rows;" for your role";""],
            " - it cannot be read through getdata; ask for a row in querypolicy.csv"];
    k:`maxrange`maxrows`maxbytes`timeout;
    @[p;k;&;caps k]}

/ The time a request covers. A date end means the whole of that day, which
/ is how TorQ's own routing reads it.
/ @eg .checkinputs.requestspan[2026.01.01D00:00;2026.01.01D01:00]  ->  0D01:00:00.000000000
/ @eg .checkinputs.requestspan[2026.01.01;2026.01.01]  ->  1D00:00:00.000000000
requestspan:{[s;e] ((`timestamp$e)+$[-14h=type e;1D;0D])-`timestamp$s}

/ Check one request against its table's policy, after TorQ's own checks have
/ passed. Refuses with the reason and what to change; returns the request
/ with its timeout capped and the limits the merge is checked against.
/ @param policy what resolvepolicy returned
/ @param instcol the table's instrument column, which `instruments` filters on
/ @param dict the request, as TorQ's checkinputs returned it
/ @return the request, with timeout and querypolicy set
checkrequest:{[policy;instcol;dict]
    t:string dict`tablename;
    span:requestspan[dict`starttime;dict`endtime];
    if[span>policy`maxrange;
        '"querypolicy: ",t," allows at most ",string[policy`maxrange]," per request and this one covers ",
            string[span]," - narrow starttime/endtime or split the request"];
    filtered:$[`filters in key dict;key dict`filters;`symbol$()],
        $[(`instruments in key dict)&0<count dict`instruments;instcol;`symbol$()];
    if[count missing:policy[`requiredfilters] except filtered;
        '"querypolicy: ",t," needs a filter on ",(", "sv string missing),
            $[instcol in missing;" - pass instruments (e.g. `EURUSD) or a filters condition on it";" - add a filters condition on it"]];
    op:$[any`aggregations`timebar`grouping in key dict;`aggregate;`raw];
    if[not op in policy`operations;
        '"querypolicy: ",t," allows ",("|"sv string policy`operations)," and this request is ",string[op],
            $[op=`raw;" - send aggregations, e.g. `max`count!(`price;`sym)";" - drop aggregations, timebar and grouping"]];
    if[(op=`aggregate)&(count policy`functions)&`aggregations in key dict;
        if[count bad:key[dict`aggregations] except policy`functions;
            '"querypolicy: ",t," allows the aggregations ",(", "sv string policy`functions)," - not ",", "sv string bad]];
    dict[`timeout]:$[`timeout in key dict;policy[`timeout]&dict`timeout;policy`timeout];
    dict[`querypolicy]:(`tablename,`maxrows`maxbytes)#(enlist[`tablename]!enlist dict`tablename),policy;
    dict}

/ Refuse a parameter no policy can see into. Before TorQ's checks, because
/ a sqlquery request returns from them early, unchecked.
checkprohibited:{[dict]
    if[count p:key[dict] inter key prohibitedparams;
        '"querypolicy: ",(", "sv string p)," not allowed for your role - ",prohibitedparams first p];
    dict}

/ The caller's .pm roles: none with .pm off, everything when typed at the
/ gateway's own console.
callerroles:{[] $[0=.z.w;trustedroles;@[{exec role from .pm.userrole where user=x};.z.u;`symbol$()]]}

/ The instrument column for a table, from TorQ's table properties.
policyinstcol:{[dict]
    $[`instrumentcolumn in key dict;dict`instrumentcolumn;
        @[{first exec instrumentcolumn from .checkinputs.tablepropertiesconfig where tablename=x};dict`tablename;`sym]]}

/ The policies this gateway enforces. Read once, as it loads; a missing or
/ malformed file leaves none, which refuses every ordinary request rather
/ than allowing them all.
querypolicies:@[readquerypolicy;querypolicypath;{[e]
    @[{.lg.e[`querypolicy;x]};"no query policies loaded - every ordinary getdata request will be refused: ",e;()];
    `tablename`role xkey flip`tablename`role`maxrange`requiredfilters`operations`functions`maxrows`maxbytes`timeout`basis!
        (`symbol$();`symbol$();`timespan$();();();();`long$();`long$();`timespan$();())}]

/ What the caller's getdata requests on a table are held to - so a refusal
/ can be understood without reading the file.
/ @eg .checkinputs.querypolicyfor `mkt_orderbook
querypolicyfor:{[table]
    r:callerroles[];
    $[any r in trustedroles;`trusted;resolvepolicy[querypolicies;policyceiling;table;r]]}

/ Only on a gateway that has TorQ's checkinputs: this file is also loaded by
/ its tests, in a process with none of TorQ.
if[(not ()~key`.checkinputs.checkinputs)&not`querypolicywrapped in key`.checkinputs;
    checkinputs:{[f;dict]
        if[99h<>type dict;:f dict];
        r:callerroles[];
        if[any r in trustedroles;:f dict];
        dict:f checkprohibited dict;
        checkrequest[resolvepolicy[querypolicies;policyceiling;dict`tablename;r];policyinstcol[dict];dict]}[checkinputs];
    querypolicywrapped:1b]

\d .dataaccess

/ The merged result against the policy's row and byte limits. After the
/ merge, so the limits are on what the client would receive; a refusal
/ reaches the client as the gateway's "failed to apply join function"
/ error, carrying this message.
/ @param limits tablename, maxrows and maxbytes
/ @param res the merged result
/ @return res, unchanged, when it fits
checkresponsesize:{[limits;res]
    t:string limits`tablename;
    if[(n:count res)>limits`maxrows;
        '"querypolicy: ",t," returns at most ",string[limits`maxrows]," rows and this result has ",string[n],
            " - narrow the time range, add filters, or aggregate"];
    if[(b:-22!res)>limits`maxbytes;
        '"querypolicy: ",t," returns at most ",string[limits`maxbytes]," bytes and this result is ",string[b],
            " - ask for fewer columns or a shorter time range"];
    res}

if[(not ()~key`.dataaccess.autojoin)&not`querypolicywrapped in key`.dataaccess;
    autojoin:{[f;options]
        j:f options;
        $[`querypolicy in key options;{[l;j;r] checkresponsesize[l] j r}[options`querypolicy;j];j]}[autojoin];
    querypolicywrapped:1b]

\d .
