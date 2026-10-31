/ export_api_reference.q - the qDoc registry docs/man.q reads, as JSON.
/ .
/ The q half of scripts/generate/q_api_docs.py, which renders it as the
/ Quarto pages under docs/reference/q-api/. It loads docs/man.q and nothing
/ else: no src/ module is loaded, no example is run and no process is
/ started. man.q reads the comments as text.
/ .
/ Run from the root of the snapshot being documented, since man.q scans
/ ./src relative to where q starts:
/   q scripts/generate/export_api_reference.q OUT.json -q < /dev/null

.qapidoc.fail:{[msg] -2 "export_api_reference: ",msg; exit 1};

.qapidoc.out:$[count .z.x; first .z.x; ""];
if[0=count .qapidoc.out; .qapidoc.fail["usage: q export_api_reference.q OUT.json"]];

@[system;"l docs/man.q";{[e] .qapidoc.fail "docs/man.q did not load: ",e}];

.qapidoc.body:.j.j `funcs`args`files`private!(.man.funcs;.man.args;.man.files;asc .man.private);
@[{[b] (hsym `$.qapidoc.out) 0: enlist b};.qapidoc.body;{[e] .qapidoc.fail "cannot write ",.qapidoc.out,": ",e}];
exit 0
