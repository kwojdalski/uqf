// test_torq_delivery.q - the TorQ half of a polling feed's delivery (#1651):
// reopening a tickerplant handle that closed, the plant's logged count, and
// the rdb's answer to which keys it holds (.qt.qtorq.delivery).
//
// No stack is started. A closed handle is a real one - an int q does not
// hold open - and what reopens it is TorQ's registry lookup, stood in for
// and put back. The plant and the rdb are functions called with the message
// a handle would be sent, evaluated here.


/ What a stand-in plant received.
.qt.qtorq.delivery.got:()

/ A stand-in plant: records every message, answers like .u.upd would.
.qt.qtorq.delivery.plant:{[msg] .qt.qtorq.delivery.got,:enlist msg; ::}

/ A handle q does not hold: sending on it throws, as on one a restarted
/ plant closed.
.qt.qtorq.delivery.closed:999i

/ The global a publisher holds its handle in, as .qproc.stream.h.
.qt.qtorq.delivery.h:0Ni

/ Run f with .servers.gethandlebytype answering `answer` (and counting the
/ calls), putting back whatever .servers held.
.qt.qtorq.delivery.with_registry:{[answer;f]
    had:@[{key x};`.servers;{[e] `symbol$()}];
    keep:$[`gethandlebytype in had; (1b;.servers.gethandlebytype); (0b;::)];
    `.qt.qtorq.delivery.lookups set 0;
    `.servers.gethandlebytype set {[answer;ptype;sel] `.qt.qtorq.delivery.lookups set 1+.qt.qtorq.delivery.lookups; answer}[answer];
    r:@[f;::;{[e] (`threw;e)}];
    $[first keep; `.servers.gethandlebytype set last keep; ![`.servers;();0b;enlist `gethandlebytype]];
    r}

.qt.qtorq.delivery.setUp_fresh:{[]
    `.qt.qtorq.delivery.got set ();
    `.qt.qtorq.delivery.h set .qt.qtorq.delivery.closed;
    }

/ --- reconnecting -----------------------------------------------------------

.qt.qtorq.delivery.test_a_closed_handle_is_reopened_and_the_publish_lands:{[t]
    r:.qt.qtorq.delivery.with_registry[.qt.qtorq.delivery.plant;
        {[unused] .qtorq.publish_reconnecting[`.qt.qtorq.delivery.h;`trades;([] sym:`a`b; px:1 2f)]}];
    .qunit.assertEquals[r;2;"the rows were published"];
    .qunit.assertEquals[first .qt.qtorq.delivery.got;(`.u.upd;`trades;(`a`b;1 2f));"onto the reopened plant"];
    .qunit.assertEquals[(.qt.qtorq.delivery.h;.qt.qtorq.delivery.lookups);(.qt.qtorq.delivery.plant;1);
        "through one registry lookup, and the global now holds the new handle"]};

.qt.qtorq.delivery.test_the_reconnect_is_logged:{[t]
    lines:.qt.support.captured_log[0b] {[unused]
        .qt.qtorq.delivery.with_registry[.qt.qtorq.delivery.plant;
            {[unused] .qtorq.publish_reconnecting[`.qt.qtorq.delivery.h;`trades;([] sym:enlist `a; px:enlist 1f)]}]};
    .qunit.assertEquals[lines[;2] where `WARNING=lines[;0];
        ("tickerplant handle closed - reopening it before sending";"tickerplant handle reopened");
        "why it reopened, and that it did"]};

.qt.qtorq.delivery.test_a_plant_that_cannot_be_reached_publishes_nothing_and_says_so:{[t]
    r:.qt.qtorq.delivery.with_registry[();
        {[unused] .qtorq.publish_reconnecting[`.qt.qtorq.delivery.h;`trades;([] sym:enlist `a; px:enlist 1f)]}];
    .qunit.assertEquals[r;(`threw;"qtorq.reopen: no segmentedtickerplant could be reached through .servers - the handle stays closed and nothing was published");
        "the error propagates, so the tick fails and the cursor stays"];
    .qunit.assertEquals[(.qt.qtorq.delivery.h;count .qt.qtorq.delivery.got);(.qt.qtorq.delivery.closed;0);"and the handle is left as it was"]};

.qt.qtorq.delivery.test_an_error_on_an_open_handle_is_not_a_reconnect:{[t]
    `.qt.qtorq.delivery.h set {[msg] '"type"};
    r:.qt.qtorq.delivery.with_registry[.qt.qtorq.delivery.plant;
        {[unused] .qtorq.publish_reconnecting[`.qt.qtorq.delivery.h;`trades;([] sym:enlist `a; px:enlist 1f)]}];
    .qunit.assertEquals[(r;.qt.qtorq.delivery.lookups;count .qt.qtorq.delivery.got);((`threw;"type");0;0);
        "the plant's own error, raised as it is, with no lookup and no second send"]};

/ The plant dies while the call is in flight: reopened, sent again, and the
/ resend flagged - the old plant may have logged the first one.
.qt.qtorq.delivery.test_a_handle_lost_mid_call_is_reopened_and_the_resend_flagged:{[t]
    `.qt.qtorq.delivery.h set {[msg] '"closed"};
    keep:.qtorq.handle_open;
    `.qt.qtorq.delivery.asked set 0;
    / open when the call starts, gone once it has failed
    .qtorq.handle_open:{[h] `.qt.qtorq.delivery.asked set 1+.qt.qtorq.delivery.asked; 1=.qt.qtorq.delivery.asked};
    lines:.qt.support.captured_log[0b] {[unused]
        `.qt.qtorq.delivery.r set .qt.qtorq.delivery.with_registry[.qt.qtorq.delivery.plant;
            {[unused] .qtorq.publish_reconnecting[`.qt.qtorq.delivery.h;`trades;([] sym:enlist `a; px:enlist 1f)]}]};
    .qtorq.handle_open:keep;
    .qunit.assertEquals[(.qt.qtorq.delivery.r;count .qt.qtorq.delivery.got);(1;1);"sent once more, on the new plant"];
    .qunit.assertTrue[any (lines[;2] where `WARNING=lines[;0]) like "*sending again, possibly duplicated";"and said to be possibly duplicated"]};

.qt.qtorq.delivery.test_an_ipc_handle_q_does_not_hold_is_closed:{[t]
    .qunit.assertEquals[(.qtorq.handle_open[999i];.qtorq.handle_open[{x}]);01b;"a function standing in for a plant counts as open"]};

/ --- the plant's logged count ------------------------------------------------

/ A stand-in plant handle that evaluates what it is sent here, against
/ .stplg counters set for the test.
.qt.qtorq.delivery.local:{[msg] (first msg) . 1_msg}

.qt.qtorq.delivery.with_stplg:{[mode;rc;tmp;f]
    had:`stplg in key `;
    keep:$[had; get `.stplg; ::];
    `.stplg.batchmode`.stplg.rowcount`.stplg.tmprowcount set' (mode;rc;tmp);
    r:@[f;::;{[e] (`threw;e)}];
    $[had; `.stplg set keep; ![`.;();0b;enlist `stplg]];
    r}

.qt.qtorq.delivery.test_defaultbatch_counts_rows_logged_since_the_last_timer:{[t]
    r:.qt.qtorq.delivery.with_stplg[`defaultbatch;`trades`quote!5 7;enlist[`trades]!enlist 3;
        {[unused] .qtorq.logged_rows[.qt.qtorq.delivery.local;`trades]}];
    .qunit.assertEquals[r;8;"5 published and counted, 3 logged and awaiting the timer"]};

