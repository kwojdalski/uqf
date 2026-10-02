// The qDoc registry: every documented q function, its arguments and its file,
// as tables (.man.funcs, .man.args, .man.files) for programmatic use.
//
// REGISTERED AT LOAD, from the qDoc comments in src/ - not generated into this
// file. It used to be: scripts/generate/generate_man_registry.py wrote ~3,000
// lines of register* calls below the helpers, every q change had to rerun it,
// and a hook and a CI step existed only to catch the times nobody did. The
// comments are the source, so this file reads them when it loads instead.
.man.funcs:([] fullname:(); ns:(); description:(); name:(); other:());
.man.registerFunc:{`.man.funcs insert `fullname`ns`description`name`other!(),/:x};
.man.args:([] fullname:(); tag:(); param:(); description:());
.man.registerArg:{`.man.args insert `fullname`tag`param`description!(),/:x};
.man.files:([] title:(); author:(); namespaces:(); header:());
.man.registerFile:{`.man.files insert `title`author`namespaces`header!(),/:x};
.man.filetags:([] title:(); tag:(); val:());
.man.registerFileTag:{`.man.filetags insert `title`tag`val!(),/:x};

/ @eg .man.getDocs[]
/ @return table of format ([] fullname; tag; param; description)
.man.getDocs:{[]
    ft:select fullname,description from .man.funcs;
    at:select fullname,tag,param,description from .man.args;
    headerTbl:0!select description:"\n" sv header by title,fullname:{first "|" vs x} each namespaces from .man.files where 1<count each trim namespaces,1<count each trim header;
    filenameToNSDict:exec first fullname by title from headerTbl;
    tagTbl:select fullname:filenameToNSDict title, tag,description:val from .man.filetags;
    t:at uj ft uj tagTbl uj (``title _ headerTbl);
    t:asc select from t where (0<count each tag) or (0<count each param) or (0<count each description);
    t };



