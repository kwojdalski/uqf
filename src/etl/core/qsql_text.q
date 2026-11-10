/ qsql_text.q - a source's query as readable qSQL, for the TRACE log (.qetl.qsql).
/ .
/ A source builds its query as a lambda that runs on the far side, so the
/ lambda's text names `from_ts`, `syms`, `skew`: what the query asked for is
/ in the ARGUMENTS, not in the text. This resolves the arguments into the
/ query's parse tree and renders the tree with patmok/unparse (vendored,
/ unchanged, at lib/unparse - see lib/unparse/NOTICE.md), so a currency pair
/ and a timestamp read as values:
/ .
/   select from trade where sym in `EURUSD`GBPUSD, time>=2026.10.09D10:00:00.000000000
/ .
/ NEVER SILENTLY SHORT. Two things cut a rendering without an error: the
/ console width (\c, at most 2000 columns - .Q.s1 and -3! both stop there and
/ end in "..") and a form unparse cannot spell. So every rendering is
/ (1) made with the console at its widest, (2) PARSED BACK and compared with
/ the tree it came from - a cut or a misspelt form fails that comparison
/ - and (3) when it fails, replaced by the whole parse tree, rendered in
/ pieces so no console width applies (`full`). Which one you got is the
/ `form`: `qsql or `tree.
/ .
/ unparse is optional at runtime: a release carries no lib/ but dbmaint
/ (python/uqs/.../deploy/payload.py), and unparse has no licence, so a tree
/ without lib/unparse renders every query as `tree - complete, less pretty.
/ Not loading it is the supported state, not an error.
/ .
/ Only the query and its arguments are rendered. A handle, a connection
/ string, a user or a password is never an input here.

\d .

/ Whether unparse is loaded. This file does not load it: src/etl/init.q, which
/ names every file from the repository root, does, when lib/unparse is there.
/ @return 1b when `.unparse.unparse` is defined
/ @eg .qetl.qsql.available[]  ->  1b
.qetl.qsql.available:{[] `unparse in key `.unparse}

/ Private: run `f x` with the console at its widest, and put it back even if
/ `f` throws.
/ @param f a unary function
/ @param x its argument
/ @return f x
/ @private
.qetl.qsql.wide:{[f;x]
    c:@[system;"c";{[e] ()}];
    if[2<>count c; :f x];
    @[system;"c ",string[c 0]," 2000";::];
    r:@[{(1b;x y)}[f];x;{(0b;x)}];
    @[system;"c "," " sv string c;::];
    if[not first r; 'last r];
    last r}

/ ------------------------------------------------------------- THE FULL TREE

/ The widest a rendering can be: q's console maximum is 2000 columns and a
/ cut one is one short of it.
.qetl.qsql.cut_at:1998

/ A parse tree (or any value) as text, IN FULL - however wide.
/ .
/ -3! and .Q.s1 stop at the console width, which is at most 2000 columns and
/ 79 by default, so a wide query came out as its first 79 characters and "..".
/ Here a general list, a dictionary and a table are rendered piece by piece
/ and a simple vector too long for one piece is split in two until each half
/ fits, joined with `,`. The text is for a person: it reads like -3!, and is
/ not guaranteed to parse back.
/ @param x any value, usually a parse tree
/ @return its text
/ @eg .qetl.qsql.full (+;1;2)  ->  "(+;1;2)"
.qetl.qsql.full:{[x]
    t:type x;
    $[t=0h; $[0=count x; "()"; 1=count x; ",",.z.s first x; "(",(";" sv .z.s each x),")"];
      t=99h; "(",.z.s[key x],")!(",.z.s[value x],")";
      t=98h; "+",.z.s flip x;
      t=100h; string x;
      t=10h; .qetl.log.value1 x;
      .qetl.qsql.leaf x]}

/ Private: a non-nested value, halving a vector that does not fit one piece.
/ @private
.qetl.qsql.leaf:{[x]
    r:.qetl.qsql.wide[-3!;x];
    if[(.qetl.qsql.cut_at<count r) and (0<t:type x) and (1<count x);
        h:count[x] div 2;
        :"(",.z.s[h#x],"),(",.z.s[h _ x],")"];
    r}

/ ---------------------------------------------------- RESOLVING ARGUMENTS

/ Private: a value as it stands in a parse tree, or `::` when it cannot be
/ spelt there (a general list, a table, a function, an empty vector).
/ A bare symbol atom in a tree is a variable's NAME, so a literal symbol is
/ enlisted: an atom is `,`a`, a vector `,`a`b`, and a one-element vector
/ `(enlist; ,`a)`.
/ @private
.qetl.qsql.embed:{[v]
    t:type v;
    $[t<0h; $[t=-11h; enlist v; v];
      t=11h; $[0=count v; (::); 1=count v; (enlist; .z.s first v); enlist v];
      (t>0h) and t<20h; $[0=count v; (::); 1=count v; (enlist; .z.s first v); v];
      ::]}

/ Private: `x` with every bare symbol naming a key of `m` replaced by its
/ value. Walks lists and dictionary values; a list nothing changed in is
/ returned as it came, so an all-symbol general list never narrows.
/ @private
.qetl.qsql.subst:{[m;x]
    t:type x;
    if[t=-11h; :$[x in key m; m x; x]];
    if[t=99h; :$[0h=type value x; (key x)!.z.s[m] each value x; x]];
    if[t<>0h; :x];
    r:.z.s[m] each x;
    $[r~x; x; r]}

/ Private: a lambda's parameter names and its body text.
/ @param f a lambda
/ @return (parameter symbols; body text)
/ @private
.qetl.qsql.body:{[f]
    s:string f;
    b:$[(s 1)="["; (1+s?"]") _ s; 1 _ s];
    ((value f) 1; trim -1 _ b)}

/ Private: a lambda, or a projection of one, with every argument it runs on.
/ A projection's fixed arguments come first and the call's own fill its gaps,
/ then follow any beyond them - the order q applies them in.
/ @param f a lambda or a projection
/ @param args the call's own arguments, a list
/ @return (lambda; its arguments in parameter order), or `::` for a function that is not a lambda underneath
/ @private
.qetl.qsql.bind:{[f;args]
    if[.qetl.declared.lambda f; :(f;args)];
    if[104h<>type f; :(::)];
    v:value f;
    fill:{[st;x] $[(::)~x; (1_st 0; (st 1),enlist first st 0); (st 0; (st 1),enlist x)]};
    st:fill/[(args;()); 1_v];
    .z.s[first v; (st 1),st 0]}

/ ------------------------------------------------------------------ RENDERING

/ Private: a functional select/exec/update/delete CALL tree as the tree qSQL
/ parses to, or the tree unchanged.
/ .
/ `parse "?[t;enlist(>;`px;1.5);0b;()]"` is a call whose where, by and select
/ arguments are expressions that BUILD the clauses; `parse "select from t where
/ px>1.5"` holds the clauses themselves, quoted. unparse spells only the second
/ as qSQL (the first it prints as the call). The arguments are evaluated here -
/ they hold constants and primitives once the call's parameters are resolved -
/ and the where list quoted again. An argument that will not evaluate (a column
/ or a local the tree cannot see) leaves the whole tree as it came.
/ @param tree a parse tree
/ @return the quoted form of a functional call, else `tree`
/ @private
.qetl.qsql.quote:{[tree] @[.qetl.qsql.quote0;tree;{[t;e] t}[tree]]}

