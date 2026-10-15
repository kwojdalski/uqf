/ rates.q - discount/growth factors and compounding conversions.
/ t is a year fraction (see daycount.q), r a decimal annual rate (0.05 = 5%).
/ .

\d .qf.px

/ Simple-compounding growth factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return 1+r*t
/ @eg .qf.px.growth_simple[0.05;1]  -> 1.05
growth_simple:{[r;t] 1+(r*t)};

/ Continuous-compounding growth factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return exp(r*t)
/ @eg .qf.px.growth_cont[0.05;1]  -> 1.051271
growth_cont:{[r;t] exp[r*t]};

/ Simple-compounding discount factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return 1/(1+r*t)
/ @eg .qf.px.df_simple[0.05;1]  -> 0.952381
df_simple:{[r;t] 1%growth_simple[r;t]};

/ Continuous-compounding discount factor.
/ @param r decimal annual rate
/ @param t year fraction
/ @return exp(-r*t)
/ @eg .qf.px.df_cont[0.05;1]  -> 0.9512294
df_cont:{[r;t] exp[neg r*t]};

/ Convert a simple-compounded rate to the equivalent continuously
/ compounded rate over the same period t.
/ @param r simple decimal annual rate
/ @param t year fraction
/ @return the continuously compounded rate with the same growth over t
/ @eg .qf.px.simple_to_cont[0.05;1]  -> 0.04879016
/ At t=0 the growth is 1 over a zero period; the t->0 limit is r itself, returned
/ rather than the null 0%0 (? not $: t may be a vector).
simple_to_cont:{[r;t] ?[t=0;r+0f*t;log[growth_simple[r;t]]%t]};

/ Convert a continuously compounded rate to the equivalent simple rate
/ over the same period t.
/ @param r continuously compounded decimal annual rate
/ @param t year fraction
/ @return the simple rate with the same growth over t
/ @eg .qf.px.cont_to_simple[0.04879016;1]  -> 0.05
/ At t=0 returns r, the t->0 limit, rather than the null 0%0.
cont_to_simple:{[r;t] ?[t=0;r+0f*t;(growth_cont[r;t]-1)%t]};

\d .
