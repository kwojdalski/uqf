/ test_docstring_examples.q - execute the @eg assertions in the qDoc blocks
/ (.egtest).
/ .
/ The other half of the executable-examples work. There are 116 `@eg`
/ lines under src/ carrying a `->`
/ expected value, and until this file NOTHING RAN ANY OF THEM. They are an
/ assertion suite with no runner: 116 claims about what this library returns,
/ every one of which goes stale the moment a formula or a return shape
/ changes, with a green test suite the whole way.
/ .
/ The first run found 21 wrong, in four distinct kinds - and the kinds matter
/ more than the count, because three of them were invisible to a reader:
/ .
/   1. ELEVEN in microstructure.q documented an atom where the function
/      returns a ONE-ELEMENT VECTOR. Each is vectorised over rows and the
/      example passes one row. The shape is part of the contract - a caller
/      doing `first` or arithmetic on it needs to know - so these now
/      document q's own display, `,1.1001`.
/   2. THREE overstated precision: implied_foreign_rate returns 0.01999995,
/      not 0.02, and bisect_vol 0.09999999, not 0.1. The residuals are
/      properties of the inputs' rounding and of BISECT_VOL_TOL, which is
/      worth knowing rather than rounding away.
/   3. FIVE were notation or TYPE errors - `(`EURJPY;165;165.052)` documents
/      a long where the function returns a float.
/   4. ONE was genuinely unverifiable: .qwcfg.explain[`backfill_from] claimed
/      (`env;"2026.09.01"), but nothing sets UQF_BACKFILL_FROM and an example
/      whose value depends on the caller's environment cannot be checked at
/      all. It now documents `dry_run`, the key production actually reads.
/ .
/ WHAT THIS ASSERTS. Two invariants, of which the second is newer:
/ .
/   ZERO MISMATCHES - no documented value may be wrong.
/   ZERO ERRORS     - no documented expression may fail to run.
/ .
/ The second used to be tolerated. Sixteen examples needed a `tape`, `quotes`,
/ `requests` or `b` that only a caller's session had, and the objection to
/ binding them was real: inventing a fixture for each would be a second,
/ unreviewed copy of the data the real tests use. That is answered by not
/ inventing any - every fixture below is a builder another suite already owns.
/ .
/ Running the sixteen found what tolerating them had hidden: signed_trade_flow
/ documented -1000000f where the registered tape gives -500000f;
/ unrealized_pnl documented 6000 where it returns 6000f; `b` named three
/ different books in one file; `requests` named two different shapes in
/ another; and one example priced a currency pair no fixture in this tree
/ carries. Of 116 assertions, 105 were then executed and compared.
/ .
/ The examples stating NO value are run too now, by tests/q/run_examples.q in
/ a separate process - see that file for why it cannot be a test here.
/ .
/ The remaining 11 state PROSE rather than a value ("one overall count-mode
/ ratio"). They are counted as prose rather than quietly passed, so a value
/ that stops parsing cannot hide among them.

\d .egtest

/ --- fixtures for the examples that need one -----------------------------

/ Twelve @eg lines reference a `tape`, `quotes`, `requests` or `b` that only
/ exists in a caller's session. They were reported as `error` and tolerated,
/ because - as this file used to say - inventing a fixture for each would be
/ "a second, unreviewed copy of the data the real tests use".
/ .
/ That objection is answered by not inventing any. Every binding below
/ REUSES a builder that already exists in another suite, so there is exactly
/ one definition of each shape and it is the one the real tests exercise. The
/ test files load before this one (see tests/run_tests.q), so their
/ namespaces are already in scope.
/ .
/ Bound as ROOT globals because that is where `value` resolves a bare name
/ from - the @eg text says `tape`, not `.evttest.tape`, and it has to keep
/ saying that, since an @eg a reader cannot paste is worse documentation
/ than one that goes unchecked.
bind_fixtures:{[]
    / Two shapes, two names. The hit family needs a `hit` column and the
    / reject family a `reject` one; each already has a builder, and the docs
    / now name whichever they require rather than calling both `requests`.
    `requests set .executiontest.mk_hit_ratio_requests[::];
    `reject_requests set .executiontest.mk_requests[];
    / Wide enough to contain both builders' timestamps. A window that
    / excluded one would make its examples report on an empty selection -
    / which is not an error, and so would be tolerated rather than seen.
    `start_ts set 2026.01.01D00:00:00.000000000;
    `end_ts set 2026.12.31D00:00:00.000000000;
    / The REGISTERED demo_events fixture, not a tape invented here. The
    / microstructure @eg values were written against this one - binding
    / anything else would make the gate report the fixture's disagreement
    / with the docs as though the docs were wrong.
    `tape set .qfeed.demo_events.fixture[];
    `quotes set .forwardstest.mk_ts_quotes_table[::];
    `trade_time set exec first time from `quotes;
    / `b` appeared in five positions.q examples meaning THREE different
    / books, each defined only in a parenthetical gloss. The three that
    / assert a value now build their own book inline, so this binding serves
    / only the two that assert nothing - a book with one open position.
    `b set .qpos.apply_fill[.qpos.empty_book[];`EURUSD;600000;1.1000;1];

    / Every binding below serves an @eg with no `->` - one that claims no
    / value, and so was never run by anything until tests/q/run_examples.q.
    / Same rule as above: each comes from a builder the owning suite already
    / uses, or from the source's own declaration. None is invented here.
    `markout_trades set .executiontest.mk_markout_trades[];
    `mid_quotes set .executiontest.mk_mid_quotes[];
    `fill_orders set .executiontest.mk_fill_orders[];
    `trades set .positionstest.mk_trades[];
    / `value `trades`, not `trades`: inside \d .egtest a bare name resolves
    / to .egtest.trades, which does not exist. Every read of a root binding
    / below goes through `value` for the same reason.
    `broker_book set .qpos.apply_fills[.qpos.empty_book[];value `trades];
    books:.forwardstest.mk_books[];
    `eurusd_book set books`eurusd;
    `usdjpy_book set books`usdjpy;
    `jpychf_book set books`jpychf;
    `t0 set min exec time from `quotes;
    `t1 set max exec time from `quotes;
    `tbl set .booktest.wide_book_table[::];
    `prefix_targets set .booktest.level_prefix_targets;
    .metatest.setUp[::];
    `trade set .metatest.source;
    `spec set .qmeta.definition[`trade;`date;enlist `sym;()!()];
    `stored set .qmeta.collect[value `spec;2026.09.01 2026.09.02];
    / The markout job's own buffer, with two rows in it. It used to be read
    / out of scripts/torq_markout_etl.q as TEXT and re-evaluated, because
    / that file could not be loaded outside TorQ; the job is a src/ file now,
    / so the real table is simply here.
    `.qsub.markout.pending set .qsub.markout.pending upsert
        ([] time:2026.01.01D00:00:00.000000000 2026.01.01D00:00:01.000000000;
            sym:`EURUSD`GBPUSD; side:1 -1; trade_price:1.1 1.25;
            size:1000000 500000f; pip_factor:10000 10000);
    `cutoff set 2026.01.01D00:00:00.500000000;
    `bound}

/ Private: files to scan. `find` rather than a hardcoded list - a new module
/ is covered the day it is added, not the day someone remembers.
/ .
/ scripts/ as well as src/. Its examples document the pipeline helpers every
/ TorQ process uses, and scanning src/ alone left thirteen of them unseen -
/ including four, added this same month, that named functions which did not
/ exist.
sources:{[] system"find src scripts -name '*.q' | sort"}

/ Private: is this line an @eg TAG, rather than prose that mentions one?
/ .
/ The first version matched "*@eg*" anywhere, and so read a sentence in
/ data.q - "...while their own @eg named `pqModule`." - as an example whose
/ code was "named `pqModule`.", which of course threw.
is_tag:{[l] (trim l) like "/ @eg *"}

/ Private: does this line continue the example above it?
/ .
/ A continuation is a comment indented by two or more spaces. Examples run
/ across lines - a status write with a spec and a progress dictionary, a log
/ call with its fields - and reading the tag line alone cut each of them off
/ mid-bracket, so they failed to parse and were reported as broken examples
/ when they were only half-read ones.
is_continuation:{[l]
    t:trim l;
    $[3>count t; 0b; ("/"=t 0) and ("  "~t 1 2) and 0<count trim 1_t]}

/ Private: (file;line;expr;expected) for every @eg. `expected` is "" when the
/ example states no value.
extract:{[f]
    ls:read0 hsym `$f;
    {[f;ls;i]
        n:count ls; j:i+1; parts:enlist 5_trim ls i;
        while[(j<n) and is_continuation ls j; parts,:enlist trim 1_trim ls j; j+:1];
        txt:trim " " sv parts;
        p:first ss[txt;"->"];
        (f;i+1;trim $[null p; txt; p#txt];$[null p; ""; trim (p+2)_txt])
      }[f;ls] each where is_tag each ls}

/ Every documented example, whether or not it states a value.
examples:{[] raze extract each sources[]}

/ The examples that state a value, which this suite compares.
assertions:{[] e:examples[]; e where 0<count each e[;3]}

/ Examples that cannot run in a test process, and why.
/ .
/ A short list on purpose, and every entry is policed from both sides by
/ tests/q/run_examples.q: an entry whose example has gone is stale, and an
/ entry whose example now RUNS no longer needs its excuse. So nothing lands
/ here to make a failure go away - only something that needs a live
/ tickerplant, a TorQ process, a licensed driver or downloaded market data,
/ none of which a test process has.
needs_live:([] expr:(
        ".qpipe.publish[h;`execution_quality;out]";
        ".qpipe.publish[h;`trades;`sym`side`trade_price`size`pip_factor!(`EURUSD;1;1.085;1e6;10000)]";
        ".qpipe.publish[h;`trades;(enlist `EURUSD;enlist 1;enlist 1.085;enlist 1e6;enlist 10000)]";
        ".qpipe.safe_timer[`markout;0D00:00:01.000;`.qproc.stream.tick;\"Run the markout streaming job\"]";
        ".qodbc.window_query[h;`deals;`deal_time;`deal_id`rate;from_ts;to_ts]";
        ".qdata.getBySymbolDate[`AAPL;2026.02.25]");
    reason:(
        "sends .u.upd over a tickerplant handle";
        "sends .u.upd over a tickerplant handle";
        "sends .u.upd over a tickerplant handle";
        "registers a TorQ timer, which needs .timer and .proc from a TorQ process";
        "opens an ODBC connection, which needs a licensed driver this tree does not require";
        "opens a Databento parquet file, which exists only where that data has been downloaded"))

