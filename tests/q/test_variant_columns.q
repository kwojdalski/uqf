/ test_variant_columns.q - a reference's declared columns and match conditions (src/etl/core/reference_variant.q, variant_run.q, #3000).
/ .
/ A transform declares the market_data columns it reads (`needs`); a variant's SELECT may carry `match`: equality keys of the
/ declared sort, and conditions the plant table declares (.qetl.plant.condition), answered by a stored pointer column. Held here, each to a brute force or a reference value:
/   * what market_data stores, as the registry checks against it, is the plant's own declaration
/   * a registration that names a column market_data does not have, a column that is not stored flat, an operand of the wrong
/     type or a condition the as-of column cannot take is refused, by name and with the words below
/   * a variant with a match (a declared condition, or an equality key) prices exactly what a scan of the books gives - the latest passing book of each source inside
/     the staleness, null where none passes - and a transform that declares an extra column gets exactly it, through the
/     gather over a stand-in for the mapped market and through the in-memory books alike
/   * columns and match are part of the stored definition, so a changed one is a changed configuration
/   * the live path refuses, by name, a buffer that does not carry what a variant declares

/ Books of three sources, a unique time per row, a tenth of each side withdrawn, two markets - as an HDB holds them.
.qt.qetl.variant.columns.market:{[n;seed]
    system "S ",string seed;
    t:2026.09.17D10:00:00+400000000*(neg n)?n;
    rows:([] sym:n?`EURUSD`GBPUSD; source:n?`a`b`c; source_time:t; time:t; market:n?`fx`crypto; clock:n#`venue;
        bid:{?[x<0.1;0n;1+x]} n?1f; ask:{?[x<0.1;0n;2+x]} n?1f);
    md:.qt.support.stand_in_market_data rows;
    / one date, so a row's position is its rid: the pointer the writer would have stored for the condition two_sided
    update two_sided_rid:.qetl.mapped.pointer[(sym;source);(not null bid) & not null ask] from md}

/ The books as the core takes them: the flat top, arranged - and the extra columns a variant declares.
.qt.qetl.variant.columns.books:{[md] .qetl.mapped.arrange[`market_data] select sym, source, source_time, bid, ask, clock, market, time from md}

/ Run f . args with `market_data` standing in for the mapped table, and put the root back.
.qt.qetl.variant.columns.with_market:{[md;f;args]
    had:$[`market_data in key `.; (1b;get `market_data); (0b;::)];
    `market_data set md;
    r:.[f;args;{[e] `threw,e}];
    $[first had; `market_data set last had; ![`.;();0b;enlist `market_data]];
    if[`threw~first r; 'last r];
    r}

/ Take test variants and transforms out of the registries again.
.qt.qetl.variant.columns.forget:{[variants;transforms]
    `.qetl.reference.variants set variants _ .qetl.reference.variants;
    `.qetl.reference.transforms set transforms _ .qetl.reference.transforms;}

/ The targets of a test: some syms at some instants, on the venue clock.
.qt.qetl.variant.columns.targets:{[m;seed]
    system "S ",string seed;
    ([] sym:m?`EURUSD`GBPUSD`NOBOOK; time:2026.09.17D10:00:00+m?600000000000; clock:m#`venue)}

