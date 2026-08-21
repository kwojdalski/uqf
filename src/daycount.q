/ daycount.q - day count fraction conventions used to turn a pair of dates
/ into the year fraction t consumed by rates.q / forwards.q / options.q.
/ .
/ NOTE ON q ARITHMETIC: q has no operator precedence (strictly right to
/ left evaluation), so every mixed +/-/* expression below is built from
/ explicitly parenthesised, named intermediate terms rather than dense
/ one-liners - see src/stats.q for the bug this avoids.

\d .qdcf

/ Actual/360: (d2-d1) actual calendar days, annualised on a 360-day year.
/ Common for USD, EUR money-market and most FX deposit/forward calcs.
/ @param d1 start date
/ @param d2 end date
/ @return (d2-d1) actual days, divided by 360
/ @eg .qdcf.dcf_act_360[2024.01.01;2025.01.01]  -> 1.016667 (2024 is a leap year)
dcf_act_360:{[d1;d2] (d2-d1)%360.0};

/ Actual/365 (Fixed): (d2-d1) actual calendar days, annualised on a
/ 365-day year. Common for GBP money markets.
/ @param d1 start date
/ @param d2 end date
/ @return (d2-d1) actual days, divided by 365
/ @eg .qdcf.dcf_act_365[2023.01.01;2024.01.01]  -> 1f
dcf_act_365:{[d1;d2] (d2-d1)%365.0};

/ 30E/360 (Eurobond basis): each month treated as having 30 days.
/ Day-of-month is capped at 30 for both dates (the simple European
/ variant - it does not carry the US/NASD end-of-February special case).
/ @param d1 start date
/ @param d2 end date
/ @return the 30E/360 year fraction between d1 and d2
/ @eg .qdcf.dcf_30e_360[2024.01.15;2024.02.15]  -> 0.08333333 (30/360)
dcf_30e_360:{[d1;d2]
    y1:`year$d1; m1:`mm$d1; day1:30&`dd$d1;
    y2:`year$d2; m2:`mm$d2; day2:30&`dd$d2;
    years_term:360*(y2-y1);
    months_term:30*(m2-m1);
    days_term:day2-day1;
    (years_term+months_term+days_term)%360.0};

/ Dispatch to a day count convention by name.
/ @param conv one of `act360`act365`30e360
/ @param d1 start date
/ @param d2 end date
/ @return the year fraction between d1 and d2 under the chosen convention
/ @throws error if conv is not one of the supported symbols
/ @eg .qdcf.year_frac[`act360;2024.01.01;2025.01.01]  -> 1.016667
year_frac:{[conv;d1;d2]
    f:`act360`act365`30e360!(dcf_act_360;dcf_act_365;dcf_30e_360);
    if[not conv in key f; '"year_frac: unknown convention ",string conv];
    f[conv][d1;d2]};

\d .
