/ coverage.q - code coverage for q (.cov), in the shape KX's own library has.
/ .
/ https://code.kx.com/developer/libraries/code-coverage/ defines the API this
/ implements: .cov.run to instrument and execute, .cov.format.go to render,
/ .cov.format.display to print. Same three entry points, same settings keys,
/ same results columns, same <<<>>> / X report marks - so anything written
/ against that library reads the same here.
/ .
/ WHAT IT MEASURES. Two granularities, counted separately:
/ .
/   LINES   every logical statement - one in a function body, one inside an
/           if/do/while. "Did this statement ever run."
/   BLOCKS  every conditional or loop ARM, including the arms of `$`. "Was
/           this branch ever taken." A function can have every line executed
/           and a branch never taken, which is where most real gaps hide.
/ .
/ Coverage is the share of CHARACTERS inside those ranges that executed,
/ which is KX's definition and is worth keeping: it weights a long branch
/ more than a short one, so a report cannot look green because the untaken
/ paths happen to be the terse ones.
/ .
/ HOW IT INSTRUMENTS. A lambda carries its own source - `last value f` - so
/ the tool rewrites that text and `value`s it back. Nothing on disk is
/ touched and the originals are restored when the run finishes, including
/ when it throws.
/ .
/ THE LIMIT, stated because a coverage number that overstates itself is worse
/ than none: this instruments the functions NAMED AT CALL TIME. A function
/ whose value was captured into a dictionary earlier - .qio.memory holds
/ write_memory - is called through that copy and its probes do not fire. For
/ whole-tree coverage where that matters, scripts/qcov.py instruments the
/ SOURCE FILES before they load and has no such blind spot. This library is
/ for the question KX's answers: run THIS call, show me what it missed.

\d .cov

/ --------------------------------------------------------------- TOKENISING

/ Private: character classes, hoisted so the scanner does not rebuild them.
alpha:.Q.a,.Q.A,"_";
alnum:alpha,.Q.n;
namechars:alnum,".";

/ Tokenize q source.
/ .
/ Returns a table kind/start/len. Only as precise as the instrumenter needs:
/ it must never mistake a bracket or a semicolon inside a string, a comment
/ or a system command for syntax. Numeric literals are not decomposed -
/ 2026.09.17D00:00 is one `other run and nothing downstream cares.
/ .
/ THE COMMENT RULES, each of which this repository has been bitten by:
/   `/` to end of line, but only when it BEGINS a token - `a/b` is the over
/   adverb, not a comment.
/   a line whose only content is `/` opens a block comment that runs until a
/   line whose only content is `\`.
/   a line beginning `\` is a system command and runs to end of line.
/ @param src the source text
/ @return a table kind/start/len
/ @eg .cov.tokens "f:{[a] a+1}"
tokens:{[src]
    n:count src;
    i:0; kinds:(); starts:(); lens:();
    lineStart:1b;
    while[i<n;
        c:src i;
        / Does a token START here? `/` is a comment only when it begins one -
        / `a/b` is the over adverb. Computed rather than inlined because q's
        / `and` is `min` and evaluates BOTH sides, so `(0<count kinds) and
        / (last kinds)...` still indexes an empty list.
        prevGap:$[0=count kinds; 1b; (last kinds) in `ws`nl];
        $[c="\n";
            [kinds,:`nl; starts,:i; lens,:1; i+:1; lineStart:1b];
          c in " \t\r";
            [j:i; while[(j<n) and src[j] in " \t\r"; j+:1];
             kinds,:`ws; starts,:i; lens,:j-i; i:j];
          lineStart and (c="/") and "/"~trim rest_of_line[src;i];
            [j:skip_block[src;i]; kinds,:`comment; starts,:i; lens,:j-i; i:j; lineStart:1b];
          lineStart and c="\\";
            [r:rest_of_line[src;i]; kinds,:`system; starts,:i; lens,:count r; i+:count r];
          (c="/") and (lineStart or prevGap);
            [r:rest_of_line[src;i]; kinds,:`comment; starts,:i; lens,:count r; i+:count r];
          c="\"";
            [j:end_string[src;i]; kinds,:`string; starts,:i; lens,:j-i; i:j; lineStart:0b];
          c="`";
            [j:end_symbol[src;i]; kinds,:`symbol; starts,:i; lens,:j-i; i:j; lineStart:0b];
          c in alpha;
            [j:i; while[(j<n) and src[j] in namechars; j+:1];
             kinds,:`name; starts,:i; lens,:j-i; i:j; lineStart:0b];
          c in "([{";
            [kinds,:`open; starts,:i; lens,:1; i+:1; lineStart:0b];
          c in ")]}";
            [kinds,:`close; starts,:i; lens,:1; i+:1; lineStart:0b];
          c=";";
            [kinds,:`semi; starts,:i; lens,:1; i+:1; lineStart:0b];
            [kinds,:`other; starts,:i; lens,:1; i+:1; lineStart:0b]]];
    ([] kind:`symbol$kinds; start:`long$starts; len:`long$lens)}

/ Private: the rest of the line starting at i, excluding the newline.
rest_of_line:{[src;i]
    r:i _ src;
    k:r?"\n";
    $[k=count r; r; k#r]}

/ Private: index just past a string literal. A raw newline inside one is not
/ legal q, so hitting one is a malformed source rather than a long string.
end_string:{[src;i]
    n:count src; j:i+1;
    while[j<n;
        c:src j;
        $[c="\\"; j+:2;
          c="\""; [j+:1; :j];
          c="\n"; '"cov.tokens: unterminated string";
          j+:1]];
    '"cov.tokens: unterminated string"}

/ Private: index just past a symbol. Covers `, `abc, `.ns.name and `:a/path.
end_symbol:{[src;i]
    n:count src; j:i+1;
    while[(j<n) and src[j] in namechars,":/-"; j+:1];
    j}

/ Private: index just past a block comment opened by a lone `/` line. An
/ unterminated one runs to end of file, which is q's own behaviour.
skip_block:{[src;i]
    n:count src; j:i;
    while[j<n;
        r:rest_of_line[src;j];
        j+:1+count r;
        if["\\"~trim r; :j]];
    n}

\d .


\d .cov

/ ------------------------------------------------------------- ANALYSING

/ The three q control words whose bracket holds STATEMENTS after its first
/ argument. `$` is deliberately NOT among them: `$[c;a;b]` is a conditional
/ EXPRESSION whose arms produce a value, so a statement probe there would
/ change what it returns. Its arms are BLOCKS, wrapped rather than preceded.
control:`if`do`while;

/ Private: the character just past the argument or statement starting at
/ token i - the next `;` or closing bracket at this nesting level.
span_end:{[t;i;n_src]
    n:count t; depth:0; j:i;
    while[j<n;
        k:t[j]`kind;
        $[k=`open; depth+:1;
          k=`close; $[depth=0; :t[j]`start; depth-:1];
          (k=`semi) and depth=0; :t[j]`start;
          ::];
        j+:1];
    n_src}

