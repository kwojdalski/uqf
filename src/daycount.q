// daycount.q - day count fraction conventions used to turn a pair of dates
// into the year fraction t consumed by rates.q / forwards.q / options.q.
//
// NOTE ON q ARITHMETIC: q has no operator precedence (strictly right to
// left evaluation), so every mixed +/-/* expression below is built from
// explicitly parenthesised, named intermediate terms rather than dense
// one-liners - see src/stats.q for the bug this avoids.

\d .uqf

// Actual/360: (d2-d1) actual calendar days, annualised on a 360-day year.
// Common for USD, EUR money-market and most FX deposit/forward calcs.
dcfAct360:{[d1;d2] (d2-d1)%360.0};

// Actual/365 (Fixed): (d2-d1) actual calendar days, annualised on a
// 365-day year. Common for GBP money markets.
dcfAct365:{[d1;d2] (d2-d1)%365.0};

// 30E/360 (Eurobond basis): each month treated as having 30 days.
// Day-of-month is capped at 30 for both dates (the simple European
// variant - it does not carry the US/NASD end-of-February special case).
dcf30E360:{[d1;d2]
    y1:`year$d1; m1:`mm$d1; day1:30&`dd$d1;
    y2:`year$d2; m2:`mm$d2; day2:30&`dd$d2;
    yearsTerm:360*(y2-y1);
    monthsTerm:30*(m2-m1);
    daysTerm:day2-day1;
    (yearsTerm+monthsTerm+daysTerm)%360.0};

// Dispatch to a day count convention by name.
// @param conv one of `act360`act365`30e360
yearFrac:{[conv;d1;d2]
    f:`act360`act365`30e360!(dcfAct360;dcfAct365;dcf30E360);
    if[not conv in key f; '"yearFrac: unknown convention ",string conv];
    f[conv][d1;d2]};

\d .