/ Private: drop a trailing prose gloss - "1.016667 (2024 is a leap year)".
/ Both forms are tried by classify below rather than only the stripped one,
/ because a legitimate value can itself end in a parenthesis and stripping
/ it turned ccy_legs' documented table into a truncated fragment.
strip_gloss:{[s] i:ss[s;" ("]; $[(0<count i) and ")"=last s; trim (first i)#s; s]}

/ Private: does this expected text describe what the expression returns?
/ .
/ DISPLAY comparison first, because the docs were written from what q shows
/ you: a float's documented digits are q's 7-significant-figure rendering,
/ so comparing parsed doubles with ~ calls 1.016667 wrong against the very
/ value it was copied from. Value comparison second, so `-0.00005` still
/ matches -5e-05 and a general-list spelling of a vector still matches.
matches:{[actual;txt]
    shown:-3! actual;
    if[shown~txt; :1b];
    ev:@[{(1b;value x)};txt;{(0b;x)}];
    $[ev 0; actual~ev 1; 0b]}

/ Private: `pass, `mismatch, `prose or `error for one assertion.
classify:{[r]
    / `-> throws` documents a refusal, which passes only by refusing. Without
    / this the text "throws" was scored as prose, so an example documenting
    / a guard went on passing after the guard itself was deleted.
    if[(r 3) like "throws*"; :$[first @[{(1b;value x)};r 2;{(0b;x)}]; `mismatch; `pass]];
    ev:@[{(1b;value x)};r 2;{(0b;x)}];
    if[not ev 0; :`error];
    forms:distinct (r 3; strip_gloss r 3);
    if[any matches[ev 1] each forms; :`pass];
    / Not a value at all: neither form parses, so this is prose after the
    / arrow rather than a claim that can be checked.
    parses:{[t] first @[{(1b;value x)};t;{(0b;x)}]} each forms;
    $[any parses; `mismatch; `prose]}

results:{[] bind_fixtures[]; classify each assertions[]}

/ --- the gate -----------------------------------------------------------

test_no_documented_example_is_wrong:{[t]
    bind_fixtures[];
    rows:assertions[];
    st:classify each rows;
    bad:rows where st=`mismatch;
    .qunit.assertEquals[count bad;0;
        "every @eg that states a value states the value the code returns"]};

test_the_wrong_ones_are_named_not_merely_counted:{[t]
    / If this ever fails, the message has to say WHICH example - a count
    / leaves the reader grepping 113 lines across sixteen files.
    bind_fixtures[];
    rows:assertions[];
    st:classify each rows;
    bad:rows where st=`mismatch;
    .qunit.assertEquals[{x[0],":",string x[1]} each bad;();
        "a failing example is reported by file and line"]};

test_every_documented_example_can_actually_be_run:{[t]
    / The gap this closes. Sixteen examples were reported as `error and
    / TOLERATED, because each needed a `tape`, `quotes`, `requests` or `b`
    / that only a caller's session had - a sixth of the documented assertions
    / free to drift with nothing watching. Every one of those fixtures now
    / comes from a builder another suite already owns, so an example that
    / still throws is a defect in the EXAMPLE: it names a fixture nothing
    / provides, a currency pair the fixtures do not carry, or a column its
    / own @throws says it requires. Each was one of those three.
    bind_fixtures[];
    rows:assertions[];
    st:classify each rows;
    bad:rows where st=`error;
    .qunit.assertEquals[{x[0],":",string x[1]} each bad;();
        "every @eg expression runs - a reader can paste it and get an answer"]};

test_most_examples_are_actually_evaluated:{[t]
    / A floor, not an exact count: adding an example must not require editing
    / this test, but a change that made evaluation collapse wholesale - a
    / renamed namespace, a loader dropped from run_tests.q - would otherwise
    / leave this suite passing while checking almost nothing. The floor sits
    / well below the 105 that pass today.
    st:results[];
    .qunit.assertTrue[95<=sum st=`pass;
        "the bulk of the documented examples are executed, not skipped"]};

test_every_assertion_is_classified:{[t]
    / No silent fifth category.
    st:results[];
    .qunit.assertEquals[count st where not st in `pass`mismatch`prose`error;0;
        "every @eg lands in exactly one of pass/mismatch/prose/error"]};

test_assertions_are_found_at_all:{[t]
    / The mirror of the discovered-list trap: if `find` or the @eg convention
    / changed, every test above would pass over an empty list.
    .qunit.assertTrue[100<=count assertions[];
        "the scan finds the documented examples rather than silently nothing"]};

/ --- the scanner itself --------------------------------------------------

/ The first version matched "*@eg*" anywhere on a line, so this sentence in
/ data.q - "...while their own @eg named `pqModule`." - became an example
/ whose code was "named `pqModule`.".
test_prose_mentioning_eg_is_not_an_example:{[t]
    .qunit.assertEquals[is_tag "/ while their own @eg named `pqModule`.";0b;"prose is not a tag"];
    .qunit.assertEquals[is_tag "/ @eg .qfwd.fwd_cont[1.1;0.02;0.01;0.5]";1b;"the tag is"]};

/ Reading the tag line alone cut multi-line examples off mid-bracket, so
/ they failed to parse and were reported as broken rather than half-read.
test_a_multi_line_example_is_read_whole:{[t]
    f:"build/test-status/eg_probe.q";
    system"mkdir -p build/test-status";
    (hsym `$f) 0: ("/ @eg .qlog.info[`w;\"msg\";";"/        `a`b!(1;2)]";"/ @return nothing";"f:{[] 1}");
    r:first extract f;
    .qunit.assertEquals[r 2;".qlog.info[`w;\"msg\"; `a`b!(1;2)]";"both lines, joined, and nothing after"]};

test_a_tag_line_is_not_a_continuation:{[t]
    .qunit.assertEquals[is_continuation "/ @return the total";0b;"a following tag ends the example"];
    .qunit.assertEquals[is_continuation "/ .";0b;"a paragraph break ends it"];
    .qunit.assertEquals[is_continuation "/   ready:x where mask;";1b;"an indented comment continues it"]};

/ `-> throws` documents a refusal. It was scored as prose, so an example
/ documenting a guard went on passing after the guard was deleted.
test_a_documented_throw_passes_only_by_throwing:{[t]
    .qunit.assertEquals[classify ("f";1;"'\"boom\"";"throws");`pass;"it threw, as documented"];
    .qunit.assertEquals[classify ("f";1;"1+1";"throws");`mismatch;"it did not, which is now a failure"]};

/ Every allowance has to name an example that exists - the full two-sided
/ check (listed but runs) needs a separate process, in run_examples.q.
test_every_needs_live_entry_names_a_real_example:{[t]
    ex:examples[][;2];
    .qunit.assertEquals[(needs_live`expr) where not (needs_live`expr) in ex;();
        "no allowance outlives the example it excused"]};

test_the_checker_rejects_a_wrong_value:{[t]
    / A checker nobody has seen say no might be saying yes to everything.
    .qunit.assertEquals[.egtest.matches[42;"43"];0b;
        "a documented value that differs from the actual is not a match"]};

test_the_checker_accepts_q_display_rounding:{[t]
    / The case that makes display comparison necessary: the full double is
    / not equal to its own 7-figure rendering, which is what the doc holds.
    .qunit.assertEquals[.egtest.matches[1%3;"0.3333333"];1b;
        "a float documented as q renders it is a match, not a rounding failure"]};

test_the_checker_accepts_an_equivalent_spelling:{[t]
    .qunit.assertEquals[.egtest.matches[-5e-05;"-0.00005"];1b;
        "the same number written differently is still a match"]};

\d .
