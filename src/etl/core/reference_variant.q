/ reference_variant.q - the market reference a markout is scored against, as a declared variant (.qetl.reference, #2288).
/ .
/ A markout asks one question of the market: where did the reference stand at this instant. How that is
/ answered is not one rule but a choice, and the choice has three independent parts:
/ .
/   SELECT     which books count: the venue set, how stale a book may be, how far to shift the instant.
/              Done by the as-of join (aj) over the flat, memory-mapped keys, so it costs the probes'
/              pages and nothing else (mapped_books.q).
/   TRANSFORM  a pure function of ONE matched book: its price from the book's own columns.
/   COMBINE    a pure function of the matched books ACROSS venues: one price per instant.
/ .
/ A VARIANT names one of each. The variant a run used is part of its configuration (config_markout.q),
/ so two results scored against different references can never share a config_id.
/ .
/ VARIANT #1 IS TODAY'S RULE, and must stay so bit for bit: every venue, the latest book at or before the
/ instant no older than the spec's max_age (SELECT); the level-0 bid and ask (TRANSFORM, `top`); the highest
/ bid and the lowest ask across the venues left, and their mid (COMBINE, `best`). A venue that went quiet
/ must not set the price, and a side no live venue quotes is null. It is what .qf.mkt.best_across_venues
/ computes, restated over the flat top so it needs no copy of the market; tests/q/test_reference_variant.q
/ holds the two equal.
/ .
/ WHAT THIS IS NOT. It does not evaluate many variants at once: a run uses ONE, read from its spec. A grid of
/ them - gathering the books once and evaluating a list - plugs in by registering more variants here and
/ calling `at` with each; nothing in the core assumes there is only one.

\d .

/ The variant a spec without a `reference` key uses: today's rule.
.qetl.reference.default:`best_top

/ The keys of a variant: venues (`all or a symbol list of sources), stale (`spec for the spec's max_age, or a
/ timespan), offset (a timespan the instant is moved EARLIER by), transform and combine (registered names).
.qetl.reference.keys:`venues`stale`offset`transform`combine

/ TRANSFORMS: name -> {[rows] ...} returning a table bid, ask, one row per matched book. `top` is the book's
/ own level 0, stored flat: a book's price is read from its bid and ask, never from a ladder.
.qetl.reference.transforms:(enlist `top)!enlist {[rows] ([] bid:rows`bid; ask:rows`ask)}

/ COMBINES: name -> {[tid;bid;ask;n] ...} returning a table bid, ask, mid of n rows, the price of each of the
/ n instants from the books matched to it (tid is the instant each book belongs to). `best` is the highest
/ bid and the lowest ask over the books, and their mid; null is the smallest float, so asks are filled high
/ before the lowest is taken and put back.
.qetl.reference.combines:(enlist `best)!enlist {[tid;bid;ask;n]
    g:0!select b:max bid, a:min 0w^ask by tid from ([] tid:tid; bid:bid; ask:ask);
    / max of a group with no bid at all is -0w, not null: put the null back
    best_bid:@[n#0n;g`tid;:;?[(g`b)=-0w;0n;g`b]];
    best_ask:@[n#0n;g`tid;:;?[(g`a)=0w;0n;g`a]];
    ([] bid:best_bid; ask:best_ask; mid:?[null best_bid;0n;0.5*best_bid+best_ask])}

/ The registered variants: name -> its definition, enlisted so the dictionary stays general.
.qetl.reference.variants:(`symbol$())!()

/ Register a variant.
/ @param name the variant's name, a non-null symbol
/ @param def a dictionary with venues, stale, offset, transform and combine
/ @return name
/ @throws error when the definition is malformed or names an unregistered transform or combine
/ @eg .qetl.reference.register[`eg_variant;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)]  ->  `eg_variant
.qetl.reference.register:{[name;def]
    if[not $[-11h=type name; not null name; 0b]; '"reference: a variant's name must be a non-null symbol"];
    if[not (99h=type def) and (asc key def)~asc .qetl.reference.keys; '"reference: ",string[name]," must define exactly ",", " sv string .qetl.reference.keys];
    if[not $[-11h=type def`venues; def[`venues]=`all; (11h=type def`venues) and 0<count def`venues]; '"reference: ",string[name],"'s venues must be `all or a list of sources"];
    if[not $[-16h=type def`stale; def[`stale]>=0D; def[`stale]~`spec]; '"reference: ",string[name],"'s stale must be `spec or a timespan"];
    if[not $[-16h=type def`offset; def[`offset]>=0D; 0b]; '"reference: ",string[name],"'s offset must be a timespan of zero or more"];
    if[not (def`transform) in key .qetl.reference.transforms; '"reference: ",string[name],"'s transform ",string[def`transform]," is not registered"];
    if[not (def`combine) in key .qetl.reference.combines; '"reference: ",string[name],"'s combine ",string[def`combine]," is not registered"];
    .qetl.reference.variants[name]:enlist (.qetl.reference.keys)#def;
    name}

/ A registered variant's definition, or a refusal naming it.
/ @param name a registered variant
/ @return its definition: a dictionary of venues, stale, offset, transform and combine
/ @throws error when nothing is registered under that name
/ @eg (.qetl.reference.def `best_top)`combine  ->  `best
.qetl.reference.def:{[name]
    if[not name in key .qetl.reference.variants; '"reference: ",string[name]," is not a registered variant - registered: ",", " sv string key .qetl.reference.variants];
    first .qetl.reference.variants name}

/ A variant as a configuration snapshot records it: its name and every key of its definition.
/ @param name a registered variant
/ @return a dictionary: name, then venues, stale, offset, transform and combine
/ @eg (.qetl.reference.describe `best_top)`name  ->  `best_top
.qetl.reference.describe:{[name] (enlist[`name]!enlist name),.qetl.reference.def name}

/ Variant #1.
.qetl.reference.register[.qetl.reference.default;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)];

/ The price of each instant under a variant, from a top-of-book table.
/ .
/ For each target (sym; time), every source the sym has contributes the latest book at or before the time
/ shifted by the variant's offset, if it is no staler than the variant allows; the transform reads a price from
/ each, and the combine folds them. Returned twice: `same`, from books on the target's OWN clock, and `any`,
/ from every book - the difference is how a clock mismatch is told from a gap.
/ .
/ The table is read as it is: one aj against its columns, no sort and no copy. It must be arranged
/ (mapped_books.q), and a (sym; source) is on one clock.
/ @param variant a registered variant's name
/ @param tob the top-of-book table: sym, source, source_time, bid, ask, clock; arranged
/ @param max_age the spec's reference age limit, used where the variant's stale is `spec
/ @param syms the sym of each target, null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return `same`any!(table bid, ask, mid; table bid, ask, mid), one row per target
/ @eg .qetl.reference.at[`best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue]`same  ->  ([] bid:enlist 0n; ask:enlist 0n; mid:enlist 0n)
.qetl.reference.at:{[variant;tob;max_age;syms;times;clocks]
    def:.qetl.reference.def variant;
    n:count syms;
    none:([] bid:n#0n; ask:n#0n; mid:n#0n);
    live:where not null syms;
    if[(0=count tob) or 0=count live; :`same`any!(none;none)];
    .qetl.mapped.require_arranged[`reference;tob];
    at:times-def`offset;
    limit:$[`spec~def`stale; max_age; def`stale];
    srcs:.qetl.mapped.sources_for[tob;distinct syms live];
    if[not `all~def`venues; srcs:select from srcs where source in def`venues];
    sp:exec source by sym from srcs;
    f:ungroup ([] tid:live; sym:syms live; source:sp syms live; source_time:at live; tclock:clocks live);
    if[0=count f; :`same`any!(none;none)];
    j:.qetl.mapped.asof[tob;f];
    j:select from j where not null quoted, limit>=source_time-quoted;
    px:.qetl.reference.transforms[def`transform] j;
    rows:([] tid:j`tid; clock:j`clock; target:j`tclock; bid:px`bid; ask:px`ask);
    comb:.qetl.reference.combines def`combine;
    same:select from rows where clock=target;
    `same`any!(comb[same`tid;same`bid;same`ask;n];comb[rows`tid;rows`bid;rows`ask;n])}

/ One source's book at each instant, for a conversion rate: no combine, the source is named. The latest book
/ of (sym; source) at or before the time, no older than max_age, with both sides a price.
/ @param tob the top-of-book table, arranged
/ @param max_age the oldest the book may be
/ @param src the source
/ @param sy the sym
/ @param times the instants
/ @param clocks the clock of each instant, or () to accept a book on any clock
/ @return table quoted, bid, ask, live: the book's own time, its sides, and whether it counts
/ @eg count .qetl.reference.book_at[0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;`a;`EURUSD;enlist 2026.09.17D10:00:00;()]  ->  1
.qetl.reference.book_at:{[tob;max_age;src;sy;times;clocks]
    k:count times;
    if[0=count tob; :([] quoted:k#0Np; bid:k#0n; ask:k#0n; live:k#0b)];
    .qetl.mapped.require_arranged[`reference;tob];
    j:.qetl.mapped.asof[tob;([] sym:k#sy; source:k#src; source_time:times)];
    ok:(not null j`quoted) & (max_age>=times-j`quoted) & (not null j`bid) & not null j`ask;
    if[count clocks; ok:ok & (j`clock)=clocks];
    ([] quoted:j`quoted; bid:j`bid; ask:j`ask; live:ok)}

\d .