/ @eg .man.getTS[`GOOG]
.man.getTS:{[symbol]  // random walk with set seed to mimic incoming data
    seed:prd `int$string symbol;
    {  walk:{ [seed;n]
    	 r:{{ abs ((1664525*x)+1013904223) mod 4294967296}\[y-1;x]};
    	 prds (100+((r[seed;n]) mod 11)-5)%100};
    	 c:{x mod `long$00:20:00.0t}x;   st:x-c;   cn:`long$c%1000;
    	 ([] time:.z.d+st+1000*til cn; gold:walk[y;cn])  }[.z.t;seed]
    };

/ @eg .man.getOHLC[`MSFT]
.man.getOHLC:{[symbol]
    seed:prd `int$string symbol;
    {  r:{{ abs ((1664525*x)+1013904223) mod 4294967296}\[y-1;x]};
	walk:{ [r;seed;n] prds (100+((r[seed;n]) mod 11)-5)%100}[r;;];
	c:{x mod `long$00:05:00.0t}x;   st:x-c;   cn:100+`long$c%1000;
	t:([] time:`second$.z.d+st+1000*til cn; open:walk[y+4;cn]; close:walk[y+3;cn]);
	-100 sublist update low:?[open > close;close;open]-(r[11;cn] mod 11)*0.02,high:?[open < close;close;open]+(r[44;cn] mod 11)*0.02,volume:(r[44;cn] mod 110) from t}[.z.t;seed]
    };

/ @eg .man.getSymbols[]
.man.getSymbols:{[]
    sym:`MSFT`AAPL`NVDA`AMZN`GOOGL`META`BRK.B`AVGO`TSLA`TSM`WMT`JPM`V`LLY`MA`NFLX`XOM`COST`ORCL`JNJ`PG`HD`UNH`SAP`ABBV`BAC`KO`NVO;
    des:("Microsoft Corporation";"Apple Inc.";"NVIDIA Corporation";"Amazon.com, Inc.";"Alphabet Inc.";"Meta Platforms, Inc.";"Berkshire Hathaway Inc.";"Broadcom Inc.";"Tesla, Inc.";"Taiwan Semiconductor Manufacturing Company Limited";"Walmart Inc.";"JPMorgan Chase & Co.";"Visa Inc.";"Eli Lilly and Company";"Mastercard Incorporated";"Netflix, Inc.";"Exxon Mobil Corporation";"Costco Wholesale Corporation";"Oracle Corporation";"Johnson & Johnson";"The Procter & Gamble Company";"The Home Depot, Inc.";"UnitedHealth Group Incorporated";"SAP SE";"AbbVie Inc.";"Bank of America Corporation";"The Coca-Cola Company";"Novo Nordisk A/S");
    t:update query:(`$".man.getTS[`XXX]") from ([] symbol:sym; title:`$des);
    t,:select query:(`$".man.getOHLC[`XXX]"),symbol:(`$string[symbol],\:"_OHLC"),title from t;
    t};

\d .man
/ ---------------------------------------------------------------------
/ READING THE qDoc COMMENTS
/ .
/ The rules are the ones the generator applied, kept exactly - its output and
/ this scan were compared row for row (1008 functions, 2011 arguments, 78
/ files, same order) before the generator was removed:
/   - a definition is `name:` at column 0 inside a `\d .ns` file, or a
/     fully qualified `.ns.name:`; names starting `_` are private
/   - a run of definitions with no blank line between them shares the one
/     comment block above the run
/   - `/ .` is a blank line inside a comment (a line of only `/` would open a
/     block comment); a line of only `\` ends the block
/   - @param, @return, @throws and @eg are tags; a comment line after a tag
/     continues the last @eg
/   - an entry with no description and no tag is skipped: registering a name
/     that says nothing would hide the gap from the very query meant to find it
/ q has no regular expressions, so each pattern is a small parser.

/ ---- character classes and small parsers
ws:" \t\r\n",`char$12 11
alpha:{(x within "az") or x within "AZ"}
wordc:{(alpha x) or (x within "09") or x="_"}
strip:{[s] if[0=count s; :s]; k:where not s in ws; $[0=count k; ""; (first k)_(1+last k)#s]}
lstrip:{[s] k:where not s in ws; $[0=count k; ""; (first k)_s]}
/ length of an identifier [A-Za-z][A-Za-z0-9_]* starting at i, 0 when none
ident:{[s;i] n:count s; if[not i<n; :0]; if[not alpha s i; :0]; j:i+1; while[(j<n) and wordc s j; j+:1]; j-i}
/ index past whitespace from i
skipws:{[s;i] n:count s; while[(i<n) and s[i] in ws; i+:1]; i}
/ ^([A-Za-z]\w*)\s*:   -> the name, or ""
funcname:{[s] k:ident[s;0]; if[0=k; :""]; j:skipws[s;k]; $[(j<count s) and ":"=s j; k#s; ""]}
/ ^(\.id(\.id)*)\.(id)\s*:   -> (ns;name), or ()
qualified:{[s]
    if[not (0<count s) and "."=first s; :()];
    ids:(); i:0; n:count s;
    while[(i<n) and "."=s i; k:ident[s;i+1]; if[0=k; :()]; ids,:enlist (i+1;k); i+:1+k];
    if[2>count ids; :()];
    j:skipws[s;i]; if[not (j<n) and ":"=s j; :()];
    last_:last ids; nsend:last_[0]-1;
    (nsend#s; last_[1]#last_[0]_s)}
/ ^\\d\s+(\.id(\.id)*)\s*$   -> the namespace, or ""
nsline:{[s]
    if[not "\\d"~2#s; :""]; i:2; n:count s;
    if[not (i<n) and s[i] in ws; :""]; i:skipws[s;i];
    st:i; if[not (i<n) and "."=s i; :""];
    while[(i<n) and "."=s i; k:ident[s;i+1]; if[0=k; :""]; i+:1+k];
    nm:(i-st)#st _s; j:skipws[s;i]; $[j=n; nm; ""]}
/ ^\s*/+  ... returns index after the slashes, or -1
slashes:{[s] i:skipws[s;0]; n:count s; if[not (i<n) and "/"=s i; :-1]; while[(i<n) and "/"=s i; i+:1]; i}
/ ^\s*/+\s*@(param|return|throws|eg)\b[ \t]*(.*)$   -> (kind;rest), or ()
tag:{[s]
    i:slashes s; if[i<0; :()]; i:skipws[s;i]; n:count s;
    if[not (i<n) and "@"=s i; :()]; i+:1;
    kinds:("param";"return";"throws";"eg");
    hit:kinds where {[s;i;k] k~(count k)#i _s}[s;i] each kinds;
    if[0=count hit; :()]; k:first hit; j:i+count k;
    if[(j<n) and wordc s j; :()];
    while[(j<n) and s[j] in " \t"; j+:1];
    (k;j _s)}
/ ^\s*/+[ \t]?(.*)$   -> the body, or "" when not a comment
comment:{[s] i:slashes s; if[i<0; :""]; if[(i<count s) and s[i] in " \t"; i+:1]; i _s}
iscomment:{[s] 0<=slashes s}

/ One file's (title; namespaces; docs), docs a list of dicts.
parsefile:{[path]
    ls:read0 hsym `$path; n:count ls;
    nss:(); out:(); cur:"";
    i:0;
    while[i<n;
        line:ls i;
        nm:nsline line;
        if[count nm; if[not any nss~\:nm; nss,:enlist nm]; cur:nm; i+:1];
        if[not count nm;
            q:qualified line;
            $[count q; [nsthis:q 0; name:q 1; if[not any nss~\:nsthis; nss,:enlist nsthis]];
              [name:funcname line; nsthis:cur]];
            ok:(0<count name) and (0<count nsthis) and not "_"=first name;
            if[ok;
                j:i-1;
                while[(j>=0) and (0<count funcname ls j) or 0<count qualified ls j; j-:1];
                blk:();
                while[(j>=0) and ("/"=first lstrip ls j) and not "\\"~strip ls j; blk,:enlist ls j; j-:1];
                blk:reverse blk;
                if[count blk;
                    d:`fullname`ns`description`params`returns`throws`examples!(nsthis,".",name;nsthis;"";();"";();());
                    prose:(); seen:0b;
                    k:0;
                    while[k<count blk;
                        raw:blk k; t:tag raw;
                        $[count t;
                            [seen:1b; kind:t 0; rest:strip t 1;
                             $[kind~"param";
                                 [ws_:where rest in ws; pn:$[count ws_; (first ws_)#rest; rest]; pd:$[count ws_; lstrip (first ws_)_rest; ""];
                                  d[`params],:enlist (pn;pd)];
                               kind~"return"; d[`returns]:rest;
                               kind~"throws"; d[`throws],:enlist rest;
                               d[`examples],:enlist rest]];
                          seen;
                            [body:strip comment raw;
                             if[(0<count body) and (not body~enlist ".") and 0<count d`examples;
                                 d[`examples;-1+count d`examples]:strip (last d`examples)," ",body]];
                          [body:strip comment raw; prose,:enlist $[body~enlist "."; ""; body]]];
                        k+:1];
                    d[`description]:strip " " sv prose where 0<count each prose;
                    if[(count d`params) or (count d`returns) or (count d`examples) or (count d`throws) or count d`description;
                        out,:enlist d]]];
            i+:1]];
    (last "/" vs path; nss; out)}

/ The file's leading comment.
header:{[path]
    ls:read0 hsym `$path; prose:();
    i:0; while[(i<count ls) and "/"=first lstrip ls i;
        body:strip comment ls i; prose,:enlist $[body~enlist "."; ""; body]; i+:1];
    strip " " sv prose where 0<count each prose}

/ Every .q file under dir, in the generator's order: path parts compared in
/ turn, so a directory sorts before a same-named file (core/ before core.q).
qfiles:{[dir]
    walk:{[p] k:key hsym `$p; $[-11h=type k; enlist p; raze .z.s each (p,"/"),/:string k]};
    f:walk dir;
    f:f where f like "*.q";
    f iasc ssr[;"/";"\001"] each f}

/ Register every documented function under dir.
scansrc:{[dir]
    {[f]
        r:parsefile f; docs:r 2;
        if[0=count docs; :()];
        registerFile (r 0;"";"|" sv r 1;header f);
        {[d]
            registerFunc (d`fullname;d`ns;d`description;d`fullname;$[count d`examples; first d`examples; ""]);
            {[fn;p] registerArg (fn;"param";p 0;p 1)}[d`fullname] each d`params;
            if[count d`returns; registerArg (d`fullname;"return";"";d`returns)];
            {[fn;x] registerArg (fn;"throws";"";x)}[d`fullname] each d`throws;
            {[fn;x] registerArg (fn;"eg";"";x)}[d`fullname] each d`examples;
            } each docs} each qfiles dir;
    count .man.funcs}

\d .

/ Read every documented function under src/ into the registry.
.man.scansrc "src";
