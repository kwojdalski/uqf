/ calendar.q - FX settlement calendars: which dates a currency pair can
/ settle on, the spot date a trade date settles to, and the value date a
/ tenor lands on (.qcal).
/ .
/ SEPARATE FROM daycount.q ON PURPOSE. daycount.q turns two dates into a year
/ fraction; it cannot say whether a date is tradable. This module decides the
/ dates; daycount.q then prices the gap between them.
/ .
/ NO HOLIDAY DATABASE IS EMBEDDED. Holidays change every year and differ by
/ provider, so every function takes the caller's `calendars` - a dictionary of
/ currency -> holiday dates - and `conventions` - a dictionary of pair ->
/ its spot lag, roll rule, end-of-month policy and weekend. A pair or a
/ currency missing from them is REFUSED rather than assumed T+2 with no
/ holidays: a settlement date computed from a guess is wrong silently.
/ .
/ A JOINT BUSINESS DAY is one that is not a weekend day and is a holiday in
/ neither currency of the pair. Holiday order and duplicates never matter:
/ membership is tested with `in`.
/ .
/ Weekend days are symbols - `sat`sun`mon`tue`wed`thu`fri - mapped from q's
/ `date mod 7`, which is 0 on a Saturday (2000.01.01 was one).
/ .
/ mock_calendars and mock_conventions at the bottom are ILLUSTRATIVE sample
/ data, for examples and for trying the functions out. They are not an
/ authoritative holiday list and must not settle a real trade.
/ .
/ NOTE ON q ARITHMETIC: no operator precedence (right to left), so mixed
/ arithmetic below goes through named intermediates.

\d .qcal

/ The weekday symbols, indexed by `date mod 7` (0 is Saturday).
weekdays:`sat`sun`mon`tue`wed`thu`fri

/ The roll conventions `adjust` accepts.
rolls:`none`following`preceding`modified_following

/ What a pair's conventions must carry.
convention_keys:`spot_lag`roll`eom`weekend

/ The weekday of a date, as a symbol.
/ @param d a date, or a list of dates
/ @return `sat`sun`mon`tue`wed`thu`fri - one per date for a list
/ @eg .qcal.weekday 2026.09.18  -> `fri
weekday:{[d] weekdays (`long$d) mod 7}

/ Private: the holidays of `ccys`, refusing a currency the calendars lack.
/ @param ccys the currencies, a symbol list
/ @param calendars dict currency -> holiday dates
/ @return every holiday date of every currency, as one list
holidays_of:{[ccys;calendars]
    if[not 99h=type calendars; '"calendar: calendars must be a dictionary of currency -> holiday dates"];
    if[count missing:ccys where not ccys in key calendars;
        '"calendar: no holiday calendar for ",(", " sv string missing)," - supply one, even an empty list, rather than assume none"];
    raze {[days] `date$days} each calendars ccys}

/ Is `d` a joint business day for `ccys`: not a weekend day, and a holiday
/ in none of them?
/ @param d a date, or a list of dates
/ @param ccys the currencies, e.g. `EUR`USD
/ @param calendars dict currency -> holiday dates
/ @param weekend the weekend days, e.g. `sat`sun
/ @return 1b for a business day - one per date for a list
/ @eg .qcal.is_business_day[2026.09.21;`EUR`USD;`EUR`USD!(();());`sat`sun]  -> 1b
is_business_day:{[d;ccys;calendars;weekend]
    off:holidays_of[ccys;calendars];
    (not (weekday d) in weekend) and not d in off}

/ Roll `d` onto a joint business day.
/ .
/ `none` leaves it; `following` moves forward to the next business day;
/ `preceding` back to the previous one; `modified_following` moves forward
/ unless that crosses into the next month, in which case it moves back.
/ @param d the date to adjust
/ @param roll one of `none`following`preceding`modified_following
/ @param ccys the currencies whose holidays count
/ @param calendars dict currency -> holiday dates
/ @param weekend the weekend days, e.g. `sat`sun
/ @return the adjusted date
/ @throws error naming an unknown roll
/ @eg .qcal.adjust[2026.05.31;`modified_following;`EUR`USD;`EUR`USD!(();());`sat`sun]  -> 2026.05.29
adjust:{[d;roll;ccys;calendars;weekend]
    if[not roll in rolls; '"adjust: roll must be one of ",(", " sv string rolls),", got ",string roll];
    if[roll=`none; :d];
    good:is_business_day[;ccys;calendars;weekend];
    step:$[roll=`preceding; -1; 1];
    moved:{[step;day] day+step}[step]/[{[good;day] not good day}[good];d];
    if[(roll=`modified_following) and not (`month$moved)=`month$d;
        moved:{[day] day-1}/[{[good;day] not good day}[good];d]];
    moved}