/ The price under a variant by a scan: of each source of the sym, the latest book at or before the instant and no older than
/ `stale` that `ok` accepts; the best bid, the lowest ask and their mid across the sources.
.qt.qetl.variant.columns.brute:{[tob;targets;stale;ok]
    keep:ok tob;
    one:{[tob;keep;stale;g]
        per:{[tob;keep;stale;g;s]
            m:keep & ((tob`sym)=g`sym) & ((tob`source)=s) & ((tob`source_time)<=g`time) & (tob`source_time)>=g[`time]-stale;
            r:select from tob where m;
            $[count r; last r; ()]}[tob;keep;stale;g] each distinct tob`source;
        per:per where 0<count each per;
        bids:{x where not null x} per@\:`bid;
        asks:{x where not null x} per@\:`ask;
        bid:$[count bids; max bids; 0n];
        ask:$[count asks; min asks; 0n];
        `bid`ask`mid!(bid;ask;$[null bid; 0n; 0.5*bid+ask])}[tob;keep;stale];
    rows:one each targets;
    ([] bid:rows@\:`bid; ask:rows@\:`ask; mid:rows@\:`mid)}

/ --- what market_data stores -----------------------------------------------------------------------------

.qt.qetl.variant.columns.test_what_the_registry_checks_against_is_the_plants_own_declaration:{[t]
    m:meta .qetl.plant.market_data;
    .qunit.assertEquals[asc key .qetl.mapped.stored;asc exec c from 0!m;"the same columns"];
    flat:exec c from 0!m where not t=" ";
    .qunit.assertEquals[(.qetl.mapped.stored flat);"h"$.Q.t?exec t from 0!m where not t=" ";"the same types, flat column by flat column"];
    .qunit.assertEquals[asc .qetl.mapped.nested;asc exec c from 0!m where t=" ";"and the same columns that are lists"]}

/ --- registration is refused by name ---------------------------------------------------------------------

.qt.qetl.variant.columns.test_a_transform_that_declares_a_column_market_data_lacks_is_refused_by_name:{[t]
    f:{[rows;params] ([] bid:rows`bid; ask:rows`ask)};
    .qunit.assertThrows[{[a] .qetl.reference.add_transform[`eg_nope;a 0;(`symbol$())!();a 1]};(`bid`ask`nope;f);"reference: transform eg_nope reads nope, which the market table does not have - it has *";"the column is named"];
    .qunit.assertThrows[{[a] .qetl.reference.add_transform[`eg_nope;a 0;(`symbol$())!();a 1]};(`bid`nope`also_nope;f);"reference: transform eg_nope reads nope, also_nope, which the market table does not have*";"every column it lacks"];
    .qunit.assertThrows[{[a] .qetl.reference.add_transform[`eg_nope;a 0;(`symbol$())!();a 1]};(`top;f);"reference: transform eg_nope must declare the columns it reads as a non-empty symbol list";"`top and `depth are no longer a declaration"];
    .qunit.assertThrows[{[a] .qetl.reference.add_transform[`eg_nope;a 0;(`symbol$())!();a 1]};(`bid`bid;f);"reference: transform eg_nope declares a column twice";"a column is declared once"];
    .qunit.assertFalse[`eg_nope in key .qetl.reference.transforms;"and nothing was registered"]}

.qt.qetl.variant.columns.test_a_match_on_anything_but_a_declared_condition_or_a_key_is_refused_by_name:{[t]
    d:`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best);
    reg:{[d;m] .qetl.reference.register[`eg_bad_match;d,enlist[`match]!enlist m]}[d];
    undeclared:"reference: eg_bad_match: match on ";
    .qunit.assertThrows[reg;enlist[`bid]!enlist 1.5;undeclared,"bid, which is neither a declared condition (two_sided) nor an equality key (sym, source) - declare it with .qetl.plant.condition so the writer stores its pointer";"a stored column that is no key and no declared condition"];
    .qunit.assertThrows[reg;enlist[`clock]!enlist `venue;undeclared,"clock, which is neither a declared condition*";"another flat column: its condition must be declared"];
    .qunit.assertThrows[reg;enlist[`nope]!enlist 1b;undeclared,"nope, which is neither a declared condition*";"a name nothing declares"];
    .qunit.assertThrows[reg;enlist[`bid_prices]!enlist 1b;undeclared,"bid_prices, which is neither a declared condition*";"a ladder"];
    .qunit.assertThrows[reg;enlist[`source_time]!enlist 2026.09.17D10:00:00;undeclared,"source_time, which is neither a declared condition*";"the as-of column is not matched"];
    .qunit.assertThrows[reg;enlist[`two_sided]!enlist 0b;"reference: eg_bad_match: match on condition two_sided must be 1b: the latest book that passes it";"a condition must hold"];
    .qunit.assertThrows[reg;enlist[`source]!enlist 5;"reference: eg_bad_match: match on source must hold a value of the key's own type";"an equality key's operand has its type"];
    .qunit.assertThrows[reg;3;"reference: eg_bad_match: match must be a dictionary of column to condition";"a match is a dictionary"];
    .qunit.assertFalse[`eg_bad_match in key .qetl.reference.variants;"and nothing was registered"]}

/ --- a match is part of the definition -------------------------------------------------------------------

.qt.qetl.variant.columns.test_columns_and_match_are_part_of_the_stored_definition_so_they_change_the_configuration:{[t]
    d:`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best);
    .qetl.reference.register[`eg_cfg_plain;d];
    .qetl.reference.register[`eg_cfg_match;d,enlist[`match]!enlist enlist[`two_sided]!enlist 1b];
    .qetl.reference.register[`eg_cfg_other;d,enlist[`match]!enlist enlist[`source]!enlist `a`b];
    ps:.qetl.reference.describe`eg_cfg_plain;
    ms:.qetl.reference.describe`eg_cfg_match;
    os:.qetl.reference.describe`eg_cfg_other;
    .qt.qetl.variant.columns.forget[`eg_cfg_plain`eg_cfg_match`eg_cfg_other;`symbol$()];
    .qunit.assertEquals[ps`needs;`bid`ask;"the transform's declared columns are in the definition"];
    .qunit.assertEquals[ps`match;(`symbol$())!();"a variant without a match has the empty one"];
    .qunit.assertEquals[ms`match;enlist[`two_sided]!enlist 1b;"the match is stored"];
    .qunit.assertTrue[not (`name _ ps)~`name _ ms;"a match is a different variant"];
    .qunit.assertTrue[not (`name _ ms)~`name _ os;"and so is another match"];
    .qunit.assertEquals[(.qetl.reference.describe`best_top)`match;(`symbol$())!();"variant #1 matches nothing"]}

/ --- a variant with a match prices what a scan prices ----------------------------------------------------

.qt.qetl.variant.columns.test_a_two_sided_variant_prices_the_latest_passing_book_of_each_source_inside_its_staleness:{[t]
    md:.qt.qetl.variant.columns.market[1500;51];
    tob:.qt.qetl.variant.columns.books md;
    tg:.qt.qetl.variant.columns.targets[500;52];
    two:enlist[`two_sided]!enlist 1b;
    d:`venues`stale`offset`transform`combine!(`all;0D00:00:06;0D;`top;`best);
    .qetl.reference.register[`eg_two_sided;d,enlist[`match]!enlist two];
    .qetl.reference.register[`eg_latest;d];
    got:.qetl.reference.at[`eg_two_sided;tob;0D00:00:05;tg`sym;tg`time;tg`clock];
    plain:.qetl.reference.at[`eg_latest;tob;0D00:00:05;tg`sym;tg`time;tg`clock];
    .qt.qetl.variant.columns.forget[`eg_two_sided`eg_latest;`symbol$()];
    want:.qt.qetl.variant.columns.brute[tob;tg;0D00:00:06;{[r] (not null r`bid) & not null r`ask}];
    .qunit.assertEquals[(got`same)`mid;want`mid;"the mid of the latest passing books"];
    .qunit.assertEquals[(got`same)`bid;want`bid;"the best bid"];
    .qunit.assertEquals[(got`same)`ask;want`ask;"the best ask"];
    .qunit.assertTrue[any (not null (got`same)`mid) & null (plain`same)`mid;"a book that does not quote both sides is passed over for an earlier one"];
    .qunit.assertTrue[any (null (got`same)`mid) & (tg`sym)<>`NOBOOK;"and where nothing in the window passes there is no price"];
    .qunit.assertTrue[all (null (got`same)`mid) | not null (got`same)`ask;"a priced instant has both sides"]}

.qt.qetl.variant.columns.test_best_top_is_still_the_variant_it_was:{[t]
    md:.qt.qetl.variant.columns.market[1500;53];
    tob:.qt.qetl.variant.columns.books md;
    tg:.qt.qetl.variant.columns.targets[500;54];
    got:.qetl.reference.at[`best_top;tob;0D00:00:05;tg`sym;tg`time;tg`clock];
    want:.qt.qetl.variant.columns.brute[tob;tg;0D00:00:05;{[r] (count r)#1b}];
    .qunit.assertEquals[(got`same)`mid;want`mid;"variant #1 over books that now carry more columns"]}

