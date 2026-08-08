// rates.q - discount/growth factors and compounding conversions.
// t is a year fraction (see daycount.q), r a decimal annual rate (0.05 = 5%).

\d .uqf

// Simple-compounding growth factor: (1+r*t)
growthSimple:{[r;t] 1+(r*t)};

// Continuous-compounding growth factor: exp(r*t)
growthCont:{[r;t] exp[r*t]};

// Simple-compounding discount factor: 1/(1+r*t)
dfSimple:{[r;t] 1%growthSimple[r;t]};

// Continuous-compounding discount factor: exp(-r*t)
dfCont:{[r;t] exp[neg r*t]};

// Convert a simple-compounded rate to the equivalent continuously
// compounded rate over the same period t.
simpleToCont:{[r;t] log[growthSimple[r;t]]%t};

// Convert a continuously compounded rate to the equivalent simple rate
// over the same period t.
contToSimple:{[r;t] (growthCont[r;t]-1)%t};

\d .
