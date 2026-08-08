/ rates.q - discount/growth factors and compounding conversions.
/ t is a year fraction (see daycount.q), r a decimal annual rate (0.05 = 5%).
/ .

\d .uqf

/ Simple-compounding growth factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return 1+r*t
/ @eg .uqf.growthSimple[0.05;1]  -> 1.05
growthSimple:{[r;t] 1+(r*t)};

/ Continuous-compounding growth factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return exp(r*t)
/ @eg .uqf.growthCont[0.05;1]  -> 1.051271
growthCont:{[r;t] exp[r*t]};

/ Simple-compounding discount factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return 1/(1+r*t)
/ @eg .uqf.dfSimple[0.05;1]  -> 0.952381
dfSimple:{[r;t] 1%growthSimple[r;t]};

/ Continuous-compounding discount factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return exp(-r*t)
/ @eg .uqf.dfCont[0.05;1]  -> 0.9512294
dfCont:{[r;t] exp[neg r*t]};

/ Convert a simple-compounded rate to the equivalent continuously
/ compounded rate over the same period t.
/ @param r simple decimal annual rate
/ @param t year fraction
/ @return the continuously compounded rate with the same growth over t
/ @eg .uqf.simpleToCont[0.05;1]  -> 0.04879016
simpleToCont:{[r;t] log[growthSimple[r;t]]%t};

/ Convert a continuously compounded rate to the equivalent simple rate
/ over the same period t.
/ @param r continuously compounded decimal annual rate
/ @param t year fraction
/ @return the simple rate with the same growth over t
/ @eg .uqf.contToSimple[0.04879016;1]  -> 0.05
contToSimple:{[r;t] (growthCont[r;t]-1)%t};

\d .