/ --- a transform's declared columns, through the gather --------------------------------------------------

.qt.qetl.variant.columns.test_a_transform_declaring_an_extra_column_gets_exactly_it_and_a_match_agrees_with_the_books_in_memory:{[t]
    md:.qt.qetl.variant.columns.market[1500;55];
    tob:.qt.qetl.variant.columns.books md;
    tg:.qt.qetl.variant.columns.targets[400;56];
    / reads market: the bid counts only on the fx market
    .qetl.reference.add_transform[`eg_fx_bid;`bid`ask`market;(`symbol$())!();{[rows;params] ([] bid:?[(rows`market)=`fx; rows`bid; 0n]; ask:rows`ask)}];
    d:`venues`stale`offset`transform`combine!(`all;0D00:00:06;0D;`eg_fx_bid;`best);
    .qetl.reference.register[`eg_fx;d,enlist[`match]!enlist enlist[`two_sided]!enlist 1b];
    .qetl.reference.register[`eg_plain;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)];
    names:`eg_fx`eg_plain;
    probes:select sym, time from tg;
    r:.qt.qetl.variant.columns.with_market[md;{[a] .qetl.variant.read[{value x};a 0;2026.09.17D09:00:00;1;a 1;0D00:00:06]};enlist (probes;names)];
    sl:r`slice;
    inmem:.qetl.variant.evaluate[names;.qetl.variant.slice tob;0D00:00:06;tg`sym;tg`time;tg`clock];
    gathered:.qetl.variant.evaluate[names;sl;0D00:00:06;tg`sym;tg`time;tg`clock];
    .qt.qetl.variant.columns.forget[names;`eg_fx_bid];
    .qunit.assertEquals[asc cols sl;asc `sym`source`source_time`bid`ask`clock`rid`market`two_sided_rid;"the flat top, exactly the declared column (not time, not a ladder) and the slice's own pointer for the condition"];
    .qunit.assertEquals[r`reads;count sl;"read for the matched books and no others"];
    .qunit.assertTrue[(count sl)<count tob;"the slice is the matched books, not the market: ",string[count sl]," of ",string count tob];
    .qunit.assertEquals[gathered`eg_fx;inmem`eg_fx;"the variant with a match and a declared column prices the same through the gather as over every book"];
    .qunit.assertEquals[gathered`eg_plain;inmem`eg_plain;"and a variant without either is unchanged beside it"];
    want:.qt.qetl.variant.columns.brute[update bid:?[market=`fx;bid;0n] from tob;tg;0D00:00:06;{[orig;r] (not null orig`bid) & not null orig`ask}[tob]];
    .qunit.assertEquals[((inmem`eg_fx)`same)`mid;want`mid;"and equals a scan"]}

.qt.qetl.variant.columns.test_the_gather_reads_the_union_of_the_columns_variants_declare:{[t]
    md:.qt.qetl.variant.columns.market[900;57];
    tg:.qt.qetl.variant.columns.targets[200;58];
    .qetl.reference.add_transform[`eg_time;`bid`ask`time;(`symbol$())!();{[rows;params] ([] bid:rows`bid; ask:rows`ask)}];
    .qetl.reference.add_transform[`eg_mkt;`bid`ask`market;(`symbol$())!();{[rows;params] ([] bid:rows`bid; ask:rows`ask)}];
    d:`venues`stale`offset`combine!(`all;`spec;0D;`best);
    .qetl.reference.register[`eg_u1;d,enlist[`transform]!enlist `eg_time];
    .qetl.reference.register[`eg_u2;d,enlist[`transform]!enlist `eg_mkt];
    one:{[a] .qetl.variant.read[{value x};a 0;2026.09.17D09:00:00;1;a 1;0D00:00:06]};
    pr:select sym, time from tg;
    a:.qt.qetl.variant.columns.with_market[md;one;enlist (pr;enlist `eg_u1)];
    b:.qt.qetl.variant.columns.with_market[md;one;enlist (pr;enlist `eg_u2)];
    u:.qt.qetl.variant.columns.with_market[md;one;enlist (pr;`eg_u1`eg_u2)];
    .qt.qetl.variant.columns.forget[`eg_u1`eg_u2;`eg_time`eg_mkt];
    .qunit.assertTrue[(`time in cols a`slice) and not `market in cols a`slice;"the first declares time"];
    .qunit.assertTrue[(`market in cols b`slice) and not `time in cols b`slice;"the second market"];
    .qunit.assertEquals[asc cols u`slice;asc distinct (cols a`slice),cols b`slice;"two variants: the union of their columns"];
    .qunit.assertEquals[u`reads;count u`slice;"each read once, for the books of both"]}

.qt.qetl.variant.columns.test_a_promoted_variants_weight_is_not_stored_in_the_market_and_is_refused_by_name:{[t]
    .qetl.reference.register[`eg_pr_src;`venues`stale`offset`transform`combine!(`all;`spec;0D;`mid_sized;`wmean)];
    .qetl.variant.promote`eg_pr_src;
    md:.qt.qetl.variant.columns.market[100;59];
    r:@[{.qt.qetl.variant.columns.with_market[x;{[a;u] @[{.qetl.variant.read[{value x};x;2026.09.17D09:00:00;1;enlist `eg_pr_src_flat;0D00:00:06]};a;{[e] e}]};(([] sym:enlist `EURUSD; time:enlist 2026.09.17D10:00:10);::)]};md;{[e] e}];
    .qt.qetl.variant.columns.forget[`eg_pr_src`eg_pr_src_flat;`symbol$()];
    .qunit.assertTrue[r like "variant: w is read by eg_pr_src_flat but the mapped market does not store it*";"the weight is named, with the variant that reads it: ",r]}