/ Private: the index of the next meaningful token at or after i, or null.
next_meaningful:{[t;i]
    n:count t; j:i;
    while[j<n;
        if[not t[j][`kind] in `ws`nl`comment; :j];
        j+:1];
    0N}

/ Private: the index of the previous meaningful token before i, or null.
prev_meaningful:{[t;i]
    j:i-1;
    while[j>=0;
        if[not t[j][`kind] in `ws`nl`comment; :j];
        j-:1];
    0N}

/ Analyse and instrument one lambda's source.
/ .
/ One walk does both, so the ranges recorded and the probes injected cannot
/ disagree about where a statement begins.
/ @param src a lambda's source, as `last value f` returns it
/ @param lbase the id this function's first LINE probe gets
/ @param bbase the id its first BLOCK probe gets
/ @return a dictionary lines/blocks/text
/ @eg .cov.analyse["{[x] $[x>0;1;2]}";0;0]
analyse:{[src;lbase;bbase]
    t:tokens src;
    n:count t; ns:count src;
    out:""; cursor:0;
    lines:(); blocks:();
    skind:(); sarm:();            / one entry per open bracket
    pending:0b;                   / next meaningful token begins a statement
    markBlock:0b;                 / ...and that statement is also a branch arm
    wrapOpen:0b;                  / a `$` arm wrapper is waiting to be closed
    i:0;
    while[i<n;
        k:t[i]`kind; s:t[i]`start;
        top:$[0=count skind; `none; last skind];

        / --- a statement begins here
        if[pending and not k in `ws`nl`comment;
            / `;}` and `;]` separate nothing - an empty statement is not one.
            if[not k=`close;
                out,:src cursor+til s-cursor; cursor:s;
                e:span_end[t;i;ns];
                lines,:enlist (s;e);
                out,:".cov.l[",string[lbase+count[lines]-1],"];";
                if[markBlock;
                    blocks,:enlist (s;e);
                    out,:".cov.bs[",string[bbase+count[blocks]-1],"];"]];
            pending:0b; markBlock:0b];

        $[k=`open;
            [c:src s;
             $[c="{";
                 [out,:src cursor+til 1+s-cursor; cursor:s+1;
                  / A parameter list belongs to the lambda, not its body.
                  nx:next_meaningful[t;i+1];
                  if[(not null nx) and (t[nx][`kind]=`open) and "["=src t[nx]`start;
                      / q has no loop break, and `:` inside a while returns
                      / from the enclosing FUNCTION - which silently made
                      / analyse return (::) the first time this ran. Ending
                      / the loop by moving the counter is the only way.
                      [d:0; j:nx; ee:ns; jt:i;
                       while[j<n;
                           kk:t[j]`kind;
                           $[kk=`open; d+:1; kk=`close; d-:1; ::];
                           if[(kk=`close) and d=0; [ee:1+t[j]`start; jt:j; j:n]];
                           j+:1];
                       out,:src cursor+til ee-cursor; cursor:ee;
                       / The TOKEN index moves too. Advancing only the
                       / character cursor left the walk re-reading the
                       / parameter list's own `;` and `]` as if they were
                       / body syntax, which corrupted the stack and drove
                       / the cursor past the tokens it was emitting.
                       i:jt]];
                  skind,:`body; sarm,:0; pending:1b];
               c="[";
                 [pv:prev_meaningful[t;i];
                  pk:$[null pv; `none; t[pv]`kind];
                  ptxt:$[null pv; ""; src (t[pv]`start)+til t[pv]`len];
                  isCtrl:(pk=`name) and (`$ptxt) in control;
                  isCond:(pk=`other) and ptxt~enlist "$";
                  skind,:$[isCtrl;`ctrl;isCond;`cond;`expr]; sarm,:0;
                  out,:src cursor+til 1+s-cursor; cursor:s+1];
               [skind,:`expr; sarm,:0;
                out,:src cursor+til 1+s-cursor; cursor:s+1]]];
          k=`close;
            [if[wrapOpen and top=`cond;
                out,:src cursor+til s-cursor; cursor:s;
                out,:"]"; wrapOpen:0b];
             if[0<count skind; [skind:-1_skind; sarm:-1_sarm]];
             out,:src cursor+til 1+s-cursor; cursor:s+1];
          k=`semi;
            [$[top=`body;
                 [out,:src cursor+til 1+s-cursor; cursor:s+1; pending:1b];
               top=`ctrl;
                 [out,:src cursor+til 1+s-cursor; cursor:s+1;
                  pending:1b; markBlock:1b];
               top=`cond;
                 [if[wrapOpen;
                     out,:src cursor+til s-cursor; cursor:s;
                     out,:"]"; wrapOpen:0b];
                  out,:src cursor+til 1+s-cursor; cursor:s+1;
                  nx:next_meaningful[t;i+1];
                  if[(not null nx) and not t[nx][`kind]=`close;
                      st:t[nx]`start;
                      out,:src cursor+til st-cursor; cursor:st;
                      blocks,:enlist (st;span_end[t;nx;ns]);
                      out,:".cov.b[",string[bbase+count[blocks]-1],";";
                      wrapOpen:1b]];
                 [out,:src cursor+til 1+s-cursor; cursor:s+1]];
             sarm[count[sarm]-1]+:1];
          ::];
        i+:1];
    out,:cursor _ src;
    `lines`blocks`text!(lines;blocks;out)}

\d .

\d .cov

/ ------------------------------------------------------------- COLLECTING

/ Hit counters, indexed by probe id. Plain vectors rather than dictionaries:
/ these are touched once per executed statement, and the library's own
/ documentation warns that the overhead is proportional to code volume.
lineHits:0#0;
blockHits:0#0;

/ Probe: a statement ran. Returns nothing, so a function's own return value
/ is untouched.
l:{[i] lineHits[i]+:1;}

/ Probe: a control-statement arm ran. Same shape as `l`, separate counter.
bs:{[i] blockHits[i]+:1;}

/ Probe: a `$` arm ran. An EXPRESSION that returns the arm's own value, so
/ wrapping an arm cannot change what the conditional evaluates to - and
/ because the wrapper sits inside the arm, q's laziness is preserved and an
/ untaken arm is still not evaluated.
b:{[i;v] blockHits[i]+:1; v}

/ ------------------------------------------------------------- SELECTING

/ Private: every lambda name in a namespace.
ns_lambdas:{[ns]
    d:@[value;ns;{[e] (::)}];
    if[not 99h=type d; :`symbol$()];
    nms:key d;
    nms:nms where {[ns;nm] 100h=type @[value;` sv ns,nm;{[e] (::)}]}[ns] each nms;
    {[ns;nm] ` sv ns,nm}[ns] each nms}

/ Private: the functions a settings dictionary selects.
/ .
/ `functions` and `namespaces` ADD; `ignoreFunctions` and `ignoreNamespaces`
/ subtract, and subtraction wins - an ignore is a statement about what must
/ not be touched, so it cannot be overridden by a broader include.
targets:{[settings]
    get_:{[d;k] $[k in key d; (),d k; `symbol$()]};
    fns:get_[settings;`functions];
    nss:get_[settings;`namespaces];
    fns:fns,raze ns_lambdas each nss;
    fns:fns except get_[settings;`ignoreFunctions];
    ig:get_[settings;`ignoreNamespaces];
    if[count ig; fns:fns except raze ns_lambdas each ig];
    distinct fns where {[nm] 100h=type @[value;nm;{[e] (::)}]} each fns}

/ Private: the namespace a fully-qualified name lives in.
namespace_of:{[nm]
    s:string nm;
    d:where s=".";
    $[0=count d; `.;
      1=count d; `.;
      `$(last d)#s]}

/ Private: define a function from source text, in its own namespace.
set_in:{[nm;txt]
    ns:namespace_of nm;
    cur:system"d";
    system"d ",string ns;
    r:@[value;txt;{[e] `set_in_error`msg!(1b;e)}];
    system"d ",string cur;
    if[$[99h=type r; `set_in_error in key r; 0b];
        '"cov: could not instrument ",string[nm],": ",r`msg];
    nm set r}

/ ----------------------------------------------------------------- RUNNING

/ Instrument, execute, restore, report - the library's entry point.
/ .
/ @param fn the function to execute under instrumentation
/ @param params its argument list (enlist a single argument)
/ @param settings a dictionary of context/functions/ignoreFunctions/
/   namespaces/ignoreNamespaces; any subset, any order
/ @return a table name/iterations/lineIterations/blockIterations/lines/
/   blocks/text, one row per instrumented function
/ @throws error if a setting names something that is not a symbol
/ @eg .cov.run[{[x] x*2};enlist 3;(enlist `functions)!enlist `myFunc]
run:{[fn;params;settings]
    if[not 99h=type settings; '"cov.run: settings must be a dictionary"];
    names:targets settings;
    if[0=count names; '"cov.run: settings selected no functions to instrument"];

    originals:names!value each names;
    lstart:(); bstart:(); texts:();
    nl:0; nb:0;
    info:();
    i:0;
    while[i<count names;
        nm:names i;
        / `value value nm`, not `value nm`: the first resolves the name to
        / the function, the second gives q's introspection list whose LAST
        / element is the source text. One `value` takes `last` of the
        / function itself.
        src:last value value nm;
        a:analyse[src;nl;nb];
        lstart,:nl; bstart,:nb;
        nl+:count a`lines; nb+:count a`blocks;
        info,:enlist a;
        texts,:enlist src;
        i+:1];

    lineHits::nl#0; blockHits::nb#0;

    / Install, run under protection, and restore whatever happened. A run
    / that threw and left the tree instrumented would poison every later
    / call in the session, which is worse than losing the measurement.
    / .
    / INSTALLED IN THE FUNCTION'S OWN NAMESPACE. Re-evaluating the source at
    / root rebinds every unqualified name in it: `.ex.c`'s body says `a[x]`,
    / meaning `.ex.a`, and valued at root that is a root `a` which does not
    / exist. The function would then fail with 'a the moment it ran - a
    / coverage tool breaking the code it measures.
    {[nm;a] set_in[nm;a`text]}'[names;info];
    ctx:$[`context in key settings; settings`context; `.];
    / THE ENTRY POINT NEEDS SUBSTITUTING, and missing this makes the whole
    / report read zero. `fn` is a VALUE, captured by the caller before
    / anything was instrumented, so calling it runs the original even though
    / the name now holds the instrumented copy. If it is one of the targets,
    / call the name instead. A caller whose `fn` merely CALLS instrumented
    / functions needs no substitution - those calls go through their names.
    same:names where {[f;v] v~f}[fn] each value originals;
    callable:$[count same; value first same; fn];
    r:@[{[f;p] $[0=count p; f[]; f . p]}[callable];params;{[e] `cov_error`msg!(1b;e)}];
    / `value originals`, NOT `value each originals`: the first gives the
    / dictionary's values (the functions), the second applies `value` to each
    / FUNCTION and hands back q's introspection list. Restoring those left
    / every instrumented name bound to a list instead of a function.
    {[nm;v] nm set v}'[names;value originals];
    / `$` rather than `and`: q's `and` is `min` and evaluates BOTH sides, so
    / `key r` runs even when r is not a dictionary. Second time in this file.
    if[$[99h=type r; `cov_error in key r; 0b];
        '"cov.run: the call failed: ",r`msg];

    ([] name:names;
        iterations:{[lh;s;a] $[0=count a`lines; 0; lh s]}[lineHits]'[lstart;info];
        lineIterations:{[lh;s;a] lh s+til count a`lines}[lineHits]'[lstart;info];
        blockIterations:{[bh;s;a] bh s+til count a`blocks}[blockHits]'[bstart;info];
        lines:info@\:`lines;
        blocks:info@\:`blocks;
        text:texts)}

\d .

/ ---------------------------------------------------------------- FORMATTING

/ Defined with fully-qualified names rather than under `\d .cov.format`,
/ because this tree's namespaces are otherwise single-level and a `\d` into
/ a nested one reads like an exception to that rule. The API name is KX's.

/ Private: which characters of a function's text are tracked, and which of
/ those executed.
/ .
/ A character is COVERED only if EVERY range containing it executed. That is
/ what makes the report branch-sensitive: a `$` arm that never ran sits
/ inside a statement that did, so scoring it by the statement alone would
/ paint an untaken branch green - which is the failure this tool exists to
/ prevent.
.cov.marks:{[row]
    n:count row`text;
    tracked:n#0b; covered:n#1b;
    ranges:(row`lines),row`blocks;
    hits:(row`lineIterations),row`blockIterations;
    i:0;
    while[i<count ranges;
        r:ranges i;
        idx:(r 0)+til (r 1)-r 0;
        if[count idx;
            tracked[idx]:1b;
            if[0=hits i; covered[idx]:0b]];
        i+:1];
    `tracked`covered!(tracked;covered)}

/ Private: one function's coverage as a percentage of tracked characters.
.cov.percent:{[row]
    m:.cov.marks row;
    t:sum m`tracked;
    $[0=t; 100f; 100f*(sum m[`tracked] and m`covered)%t]}

/ Render a coverage result as text.
/ .
/ Unexecuted sections are wrapped in <<<>>> and any source line containing
/ one is prefixed with X, which is KX's presentation - the marks are what a
/ reader's eye lands on, and the percentage is the summary they quote.
/ @param results a table from .cov.run
/ @return a list of strings
/ @eg .cov.format.go .cov.run[f;enlist 1;(enlist `functions)!enlist `f]
.cov.format.go:{[results]
    if[0=count results; :enlist "cov: nothing was instrumented"];
    pcts:.cov.percent each results;
    marks:.cov.marks each results;
    total:sum {[m] sum m`tracked} each marks;
    hit:sum {[m] sum m[`tracked] and m`covered} each marks;
    overall:$[0=total; 100f; 100f*hit%total];
    incomplete:sum pcts<100f;
    out:enlist "Coverage: ",(.cov.pct1 overall),"% of ",string[total],
        " tracked character(s) in ",string[count results]," function(s)";
    out,:enlist string[incomplete]," function(s) with incomplete coverage";
    out,:enlist "";
    i:0;
    while[i<count results;
        out,:enlist (string results[i]`name),"  ",(.cov.pct1 pcts i),"%";
        out,:.cov.annotate[results[i]`text;marks i];
        out,:enlist "";
        i+:1];
    out}

/ Private: one decimal place, without scientific notation for round numbers.
.cov.pct1:{[x] ssr[string 0.1*"j"$10*x;"e";"e"]}

/ Private: a function's source with <<<>>> marks and X-prefixed lines.
.cov.annotate:{[text;m]
    bad:m[`tracked] and not m`covered;
    / Wrap maximal RUNS rather than each character: one untaken branch is one
    / fact, and per-character marks would bury the source it is marking.
    marked:"";
    inRun:0b;
    i:0;
    while[i<count text;
        if[bad[i] and not inRun; [marked,:"<<<"; inRun:1b]];
        if[inRun and not bad i; [marked,:">>>"; inRun:0b]];
        marked,:text i;
        i+:1];
    if[inRun; marked,:">>>"];
    lines:"\n" vs marked;
    {[ln] $[ln like "*<<<*"; "X  ",ln; "   ",ln]} each lines}

/ Print a coverage result.
/ @param results a table from .cov.run
/ @return null - the report goes to standard output
/ @eg .cov.format.display .cov.run[f;enlist 1;(enlist `functions)!enlist `f]
.cov.format.display:{[results] -1 each .cov.format.go results; }
