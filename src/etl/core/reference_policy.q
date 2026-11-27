/ reference_policy.q - the markout reference chosen per instant from a table of rules (.qetl.reference.policy, #2996).
/ .
/ A variant (reference_variant.q) answers "where did the reference stand" one way for a whole run. A POLICY lets the
/ answer depend on the instant: which sources count and which variant prices them, by UTC time of day, weekday and
/ instrument. It is declared data, one complete regime per rule row:
/ .
/   rule_id              a non-null symbol, unique in the policy
/   priority             a long; where two rules match an instant the higher one wins, and a tie is refused
/   active_from          UTC time of day, inclusive (a minute, second, time or timespan: 08:00)
/   active_to            UTC time of day, exclusive; 24:00 is the end of the day. A window crossing midnight is two rules
/   weekdays             `all, `weekdays (mon-fri), or a list of days (`mon`tue`wed`thu`fri`sat`sun)
/   instrument_selector  `all, or the instruments (the execution's sym) the rule is for
/   sources              `all, or the sources whose books may set the price - no other source can
/   reference_variant    a registered variant: the transform and combine, and its own venues and staleness
/   max_age              a timespan replacing the variant's staleness, or 0Nn to keep it
/   parameters           a dictionary merged into the variant's params (a key its transform lacks is refused), or ()
/ .
/ conditions -> rule row -> registered variant + sources -> the existing reference engine. Selection is a pure function
/ of (instrument, instant), matched on the REFERENCE timestamp - t0 and t1 independently - never on the clock the job
/ runs by. Pricing is .qetl.reference.priced, the one road .qetl.reference.at takes too, over each rule's group of
/ targets with the rule's definition: no second pricing path, no time-of-day condition inside a transform.
/ .
/ REFUSED AT DECLARATION, by name: a malformed or duplicate rule, an unknown weekday or variant, active_from not before
/ active_to, a source the variant's own venues exclude, and - proved over every boundary the rules declare rather than
/ sampled - any (weekday, time of day, instrument) no rule matches, or two rules match at the top priority. The
/ instruments proved are those the rules name, and "any other instrument" when a rule selects `all. REFUSED AT RUN
/ TIME: an instant no rule matches (`unmatched_reference_rule`, an instrument no rule names) - never a silent fallback
/ to another source, method or stale quote.
/ .
/ A policy's whole content - its rules canonically ordered, and the definition each rule prices with - is part of a
/ markout's configuration (config_markout.q), so a different policy is a different config_id, and a scored row's rule
/ at t0 and t1 is re-derived from its config_id, sym, source_time and horizon (`lineage`) rather than repeated on it.
/ Calendars, sessions, size buckets and the other conditions of #2996's later chunk are not here.

\d .

/ The columns a policy's rule table declares.
.qetl.reference.policy.cols:`rule_id`priority`active_from`active_to`weekdays`instrument_selector`sources`reference_variant`max_age`parameters

/ The days, in the order `date mod 7` counts them: 2000.01.01, day 0, was a Saturday.
.qetl.reference.policy.days:`sat`sun`mon`tue`wed`thu`fri

/ The days a canonical rule lists, Monday first; `weekdays` is the first five.
.qetl.reference.policy.week:`mon`tue`wed`thu`fri`sat`sun

/ The one-rule policy every instant falls under: variant #1 over every source, all day, every day. It is the fallback
/ row a policy's other rules are layered over at a higher priority, and the examples' table.
.qetl.reference.policy.example:([] rule_id:enlist `all_day; priority:enlist 0; active_from:enlist 00:00; active_to:enlist 24:00; weekdays:enlist `all; instrument_selector:enlist `all; sources:enlist `all; reference_variant:enlist `best_top; max_age:enlist 0Nn; parameters:enlist ())

/ The registered policies: name -> rules (canonical) and defs (each rule's definition), enlisted so the dictionary stays general.
.qetl.reference.policies:(`symbol$())!()

/ Private: refuse a rule, naming the policy and the rule.
/ @private
.qetl.reference.policy.refuse:{[label;rid;what] '"reference_policy: ",string[label],".",string[rid],": ",what}

/ Private: a time of day as a timespan, within 00:00 to 24:00.
/ @private
.qetl.reference.policy.time_of:{[label;rid;field;v]
    if[not (type v) in -16 -17 -18 -19h; .qetl.reference.policy.refuse[label;rid;string[field]," must be a UTC time of day (a minute, second, time or timespan), e.g. 08:00"]];
    ts:"n"$v;
    if[(null ts) or (ts<0D) or ts>1D; .qetl.reference.policy.refuse[label;rid;string[field]," must lie between 00:00 and 24:00"]];
    ts}

/ Private: `all, or the ascending distinct symbols a selector names.
/ @private
.qetl.reference.policy.selector:{[label;rid;field;v]
    if[`all~v; :`all];
    if[-11h=type v; v:enlist v];
    if[not (11h=type v) and 0<count v; .qetl.reference.policy.refuse[label;rid;string[field]," must be `all or a non-empty list of symbols"]];
    if[any null v; .qetl.reference.policy.refuse[label;rid;string[field]," must not hold a null symbol"]];
    if[`all in v; .qetl.reference.policy.refuse[label;rid;string[field]," names `all inside a list: write the atom `all to mean every one"]];
    / sorted, so the order a rule lists them in changes nothing; the sorted attribute is not part of the value
    `#asc distinct v}

/ Private: the days a weekdays cell names, Monday first.
/ @private
.qetl.reference.policy.weekdays_of:{[label;rid;v]
    v:(),v;
    if[not (11h=type v) and 0<count v; .qetl.reference.policy.refuse[label;rid;"weekdays must be `all, `weekdays or a list of days"]];
    if[count bad:v except `all`weekdays,.qetl.reference.policy.week; .qetl.reference.policy.refuse[label;rid;"unknown weekday ",(", " sv string bad)," (mon tue wed thu fri sat sun, `weekdays or `all)"]];
    named:distinct raze {[d] $[d=`all; .qetl.reference.policy.week; d=`weekdays; 5#.qetl.reference.policy.week; enlist d]} each v;
    .qetl.reference.policy.week where .qetl.reference.policy.week in named}

/ Private: a parameters cell as a symbol-keyed dictionary in key order; () or an empty dictionary is none.
/ @private
.qetl.reference.policy.params_of:{[label;rid;v]
    if[(type[v] in 0 99h) and 0=count v; :(`symbol$())!()];
    if[not (99h=type v) and 11h=type key v; .qetl.reference.policy.refuse[label;rid;"parameters must be a dictionary of symbol to value, or ()"]];
    (asc key v)#v}

/ Private: one rule row, checked and in canonical form.
/ @private
.qetl.reference.policy.canonical_rule:{[label;r]
    rid:r`rule_id;
    if[not $[-11h=type rid; not null rid; 0b]; '"reference_policy: ",string[label],": every rule_id must be a non-null symbol"];
    p:r`priority;
    if[not $[(type p) in -5 -6 -7h; not null p; 0b]; .qetl.reference.policy.refuse[label;rid;"priority must be a non-null integer"]];
    fr:.qetl.reference.policy.time_of[label;rid;`active_from;r`active_from];
    to:.qetl.reference.policy.time_of[label;rid;`active_to;r`active_to];
    if[fr>=to; .qetl.reference.policy.refuse[label;rid;"active_from ",(.qetl.reference.policy.hhmm fr)," must be before active_to ",(.qetl.reference.policy.hhmm to)," (a window crossing midnight is two rules)"]];
    v:r`reference_variant;
    if[not $[-11h=type v; v in key .qetl.reference.variants; 0b]; .qetl.reference.policy.refuse[label;rid;"reference_variant ",string[v]," is not a registered variant - registered: ",", " sv string key .qetl.reference.variants]];
    ma:r`max_age;
    if[not $[-16h=type ma; (null ma) or ma>0D; 0b]; .qetl.reference.policy.refuse[label;rid;"max_age must be a positive timespan, or 0Nn for the variant's own staleness"]];
    .qetl.reference.policy.cols!(rid;"j"$p;fr;to;.qetl.reference.policy.weekdays_of[label;rid;r`weekdays];
        .qetl.reference.policy.selector[label;rid;`instrument_selector;r`instrument_selector];
        .qetl.reference.policy.selector[label;rid;`sources;r`sources];v;ma;.qetl.reference.policy.params_of[label;rid;r`parameters])}

/ Private: the definition a rule prices with - its variant's, with the rule's sources as the venues, its max_age as the
/ staleness and its parameters over the variant's - completed and refused by the variant registry's own rules.
/ @private
.qetl.reference.policy.effective:{[label;r]
    base:.qetl.reference.def r`reference_variant;
    src:r`sources;
    bv:base`venues;
    if[not (`all~src) or (`all~bv) or all src in bv;
        .qetl.reference.policy.refuse[label;r`rule_id;"sources ",(", " sv string src except bv)," are not among variant ",string[r`reference_variant],"'s venues"]];
    venues:$[`all~src; bv; src];
    stale:$[null r`max_age; base`stale; r`max_age];
    def:`venues`stale`offset`transform`combine`params!(venues;stale;base`offset;base`transform;base`combine;(base`params),r`parameters);
    .qetl.reference.complete[`$string[label],".",string r`rule_id;def]}

/ A policy's rules, validated and canonical: every cell checked, weekdays expanded Monday first, selectors sorted,
/ parameters in key order, the rows sorted by rule_id - so the order rules are declared in changes nothing.
/ @param label the policy's name, which a refusal names
/ @param rules a table with exactly the columns .qetl.reference.policy.cols
/ @return the canonical rules table
/ @throws error naming the rule and what is wrong with it, or a duplicate rule_id
/ @eg (.qetl.reference.policy.canonical[`eg;.qetl.reference.policy.example])`active_to  ->  enlist 1D
.qetl.reference.policy.canonical:{[label;rules]
    if[not 98h=type rules; '"reference_policy: ",string[label],": rules must be a table of ",", " sv string .qetl.reference.policy.cols];
    if[not (asc cols rules)~asc .qetl.reference.policy.cols; '"reference_policy: ",string[label],": rules must have exactly the columns ",", " sv string .qetl.reference.policy.cols];
    if[0=count rules; '"reference_policy: ",string[label],": a policy needs at least one rule"];
    rs:.qetl.reference.policy.canonical_rule[label] each rules;
    if[count dup:where 1<count each group rs`rule_id; '"reference_policy: ",string[label],": duplicate rule_id ",", " sv string dup];
    .qetl.reference.policy.cols#`rule_id xasc rs}

/ Private: whether each rule matches each target - a list of boolean vectors, one per rule, one item per target.
/ @private
.qetl.reference.policy.matrix:{[rules;syms;dows;tods]
    {[syms;dows;tods;r]
        inst:$[`all~r`instrument_selector; (count syms)#1b; syms in r`instrument_selector];
        inst & (dows in "j"$.qetl.reference.policy.days?r`weekdays) & (tods>=r`active_from) & tods<r`active_to}[syms;dows;tods] each rules}

/ Private: the winning rule of each target - the matching rule of the highest priority - and the targets no rule or
/ more than one top rule decides.
/ @private
.qetl.reference.policy.decide:{[rules;m]
    pm:{[hit;p] ?[hit;p;0N]}'[m;rules`priority];
    top:(|/) pm;
    at_top:pm=\:top;
    hits:sum at_top;
    `win`unmatched`ambiguous`at_top!((flip at_top)?\:1b;where null top;where (not null top) & hits>1;at_top)}

/ Private: a time of day as text - hh:mm, or hh:mm:ss, when it is a whole minute or second; the timespan otherwise.
/ @private
.qetl.reference.policy.hhmm:{[ts] $[0=("j"$ts) mod 60000000000; string `minute$ts; 0=("j"$ts) mod 1000000000; string `second$ts; string ts]}

/ Prove a canonical policy covers every instant exactly once: for each instrument it names (and any other instrument,
/ when a rule selects `all), each weekday and each window between consecutive boundaries the rules declare, at least
/ one rule matches and only one at the top priority. The rules that match are the same everywhere inside such a
/ window, so its start stands for all of it: the check is exhaustive, not sampled.
/ @param label the policy's name, which a refusal names
/ @param rules canonical rules (canonical)
/ @return the number of (instrument, weekday, window) cells proved
/ @throws error naming the first cell no rule covers, or the rules tied at its top priority
/ @eg .qetl.reference.policy.prove[`eg;.qetl.reference.policy.canonical[`eg;.qetl.reference.policy.example]]  ->  7
.qetl.reference.policy.prove:{[label;rules]
    ins:rules`instrument_selector;
    open:`all~/:ins;
    named:`symbol$asc distinct raze ins where not open;
    classes:named,$[any open; enlist `; `symbol$()];
    bounds:asc distinct 0D,1D,rules[`active_from],rules`active_to;
    starts:-1_bounds;
    nw:count starts;
    k:(count classes)*7*nw;
    cell:til k;
    syms:classes cell div 7*nw;
    dows:(cell div nw) mod 7;
    wi:cell mod nw;
    d:.qetl.reference.policy.decide[rules;.qetl.reference.policy.matrix[rules;syms;dows;starts wi]];
    who:{[s] $[null s; "any other instrument"; string s]};
    span:{[b;w] (.qetl.reference.policy.hhmm b w),"-",.qetl.reference.policy.hhmm b w+1}[bounds];
    if[count d`unmatched;
        i:first d`unmatched;
        '"reference_policy: ",string[label],": no rule covers ",who[syms i]," on ",string[.qetl.reference.policy.days dows i]," ",span wi i];
    if[count d`ambiguous;
        i:first d`ambiguous;
        tied:(rules`rule_id) where (d`at_top)[;i];
        '"reference_policy: ",string[label],": rules ",(", " sv string tied)," tie at priority ",string[first (rules`priority) where (d`at_top)[;i]]," for ",who[syms i]," on ",string[.qetl.reference.policy.days dows i]," ",span wi i];
    k}

/ Register a policy: its rules are validated, made canonical and proved to cover every instant exactly once, and each
/ rule's definition is completed now, so the policy prices as it was declared.
/ @param name the policy's name, a non-null symbol that is not a registered variant's
/ @param rules a table with exactly the columns .qetl.reference.policy.cols
/ @return name
/ @throws error naming the rule, the gap or the tie, or a name a variant already has
/ @eg .qetl.reference.policy.register[`eg_policy;.qetl.reference.policy.example]  ->  `eg_policy
.qetl.reference.policy.register:{[name;rules]
    if[not $[-11h=type name; not null name; 0b]; '"reference_policy: a policy's name must be a non-null symbol"];
    if[name in key .qetl.reference.variants; '"reference_policy: ",string[name]," is a registered variant - a spec's reference names one or the other"];
    rs:.qetl.reference.policy.canonical[name;rules];
    defs:.qetl.reference.policy.effective[name] each rs;
    .qetl.reference.policy.prove[name;rs];
    .qetl.reference.policies[name]:enlist `rules`defs!(rs;defs);
    name}

/ Whether a name is a registered policy.
/ @param name any value
/ @return 1b for a registered policy's name
/ @eg .qetl.reference.policy.is `best_top  ->  0b
.qetl.reference.policy.is:{[name] $[-11h=type name; name in key .qetl.reference.policies; 0b]}

/ A registered policy, or a refusal naming it.
/ @param name a registered policy
/ @return a dictionary: rules (canonical) and defs (each rule's definition, in the rules' order)
/ @throws error when nothing is registered under that name
/ @eg @[.qetl.reference.policy.def;`nope;{[e] e like "*not a registered policy*"}]  ->  1b
.qetl.reference.policy.def:{[name]
    if[not .qetl.reference.policy.is name; '"reference_policy: ",string[name]," is not a registered policy - registered: ",", " sv string key .qetl.reference.policies];
    first .qetl.reference.policies name}

/ The rule each target falls under, from canonical rules: matched on the target's instant and instrument, the highest
/ priority winning. Pure: what selects a run's rules and what re-derives them from a snapshot are this one function.
/ @param label the policy's name, which a refusal names
/ @param rules canonical rules
/ @param syms the instrument of each target; a null sym is no target
/ @param times the reference instant of each target (UTC); a null time is no target
/ @return the rule_id of each target, null where there is no target
/ @throws error `unmatched_reference_rule` naming an instant no rule matches, or `ambiguous_reference_rule` naming a tie
/ @eg .qetl.reference.policy.match[`eg;.qetl.reference.policy.canonical[`eg;.qetl.reference.policy.example];`EURUSD`;2#2026.09.17D10:00:00]  ->  `all_day`
.qetl.reference.policy.match:{[label;rules;syms;times]
    out:(count syms)#`;
    live:where not (null syms) or null times;
    if[0=count live; :out];
    t:times live;
    dt:`date$t;
    m:.qetl.reference.policy.matrix[rules;syms live;"j"$dt mod 7;t-"p"$dt];
    d:.qetl.reference.policy.decide[rules;m];
    if[count d`unmatched;
        i:first d`unmatched;
        '"reference_policy: unmatched_reference_rule: ",string[label]," has no rule for ",string[(syms live) i]," at ",string t i];
    if[count d`ambiguous;
        i:first d`ambiguous;
        '"reference_policy: ambiguous_reference_rule: ",string[label],"'s rules ",(", " sv string (rules`rule_id) where (d`at_top)[;i])," tie for ",string[(syms live) i]," at ",string t i];
    @[out;live;:;(rules`rule_id) d`win]}

/ Refuse a policy any of whose rules allows an older book than a spec's max_age. A markout job reads its books by the
/ spec's max_age, so a longer rule limit could never be honoured - the rule would see no book the spec's limit had not
/ already cut off, which is a staleness that silently is not what the rule declares.
/ @param name a registered policy
/ @param max_age the spec's max_age, after defaults
/ @return name
/ @throws error naming the first such rule, both limits and the fix
/ @eg .qetl.reference.policy.require_max_age[.qetl.reference.policy.register[`eg_policy;.qetl.reference.policy.example];0D00:00:05]  ->  `eg_policy
.qetl.reference.policy.require_max_age:{[name;max_age]
    rs:(.qetl.reference.policy.def name)`rules;
    long:where (not null rs`max_age) & (rs`max_age)>max_age;
    if[count long;
        i:first long;
        '"reference_policy: ",string[name],".",string[(rs`rule_id) i],": max_age ",string[(rs`max_age) i]," is longer than the spec's max_age ",string[max_age],
            " - the job reads no book older than the spec's limit, so raise the spec's max_age or lower the rule's"];
    name}

/ The rule a registered policy selects for each target.
/ @param name a registered policy
/ @param syms the instrument of each target, null where there is none
/ @param times the reference instant of each target
/ @return the rule_id of each target, null where there is no target
/ @throws error when the policy is not registered, or as match
/ @eg .qetl.reference.policy.select[.qetl.reference.policy.register[`eg_policy;.qetl.reference.policy.example];enlist `EURUSD;enlist 2026.09.17D10:00:00]  ->  enlist `all_day
.qetl.reference.policy.select:{[name;syms;times]
    .qetl.reference.policy.match[name;(.qetl.reference.policy.def name)`rules;syms;times]}

/ The price of each target under the rule selected for it: the targets grouped by rule, each group priced through
/ .qetl.reference.priced with its rule's definition - the rule's sources as the venues, its max_age, its variant's
/ transform and combine - so a source outside the selected rule cannot set a target's price.
/ @param name a registered policy
/ @param rule_ids the rule of each target (select), null where there is none
/ @param tob the top-of-book table: sym, source, source_time, bid, ask, clock; arranged
/ @param max_age the spec's reference age limit, used where a rule's definition says `spec
/ @param syms the sym priced for each target (a leg), null where there is none
/ @param times the instant of each target
/ @param clocks the clock of each target
/ @return `same`any!(table bid, ask, mid; table bid, ask, mid), one row per target, as .qetl.reference.at
/ @throws error when the policy is not registered, a rule_id is not one of its rules or there is not one per target
/ @eg (.qetl.reference.policy.at[.qetl.reference.policy.register[`eg_policy;.qetl.reference.policy.example];enlist `all_day;0#([] sym:`symbol$(); source:`symbol$(); source_time:`timestamp$(); bid:`float$(); ask:`float$(); clock:`symbol$());0D00:00:05;enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist `venue]`same)`mid  ->  enlist 0n
.qetl.reference.policy.at:{[name;rule_ids;tob;max_age;syms;times;clocks]
    p:.qetl.reference.policy.def name;
    n:count syms;
    if[not n=count rule_ids; '"reference_policy: ",string[name],": at needs one rule_id per target"];
    ids:(p`rules)`rule_id;
    if[count bad:(distinct rule_ids except `) except ids; '"reference_policy: ",string[name]," has no rule ",", " sv string bad];
    none:([] bid:n#0n; ask:n#0n; mid:n#0n);
    g:group rule_ids;
    g:(key[g] except `)#g;
    c:`name`p`ids`tob`ma`syms`times`clocks!(name;p;ids;tob;max_age;syms;times;clocks);
    one:{[c;acc;rid;ix]
        r:.qetl.reference.priced[`$string[c`name],".",string rid;(c[`p]`defs) (c`ids)?rid;c`tob;c`ma;(c`syms) ix;(c`times) ix;(c`clocks) ix];
        `same`any!(@[acc`same;ix;:;r`same];@[acc`any;ix;:;r`any])};
    one[c]/[`same`any!(none;none);key g;value g]}

/ A policy as a configuration snapshot records it: its name, its canonical rules and the definition each rule prices
/ with, so a changed rule - or a changed variant a rule names - is a changed configuration.
/ @param name a registered policy
/ @return a dictionary: name, kind (`policy), rules, effective (rule_id -> definition)
/ @throws error when the policy is not registered
/ @eg (.qetl.reference.policy.describe .qetl.reference.policy.register[`eg_policy;.qetl.reference.policy.example])`kind  ->  `policy
.qetl.reference.policy.describe:{[name]
    p:.qetl.reference.policy.def name;
    rs:p`rules;
    `name`kind`rules`effective!(name;`policy;rs;(rs`rule_id)!p`defs)}

/ The source universe of each rule of a policy: the variant it prices with and the sources that may set its price.
/ @param name a registered policy
/ @return table rule_id, reference_variant, sources (`all or a symbol list)
/ @throws error when the policy is not registered
/ @eg exec rule_id from .qetl.reference.policy.universe .qetl.reference.policy.register[`eg_policy;.qetl.reference.policy.example]  ->  enlist `all_day
.qetl.reference.policy.universe:{[name]
    p:.qetl.reference.policy.def name;
    rs:p`rules;
    ([] rule_id:rs`rule_id; reference_variant:rs`reference_variant; sources:{[d] d`venues} each p`defs)}

/ The rule and variant that priced each row at its execution (t0) and its horizon (t1), from the reference a
/ configuration snapshot records (config_markout.q): re-derived by match over the RECORDED rules, never the policy as
/ registered now. A snapshot with no reference was scored under variant #1, and one naming a variant under it; their
/ rule is null.
/ @param ref the snapshot's reference: a policy's describe, a variant's describe, or (::) for none
/ @param syms the instrument of each row
/ @param t0s the execution instant of each row
/ @param t1s the horizon instant of each row
/ @return table ref_rule_t0, ref_rule_t1, ref_variant_t0, ref_variant_t1, one row per row
/ @throws error as match, for an instant the recorded rules do not cover
/ @eg .qetl.reference.policy.lineage[(::);enlist `EURUSD;enlist 2026.09.17D10:00:00;enlist 2026.09.17D10:00:10]  ->  ([] ref_rule_t0:enlist `; ref_rule_t1:enlist `; ref_variant_t0:enlist `best_top; ref_variant_t1:enlist `best_top)
.qetl.reference.policy.lineage:{[ref;syms;t0s;t1s]
    n:count syms;
    if[not $[99h=type ref; `policy~ref`kind; 0b];
        v:$[99h=type ref; ref`name; .qetl.reference.default];
        :([] ref_rule_t0:n#`; ref_rule_t1:n#`; ref_variant_t0:n#v; ref_variant_t1:n#v)];
    rs:ref`rules;
    r0:.qetl.reference.policy.match[ref`name;rs;syms;t0s];
    r1:.qetl.reference.policy.match[ref`name;rs;syms;t1s];
    vm:(rs`rule_id)!rs`reference_variant;
    ([] ref_rule_t0:r0; ref_rule_t1:r1; ref_variant_t0:vm r0; ref_variant_t1:vm r1)}

\d .
