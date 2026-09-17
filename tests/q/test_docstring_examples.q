/ test_docstring_examples.q - execute the @eg assertions in the qDoc blocks
/ (.egtest).
/ .
/ I-08's other half. There are 116 `@eg` lines under src/ carrying a `->`
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
/ carries. Of 116 assertions, 105 are now executed and compared.
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
    `tape set .qsevt.fixture[];
    `quotes set .forwardstest.mk_ts_quotes_table[::];
    `trade_time set exec first ts from `quotes;
    / `b` appeared in five positions.q examples meaning THREE different
    / books, each defined only in a parenthetical gloss. The three that
    / assert a value now build their own book inline, so this binding serves
    / only the two that assert nothing - a book with one open position.
    `b set .qpos.apply_fill[.qpos.empty_book[];`EURUSD;600000;1.1000;1];
    `bound}

/ Private: files to scan. `find` rather than a hardcoded list - a new module
/ is covered the day it is added, not the day someone remembers.
sources:{[] system"find src -name '*.q' | sort"}

/ Private: (file;line;expr;expected) for every @eg carrying a `->`.
extract:{[f]
    ls:read0 hsym `$f;
    idx:where (ls like "*@eg*") and ls like "*->*";
    {[f;ls;i]
        l:ls i;
        after:(1+first[ss[l;"@eg"]]+2)_l;
        p:first ss[after;"->"];
        (f;i+1;trim p#after;trim (p+2)_after)
      }[f;ls] each idx}

assertions:{[] raze extract each sources[]}

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
