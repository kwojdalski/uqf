// test_calendar.q - tests for src/foundation/calendar.q (.qf.px): weekdays,
// business-day rolls, spot dates and tenor value dates. Load src/init.q,
// tests/lib/qunit.q and tests/lib/support.q before this file.


/ No holidays in either currency: weekends alone decide.
.qt.qf.px.calendar.no_holidays:{[] `EUR`USD!(`date$();`date$())}

/ A calendar with Monday 2026.09.21 a holiday in `ccy` only.
.qt.qf.px.calendar.monday_off:{[ccy] @[.qt.qf.px.calendar.no_holidays[];ccy;:;enlist 2026.09.21]}

/ T+2 sat/sun conventions for EURUSD with a given roll.
.qt.qf.px.calendar.conventions:{[lag] (enlist `EURUSD)!enlist `spot_lag`roll`eom`weekend!(lag;`modified_following;1b;`sat`sun)}

.qt.qf.px.calendar.test_t0_from_a_weekend_settles_on_the_next_business_day:{[t]
    / #775: Saturday 2026.09.19 is not a spot date.
    r:.qf.px.spot_date[2026.09.19;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[0]];
    .qunit.assertEquals[r`date;2026.09.21;"Saturday's T+0 settles Monday"];
    .qunit.assertEquals[r`skipped;enlist 2026.09.20;"Sunday is passed over on the way"]};

.qt.qf.px.calendar.test_t0_from_a_holiday_settles_past_it:{[t]
    / Monday 2026.09.21 off in USD: T+0 on it, and T+0 from the Saturday before.
    .qunit.assertEquals[.qf.px.spot_date[2026.09.21;`EURUSD;.qt.qf.px.calendar.monday_off[`USD];.qt.qf.px.calendar.conventions[0]]`date;2026.09.22;
        "a USD holiday is not a EURUSD spot date"];
    .qunit.assertEquals[.qf.px.spot_date[2026.09.19;`EURUSD;.qt.qf.px.calendar.monday_off[`EUR];.qt.qf.px.calendar.conventions[0]]`date;2026.09.22;
        "nor a EUR one, after a weekend"]};

