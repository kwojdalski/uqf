// test_calendar.q - tests for src/foundation/calendar.q (.qcal): weekdays,
// business-day rolls, spot dates and tenor value dates. Load src/init.q,
// tests/lib/qunit.q and tests/lib/testutil.q before this file.

\d .calendartest

/ No holidays in either currency: weekends alone decide.
no_holidays:{[] `EUR`USD!(`date$();`date$())}

/ A calendar with Monday 2026.09.21 a holiday in `ccy` only.
monday_off:{[ccy] @[no_holidays[];ccy;:;enlist 2026.09.21]}

/ T+2 sat/sun conventions for EURUSD with a given roll.
conventions:{[lag] (enlist `EURUSD)!enlist `spot_lag`roll`eom`weekend!(lag;`modified_following;1b;`sat`sun)}

test_t0_from_a_weekend_settles_on_the_next_business_day:{[t]
    / #775: Saturday 2026.09.19 is not a spot date.
    r:.qcal.spot_date[2026.09.19;`EURUSD;no_holidays[];conventions 0];
    .qunit.assertEquals[r`date;2026.09.21;"Saturday's T+0 settles Monday"];
    .qunit.assertEquals[r`skipped;enlist 2026.09.20;"Sunday is passed over on the way"]};

test_t0_from_a_holiday_settles_past_it:{[t]
    / Monday 2026.09.21 off in USD: T+0 on it, and T+0 from the Saturday before.
    .qunit.assertEquals[.qcal.spot_date[2026.09.21;`EURUSD;monday_off `USD;conventions 0]`date;2026.09.22;
        "a USD holiday is not a EURUSD spot date"];
    .qunit.assertEquals[.qcal.spot_date[2026.09.19;`EURUSD;monday_off `EUR;conventions 0]`date;2026.09.22;
        "nor a EUR one, after a weekend"]};

test_t0_on_a_business_day_is_the_trade_date:{[t]
    .qunit.assertEquals[.qcal.spot_date[2026.09.18;`EURUSD;no_holidays[];conventions 0]`date;2026.09.18;
        "a Friday's T+0 is the Friday"]};

test_weekday_maps_date_mod_7:{[t]
    .qunit.assertEquals[.qcal.weekday 2026.09.18 2026.09.19 2026.09.20 2026.09.21;`fri`sat`sun`mon;"Fri, Sat, Sun, Mon"]};

test_friday_trade_settles_tuesday:{[t]
    r:.qcal.spot_date[2026.09.18;`EURUSD;no_holidays[];conventions 2];
    .qunit.assertEquals[r`date;2026.09.22;"Fri T+2 skips the weekend"];
    .qunit.assertEquals[r`skipped;2026.09.19 2026.09.20;"the weekend is named"]};

test_a_monday_holiday_in_either_currency_moves_spot_to_wednesday:{[t]
    eur:.qcal.spot_date[2026.09.18;`EURUSD;monday_off `EUR;conventions 2];
    usd:.qcal.spot_date[2026.09.18;`EURUSD;monday_off `USD;conventions 2];
    .qunit.assertEquals[(eur`date;usd`date);2026.09.23 2026.09.23;"a holiday in EITHER currency is skipped"];
    .qunit.assertEquals[eur`skipped;2026.09.19 2026.09.20 2026.09.21;"the holiday is named"]};

test_spot_lag_is_the_pairs:{[t]
    .qunit.assertEquals[.qcal.spot_date[2026.09.18;`EURUSD;no_holidays[];conventions 1]`date;2026.09.21;"T+1 from Friday is Monday"];
    .qunit.assertEquals[.qcal.spot_date[2026.09.16;`EURUSD;no_holidays[];conventions 2]`date;2026.09.18;"T+2 from Wednesday is Friday"]};

test_holiday_order_and_duplicates_do_not_matter:{[t]
    tidy:`EUR`USD!(2026.09.21 2026.09.22;`date$());
    messy:`EUR`USD!(2026.09.22 2026.09.21 2026.09.22 2026.09.21;`date$());
    .qunit.assertEquals[.qcal.spot_date[2026.09.18;`EURUSD;messy;conventions 2];
        .qcal.spot_date[2026.09.18;`EURUSD;tidy;conventions 2];"same spot, same skipped days"]};

test_modified_following_rolls_back_across_month_end:{[t]
    .qunit.assertEquals[.qcal.adjust[2026.05.31;`modified_following;`EUR`USD;no_holidays[];`sat`sun];2026.05.29;
        "Sunday 31 May rolls back to Friday 29"]};

test_each_roll_rule:{[t]
    f:.qcal.adjust[2026.05.30;;`EUR`USD;no_holidays[];`sat`sun];
    .qunit.assertEquals[f each `none`following`preceding`modified_following;2026.05.30 2026.06.01 2026.05.29 2026.05.29;
        "Saturday 30 May under each rule"]};

