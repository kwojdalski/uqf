// test_config_audit.q - the runtime configuration audit trail (.cfgatest).
//
// The thing under test is a DIFFERENCE ENGINE, so most of these assert
// state across two calls rather than the result of one: the property that
// matters is "reports a change exactly once", and a single call cannot
// show it.

\d .cfgatest

/ Two globals of this namespace's own to watch, so no test depends on a
/ real job's tunables staying the values they are today.
a_number:42
a_span:0D00:00:02

beforeNamespace_load:{[]
    `.cfgatest.saved_watched set .qcfgaudit.watched;
    `.cfgatest.saved_seen set .qcfgaudit.seen;
    }

afterNamespace_restore:{[]
    `.qcfgaudit.watched set .cfgatest.saved_watched;
    `.qcfgaudit.seen set .cfgatest.saved_seen;
    }

reset:{[]
    `.qcfgaudit.watched set (`symbol$())!();
    .qcfgaudit.forget[];
    `.cfgatest.a_number set 42;
    `.cfgatest.a_span set 0D00:00:02;
    / unset, not re-set: one test asserts what happens when a watched name
    / APPEARS, which it cannot do twice if the previous run left it behind.
    if[`appears_later in key `.cfgatest; ![`.cfgatest;();0b;enlist `appears_later]];
    }

t0:2026.09.19D12:00:00.000000000

/ --- registration ---------------------------------------------------------

test_a_bare_name_is_refused:{[t]
    reset[];
    / `notional` on its own resolves against whatever namespace is current
    / when the timer fires, which is not the one the author meant.
    .qunit.assertThrows[{.qcfgaudit.watch[`j;x]};`a_number;
        "*fully qualified*";
        "an unqualified name would silently audit the wrong variable"]};

test_registering_twice_does_not_duplicate:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.a_number];
    .qcfgaudit.watch[`j;`.cfgatest.a_number`.cfgatest.a_span];
    .qunit.assertEquals[asc .qcfgaudit.watching[`j];
        asc `.cfgatest.a_number`.cfgatest.a_span;
        "re-registering adds the new name and keeps one copy of the old"]};

test_an_owner_that_declares_nothing_watches_nothing:{[t]
    reset[];
    .qunit.assertEquals[count .qcfgaudit.watching[`never_registered];0;
        "and is not an error - most jobs have no tunables"]};

/ --- the difference engine ------------------------------------------------

test_the_first_poll_records_the_starting_value:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.a_number];
    r:first .qcfgaudit.poll[`j;t0];
    .qunit.assertEquals[r`new;"42";"the value the process started with"];
    .qunit.assertEquals[r`old;"";
        "with no previous value - a log that only records later edits cannot say what they were edits from"]};

test_an_unchanged_value_is_not_reported_again:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.a_number];
    .qcfgaudit.poll[`j;t0];
    .qunit.assertEquals[count .qcfgaudit.poll[`j;t0+0D00:00:05];0;
        "a timer that republished every tick would be a sampler, not an audit log"]};

test_a_change_is_reported_once_with_both_values:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.a_number];
    .qcfgaudit.poll[`j;t0];
    `.cfgatest.a_number set 99;
    r:first .qcfgaudit.poll[`j;t0+0D00:00:05];
    .qunit.assertEquals[r`old;"42";"what it was"];
    .qunit.assertEquals[r`new;"99";"and what it became"];
    .qunit.assertEquals[count .qcfgaudit.poll[`j;t0+0D00:00:10];0;
        "and not again on the next tick"]};

test_a_non_float_type_survives_the_round_trip:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.a_span];
    r:first .qcfgaudit.poll[`j;t0];
    .qunit.assertEquals[r`new;"0D00:00:02.000000000";
        "one column holds every config type, so values are rendered rather than cast"]};

test_only_the_named_owner_is_polled:{[t]
    reset[];
    .qcfgaudit.watch[`mine;`.cfgatest.a_number];
    .qcfgaudit.watch[`theirs;`.cfgatest.a_span];
    r:.qcfgaudit.poll[`mine;t0];
    .qunit.assertEquals[count r;1;"one owner's poll reports one owner's config"];
    .qunit.assertEquals[(first r)`name;`.cfgatest.a_number;
        "every job is loaded into every process, so polling all owners would report fourteen jobs this process is not running"]};

/ --- a name that is not there ---------------------------------------------

test_an_undefined_name_is_recorded_rather_than_thrown:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.nothing.is.here];
    r:first .qcfgaudit.poll[`j;t0];
    .qunit.assertEquals[r`new;"(undefined)";
        "a typo in a watch declaration must not take down the timer that carries it"]};

test_a_name_that_appears_later_is_reported_as_a_change:{[t]
    reset[];
    .qcfgaudit.watch[`j;`.cfgatest.appears_later];
    .qcfgaudit.poll[`j;t0];
    `.cfgatest.appears_later set 7;
    r:first .qcfgaudit.poll[`j;t0+0D00:00:05];
    .qunit.assertEquals[r`old;"(undefined)";"it was not there"];
    / `enlist "7"`, not `"7"`: a single character is an ATOM in q and a
    / two-character one is a list, so the obvious literal here compares a
    / char against the one-element string render actually returns.
    .qunit.assertEquals[r`new;enlist "7";"and then it was"]};

/ --- the publisher seam ---------------------------------------------------

test_the_publisher_is_niladic_and_quiet_when_nothing_moved:{[t]
    reset[];
    .qunit.assertEquals[.qcfgaudit.publisher[`never_registered][];();
        "a job with no watched config runs the timer and publishes nothing"]};

test_the_publisher_sends_through_the_jobs_own_seam:{[t]
    reset[];
    `.cfgatest.sent set ();
    .qcfgaudit.watch[`cross_arbitrage;`.cfgatest.a_number];
    .qstream.wire[`cross_arbitrage;{[tbl;rows] `.cfgatest.sent set (tbl;rows)}];
    .qcfgaudit.publisher[`cross_arbitrage][];
    .qunit.assertEquals[first .cfgatest.sent;`config_change;
        "onto the table the job declares, through the publisher the runner wired"];
    .qunit.assertEquals[count last .cfgatest.sent;1;"carrying the one change"]};

/ --- what the jobs actually declare ---------------------------------------

test_the_watched_names_exist:{[t]
    / A watch declaration that names nothing is not an error at runtime -
    / it records "(undefined)" forever - so it has to be caught here.
    reset[];
    `.qcfgaudit.watched set .cfgatest.saved_watched;
    declared:raze value .qcfgaudit.watched;
    missing:declared where {[n] `undefined~@[get;n;`undefined]} each declared;
    .qunit.assertEquals[count missing;0;
        "every name a job declares as configuration resolves to something"]};

test_every_watching_job_declares_config_change_as_an_output:{[t]
    / The timer publishes onto config_change through the job's own seam, so
    / a job that watches config and does not declare the table would be
    / publishing something its own registration denies - and
    / verify_pipeline_edges reads that registration.
    reset[];
    `.qcfgaudit.watched set .cfgatest.saved_watched;
    owners:key .qcfgaudit.watched;
    owners:owners where owners in .qstream.registered[];
    bad:owners where not {[j] `config_change in (.qstream.declaration j)`publishes} each owners;
    .qunit.assertEquals[count bad;0;
        "a job that audits its config must declare config_change among its publishes"]};