/ --- the live path -------------------------------------------------------------------------------------

.qt.qetl.variant.columns.test_the_live_path_refuses_a_buffer_that_lacks_a_declared_column_by_name:{[t]
    md:.qt.qetl.variant.columns.market[600;60];
    tg:.qt.qetl.variant.columns.targets[100;61];
    .qetl.reference.add_transform[`eg_mkt2;`bid`ask`market;(`symbol$())!();{[rows;params] ([] bid:?[(rows`market)=`fx; rows`bid; 0n]; ask:rows`ask)}];
    d:`venues`stale`offset`transform`combine!(`all;`spec;0D;`eg_mkt2;`best);
    .qetl.reference.register[`eg_live_a;d];
    .qetl.reference.register[`eg_live_b;`venues`stale`offset`transform`combine`match!(`all;`spec;0D;`top;`best;enlist[`source]!enlist `a`b`c)];
    full:select sym, source, source_time, bid, ask, clock, market from md;
    live:.qetl.variant.live[`eg_live_a`eg_live_b;full;0D00:00:06;tg`sym;tg`time;tg`clock];
    gathered:.qetl.variant.evaluate[`eg_live_a`eg_live_b;.qetl.variant.slice full;0D00:00:06;tg`sym;tg`time;tg`clock];
    .qt.qetl.variant.columns.forget[`eg_live_a`eg_live_b;`eg_mkt2];
    .qunit.assertEquals[live;gathered;"a buffer that carries what the variants declare prices what the gathered books do"]}

.qt.qetl.variant.columns.test_the_live_buffer_lacking_a_column_is_named_with_the_variants_that_read_it:{[t]
    md:.qt.qetl.variant.columns.market[100;62];
    tg:.qt.qetl.variant.columns.targets[10;63];
    .qetl.reference.add_transform[`eg_mkt3;`bid`ask`market;(`symbol$())!();{[rows;params] ([] bid:rows`bid; ask:rows`ask)}];
    d:`venues`stale`offset`transform`combine!(`all;`spec;0D;`eg_mkt3;`best);
    .qetl.reference.register[`eg_lv_a;d];
    .qetl.reference.register[`eg_lv_b;`venues`stale`offset`transform`combine`match!(`all;`spec;0D;`top;`best;enlist[`source]!enlist `a`b`c)];
    thin:select sym, source, source_time, bid, clock from md;
    f:{[a] .qetl.variant.live[`eg_lv_a`eg_lv_b;a 0;0D00:00:06;a[1]`sym;a[1]`time;a[1]`clock]};
    .qunit.assertThrows[f;(thin;tg);"variant: the live buffer lacks ask, market, read by eg_lv_a, eg_lv_b - a live job's buffer must carry every column its variants declare";"the missing columns, and who reads them"];
    .qt.qetl.variant.columns.forget[`eg_lv_a`eg_lv_b;`eg_mkt3]}

/ --- a policy built from a matched variant keeps the match -------------------------------------------------

.qt.qetl.variant.columns.test_a_policy_rule_built_from_a_matched_variant_carries_its_match:{[t]
    d:`venues`stale`offset`transform`combine`match!(`all;`spec;0D;`top;`best;enlist[`two_sided]!enlist 1b);
    .qetl.reference.register[`eg_pol_v;d];
    rules:update reference_variant:`eg_pol_v from .qetl.reference.policy.example;
    .qetl.reference.policy.register[`eg_pol_3000;rules];
    p:.qetl.reference.policy.def`eg_pol_3000;
    defs:p`defs;
    .qt.qetl.reference.policy.forget[`eg_pol_3000;`eg_pol_v];
    .qunit.assertEquals[(first defs)`match;enlist[`two_sided]!enlist 1b;"the rule's definition has the variant's match"];
    .qunit.assertEquals[(first defs)`needs;`bid`ask;"and its transform's columns"]}
