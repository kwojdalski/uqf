/ reach.q - can THIS process open a connection to host:port? Asked of discovery
/ about a publisher on another machine (#1651).
/ .
/ Discovery connects back to every process that registers with it, at the host
/ and port it lists; a publisher it cannot reach is listed and unusable. This
/ opens a handle the way TorQ's .servers.opencon does - the stack's own
/ credentials appended, .servers.USERPASS overridden by .servers.PASSWORDS for
/ that address - with a short timeout, and closes it at once. It changes nothing
/ on the process: no variable is set, and the handle is gone before it answers.
/ The credentials are never part of the answer.
/ .
/ @param req JSON {"host": "name", "port": 6020, "timeout": 2000} - timeout in ms
/ @return JSON {"reached": true|false, "address": "host:port", "error": "..."} -
/   error is q's own text ("timeout", "access", "hop. OS reports: ...")
{[req]
 r:.j.k $[10h=type req;req;string req];
 g:{@[value;x;y]};
 addr:r[`host],":",string `long$r`port;
 hp:`$":",addr;
 up:g[`.servers.USERPASS;`]^g[`.servers.PASSWORDS;(`$())!`$()] hp;
 con:$[null up;hp;`$(string hp),":",string up];
 h:@[{(hopen x;"")};(con;`long$r`timeout);{(0Ni;x)}];
 if[not null first h;@[hclose;first h;{}]];
 .j.j `reached`address`error!(not null first h;addr;last h)}
