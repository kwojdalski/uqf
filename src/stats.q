// stats.q - normal distribution helpers used throughout uqf
//
// ncdf/npdf use the Abramowitz & Stegun 7.1.26 rational approximation to
// the error function (max absolute error ~1.5e-7).
// invNcdf uses Peter Acklam's rational approximation to the inverse
// standard normal CDF (max relative error ~1.15e-9), released by its
// author into the public domain. See:
//   https://web.archive.org/web/20151030215612/http://home.online.no/~pjacklam/notes/invnorm/
//
// NOTE ON q ARITHMETIC: q has no operator precedence - expressions are
// evaluated strictly right to left, so `a*x+b` means `a*(x+b)`, NOT
// `(a*x)+b`. Every polynomial below is therefore evaluated through
// hornerEval rather than hand-written chains, to keep the one tricky bit
// of arithmetic in a single, well-tested place.

\d .uqf

PI:acos -1;

// Evaluate a polynomial at x via Horner's method. coeffs are ordered
// highest degree first, constant term last (as in numpy.polyval), e.g.
// hornerEval[2 3 4;x] evaluates 2*x*x + 3*x + 4.
hornerEval:{[coeffs;x]
    {[x;acc;c] (acc*x)+c}[x;]/[first coeffs;1 _ coeffs]};

// Standard normal probability density function n(x)
npdf:{[x] exp[-0.5*x*x] % sqrt 2*PI};

// Standard normal cumulative distribution function N(x)
ncdf:{[x]
    coeffs:1.061405429 -1.453152027 1.421413741 -0.284496736 0.254829592 0f; / a5 a4 a3 a2 a1 0, highest degree first
    s:signum x;
    z:(abs x)%sqrt 2f;
    t:1%1+0.3275911*z;
    poly:hornerEval[coeffs;t];
    y:1-(poly*exp neg z*z);
    0.5*1+s*y};

// Inverse standard normal CDF (probit function): invNcdf[p] returns x such
// that ncdf[x]~p. p must lie strictly within (0,1); works on an atom or a
// list of atoms.
invNcdf:{[p]
    if[not all(p>0)&(p<1); '"invNcdf: p must be strictly between 0 and 1"];
    a:-39.69683028665376 220.9460984245205 -275.9285104469687 138.3577518672690 -30.66479806614716 2.506628277459239;
    b:-54.47609879822406 161.5858368580409 -155.6989798598866 66.80131188771972 -13.28068155288572,1f;
    c:-0.007784894002430293 -0.3223964580411365 -2.400758277161838 -2.549732539343734 4.374664141464968 2.938163982698783;
    d:0.007784695709041462 0.3224671290700398 2.445134137142996 3.754408661907416,1f;
    plow:0.02425; phigh:1-plow;
    one:{[a;b;c;d;plow;phigh;p]
        $[p<plow;
            [q:sqrt neg 2*log p;
             hornerEval[c;q] % hornerEval[d;q]];
          p>phigh;
            [q:sqrt neg 2*log 1-p;
             neg hornerEval[c;q] % hornerEval[d;q]];
            [q:p-0.5; r:q*q;
             (hornerEval[a;r]*q) % hornerEval[b;r]]]};
    $[0>type p; one[a;b;c;d;plow;phigh;p]; one[a;b;c;d;plow;phigh] each p]};

\d .