test_a_business_day_is_left_alone:{[t]
    f:.qcal.adjust[2026.09.22;;`EUR`USD;no_holidays[];`sat`sun];
    .qunit.assertEquals[f each `following`preceding`modified_following;3#2026.09.22;"no roll needed"]};

test_an_unknown_roll_is_refused:{[t]
    .qunit.assertThrows[.qcal.adjust[2026.05.30;;`EUR`USD;no_holidays[];`sat`sun];`nearest;
        "adjust: roll must be one of*nearest";"named, with the rules it accepts"]};

test_add_months_clamps_to_month_end_and_leap_day:{[t]
    .qunit.assertEquals[.qcal.add_months[2028.01.31;1];2028.02.29;"Jan 31 + 1M in a leap year"];
    .qunit.assertEquals[.qcal.add_months[2027.01.31;1];2027.02.28;"Jan 31 + 1M otherwise"];
    .qunit.assertEquals[.qcal.add_months[2028.02.29;12];2029.02.28;"a leap day + 1Y"];
    .qunit.assertEquals[.qcal.add_months[2026.03.15;-2];2026.01.15;"negative months subtract"]};

test_forward_date_month_tenor:{[t]
    r:.qcal.forward_date[2026.09.22;`1M;`EURUSD;no_holidays[];conventions 2];
    .qunit.assertEquals[r`date;2026.10.22;"1M from 22 Sep"];
    .qunit.assertEquals[r`eom_applied;0b;"22 Sep is not month end"]};

test_forward_date_day_and_week_tenors_add_calendar_days:{[t]
    f:{[tenor] .qcal.forward_date[2026.09.22;tenor;`EURUSD;.calendartest.no_holidays[];.calendartest.conventions 2]`date};
    .qunit.assertEquals[f each `1D`1W`2W;2026.09.23 2026.09.29 2026.10.06;"days and weeks"]};

test_forward_date_end_of_month_rule:{[t]
    r:.qcal.forward_date[2026.04.30;`1M;`EURUSD;no_holidays[];conventions 2];
    .qunit.assertEquals[r`date;2026.05.29;"month-end spot maps to the target month's last business day"];
    .qunit.assertEquals[r`eom_applied;1b;"and says so"]};

test_forward_date_rolls_an_explicit_value_date:{[t]
    .qunit.assertEquals[.qcal.forward_date[2026.09.22;2026.10.04;`EURUSD;no_holidays[];conventions 2]`date;2026.10.05;
        "a Sunday value date rolls to Monday"]};

test_a_bad_tenor_is_refused:{[t]
    .qunit.assertThrows[.qcal.forward_date[2026.09.22;;`EURUSD;no_holidays[];conventions 2];`3Q;
        "forward_date: tenor must be*3Q";"names the tenor"]};

test_a_missing_pair_is_refused:{[t]
    .qunit.assertThrows[.qcal.spot_date[2026.09.18;;no_holidays[];conventions 2];`AUDUSD;"*AUDUSD*";"names the pair"]};

test_a_missing_calendar_is_refused:{[t]
    .qunit.assertThrows[.qcal.spot_date[2026.09.18;`EURUSD;;conventions 2];(enlist `EUR)!enlist `date$();
        "calendar: no holiday calendar for USD*";"never assumes a currency has no holidays"]};

test_mock_data_spot_skips_the_mock_jpy_holiday:{[t]
    .qunit.assertEquals[.qcal.spot_date[2026.09.18;`USDJPY;.qcal.mock_calendars;.qcal.mock_conventions]`date;2026.09.23;
        "mock JPY has 21 Sep off"];
    .qunit.assertEquals[.qcal.spot_date[2026.09.18;`EURUSD;.qcal.mock_calendars;.qcal.mock_conventions]`date;2026.09.22;
        "mock EUR and USD do not"]};

/ T+0 from a weekend or a holiday rolled forward (#775): with a lag of 0 no
/ day is counted, and the trade date used to come back as its own spot.
test_t_plus_0_from_a_weekend_or_holiday_rolls_forward:{[t]
    sat:.qcal.spot_date[2026.09.19;`EURUSD;no_holidays[];conventions 0];
    hol:.qcal.spot_date[2026.09.21;`EURUSD;monday_off `USD;conventions 0];
    biz:.qcal.spot_date[2026.09.22;`EURUSD;no_holidays[];conventions 0];
    .qunit.assertEquals[(sat`date;hol`date;biz`date);2026.09.21 2026.09.22 2026.09.22;
        "Saturday to Monday, a USD holiday Monday to Tuesday, a business day stays put"]};

\d .