.qt.qtorq.delivery.test_a_table_the_plant_has_not_counted_has_logged_none:{[t]
    r:.qt.qtorq.delivery.with_stplg[`defaultbatch;enlist[`quote]!enlist 7;()!();
        {[unused] .qtorq.logged_rows[.qt.qtorq.delivery.local;`trades]}];
    .qunit.assertEquals[r;0;"zero, not null, and not the empty dict's ()"]};

.qt.qtorq.delivery.test_immediate_mode_counts_rowcount_alone:{[t]
    r:.qt.qtorq.delivery.with_stplg[`immediate;enlist[`trades]!enlist 5;enlist[`trades]!enlist 99;
        {[unused] .qtorq.logged_rows[.qt.qtorq.delivery.local;`trades]}];
    .qunit.assertEquals[r;5;"immediate counts as it logs; tmprowcount is not its"]};

.qt.qtorq.delivery.test_memorybatch_cannot_acknowledge_a_publish:{[t]
    r:.qt.qtorq.delivery.with_stplg[`memorybatch;enlist[`trades]!enlist 5;()!();
        {[unused] .qtorq.logged_rows[.qt.qtorq.delivery.local;`trades]}];
    .qunit.assertEquals[r;(`threw;"qtorq.logged_rows: the tickerplant is in memorybatch - it logs only on its timer, so a publish cannot be acknowledged; turn the poll's ack off");
        "refused, naming why"]};

/ --- the rdb's answer --------------------------------------------------------

.qt.qtorq.delivery.test_present_keys_matches_rows_on_the_key_columns:{[t]
    `qtdeliverytbl set ([] time:3#.z.p; id:`a`b`c; v:1 2 3f);
    r:.qtorq.present_keys[.qt.qtorq.delivery.local;`qtdeliverytbl;([] id:`b`x`c)];
    ![`.;();0b;enlist `qtdeliverytbl];
    .qunit.assertEquals[r;101b;"b and c are held, x is not"]};

.qt.qtorq.delivery.test_a_table_the_rdb_does_not_define_holds_no_keys:{[t]
    .qunit.assertEquals[.qtorq.present_keys[.qt.qtorq.delivery.local;`qtdelivery_nosuch;([] id:`a`b)];00b;
        "nothing is held, so every row is published"]};

/ Run f with .servers' getservers, registerfromdiscovery and gethandlebytype
/ stood in for - rdbs registered or not, a handle or none - recording what
/ was asked, and put back afterwards.
.qt.qtorq.delivery.with_rdbs:{[registered;answer;f]
    names:`getservers`registerfromdiscovery`gethandlebytype;
    had:@[{key x};`.servers;{[e] `symbol$()}];
    keep:{[had;n] $[n in had; (1b;.servers n); (0b;::)]}[had] each names;
    `.qt.qtorq.delivery.asked set ();
    `.servers.getservers set {[registered;field;wanted;attrs;autoopen;onlyone]
        .qt.qtorq.delivery.asked,:enlist (`getservers;wanted); ([] w:registered#0i)}[registered];
    `.servers.registerfromdiscovery set {[procs;connect] .qt.qtorq.delivery.asked,:enlist (`registerfromdiscovery;procs);};
    `.servers.gethandlebytype set {[answer;ptype;sel] .qt.qtorq.delivery.asked,:enlist (`gethandlebytype;ptype); answer}[answer];
    r:@[f;::;{[e] (`threw;e)}];
    {[nm;k] $[first k; (` sv `.servers,nm) set last k; ![`.servers;();0b;enlist nm]]}'[names;keep];
    r}

.qt.qtorq.delivery.test_an_rdb_is_registered_from_discovery_only_when_none_is:{[t]
    r:.qt.qtorq.delivery.with_rdbs[0;7i;{[unused] .qtorq.reader_handle[]}];
    .qunit.assertEquals[(r;.qt.qtorq.delivery.asked[;0]);(7i;`getservers`registerfromdiscovery`gethandlebytype);
        "asked of discovery the first time, then the handle"];
    r:.qt.qtorq.delivery.with_rdbs[1;7i;{[unused] .qtorq.reader_handle[]}];
    .qunit.assertEquals[(r;.qt.qtorq.delivery.asked[;0]);(7i;`getservers`gethandlebytype);"not again once one is registered"]};

.qt.qtorq.delivery.test_no_rdb_to_ask_is_an_error_not_a_guess:{[t]
    r:.qt.qtorq.delivery.with_rdbs[0;();{[unused] .qtorq.reader_handle[]}];
    .qunit.assertEquals[r;(`threw;"qtorq.reader_handle: no rdb could be reached to ask which rows of an in-doubt page are published - nothing was published");
        "so the tick fails and the page waits"]};
