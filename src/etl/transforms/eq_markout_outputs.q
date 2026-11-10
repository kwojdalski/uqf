/ eq_markout_outputs.q - the equity markout DATASET: its spec, its scoring
/ transform, and the predicates the live job and its bounded twin share
/ (.qpipe.transform.eq_markout_outputs, #1981).
/ .
/ eq_domain.q (#1939) is the equity DOMAIN: how an instrument is priced, its
/ sessions and its halts. This file is what turns it into a dataset: the one grid
/ and policy every producer scores with, the declared transform, and the keep/legs
/ callbacks. The live job (src/etl/streaming/eq_markout.q) and the twin
/ (src/etl/workers/hdb_eq_markout_backfill.q) both apply the transform declared
/ here, so a live row and a refilled one are produced by the same call and cannot
/ differ, as for crypto_markout_outputs.q.
/ .
/ THE INPUTS ARE THE CANONICAL TABLES, `executions` and `market_data`, and BOTH are
/ shared with other asset classes: the executions normalizer, the FX and crypto
/ fills and the equity fills worker (eq_fills_backfill) all write the one
/ `executions`. The equity domain prices ANY non-null symbol, so it cannot tell an
/ equity fill from an FX one by the symbol. The tell is the venue: .qetl.venue
/ (src/etl/core/venue_market.q, #2650) says which market a venue is, and owns the
/ equity list (a fill's `venue` and a book's `source`), so everything else is left to
/ the other datasets without any of them naming this one. A site that adds a venue
/ adds it there, and the spec's digest is not affected: the venues select inputs,
/ they are not a calculation.
/ .
/ THE SPEC, each choice and why:
/   horizons      5s, 30s and 1min: an equity touch moves in hundreds of milliseconds
/                 and a markout is read where the fill's footprint has gone; a horizon
/                 past the close is unavailable (the sessions below), so the grid is
/                 not stretched toward minutes
/   report_ccy    USD; the instruments are USD quoted (eq_domain's default), so no rate
/                 is read and none is declared
/   rate_timing   execution: a fill is valued at the rate it was struck at
/   max_age       one minute. An equity touch can sit unchanged for far longer than the
/                 shared .qf.mkt.reference_max_age (5s, set for FX and crypto, whose
/                 books update continuously): a quiet name's last book is its price
/                 until the next one, and a limit tighter than the quiet periods would
/                 turn them into holes. A minute is also the furthest horizon, so a book
/                 older than the horizon it is read at is never a reference
/   sessions      the US regular session, 09:30 to 16:00 New York (13:00 on early-close days),
/                 for every NYSE trading day of calendar_from to calendar_to (regular_sessions below), as UTC instants.
/                 Half-open: a horizon that lands ON the close is already after it
/   halts         none declared
/ .
/ WHAT THE CALENDAR KNOWS AND DOES NOT. It is the NYSE's: no session on a holiday (nothing trades, so
/ nothing is scored) and a 13:00 New York close on the early-close days - the day after Thanksgiving,
/ Christmas Eve and 3 July (#2572; nyse_year holds the rules, nyse_ad_hoc_closures the closures no
/ rule produces). A fill after an early close, or on a holiday, is `unavailable like any fill outside
/ the regular session, and a horizon past the early close is unavailable. It has no extended hours: a fill outside
/ the regular session is `unavailable, not scored against a pre-market book. Outside
/ calendar_from..calendar_to the declared table is data, but scoring does not stop at its end
/ (#2183): the rule that generated it is applied on demand to the days a batch of fills
/ reaches (available below), so a fill of 2029 or 2035 is in session at the right UTC
/ instants. Before calendar_from it is applied the same way (#2419), so a backfilled fill of
/ 2025 is in session too. The rule has two hard bounds, calendar_floor (2007, when its
/ daylight-time dates came in) and calendar_horizon, outside which scoring refuses the batch
/ with a named error - a fill is never marked unavailable for want of a calendar.
/ A closure the exchange announces after this was written (a day of mourning) is not in the rule until it is added to
/ nyse_ad_hoc_closures. A site with another calendar declares its own sessions table in a
/ spec of its own; this one is data, not logic.
/ .
/ THE MEASURE is markout_bps, 10000*move%price, the same figure the crypto dataset
/ reports (positive when the market moved in the fill's favour). The fee a drop
/ carries is not netted: equity commissions are quoted per share or per order and in
/ any currency, and no rule for turning them into a share of notional is declared
/ yet. When one is, it is a measure beside this one, not a change to it.
/ .
/ WHAT STAYS OUT OF THE LONG ROWS. `time` is the plant's on a live row, so the live
/ transform's output is the plant table without it; the twin writes the core's own
/ `time`, the execution's time plus the furthest horizon: the earliest a live row
/ could have been stamped, since all of an execution's horizons are published together.

\d .

/ The first and last date of the declared calendar, inclusive.
.qpipe.transform.eq_markout_outputs.calendar_from:2026.01.01
.qpipe.transform.eq_markout_outputs.calendar_to:2028.12.31

/ The last date the US session rule is trusted to: scoring a fill past it throws (available).
.qpipe.transform.eq_markout_outputs.calendar_horizon:2099.12.31

/ The first date the US session rule is trusted from: the daylight-time dates it applies
/ (second Sunday of March to first Sunday of November) are the Energy Policy Act's, in force
/ from 2007. Scoring a fill before it throws (available), rather than shift its session by an hour.
.qpipe.transform.eq_markout_outputs.calendar_floor:2007.01.01

/ Closures no rule produces: the 2007 Ford funeral, Hurricane Sandy (two days), the 2018 Bush funeral
/ and the 2025 Carter funeral.
.qpipe.transform.eq_markout_outputs.nyse_ad_hoc_closures:2007.01.02 2012.10.29 2012.10.30 2018.12.05 2025.01.09

/ The days of one year the NYSE does not open and the days it closes at 13:00 New York, from the
/ exchange's published rules (#2572). Day of week below is the day number mod 7: 0 Saturday, 1 Sunday,
/ 2 Monday .. 6 Friday. Holidays: New Year's Day (a Sunday moves to Monday; a Saturday is not
/ observed on the Friday), Martin Luther King Jr. Day (third Monday of January), Presidents Day (third
/ Monday of February), Good Friday (two days before the Gregorian Easter), Memorial Day (last Monday of May),
/ Juneteenth (19 June, from 2022), Independence Day, Labor Day (first Monday of September),
/ Thanksgiving (fourth Thursday of November) and Christmas; Juneteenth, Independence Day and Christmas
/ move to the Friday before when they fall on a Saturday and to the Monday after on a Sunday.
/ Early closes: the day after Thanksgiving, 24 December and 3 July when each is a Monday to Thursday
/ (a Friday 3 July or 24 December is the observed holiday itself). The rule is trusted between
/ calendar_floor and calendar_horizon; nyse_ad_hoc_closures adds what no rule produces.
/ @param y the year, a long
/ @return a dictionary: holidays and early, each an ascending list of dates
/ @eg .qpipe.transform.eq_markout_outputs.nyse_year[2026]`early  ->  2026.07.03 is a holiday so 2026.11.27 2026.12.24
/ @private
.qpipe.transform.eq_markout_outputs.nyse_year:{[y]
    dow:{[x] (`long$x) mod 7};
    first_of:{[y;m] `date$`month$(12*(y-2000))+m-1};
    nth:{[y;m;wd;n] f:`date$`month$(12*(y-2000))+m-1; f+(7*(n-1))+(7+wd-(`long$f) mod 7) mod 7};
    last_wd:{[y;m;wd] e:(`date$`month$(12*(y-2000))+m)-1; e-((`long$e)-wd) mod 7};
    observed:{[x] x+(-1 1 0 0 0 0 0)(`long$x) mod 7};
    / Gregorian Easter by the anonymous algorithm; Good Friday is two days before it
    a:y mod 19; b:y div 100; c:y mod 100;
    d:b div 4; e:b mod 4; f:(b+8) div 25; g:(1+b-f) div 3;
    h:((15+((19*a)+b))-(d+g)) mod 30; i:c div 4; k:c mod 4;
    wl:(((32+(2*e)+(2*i))-h)-k) mod 7;
    m:(a+(11*h)+(22*wl)) div 451;
    t:(114+h+wl)-7*m;
    easter:(first_of[y;t div 31])+(t mod 31);
    new_year:first_of[y;1];
    ny:$[1=dow new_year;enlist new_year+1;0=dow new_year;`date$();enlist new_year];
    juneteenth:$[y>=2022;enlist observed first_of[y;6]+18;`date$()];
    thanksgiving:nth[y;11;5;4];
    hol:ny,(nth[y;1;2;3];nth[y;2;2;3];easter-2;last_wd[y;5;2]),juneteenth,(observed first_of[y;7]+3;nth[y;9;2;1];thanksgiving;observed first_of[y;12]+24);
    jul3:first_of[y;7]+2;
    dec24:first_of[y;12]+23;
    early:(jul3;thanksgiving+1;dec24) where ((dow[jul3] within 2 5);1b;(dow[dec24] within 2 5));
    `holidays`early!(asc hol;asc early)}

/ The US regular session of each weekday in a range, as UTC instants: 09:30 to 16:00
/ New York, which is 14:30 to 21:00 UTC in standard time and 13:30 to 20:00 in daylight
/ time. Daylight time runs from the second Sunday of March to the first Sunday of
/ November; both changes are made at 02:00 on a Sunday, when no session is open, so a
/ session's offset is a function of its date alone. A weekday the NYSE is shut on (nyse_year's holidays,
/ nyse_ad_hoc_closures) has no row; an early-close day's session ends at 13:00 New York (#2572).
/ @param from_d first date, inclusive
/ @param to_d last date, inclusive
/ @return table sym (null: every instrument), open_time, close_time; half-open, one row per weekday
/ @eg count .qpipe.transform.eq_markout_outputs.regular_sessions[2026.02.23;2026.03.01]  ->  5
/ @eg exec close_time from .qpipe.transform.eq_markout_outputs.regular_sessions[2026.02.25;2026.02.25]  ->  enlist 2026.02.25D21:00:00.000000000
/ @eg exec close_time from .qpipe.transform.eq_markout_outputs.regular_sessions[2026.06.15;2026.06.15]  ->  enlist 2026.06.15D20:00:00.000000000
.qpipe.transform.eq_markout_outputs.regular_sessions:{[from_d;to_d]
    d:from_d+til 1+to_d-from_d;
    / 2000.01.01 was a Saturday: the day number mod 7 is 0 Saturday, 1 Sunday, 2 Monday .. 6 Friday
    d:d where 1<(`long$d) mod 7;
    / no session on a holiday; a 13:00 New York close on an early-close day (#2572)
    cal:.qpipe.transform.eq_markout_outputs.nyse_year each distinct `long$`year$d;
    shut:.qpipe.transform.eq_markout_outputs.nyse_ad_hoc_closures,raze {x`holidays} each cal;
    d:d except shut;
    early:d in raze {x`early} each cal;
    mnth:{[d;k] `date$`month$(12*(`long$`year$d)-2000)+k};
    first_sunday:{[x] x+`long$((1-(`long$x) mod 7) mod 7)};
    dst_from:first_sunday[7+mnth[d;2]];
    dst_to:first_sunday mnth[d;10];
    dst:(d>=dst_from) and d<dst_to;
    open_off:?[dst;0D13:30:00;0D14:30:00];
    close_off:?[dst;0D20:00:00;0D21:00:00];
    close_off:close_off-?[early;0D03:00:00;0D00:00:00];
    ([] sym:(count d)#`; open_time:(`timestamp$d)+open_off; close_time:(`timestamp$d)+close_off)}

/ The domain's availability rule over a calendar that reaches as far as the fills do: the declared
/ sessions, plus regular_sessions for every date between the end of the declared table and the
/ latest instant the frame reads, and for every date between the earliest instant it reads and the
/ start of the declared table (#2419: backfilled history before calendar_from). The declared table
/ is untouched, so the spec and its snapshot id are what they were; for frames inside it the result
/ is the domain's own.
/ @param spec the validated spec, at least sessions and halts
/ @param fr the core's frame: t0 and t1 are read
/ @return a boolean per frame row
/ @throws if a frame instant lies after calendar_horizon or before calendar_floor
/ @eg .qpipe.transform.eq_markout_outputs.available[.qpipe.transform.markout_core.validate[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec];([] sym:enlist `AAPL; t0:enlist 2035.03.12D13:30:00; t1:enlist 2035.03.12D13:30:05)]  ->  enlist 1b
.qpipe.transform.eq_markout_outputs.available:{[spec;fr]
    hi_t:max (fr`t0)|fr`t1;
    hi_d:`date$hi_t;
    hz:.qpipe.transform.eq_markout_outputs.calendar_horizon;
    if[(not null hi_t) and hi_d>hz;
        '"eq_markout_outputs: a fill at ",string[hi_t]," is past the session calendar horizon ",string[hz],": scoring it would mark it unavailable for want of a calendar"];
    lo_t:min (fr`t0)&fr`t1;
    lo_d:`date$lo_t;
    fl:.qpipe.transform.eq_markout_outputs.calendar_floor;
    if[(not null lo_t) and lo_d<fl;
        '"eq_markout_outputs: a fill at ",string[lo_t]," is before the session calendar floor ",string[fl],": scoring it would mark it unavailable for want of a calendar"];
    last_d:.qpipe.transform.eq_markout_outputs.calendar_to;
    ext:$[(null hi_t) or hi_d<=last_d; 0#spec`sessions; .qpipe.transform.eq_markout_outputs.regular_sessions[last_d+1;hi_d]];
    first_d:.qpipe.transform.eq_markout_outputs.calendar_from;
    pre:$[(null lo_t) or lo_d>=first_d; 0#spec`sessions; .qpipe.transform.eq_markout_outputs.regular_sessions[lo_d;first_d-1]];
    .qpipe.transform.eq_domain.available[@[spec;`sessions;,;pre,ext];fr]}

/ The equity domain as markout_core.calc takes it for scoring: eq_domain.domain with the
/ calendar-extending availability rule above. Everything else about it is the domain's own.
.qpipe.transform.eq_markout_outputs.scoring_domain:@[.qpipe.transform.eq_domain.domain;`available;:;.qpipe.transform.eq_markout_outputs.available]

/ The grid and policy the live job and its twin score with - one definition. COMPLETE: it carries
/ every key the core and the domain default (rates, and eq_domain's quote_ccy, quote_ccys, benchmark
/ and halts), so the snapshot id below is a function of the spec as it stands, as the other markout
/ datasets' is, and needs no validator at load.
.qpipe.transform.eq_markout_outputs.job_spec:(`horizons`report_ccy`rate_timing`max_age`rates!(
    0D00:00:05 0D00:00:30 0D00:01:00;
    `USD;
    `execution;
    0D00:01:00;
    (`symbol$())!()))
  ,.qpipe.transform.eq_domain.defaults
  ,enlist[`sessions]!enlist .qpipe.transform.eq_markout_outputs.regular_sessions[.qpipe.transform.eq_markout_outputs.calendar_from;.qpipe.transform.eq_markout_outputs.calendar_to]

/ The snapshot id the job spec's results belong to: stamped by the live job, its bounded twin
/ and the examples. The spec is complete, so config_markout.id takes it as it stands;
/ tests/q/test_config_markout.q holds that equal to the validated spec's.
.qpipe.transform.eq_markout_outputs.config_id:.qpipe.transform.config_markout.id[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec]

/ Pin the job spec's configuration snapshot: the live job's `on_start` and its twin's `pin`
/ call it, so the snapshot is durable before the first calculation (#1999).
/ @return the snapshot id, as a one-item list
/ @eg count .qpipe.transform.eq_markout_outputs.pin[]  ->  1
.qpipe.transform.eq_markout_outputs.pin:{[]
    enlist .qpipe.transform.config_markout.pin[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec;"eq_markout job spec: the live job eq_markout1 and its bounded twin"]}

/ The executions columns scoring reads: the core's.
.qpipe.transform.eq_markout_outputs.executions_in:.qetl.plant.columns[`executions;`source_time`venue`sym`side`size`price`fill_id`clock]

/ The market_data columns scoring reads: the books, one row per snapshot, and the clock each is stamped on.
.qpipe.transform.eq_markout_outputs.market_data_in:.qetl.plant.columns[`market_data;`source_time`sym`source`bid`ask`clock]

/ The live output: the plant table, without `time`.
.qpipe.transform.eq_markout_outputs.out:.qetl.plant.published `eq_markout

/ The least a job waits before scoring, however early its horizons are.
.qpipe.transform.eq_markout_outputs.settle:0D00:00:01

/ How long a job waits for a fill's references before giving up on it: the wait for
/ its horizons, and five minutes more.
/ @return a timespan
/ @eg .qpipe.transform.eq_markout_outputs.expire_after[]  ->  0D00:06:00
.qpipe.transform.eq_markout_outputs.expire_after:{[]
    .qpipe.transform.eq_markout_outputs.wait[]+0D00:05}

/ How long a job remembers a fill it has scored, to drop a redelivery.
.qpipe.transform.eq_markout_outputs.remember:0D01

/ How much older than its arrival a fill's source_time can be: the bound on the books the
/ horizon kind keeps for a late fill. A broker's drop copy trails the exchange by seconds;
/ thirty seconds leaves room for that and a reconnect without holding a day of every
/ symbol's books. A fill later than this may find its books evicted and expires unscored -
/ the bounded twin re-derives it from the HDB.
.qpipe.transform.eq_markout_outputs.lateness:0D00:00:30

/ How long a job waits before scoring a fill: its furthest horizon, but never less than settle.
/ @return the horizon the horizon kind declares
/ @eg .qpipe.transform.eq_markout_outputs.wait[]  ->  0D00:01:00
.qpipe.transform.eq_markout_outputs.wait:{[]
    .qpipe.transform.markout_core.wait[.qpipe.transform.eq_markout_outputs.job_spec;.qpipe.transform.eq_markout_outputs.settle]}

/ How far BEFORE a fill the reference is read: the most negative horizon, or none.
/ @return a timespan of zero or more
/ @eg .qpipe.transform.eq_markout_outputs.lookback[]  ->  0D00:00:00
.qpipe.transform.eq_markout_outputs.lookback:{[]
    .qpipe.transform.markout_core.lookback .qpipe.transform.eq_markout_outputs.job_spec}

/ What the horizon kind keeps of a batch of either table: the equity venues' fills the core can
/ score, and the equity venues' books. Fills and books are told apart by `fill_id`, as the other
/ markout jobs do. The FX and crypto fills of the shared `executions` are not kept: the equity
/ domain would price their symbols, and they belong to the datasets of their own domains.
/ @param x a batch of executions or of market_data
/ @return a boolean per row
/ @eg .qpipe.transform.eq_markout_outputs.keep[([] sym:`AAPL`AAPL; source:`XNAS`a)]  ->  10b
.qpipe.transform.eq_markout_outputs.keep:{[x]
    dom:.qpipe.transform.eq_domain.domain;
    spec:.qpipe.transform.markout_core.validate[dom;.qpipe.transform.eq_markout_outputs.job_spec];
    if[`fill_id in cols x; :(null .qpipe.transform.markout_core.why[dom;spec;x]) and .qetl.venue.in_market[`equity;x`venue]];
    .qetl.venue.in_market[`equity;x`source]}

/ Each pending fill's legs: the reference keys readiness waits on - its symbol, and the
/ conversion pair when the spec reads a rate from a book (it does not).
/ @param pending the pending executions
/ @return a list of symbol lists
/ @eg .qpipe.transform.eq_markout_outputs.legs[([] sym:enlist `AAPL)]  ->  enlist enlist `AAPL
.qpipe.transform.eq_markout_outputs.legs:{[pending]
    .qpipe.transform.markout_core.legs_of[.qpipe.transform.eq_domain.domain;.qpipe.transform.eq_markout_outputs.job_spec;pending]}

/ The equity measure on the core's rows: markout_bps, the move as a share of the fill price.
/ @param rows the core's result rows
/ @return the rows with markout_bps appended
/ @eg cols .qpipe.transform.eq_markout_outputs.measures[0#.qpipe.transform.markout_core.result]  ->  (cols .qpipe.transform.markout_core.result),`markout_bps
.qpipe.transform.eq_markout_outputs.measures:{[rows]
    update markout_bps:10000*move%price from rows}

/ The plant table's rows for some fills and the books around them: the core's rows and
/ the equity measure, with the core's own `time`. Fills of any other venue are not scored.
/ @param exe executions, as executions_in
/ @param md market_data, as market_data_in
/ @return eq_markout's rows, `time` first
/ @eg count .qpipe.transform.eq_markout_outputs.rows[0#.qpipe.transform.eq_markout_outputs.executions_in;0#.qpipe.transform.eq_markout_outputs.market_data_in]  ->  0
.qpipe.transform.eq_markout_outputs.rows:{[exe;md]
    dom:.qpipe.transform.eq_markout_outputs.scoring_domain;
    spec:.qpipe.transform.markout_core.validate[dom;.qpipe.transform.eq_markout_outputs.job_spec];
    exe:select from exe where .qetl.venue.in_market[`equity;venue];
    r:.qpipe.transform.eq_markout_outputs.measures[.qpipe.transform.markout_core.score[dom;spec;exe;md]];
    (cols .qetl.plant.shape `eq_markout)#r}

/ The live job's scoring: the rows without `time`, which the plant stamps on publication.
/ @param exe executions, as executions_in
/ @param md market_data, as market_data_in
/ @return the live output's rows
/ @eg count .qpipe.transform.eq_markout_outputs.live[0#.qpipe.transform.eq_markout_outputs.executions_in;0#.qpipe.transform.eq_markout_outputs.market_data_in]  ->  0
.qpipe.transform.eq_markout_outputs.live:{[exe;md]
    (cols .qpipe.transform.eq_markout_outputs.out)#.qpipe.transform.eq_markout_outputs.rows[exe;md]}

/ ------------------------------------------------ THE EXAMPLES' INPUTS AND ROWS
/ .
/ Worked by hand; none is read back from the function it checks. See the
/ declaration below for the arithmetic.

/ One AAPL buy of 100 at 271.50 at 15:00:00 on the venue clock, in the regular session.
/ @private
.qpipe.transform.eq_markout_outputs.sample_executions:{[]
    ([] source_time:enlist 2026.02.25D15:00:00; venue:enlist `XNAS; sym:enlist `AAPL; side:enlist 1;
        size:enlist 100f; price:enlist 271.5; fill_id:enlist `e1; clock:enlist `venue)}

/ XNAS's AAPL books around it: a touch at the fill, then re-quoted at +3s, +20s and +50s.
/ @private
.qpipe.transform.eq_markout_outputs.sample_books:{[]
    ([] source_time:2026.02.25D15:00:00 2026.02.25D15:00:03 2026.02.25D15:00:20 2026.02.25D15:00:50;
        sym:4#`AAPL; source:4#`XNAS;
        bid:271.25 271.5 271.75 272f;
        ask:271.5 271.75 272 272.25f;
        clock:4#`venue)}

/ Its rows: a total and an execution component at each of the three horizons.
/ @private
.qpipe.transform.eq_markout_outputs.sample_rows:{[]
    n:6;
    mv:0.125 -0.125 0.375 -0.125 0.625 -0.125;
    ([] time:n#2026.02.25D15:01:00; source_time:n#2026.02.25D15:00:00; clock:n#`venue; venue:n#`XNAS; fill_id:n#`e1; sym:n#`AAPL;
        side:n#1; size:n#100f; horizon:0D00:00:05 0D00:00:05 0D00:00:30 0D00:00:30 0D00:01:00 0D00:01:00; kind:n#`total`execution; step:n#0;
        price:n#271.5; quote_ccy:n#`USD; ref_t0:n#271.375; ref_t1:271.625 271.625 271.875 271.875 272.125 272.125;
        move:mv; quote_amount:100*mv; rate:n#1f; rate_source:n#`fixed;
        rate_sym:n#`; rate_time:n#0Np; report_amount:100*mv;
        status:n#`ok; config_id:n#.qpipe.transform.eq_markout_outputs.config_id;
        markout_bps:10000*mv%271.5)}

\d .

/ ------------------------------------------------------------- THE EXAMPLES
/ .
/ All worked by hand; none is read back from the function it checks.
/ .
/ A buy of 100 AAPL at 271.50 at 15:00:00. XNAS's book at the fill is 271.25/271.50: mid 271.375,
/ the reference at the fill. At +5s (15:00:05) the latest book is the 15:00:03 one, 271.50/271.75,
/ mid 271.625; at +30s the 15:00:20 one, 271.75/272.00, mid 271.875; at +60s the 15:00:50 one,
/ 272.00/272.25, mid 272.125 (ten seconds old, inside the minute max_age). Every instant is inside
/ the session [14:30, 21:00) UTC of a February day (New York is at UTC-5). Every price is a
/ multiple of an eighth, so the arithmetic is exact in binary floating point.
/ The total moves are 271.625-271.50 = 0.125, 271.875-271.50 = 0.375 and 272.125-271.50 = 0.625; the
/ execution component is the reference at the fill less the price, 271.375-271.50 = -0.125, at every
/ horizon. The instrument is USD quoted and reported in USD, so the rate is 1 (the reporting
/ currency's own) and the amounts are size*move: 12.5, 37.5, 62.5 and -12.5. bps = 10000*move%271.50.
.qetl.transform.define[`eq_markout_score;`inputs`output`fn`examples!(
    `executions`market_data!(.qpipe.transform.eq_markout_outputs.executions_in;.qpipe.transform.eq_markout_outputs.market_data_in);
    .qpipe.transform.eq_markout_outputs.out;
    .qpipe.transform.eq_markout_outputs.live;
    enlist `inputs`expected!(
        `executions`market_data!(.qpipe.transform.eq_markout_outputs.sample_executions[];.qpipe.transform.eq_markout_outputs.sample_books[]);
        delete time from .qpipe.transform.eq_markout_outputs.sample_rows[]))];