/ Private: quote, unprotected.
/ @private
.qetl.qsql.quote0:{[tree]
    if[not (0h=type tree) and 5=count tree; :tree];
    if[not (first tree) in (?;!); :tree];
    if[not 11h=abs type tree 1; :tree];
    built:{[x] (0h=type x) and (count x)and (enlist~first x)};
    if[not built tree 2; if[not ()~tree 2; :tree]];
    ev:{[x] @[{(1b;eval x)};x;{(0b;x)}]};
    c:$[()~tree 2; (1b;()); ev tree 2];
    b:$[0b~tree 3; (1b;0b); ev tree 3];
    a:$[()~tree 4; (1b;()); ev tree 4];
    if[not all (c 0;b 0;a 0); :tree];
    / delete's column list is quoted as the where list is; update's dictionary is not.
    (tree 0; tree 1; $[count c 1; enlist c 1; ()]; b 1; $[(11h=type a 1) and (0<count a 1) and (!)~tree 0; enlist a 1; a 1])}

/ A parse tree as qSQL, or as the whole tree when qSQL cannot carry it.
/ .
/ Tried in order: unparse, with the console at its widest; the result parsed
/ back and compared with the tree (`~`); and, if either step fails or throws,
/ `full tree`. A rendering that does not round-trip is never returned as qSQL.
/ A functional call (`?[t;c;b;a]` with its arguments as expressions) is quoted
/ first (`quote`) and compared in that form.
/ @param tree a parse tree, e.g. `parse "select from t"`
/ @return (form; text): form is `qsql, `call (unparse spelt it as ?[...] or ![...], still complete) or `tree
/ @eg .qetl.qsql.render parse "select from t where x>1"  ->  (`qsql;"select from t where x>1")
.qetl.qsql.render:{[tree]
    q:.qetl.qsql.quote tree;
    if[.qetl.qsql.available[];
        text:@[.qetl.qsql.wide[.unparse.unparse];q;{[e] ""}];
        if[(0<count text) and @[{x~parse y}[q];text;{[e] 0b}];
            :($[text like "[?!][[]*"; `call; `qsql]; text)]];
    (`tree; .qetl.qsql.full tree)}

/ A source's query as readable text, with its arguments resolved.
/ .
/ `f` is what the source sends - a lambda, or a projection of one that closed
/ over the source's own values (syms, skew) - and `args` what the far side
/ runs it on (the window's bounds, a cursor). Each parameter's name in the
/ lambda's body is replaced by the value it was called with, then the tree is
/ rendered by `render`. A value a tree cannot spell (a table, a general list)
/ leaves its name; a column named like a parameter would be taken for the
/ parameter - qSQL itself prefers the column, so keep parameter names off
/ column names, as the sources here do.
/ .
/ A body that will not parse (a multi-line one with a local, say) is returned
/ as the lambda's text and its arguments, in full, form `lambda.
/ @param f the lambda or projection the source sends
/ @param args its arguments, a list
/ @return (form; text): form is `qsql, `call, `tree or `lambda
/ @eg .qetl.qsql.of_call[{[s;t] select from trade where sym=s, time>t};(`EURUSD;2026.10.09D10:00:00.000000000)]  ->  (`qsql;"select from trade where sym=`EURUSD,time>2026.10.09D10:00:00.000000000")
.qetl.qsql.of_call:{[f;args]
    b:.qetl.qsql.bind[f;args];
    if[(::)~b; :(`lambda; .qetl.qsql.full (f;args))];
    pb:.qetl.qsql.body b 0;
    vals:b 1;
    tree:@[parse;pb 1;{[e] (::)}];
    if[(::)~tree; :(`lambda; .qetl.qsql.full b)];
    if[(count pb 0)<>count vals; :(`lambda; .qetl.qsql.full b)];
    e:.qetl.qsql.embed each vals;
    ok:where not (::)~/: e;
    m:(pb[0] ok)!e ok;
    .qetl.qsql.render .qetl.qsql.subst[m;tree]}

\d .