.qt.qf.px.calendar.test_t0_on_a_business_day_is_the_trade_date:{[t]
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[0]]`date;2026.09.18;
        "a Friday's T+0 is the Friday"]};

.qt.qf.px.calendar.test_weekday_maps_date_mod_7:{[t]
    .qunit.assertEquals[.qf.px.weekday 2026.09.18 2026.09.19 2026.09.20 2026.09.21;`fri`sat`sun`mon;"Fri, Sat, Sun, Mon"]};

.qt.qf.px.calendar.test_friday_trade_settles_tuesday:{[t]
    r:.qf.px.spot_date[2026.09.18;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]];
    .qunit.assertEquals[r`date;2026.09.22;"Fri T+2 skips the weekend"];
    .qunit.assertEquals[r`skipped;2026.09.19 2026.09.20;"the weekend is named"]};

.qt.qf.px.calendar.test_a_monday_holiday_in_the_non_usd_currency_moves_spot_to_wednesday:{[t]
    eur:.qf.px.spot_date[2026.09.18;`EURUSD;.qt.qf.px.calendar.monday_off[`EUR];.qt.qf.px.calendar.conventions[2]];
    .qunit.assertEquals[eur`date;2026.09.23;"a EUR holiday on T+1 is not counted"];
    .qunit.assertEquals[eur`skipped;2026.09.19 2026.09.20 2026.09.21;"the holiday is named"]};

/ #997: a USD holiday on T+1 does not push a non-USD pair's spot out.
.qt.qf.px.calendar.test_a_usd_holiday_on_t_plus_1_does_not_move_spot:{[t]
    usd:.qf.px.spot_date[2026.09.18;`EURUSD;.qt.qf.px.calendar.monday_off[`USD];.qt.qf.px.calendar.conventions[2]];
    .qunit.assertEquals[usd`date;2026.09.22;"Fri T+2 counts Mon (EUR open) and Tue"];
    / the issue's example, on the module's own mock data (Labor Day 2026.09.07)
    .qunit.assertEquals[.qf.px.spot_date[2026.09.04;`EURUSD;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.08;
        "EURUSD before Labor Day spots Tuesday"];
    .qunit.assertEquals[.qf.px.spot_date[2026.09.04;`GBPUSD;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.08;
        "GBPUSD likewise"]};

/ A USD holiday on the value date itself still rolls it.
.qt.qf.px.calendar.test_a_usd_holiday_on_the_value_date_still_rolls:{[t]
    / Thu 2026.09.17 T+2 lands on Mon 21 (Fri 18 is T+1), USD off Monday
    .qunit.assertEquals[.qf.px.spot_date[2026.09.17;`EURUSD;.qt.qf.px.calendar.monday_off[`USD];.qt.qf.px.calendar.conventions[2]]`date;2026.09.22;
        "counted Monday is a USD holiday: roll to Tuesday"];
    .qunit.assertEquals[.qf.px.spot_date[2026.09.03;`EURUSD;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.08;
        "Thu before Labor Day: T+2 is Mon 7 (USD off), rolls to Tue 8"]};

.qt.qf.px.calendar.test_usdcad_t_plus_1_with_a_usd_holiday_on_the_value_date_rolls:{[t]
    / mock: USD and CAD both off 2026.09.07
    .qunit.assertEquals[.qf.px.spot_date[2026.09.04;`USDCAD;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.08;
        "T+1 on the joint holiday rolls to Tuesday"];
    / USD-only holiday on the T+1 date: CAD counts it, USD rolls it
    cal:`USD`CAD!(enlist 2026.09.21;`date$());
    conv:(enlist `USDCAD)!enlist `spot_lag`roll`eom`weekend!(1;`modified_following;1b;`sat`sun);
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`USDCAD;cal;conv]`date;2026.09.22;
        "T+1 is a USD holiday: spot rolls to Tuesday"]};

.qt.qf.px.calendar.test_a_cross_needs_usd_on_the_spot_date_not_in_between:{[t]
    / EURGBP Fri 2026.09.04: T+2 over EUR/GBP is Tue 8 (Mon 7 open for both)
    .qunit.assertEquals[.qf.px.spot_date[2026.09.04;`EURGBP;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.08;
        "USD holiday on T+1 is not counted for a cross"];
    / Thu 2026.09.03: counted T+2 is Mon 7, a USD holiday, so spot rolls to Tue 8
    .qunit.assertEquals[.qf.px.spot_date[2026.09.03;`EURGBP;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.08;
        "a USD holiday on a cross's spot date is avoided"];
    cal:`EUR`GBP!(`date$();`date$());
    conv:(enlist `EURGBP)!enlist `spot_lag`roll`eom`weekend!(2;`modified_following;1b;`sat`sun);
    .qunit.assertThrows[.qf.px.spot_date[2026.09.03;`EURGBP;;conv];cal;
        "calendar: no holiday calendar for USD*";"a cross refuses a missing USD calendar"]};

.qt.qf.px.calendar.test_spot_lag_is_the_pairs:{[t]
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[1]]`date;2026.09.21;"T+1 from Friday is Monday"];
    .qunit.assertEquals[.qf.px.spot_date[2026.09.16;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]]`date;2026.09.18;"T+2 from Wednesday is Friday"]};

.qt.qf.px.calendar.test_holiday_order_and_duplicates_do_not_matter:{[t]
    tidy:`EUR`USD!(2026.09.21 2026.09.22;`date$());
    messy:`EUR`USD!(2026.09.22 2026.09.21 2026.09.22 2026.09.21;`date$());
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`EURUSD;messy;.qt.qf.px.calendar.conventions[2]];
        .qf.px.spot_date[2026.09.18;`EURUSD;tidy;.qt.qf.px.calendar.conventions[2]];"same spot, same skipped days"]};

.qt.qf.px.calendar.test_modified_following_rolls_back_across_month_end:{[t]
    .qunit.assertEquals[.qf.px.adjust[2026.05.31;`modified_following;`EUR`USD;.qt.qf.px.calendar.no_holidays[];`sat`sun];2026.05.29;
        "Sunday 31 May rolls back to Friday 29"]};

.qt.qf.px.calendar.test_each_roll_rule:{[t]
    f:.qf.px.adjust[2026.05.30;;`EUR`USD;.qt.qf.px.calendar.no_holidays[];`sat`sun];
    .qunit.assertEquals[f each `none`following`preceding`modified_following;2026.05.30 2026.06.01 2026.05.29 2026.05.29;
        "Saturday 30 May under each rule"]};

.qt.qf.px.calendar.test_a_business_day_is_left_alone:{[t]
    f:.qf.px.adjust[2026.09.22;;`EUR`USD;.qt.qf.px.calendar.no_holidays[];`sat`sun];
    .qunit.assertEquals[f each `following`preceding`modified_following;3#2026.09.22;"no roll needed"]};

.qt.qf.px.calendar.test_an_unknown_roll_is_refused:{[t]
    .qunit.assertThrows[.qf.px.adjust[2026.05.30;;`EUR`USD;.qt.qf.px.calendar.no_holidays[];`sat`sun];`nearest;
        "adjust: roll must be one of*nearest";"named, with the rules it accepts"]};

.qt.qf.px.calendar.test_add_months_clamps_to_month_end_and_leap_day:{[t]
    .qunit.assertEquals[.qf.px.add_months[2028.01.31;1];2028.02.29;"Jan 31 + 1M in a leap year"];
    .qunit.assertEquals[.qf.px.add_months[2027.01.31;1];2027.02.28;"Jan 31 + 1M otherwise"];
    .qunit.assertEquals[.qf.px.add_months[2028.02.29;12];2029.02.28;"a leap day + 1Y"];
    .qunit.assertEquals[.qf.px.add_months[2026.03.15;-2];2026.01.15;"negative months subtract"]};

.qt.qf.px.calendar.test_forward_date_month_tenor:{[t]
    r:.qf.px.forward_date[2026.09.22;`1M;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]];
    .qunit.assertEquals[r`date;2026.10.22;"1M from 22 Sep"];
    .qunit.assertEquals[r`eom_applied;0b;"22 Sep is not month end"]};

.qt.qf.px.calendar.test_forward_date_day_and_week_tenors_add_calendar_days:{[t]
    f:{[tenor] .qf.px.forward_date[2026.09.22;tenor;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]]`date};
    .qunit.assertEquals[f each `1D`1W`2W;2026.09.23 2026.09.29 2026.10.06;"days and weeks"]};

.qt.qf.px.calendar.test_forward_date_end_of_month_rule:{[t]
    r:.qf.px.forward_date[2026.04.30;`1M;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]];
    .qunit.assertEquals[r`date;2026.05.29;"month-end spot maps to the target month's last business day"];
    .qunit.assertEquals[r`eom_applied;1b;"and says so"]};