/ Add `n` joint business days to `d`: each day after `d` that is a business
/ day counts one. `d` itself need not be a business day.
/ @param d the start date
/ @param n business days to add, a non-negative integer
/ @param ccys the currencies whose holidays count
/ @param calendars dict currency -> holiday dates
/ @param weekend the weekend days
/ @return the date `n` business days after `d`
/ @eg .qcal.add_business_days[2026.09.18;2;`EUR`USD;`EUR`USD!(();());`sat`sun]  -> 2026.09.22
add_business_days:{[d;n;ccys;calendars;weekend]
    if[n<0; '"add_business_days: n must not be negative"];
    good:is_business_day[;ccys;calendars;weekend];
    left:n;
    at:d;
    while[left>0;
        at+:1;
        if[good at; left-:1]];
    at}

/ Add `n` months to a date, keeping the day of the month where the target
/ month has it and taking that month's last day where it does not.
/ @param d a date
/ @param n months to add (negative subtracts)
/ @return the date `n` months on
/ @eg .qcal.add_months[2028.01.31;1]  -> 2028.02.29
add_months:{[d;n]
    target:(`month$d)+n;
    month_start:`date$target;
    month_days:(`date$target+1)-month_start;
    day:(`dd$d)&month_days;
    month_start+(day-1)}

/ The last joint business day of `d`'s month.
/ @param d any date in the month
/ @param ccys the currencies
/ @param calendars dict currency -> holiday dates
/ @param weekend the weekend days
/ @return that month's last business day
/ @eg .qcal.month_end_business_day[2026.05.10;`EUR`USD;`EUR`USD!(();());`sat`sun]  -> 2026.05.29
month_end_business_day:{[d;ccys;calendars;weekend]
    last_day:(`date$1+`month$d)-1;
    adjust[last_day;`preceding;ccys;calendars;weekend]}

/ Private: a pair's two currencies and its conventions, refusing what is
/ missing or malformed.
/ @return dict ccys, spot_lag, roll, eom, weekend
pair_terms:{[pair;conventions]
    if[not 99h=type conventions; '"calendar: conventions must be a dictionary of pair -> conventions"];
    p:.qccy.normalize_ccy_pair pair;
    if[not p in key conventions;
        '"calendar: no conventions for ",string[p]," - supply spot_lag, roll, eom and weekend rather than assume T+2"];
    c:conventions p;
    if[not 99h=type c; '"calendar: ",string[p],"'s conventions must be a dictionary"];
    if[count missing:convention_keys where not convention_keys in key c;
        '"calendar: ",string[p],"'s conventions are missing ",", " sv string missing];
    if[not -7h=type c`spot_lag; '"calendar: ",string[p],"'s spot_lag must be a long from 0 to 5"];
    if[not c[`spot_lag] within 0 5; '"calendar: ",string[p],"'s spot_lag must be a long from 0 to 5"];
    if[count bad:(c`weekend) where not (c`weekend) in weekdays;
        '"calendar: ",string[p],"'s weekend has unknown day(s) ",", " sv string bad];
    legs:.qccy.ccy_pair_legs p;
    `pair`ccys`spot_lag`roll`eom`weekend!(p;legs`base`quote;c`spot_lag;c`roll;c`eom;(),c`weekend)}

/ The spot date a trade date settles on, and why.
/ .
/ Spot is `spot_lag` joint business days after the trade date (holidays of
/ both currencies count). The result names the lag, the calendars used and
/ every business day skipped over on the way.
/ @param trade_date the trade date
/ @param pair the currency pair, e.g. `EURUSD
/ @param calendars dict currency -> holiday dates; both currencies required
/ @param conventions dict pair -> `spot_lag`roll`eom`weekend!(...)
/ @return dict date, trade_date, pair, spot_lag, calendars, skipped - the
/   non-business days passed over between the trade date and spot
/ @throws error naming a missing calendar, pair or convention
/ @eg (.qcal.spot_date[2026.09.18;`EURUSD;.qcal.mock_calendars;.qcal.mock_conventions])`date  -> 2026.09.22
spot_date:{[trade_date;pair;calendars;conventions]
    c:pair_terms[pair;conventions];
    settle:add_business_days[trade_date;c`spot_lag;c`ccys;calendars;c`weekend];
    between:trade_date+1+til 0|settle-trade_date;
    skipped:between where not is_business_day[between;c`ccys;calendars;c`weekend];
    `date`trade_date`pair`spot_lag`calendars`skipped!(settle;trade_date;c`pair;c`spot_lag;c`ccys;skipped)}

/ Private: a tenor symbol such as `3D`1W`2M`1Y as (count;unit).
tenor_parts:{[tenor]
    s:string tenor;
    unit:last s;
    n:"J"$-1_s;
    if[(null n) or (n<1) or not unit in "DWMY";
        '"forward_date: tenor must be a count and one of D W M Y, e.g. `1W or `3M - got ",s];
    (n;unit)}

/ The value date a tenor from spot lands on, and why.
/ .
/ D and W tenors add calendar days (W = 7 days), M and Y tenors add months
/ (Y = 12). The unadjusted date is then rolled by the pair's convention. With
/ `eom` on, a spot date that is the last business day of its month maps a
/ month tenor to the last business day of the target month. A date rather
/ than a tenor is taken as an explicit value date and only rolled.
/ @param spot_date the spot date the tenor runs from
/ @param tenor a tenor symbol (`1W`3M`1Y) or an explicit value date
/ @param pair the currency pair
/ @param calendars dict currency -> holiday dates
/ @param conventions dict pair -> `spot_lag`roll`eom`weekend!(...)
/ @return dict date, spot_date, tenor, unadjusted, roll, eom_applied, calendars
/ @throws error naming a missing calendar, pair or convention, or a bad tenor
/ @eg (.qcal.forward_date[2026.09.22;`1M;`EURUSD;.qcal.mock_calendars;.qcal.mock_conventions])`date  -> 2026.10.22
forward_date:{[spot_date;tenor;pair;calendars;conventions]
    c:pair_terms[pair;conventions];
    explicit:-14h=type tenor;
    parts:$[explicit; (0;"V"); tenor_parts tenor];
    n:parts 0;
    unit:parts 1;
    unadjusted:$[explicit; tenor;
        unit="D"; spot_date+n;
        unit="W"; spot_date+7*n;
        unit="M"; add_months[spot_date;n];
        add_months[spot_date;12*n]];
    month_tenor:unit in "MY";
    at_month_end:spot_date=month_end_business_day[spot_date;c`ccys;calendars;c`weekend];
    eom_applied:(c`eom) and month_tenor and at_month_end;
    settle:$[eom_applied;
        month_end_business_day[unadjusted;c`ccys;calendars;c`weekend];
        adjust[unadjusted;c`roll;c`ccys;calendars;c`weekend]];
    `date`spot_date`tenor`unadjusted`roll`eom_applied`calendars!(
        settle;spot_date;tenor;unadjusted;c`roll;eom_applied;c`ccys)}

/ ------------------------------------------------------------- MOCK DATA
/ .
/ ILLUSTRATIVE ONLY - a handful of 2026 dates shaped like each currency's
/ holidays, and common-looking conventions, so the functions above can be
/ tried and their examples run. Not an authoritative calendar: a real desk
/ loads its provider's holidays and passes them in.

/ Sample holidays: currency -> dates. MOCK DATA.
mock_calendars:`EUR`USD`GBP`JPY`CAD!(
    2026.01.01 2026.04.03 2026.04.06 2026.05.01 2026.12.25 2026.12.26;
    2026.01.01 2026.01.19 2026.05.25 2026.07.03 2026.09.07 2026.11.26 2026.12.25;
    2026.01.01 2026.04.03 2026.04.06 2026.05.04 2026.08.31 2026.12.25 2026.12.28;
    2026.01.01 2026.01.02 2026.01.12 2026.05.04 2026.05.05 2026.09.21 2026.11.23;
    2026.01.01 2026.07.01 2026.09.07 2026.10.12 2026.12.25)

/ Sample conventions: pair -> spot lag, roll, end-of-month and weekend. MOCK DATA.
mock_conventions:`EURUSD`GBPUSD`USDJPY`USDCAD`EURGBP!(
    `spot_lag`roll`eom`weekend!(2;`modified_following;1b;`sat`sun);
    `spot_lag`roll`eom`weekend!(2;`modified_following;1b;`sat`sun);
    `spot_lag`roll`eom`weekend!(2;`modified_following;1b;`sat`sun);
    `spot_lag`roll`eom`weekend!(1;`modified_following;1b;`sat`sun);
    `spot_lag`roll`eom`weekend!(2;`modified_following;1b;`sat`sun))

\d .
