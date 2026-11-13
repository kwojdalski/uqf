/ declared.q - the two questions every declaration asks of its own keys:
/ "is this key one I take?" and "is this value something I can call?"
/ (.qetl.declared, #2112, #2114).
/ .
/ Both were answered by each `define` for itself, and the answers drifted. Eight
/ declarations refused a key they did not know and three accepted it silently,
/ so `start_with_al:1b` on a streaming job registered, was never read, and
/ left the job off the stack. "Is a function" had five spellings (100-112h,
/ lambda-only, 100-104h, `100h<=`, 100-107h), two of which accepted `(::)`; a
/ bounded worker's `check` written as a projection passed `define` and failed
/ every window. A rule stated five times is five rules, so it is stated once,
/ here, and tests/q/test_declared.q holds the tree to it.
/ .
/ UNKNOWN KEYS. A declaration is a literal with nothing to defer, and a key no
/ reader reads is a typo by definition: refuse it, naming it and the keys the
/ declaration does take. A `define` makes this its FIRST check on the
/ dictionary, before it looks for what is missing - a misspelled required key
/ then names the misspelling instead of reporting a key as absent, and
/ test_declared.q can probe every `define` with one key and one expectation.
/ .
/ CALLABLE. A projection counts, and so does a composition or an iterator
/ (100-112h, minus `(::)`). The natural way to hand a declaration a function is
/ to bind a handle or a configuration into one - `.qtorq.publish[h;;]`,
/ `.qf.mkt.summarize_checks[cfg;]` - and that is a projection (104h); a rule
/ that admitted only lambdas would reject exactly what authors write.
/ `(::)` is type 101h like `neg` and `count`, so the type range cannot refuse it;
/ the identity test does, because a null function that passes "is it callable"
/ is a hook that silently does nothing. ARITY stays lambda-only and is
/ load-bearing: q can read the parameter list of a 100h lambda and of nothing
/ else, so a declaration checks arity when `lambda` says it can and accepts
/ the rest on trust (react.q, transform.q).
/ .
/ WHICH Q (#2747). A job may say which q interpreters it runs on, as a `q`
/ key: one string of space-separated terms, `kdbx` (any kdb+ or KDB-X),
/ `kdbx>=4.1` (a minimum), `kdbx=4` (a version prefix, so every 4.x) and
/ `peachq`. Absent, the job runs on every q. Streaming jobs and bounded
/ workers take it alike, so its form is checked here, once. q only checks the
/ form: uqs reads the key as text and refuses a start or a push on a q it
/ does not name (uqs.model.q_support), where the interpreter is known.
/ .
/ Nothing here knows any declaration; the library's own .qf.exec.define
/ (src/portfolio) cannot call .qetl and keeps its own rule.

\d .

/ Can this value be called? Lambdas, projections, compositions and iterators;
/ never `(::)`.
/ @param v any value
/ @return 1b when v is something a declaration may take as a function
/ @eg .qetl.declared.callable {x}  ->  1b
/ @eg .qetl.declared.callable (::)  ->  0b
.qetl.declared.callable:{[v] $[(::)~v; 0b; (type v) within 100 112h]}

/ Is this value a lambda, whose parameter list q can read? Arity is checked
/ only when this is true; a projection or composition is callable but opaque.
/ @param v any value
/ @return 1b when v is a lambda
/ @eg .qetl.declared.lambda {[a;b] a+b}  ->  1b
.qetl.declared.lambda:{[v] 100h=type v}

/ Refuse a declaration key that the declaration does not take.
/ @param who the message prefix, e.g. "define: bars job foo"
/ @param kind what takes the keys, e.g. "a bars job"
/ @param decl the declaration dictionary
/ @param allowed the symbol list of keys it takes
/ @return nothing - it throws naming every unknown key and the keys it takes
/ @throws error naming every key outside allowed
/ @eg .qetl.declared.refuse_unknown["define: x";"a thing";`a`b!1 2;`a`b`c]
.qetl.declared.refuse_unknown:{[who;kind;decl;allowed]
    unknown:(key decl) except allowed;
    if[count unknown;
        'who," declares ",(", " sv string unknown),", which ",kind," does not take - the keys are ",", " sv string allowed]}

/ Refuse a declared function that cannot be called.
/ @param who the message prefix, e.g. "define: demo"
/ @param name the declaration key, for the message
/ @param v the value declared under it
/ @param shape what the function should be, completing "<name> must be ..."
/ @return nothing - it throws
/ @throws error naming the key when v is not callable, including `(::)`
/ @eg .qetl.declared.require_callable["define: x";`on_batch;{x};"a function of the rows"]
.qetl.declared.require_callable:{[who;name;v;shape]
    if[not .qetl.declared.callable v;
        'who,"'s ",string[name]," must be ",shape]}

/ The interpreters a `q` term may name (#2747).
.qetl.declared.q_impls:("kdbx";"peachq")

/ Is this text a version a `q` term may name: dot-separated digits?
/ @param v a string
/ @return 1b when every part is one or more digits
/ @eg .qetl.declared.q_version "4.1"  ->  1b
/ @eg .qetl.declared.q_version "4."  ->  0b
.qetl.declared.q_version:{[v]
    parts:"." vs v;
    $[0=count v; 0b; all {[p] (0<count p) and all p in .Q.n} each parts]}

/ Is this one term of a `q` key: kdbx, kdbx>=VERSION, kdbx=VERSION or peachq?
/ @param t a string
/ @return 1b when it is one of those forms
/ @eg .qetl.declared.q_term "kdbx>=4.1"  ->  1b
/ @eg .qetl.declared.q_term "peachq>=1"  ->  0b
.qetl.declared.q_term:{[t]
    $[any t~/:.qetl.declared.q_impls; 1b;
      t like "kdbx>=*"; .qetl.declared.q_version[6_t];
      t like "kdbx=*"; .qetl.declared.q_version[5_t];
      0b]}

/ Refuse a malformed `q` key: the q interpreters a job runs on.
/ @param who the message prefix, e.g. "define: demo"
/ @param v the value declared under `q`
/ @return v
/ @throws error when v is not a string, names no term, or holds a term that
/   is not kdbx, kdbx>=VERSION, kdbx=VERSION or peachq
/ @eg .qetl.declared.require_q["define: x";"kdbx>=4.1 peachq"]  ->  "kdbx>=4.1 peachq"
.qetl.declared.require_q:{[who;v]
    if[not 10h=type v;
        'who,"'s q must be a string naming the q interpreters it runs on, e.g. \"kdbx>=4.1 peachq\""];
    words:" " vs v;
    terms:words where 0<count each words;
    if[0=count terms;
        'who,"'s q names no interpreter - drop the key to run on every q"];
    bad:terms where not .qetl.declared.q_term each terms;
    if[count bad;
        'who,"'s q holds ",(", " sv bad),", which is not kdbx, kdbx>=VERSION, kdbx=VERSION or peachq"];
    v}

/ A bounded worker's `q` as worker_cfg stores it: "" for every q, which is also
/ what a stored declaration carries back, so it declares again as it was.
/ Resolved before storage, as its note is: the table's rows share one shape.
/ @param who the message prefix, e.g. "define: demo_backfill"
/ @param decl the worker's declaration
/ @return the declared q, checked, or ""
/ @eg .qetl.declared.stored_q["define: x";`a`q!(1;"peachq")]  ->  "peachq"
/ @eg .qetl.declared.stored_q["define: x";enlist[`a]!enlist 1]  ->  ""
.qetl.declared.stored_q:{[who;decl]
    $[$[`q in key decl; not ""~decl`q; 0b]; .qetl.declared.require_q[who;decl`q]; ""]}