/ #1023: a cross's tenor clears USD as its spot does. EURGBP 1M from Wed
/ 2026.06.03 lands on Fri 2026.07.03, a USD holiday in the mock calendars.
.qt.qf.px.calendar.test_forward_date_on_a_cross_avoids_a_usd_holiday:{[t]
    r:.qf.px.forward_date[2026.06.03;`1M;`EURGBP;.qf.px.mock_calendars;.qf.px.mock_conventions];
    .qunit.assertEquals[r`unadjusted;2026.07.03;"the tenor lands on the USD holiday"];
    .qunit.assertEquals[r`date;2026.07.06;"and rolls past it, as EURUSD's does"];
    .qunit.assertEquals[r`calendars;`EUR`GBP`USD;"on the pair's currencies and USD"]};

/ #1024: the vehicle currency is a convention, not code. ` (null) for none:
/ the pair's own two currencies count and settle, as before #997.
.qt.qf.px.calendar.test_settle_via_is_the_pairs_convention:{[t]
    none:(enlist `EURGBP)!enlist `spot_lag`roll`eom`weekend`settle_via!(2;`modified_following;1b;`sat`sun;`);
    cal:`EUR`GBP!(`date$();`date$());
    .qunit.assertEquals[.qf.px.forward_date[2026.06.03;`1M;`EURGBP;cal;none]`date;2026.07.03;
        "with no vehicle a USD holiday is not consulted, and USD's calendar is not needed"];
    .qunit.assertEquals[.qf.px.spot_date[2026.09.03;`EURGBP;cal;none]`date;2026.09.07;
        "nor on the spot date"];
    eur:(enlist `GBPUSD)!enlist `spot_lag`roll`eom`weekend`settle_via!(2;`modified_following;1b;`sat`sun;`EUR);
    cal:`EUR`GBP`USD!(enlist 2026.09.22;`date$();`date$());
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`GBPUSD;cal;eur]`date;2026.09.23;
        "a vehicle other than USD is cleared on the spot date instead"]};

