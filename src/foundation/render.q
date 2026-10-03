/ render.q - a value as q text, without the console-width cut (.qrender).
/ .
/ .Q.s1 and -3! stop at the console width (\c: 80 columns by default, 200
/ under TorQ) and end the text with "..". Anything that RECORDS a rendering
/ - a data-quality failure's detail, a run fact - is then cut before the
/ values that explain it: a crossed book's detail is 391 characters, and
/ what survived was its column names (#605).
/ .
/ In foundation so the library (dqchecks) and the ETL (the workers' quality
/ checks) share one. src/etl/core/log.q keeps its own copy (value1, quoted)
/ because processes load it without the library - torq_tap.q and every
/ worked example - and tests/q/test_render.q holds the two to the same
/ output.

\d .qrender

/ A value as q text, IN FULL: a string escaped with no length limit, anything
/ else with the console widened to its 2000-column maximum for the one call
/ and put back even if rendering throws. Past 2000 columns a non-string still
/ ends in "..", which is q's own limit.
/ @param v any value
/ @return its q literal, as .Q.s1 spells it
/ @eg count .qrender.full 300#"a"  ->  302
full:{[v]
    if[10h=type v; :quoted v];
    c:@[system;"c";{[e] ()}];
    if[2<>count c; :.Q.s1 v];
    @[system;"c ",string[c 0]," 2000";::];
    r:@[.Q.s1;v;{[e] "'",e}];
    @[system;"c "," " sv string c;::];
    r}

/ A string as a q string literal: \ " newline, carriage return and tab
/ escaped as q writes them, every other byte outside printable ASCII as a
/ three-digit octal escape - exactly as -3! spells it, without its cut.
/ @param s a string
/ @return the literal
/ @eg .qrender.quoted "a\nb"  ->  "\"a\\nb\""
quoted:{[s]
    esc:{[ch] i:`int$ch;
        $[ch in "\\\""; "\\",ch;
          ch="\n"; "\\n";
          ch="\r"; "\\r";
          ch="\t"; "\\t";
          (i<32) or i>126; "\\",raze string 8 8 8 vs i;
          enlist ch]};
    "\"",(raze esc each s),"\""}

\d .
