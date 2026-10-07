/ deploy_smoke.q - the offline check scripts/deploy.py runs on a release
/ before it starts anything (#773).
/ .
/ Loads the quant library from the release and checks a few numbers against
/ values computed independently: a forward, a discount factor, the normal
/ CDF, a Garman-Kohlhagen call and a pip factor. Enough to prove the release
/ is complete and the interpreter is a working, licensed q - not a test suite.
/ .
/ Prints DEPLOY_SMOKE_OK and exits 0 only when every check passes. Anything
/ else - a failed load, a wrong number, an error - prints DEPLOY_SMOKE_FAILED
/ and the reason on stderr, and exits 1. deploy.py requires the marker AND the
/ exit code, so a q that dies quietly part-way cannot pass.
/ .
/ Run from the release root: q scripts/deploy_smoke.q -q

\d .qdeploysmoke

/ One check: within 1e-6 of the expected value, or say which and how far off.
/ @param name what is checked
/ @param got the library's value
/ @param want the expected value
/ @return 1b when it matches
expect:{[name;got;want]
    ok:1e-6>abs got-want;
    if[not ok; -2 "DEPLOY_SMOKE_FAILED: ",name," gave ",(.Q.s1 got),", expected ",.Q.s1 want];
    ok}

/ Every check. The expected values were computed outside q: the GK call with
/ the same Abramowitz-Stegun normal CDF the library uses.
/ @return a boolean per check
checks:{[]
    (expect["fwd_simple";.qfwd.fwd_simple[1.1;0.05;0.02;1f];1.1323529411764708];
     expect["df_cont";.qrates.df_cont[0.05;2f];0.9048374180359595];
     expect["ncdf at 0";.qstats.ncdf 0f;0.5];
     expect["gk_call at the money";.qopt.gk_call[1.1;1.1;0.05;0.02;0.1;1f];0.06018482163918004];
     expect["pip_factor USDJPY";"f"$.qccy.pip_factor`USDJPY;100f])}

\d .

@[system;"l src/init.q";{[e] -2 "DEPLOY_SMOKE_FAILED: could not load the quant library: ",e; exit 1}];
.qdeploysmoke.results:@[.qdeploysmoke.checks;::;{[e] -2 "DEPLOY_SMOKE_FAILED: a check threw: ",e; exit 1}];
if[not all .qdeploysmoke.results; exit 1];
-1 "DEPLOY_SMOKE_OK";
exit 0
