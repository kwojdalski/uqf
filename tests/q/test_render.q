/ test_render.q - .qrender: values as q text without the console-width cut.
/ Library-only, so it runs in the PeachQ-portable suites too.

\d .rendertest

test_a_long_string_is_quoted_in_full:{[t]
    r:.qrender.full 300#"a";
    .qunit.assertEquals[(count r;last r);(302;"\"");"all 300 characters and both quotes - no .. at 80"]};

test_a_long_list_is_rendered_past_the_console_width:{[t]
    c:system"c"; system"c 25 80";
    r:.qrender.full til 100;
    system"c "," " sv string c;
    .qunit.assertTrue[(count r)>200;"100 numbers, not cut at 80 columns"]};

test_the_console_width_is_put_back:{[t]
    c:system"c";
    .qrender.full til 1000;
    .qunit.assertEquals[system"c";c;"rendering leaves \\c as it found it"]};

test_quoting_matches_q_on_every_byte:{[t]
    strs:{"a",x,"b"} each `char$til 256;
    .qunit.assertEquals[.qrender.quoted each strs;-3!'strs;"escaped exactly as -3! escapes them"]};

/ The data-quality detail a crossed book produces - the case that lost its
/ values (#605).
test_a_dq_failure_detail_carries_its_values:{[t]
    row:`time`sym`bid_prices`ask_prices!(2026.01.01D00:00:00.000000000;`EURUSD;1.1003 1.1002 1.1001 1.1000 1.0999;1.1001 1.1002 1.1003 1.1004 1.1005);
    d:.qrender.full row;
    .qunit.assertTrue[(d like "*1.1005*") and not d like "*..";"the ask prices are there, and nothing is cut"]};

\d .
