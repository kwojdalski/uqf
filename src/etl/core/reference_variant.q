/ reference_variant.q - the market reference a markout is scored against, as a declared variant (.qetl.reference, #2288).
/ .
/ A markout asks one question of the market: where did the reference stand at this instant. How that is
/ answered is not one rule but a choice, and the choice has three independent parts:
/ .
/   SELECT     which books count: the venue set, how stale a book may be, how far to shift the instant, and - `match` - what
/              the book must satisfy on its own flat columns (a two-sided book, a market). Done by the as-of search over the
/              flat, memory-mapped keys, so it costs the probes' pages and nothing else (mapped_books.q).
/   TRANSFORM  a pure function of ONE matched book: its price from the columns it declares it reads (`needs`).
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
/ MANY VARIANTS AT ONCE. `at` prices one. A list of them - the books gathered once, every distinct transform run
/ once over the gathered slice, each variant's combine on its own SELECT - is .qetl.variant (variant_run.q); the
/ transforms and combines beyond `top` and `best` that read a book's DEPTH are reference_library.q, and the variants
/ declared with them are src/etl/core/variants/*.q. This file is the registry and the single-variant pricing both build on.
/ .
/ A VARIANT PER INSTANT. A reference policy (reference_policy.q, #2996) chooses the sources and the variant for each
/ reference instant from a rule table, and prices each rule's targets through `priced` below - the road `at` takes.

\d .

/ The variant a spec without a `reference` key uses: today's rule.
.qetl.reference.default:`best_top

/ The keys of a variant: venues (`all or a symbol list of sources), stale (`spec for the spec's max_age, or a
/ timespan), offset (a timespan the instant is moved EARLIER by), transform and combine (registered names).
.qetl.reference.keys:`venues`stale`offset`transform`combine

/ A variant may also carry `params`, the transform's parameters (a quantity, a number of levels), and `match`, the conditions the
/ matched book must meet. The stored definition always has both, and the `needs` of its transform, completed: a changed default, a
/ changed list of columns or a changed condition is a changed variant (and so a changed config_id).
.qetl.reference.stored:`venues`stale`offset`transform`combine`params`needs`match

/ The columns a transform may declare beyond what market_data stores: the weight a promoted variant (.qetl.variant.flat) writes beside
/ each book. It exists only on books that carry it, never in the mapped read.
.qetl.reference.promoted:enlist `w

/ The columns a transform's `needs` may name, and the refusal when one is not among them.
/ @param who the transform, for the message
/ @param needs a symbol list of columns
/ @return needs
/ @throws error naming a column market_data does not store, or an empty or repeated list
/ @eg .qetl.reference.require_needs[`eg;`bid`ask]  ->  `bid`ask
.qetl.reference.require_needs:{[who;needs]
    if[not (11h=type needs) and 0<count needs; '"reference: transform ",string[who]," must declare the columns it reads as a non-empty symbol list"];
    if[not (count needs)=count distinct needs; '"reference: transform ",string[who]," declares a column twice"];
    ok:(key .qetl.mapped.stored),.qetl.reference.promoted;
    if[count bad:needs except ok; '"reference: transform ",string[who]," reads ",(", " sv string bad),", which the market table does not have - it has ",", " sv string ok];
    needs}

/ Does a list of columns include a ladder? A transform that does cannot be run over the books a SELECT matched (they carry flat columns):
/ it is run once over the gathered slice, which carries the ladders, and read by rid.
/ @param needs a symbol list of columns
/ @return 1b when any is a ladder
/ @eg .qetl.reference.reads_depth `bid`ask  ->  0b
.qetl.reference.reads_depth:{[needs] any needs in .qetl.mapped.nested}

/ TRANSFORMS: name -> a dictionary of `needs`, `defaults` and `fn`. `needs` is the symbol list of market_data columns the transform
/ reads: flat ones only (bid, ask, market ...) and it is run over the books a variant matched, which carry them; with a ladder among
/ them it is run ONCE over the whole gathered slice, which must carry every column it names. `defaults` is the parameter dictionary a
/ variant's `params` completes; `fn` is {[rows;params] ...} returning a table bid, ask and optionally w (a weight the
/ combine may use), one row per book. `top` is the book's own level 0, stored flat: a price is read from bid and ask.
.qetl.reference.transforms:(`symbol$())!()

/ Register a transform.
/ @param name the transform's name
/ @param needs the market_data columns it reads, a symbol list
/ @param defaults the parameter dictionary (symbol keys); a variant may override a key, never add one
/ @param fn {[rows;params] ...} returning a table bid, ask, and optionally w
/ @return name
/ @throws error when needs names a column market_data does not have, or the defaults are not a symbol-keyed dictionary
/ @eg .qetl.reference.add_transform[`eg_top;`bid`ask;(`symbol$())!();{[rows;params] ([] bid:rows`bid; ask:rows`ask)}]  ->  `eg_top
.qetl.reference.add_transform:{[name;needs;defaults;fn]
    .qetl.reference.require_needs[name;needs];
    if[not (99h=type defaults) and 11h=abs type key defaults; '"reference: a transform's defaults must be a dictionary of symbol to value"];
    / enlisted: a bare dictionary stored under a key would be flipped, with its peers, into a table
    .qetl.reference.transforms[name]:enlist `needs`defaults`fn!(needs;defaults;fn);
    name}

.qetl.reference.add_transform[`top;`bid`ask;(`symbol$())!();{[rows;unused_params] ([] bid:rows`bid; ask:rows`ask)}];

/ A registered transform's definition.
/ @param name a registered transform
/ @return a dictionary: needs, defaults, fn
/ @eg (.qetl.reference.transform `top)`needs  ->  `bid`ask
.qetl.reference.transform:{[name] first .qetl.reference.transforms name}

/ COMBINES: name -> {[tid;px;n] ...} returning a table bid, ask, mid of n rows, the price of each of the n instants
/ from the books matched to it (tid is the instant each book belongs to; px is the transform's table, bid, ask and w
/ where it has one, one row per book). `best` is the highest bid and the lowest ask over the books, and their mid;
/ null is the smallest float, so asks are filled high before the lowest is taken and put back.
.qetl.reference.combines:(enlist `best)!enlist {[tid;px;n]
    g:0!select b:max bid, a:min 0w^ask by tid from ([] tid:tid; bid:px`bid; ask:px`ask);
    / max of a group with no bid at all is -0w, not null: put the null back
    best_bid:@[n#0n;g`tid;:;?[(g`b)=-0w;0n;g`b]];
    best_ask:@[n#0n;g`tid;:;?[(g`a)=0w;0n;g`a]];
    ([] bid:best_bid; ask:best_ask; mid:?[null best_bid;0n;0.5*(best_bid+best_ask)])}

/ The registered variants: name -> its definition, enlisted so the dictionary stays general.
.qetl.reference.variants:(`symbol$())!()

/ A variant's definition, validated and completed, without registering it: the checks `register` makes, so a
/ definition built elsewhere - a reference policy's rule (reference_policy.q, #2996) - is refused by the same rules
/ and with the same words.
/ @param name the name the definition is refused under, a non-null symbol
/ @param def a dictionary with venues, stale, offset, transform and combine, and optionally params and match
/ @return the stored form: venues, stale, offset, transform, combine, params completed with the transform's defaults, the transform's needs, and the match in canonical form
/ @throws error when the definition is malformed or names an unregistered transform or combine, or a parameter its transform lacks, or a match on a column market_data does not store flat
/ @eg (.qetl.reference.complete[`eg_variant;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)])`params  ->  (`symbol$())!()
.qetl.reference.complete:{[name;def]
    if[not $[-11h=type name; not null name; 0b]; '"reference: a variant's name must be a non-null symbol"];
    if[not (99h=type def) and (asc (key def) except `params`match)~asc .qetl.reference.keys; '"reference: ",string[name]," must define exactly ",(", " sv string .qetl.reference.keys),", and optionally params and match"];
    if[not $[-11h=type def`venues; def[`venues]=`all; (11h=type def`venues) and 0<count def`venues]; '"reference: ",string[name],"'s venues must be `all or a list of sources"];
    if[not $[-16h=type def`stale; def[`stale]>=0D; def[`stale]~`spec]; '"reference: ",string[name],"'s stale must be `spec or a timespan"];
    if[not $[-16h=type def`offset; def[`offset]>=0D; 0b]; '"reference: ",string[name],"'s offset must be a timespan of zero or more"];
    if[not (def`transform) in key .qetl.reference.transforms; '"reference: ",string[name],"'s transform ",string[def`transform]," is not registered"];
    if[not (def`combine) in key .qetl.reference.combines; '"reference: ",string[name],"'s combine ",string[def`combine]," is not registered"];
    want:(.qetl.reference.transform def`transform)`defaults;
    given:$[`params in key def; def`params; (`symbol$())!()];
    / an empty dictionary of any key type is no parameters
    if[(99h=type given) and 0=count given; given:(`symbol$())!()];
    if[not (99h=type given) and 11h=abs type key given; '"reference: ",string[name],"'s params must be a dictionary of symbol to value"];
    if[count extra:(key given) except key want; '"reference: ",string[name],"'s transform ",string[def`transform]," has no parameter ",", " sv string extra];
    if[count bad:(key given) where not (type each given key given)=type each want key given; '"reference: ",string[name],"'s parameter ",(", " sv string bad)," must have the type of the transform's default"];
    / the order is read only for a match that has conditions: a variant without one registers wherever the tree loads
    given_match:$[`match in key def; def`match; (`symbol$())!()];
    if[(99h=type given_match) and 0=count given_match; given_match:(`symbol$())!()];
    match:.qetl.mapped.require_match[`$"reference: ",string name;$[count given_match; .qetl.mapped.market_order[]; `symbol$()];.qetl.mapped.stored;.qetl.mapped.market_conditions[];given_match];
    (`venues`stale`offset`transform`combine#def),`params`needs`match!(want,given;(.qetl.reference.transform def`transform)`needs;match)}

/ Register a variant.
/ @param name the variant's name, a non-null symbol
/ @param def a dictionary with venues, stale, offset, transform and combine, and optionally params and match
/ @return name
/ @throws error when the definition is malformed or names an unregistered transform or combine, or a parameter its transform lacks, or a match on a column market_data does not store flat
/ @eg .qetl.reference.register[`eg_variant;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)]  ->  `eg_variant
.qetl.reference.register:{[name;def]
    full:.qetl.reference.complete[name;def];
    .qetl.reference.variants[name]:enlist full;
    name}

/ A registered variant's definition, or a refusal naming it.
/ @param name a registered variant
/ @return its definition: a dictionary of venues, stale, offset, transform, combine, params, needs and match
/ @throws error when nothing is registered under that name
/ @eg (.qetl.reference.def `best_top)`combine  ->  `best
.qetl.reference.def:{[name]
    if[not name in key .qetl.reference.variants; '"reference: ",string[name]," is not a registered variant - registered: ",", " sv string key .qetl.reference.variants];
    first .qetl.reference.variants name}

/ A variant as a configuration snapshot records it: its name and every key of its definition.
/ @param name a registered variant
/ @return a dictionary: name, then venues, stale, offset, transform, combine, params, needs and match
/ @eg (.qetl.reference.describe `best_top)`name  ->  `best_top
.qetl.reference.describe:{[name] (enlist[`name]!enlist name),.qetl.reference.def name}

/ Variant #1.
.qetl.reference.register[.qetl.reference.default;`venues`stale`offset`transform`combine!(`all;`spec;0D;`top;`best)];

/ The books a variant matches: for each target (sym; time), every source the sym has contributes the latest book at
/ or before the time shifted by the variant's offset, if it is no staler than the variant allows. The SELECT of a
/ variant, and all of it that touches the table: one as-of search against its columns, no sort and no copy. The table
/ must be arranged (mapped_books.q), and a (sym; source) is on one clock.
/ @param def a variant's definition
/ @param tob the top-of-book table: sym, source, source_time, bid, ask, clock (and rid); arranged
/ @param max_age the spec's reference age limit, used where the variant's stale is `spec
/ @param syms the sym of each target, null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return table tid, sym, source, source_time, tclock, quoted, bid, ask, clock (and rid) - one row per matched book, tid the index of its target - or () when nothing could be asked
/ @eg count .qetl.reference.chosen[.qetl.reference.def `best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue]  ->  0
.qetl.reference.chosen:{[def;tob;max_age;syms;times;clocks]
    live:where not null syms;
    if[(0=count tob) or 0=count live; :()];
    o:.qetl.mapped.market_order[];
    .qetl.mapped.require_arranged[`reference;o;tob];
    / the order's equality keys are the target's sym and the venue the variant's venue set picks from
    if[3<>count o; '"reference: a SELECT needs an order of (sym; venue; as-of column), not ",", " sv string o];
    ks:-1_o;
    ac:last o;
    at:times-def`offset;
    limit:$[`spec~def`stale; max_age; def`stale];
    srcs:.qetl.mapped.keys_for[o;tob;distinct syms live];
    if[not `all~def`venues; srcs:srcs where (srcs ks 1) in def`venues];
    grp:group srcs ks 0;
    sp:(key grp)!(srcs ks 1) each value grp;
    f:ungroup flip (`tid,ks,ac,`tclock)!(live;syms live;sp syms live;at live;clocks live);
    if[0=count f; :()];
    / a match with a condition is searched for inside the staleness window: no row older than the limit takes the place of a newer one that fails
    books:tob;
    if[count def`match;
        back:(f ac)-limit;
        f:f,'([] oldest:?[back>f ac; -0Wp; back]);
        books:.qetl.mapped.with_pointers[o;tob;(key def`match) except ks]];
    j:.qetl.mapped.asof_where[o;books;f;(def`needs) except .qetl.mapped.nested;def`match];
    j where (not null j`quoted) & limit>=(j ac)-j`quoted}

/ A variant's combine over the books it matched: returned twice, `same`, from books on the target's OWN clock, and
/ `any`, from every book - the difference is how a clock mismatch is told from a gap.
/ @param def a variant's definition
/ @param j the matched books (chosen)
/ @param px the transform's table, one row per row of j
/ @param n the number of targets
/ @return `same`any!(table bid, ask, mid; table bid, ask, mid), one row per target
/ @eg (.qetl.reference.folded[.qetl.reference.def `best_top;([] tid:enlist 0; clock:enlist `venue; tclock:enlist `venue);([] bid:enlist 1.5; ask:enlist 2.5);1]`same)`mid  ->  enlist 2f
.qetl.reference.folded:{[def;j;px;n]
    rows:([] tid:j`tid; clock:j`clock; target:j`tclock; bid:px`bid; ask:px`ask);
    if[`w in cols px; rows:update w:px`w from rows];
    comb:.qetl.reference.combines def`combine;
    same:select from rows where clock=target;
    `same`any!(comb[same`tid;same;n];comb[rows`tid;rows;n])}

/ The price of each instant under a variant, from a top-of-book table.
/ .
/ The books are chosen (the SELECT), the transform reads a price from each, and the combine folds them. A transform
/ that reads depth needs the table to carry the ladders and a `rid` that is the row's own index (the slice
/ .qetl.variant builds), and is run over the whole table, which is in memory by construction.
/ @param variant a registered variant's name
/ @param tob the top-of-book table: sym, source, source_time, bid, ask, clock; arranged
/ @param max_age the spec's reference age limit, used where the variant's stale is `spec
/ @param syms the sym of each target, null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return `same`any!(table bid, ask, mid; table bid, ask, mid), one row per target
/ @eg .qetl.reference.at[`best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue]`same  ->  ([] bid:enlist 0n; ask:enlist 0n; mid:enlist 0n)
.qetl.reference.at:{[variant;tob;max_age;syms;times;clocks]
    .qetl.reference.priced[variant;.qetl.reference.def variant;tob;max_age;syms;times;clocks]}

/ The price of each instant under a variant's DEFINITION: what `at` does once it has looked the name up, and the one
/ road a reference policy's rule (reference_policy.q, #2996) is priced by too, so a rule is never a second pricing path.
/ @param label the name a refusal names (the variant, or a policy's rule)
/ @param def a definition as `complete` returns it
/ @param tob the top-of-book table: sym, source, source_time, bid, ask, clock; arranged
/ @param max_age the spec's reference age limit, used where the definition's stale is `spec
/ @param syms the sym of each target, null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return `same`any!(table bid, ask, mid; table bid, ask, mid), one row per target
/ @throws error when the transform reads depth and tob is not a slice
/ @eg (.qetl.reference.priced[`eg;.qetl.reference.def `best_top;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue]`same)`mid  ->  enlist 0n
.qetl.reference.priced:{[label;def;tob;max_age;syms;times;clocks]
    n:count syms;
    none:([] bid:n#0n; ask:n#0n; mid:n#0n);
    j:.qetl.reference.chosen[def;tob;max_age;syms;times;clocks];
    if[() ~ j; :`same`any!(none;none)];
    t:.qetl.reference.transform def`transform;
    deep:.qetl.reference.reads_depth def`needs;
    books:$[deep; .qetl.reference.require_slice[label;def`needs;tob]; ()];
    px:$[deep; (t[`fn][books;def`params]) j`rid; t[`fn][j;def`params]];
    .qetl.reference.folded[def;j;px;n]}

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
    o:.qetl.mapped.market_order[];
    .qetl.mapped.require_arranged[`reference;o;tob];
    ks:-1_o;
    j:.qetl.mapped.asof[o;tob;flip (ks,last o)!(k#sy;k#src;times)];
    ok:(not null j`quoted) & (max_age>=times-j`quoted) & (not null j`bid) & not null j`ask;
    if[count clocks; ok:ok & (j`clock)=clocks];
    ([] quoted:j`quoted; bid:j`bid; ask:j`ask; live:ok)}

\d .