.qt.qf.px.calendar.test_a_settle_via_that_is_not_a_symbol_is_refused:{[t]
    bad:(enlist `EURGBP)!enlist `spot_lag`roll`eom`weekend`settle_via!(2;`modified_following;1b;`sat`sun;"USD");
    .qunit.assertThrows[.qf.px.spot_date[2026.09.03;`EURGBP;.qf.px.mock_calendars;];bad;
        "calendar: EURGBP's settle_via must be a currency symbol*";"names the pair and the key"]};

.qt.qf.px.calendar.test_forward_date_rolls_an_explicit_value_date:{[t]
    .qunit.assertEquals[.qf.px.forward_date[2026.09.22;2026.10.04;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]]`date;2026.10.05;
        "a Sunday value date rolls to Monday"]};

.qt.qf.px.calendar.test_a_bad_tenor_is_refused:{[t]
    .qunit.assertThrows[.qf.px.forward_date[2026.09.22;;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]];`3Q;
        "forward_date: tenor must be*3Q";"names the tenor"]};

.qt.qf.px.calendar.test_a_missing_pair_is_refused:{[t]
    .qunit.assertThrows[.qf.px.spot_date[2026.09.18;;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[2]];`AUDUSD;"*AUDUSD*";"names the pair"]};

.qt.qf.px.calendar.test_a_missing_calendar_is_refused:{[t]
    .qunit.assertThrows[.qf.px.spot_date[2026.09.18;`EURUSD;;.qt.qf.px.calendar.conventions[2]];(enlist `EUR)!enlist `date$();
        "calendar: no holiday calendar for USD*";"never assumes a currency has no holidays"]};

.qt.qf.px.calendar.test_mock_data_spot_skips_the_mock_jpy_holiday:{[t]
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`USDJPY;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.23;
        "mock JPY has 21 Sep off"];
    .qunit.assertEquals[.qf.px.spot_date[2026.09.18;`EURUSD;.qf.px.mock_calendars;.qf.px.mock_conventions]`date;2026.09.22;
        "mock EUR and USD do not"]};

/ T+0 from a weekend or a holiday rolled forward (#775): with a lag of 0 no
/ day is counted, and the trade date used to come back as its own spot.
.qt.qf.px.calendar.test_t_plus_0_from_a_weekend_or_holiday_rolls_forward:{[t]
    sat:.qf.px.spot_date[2026.09.19;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[0]];
    hol:.qf.px.spot_date[2026.09.21;`EURUSD;.qt.qf.px.calendar.monday_off[`USD];.qt.qf.px.calendar.conventions[0]];
    biz:.qf.px.spot_date[2026.09.22;`EURUSD;.qt.qf.px.calendar.no_holidays[];.qt.qf.px.calendar.conventions[0]];
    .qunit.assertEquals[(sat`date;hol`date;biz`date);2026.09.21 2026.09.22 2026.09.22;
        "Saturday to Monday, a USD holiday Monday to Tuesday, a business day stays put"]};
