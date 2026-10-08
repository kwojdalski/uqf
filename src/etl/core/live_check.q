/ live_check.q - is a source reachable, authorised and the shape it is
/ declared, right now? (.qetl.livecheck, #840)
/ .
/ A source's fixture tests prove the adapter's logic; they say nothing about
/ whether a server can load the driver, authenticate, or read what the
/ declaration expects. This asks, through the SAME code a worker reads with -
/ the source's transport, .qetl.source.validate_live and
/ .qetl.source.fetch_window - never a second connection path.
/ .
/ ONE SOURCE, IN STAGES, each named in the result when it fails:
/ .
/   credential  the source's credential, from its variable or sources.csv.
/               Missing is a FAILURE here, never the fixture: a check that
/               read the fixture would pass while proving nothing.
/   tls         the credential does not turn certificate verification off.
/   connect     the transport opens it - for ODBC, the driver loads and the
/               server accepts the login.
/   schema      validate_live: every declared table and column, with its type.
/   read        fetch_window over the last `window`, checked against the
/               declaration. A valid read of no rows is `empty`, not a failure:
/               the source answered correctly and has nothing in that span.
/ .
/ THE CONNECTION IS CLOSED whatever happened. The check publishes nothing,
/ moves no cursor and records no coverage: it calls none of those paths.
/ .
/ EVERY DIAGNOSTIC IS REDACTED before it leaves: the credential itself, and
/ the value of any secret-shaped setting in it, are masked wherever an error
/ message repeats them - a driver's login error often quotes the connection
/ string it was given.
/ .
/ Loaded by src/etl/init.q in any q process: nothing here touches TorQ.

\d .qetl.livecheck

/ Setting names whose values are secrets, lower case. Matched against the
/ `key=value` parts of a credential.
secret_keys:`pwd`password`passwd`secret`token`apikey`api_key`accesskey`access_key

/ Settings that turn certificate verification off, lower case: key -> the
/ values that do it. A credential carrying one is refused.
tls_off:(!) . flip (
    (`sslverify;`0`false`no`off);
    (`ssl_verify;`0`false`no`off);
    (`sslverifyservercert;`0`false`no);
    (`trustservercertificate;`yes`true`1);
    (`sslmode;`disable`allow`prefer);
    (`tlsverify;`0`false`no);
    (`verifyservercertificate;`0`false`no))

/ Private: a credential's `key=value` parts as lower-case key symbol -> value.
/ Symbols, not strings: a dictionary looked up with a string indexes it by
/ each character.
/ @private
settings:{[cred]
    parts:{x where 0<count each x} ";" vs cred;
    parts:parts where "=" in/: parts;
    (`$lower {trim (x?"=")#x} each parts)!{trim (1+x?"=")_x} each parts}

/ Private: is `x` a trapped failure, (`error;message)?
/ @private
failed:{[x] (0h=type x) and (2=count x) and `error~first x}

/ Mask every secret value in the credential wherever `text` repeats it. The
/ rest stays: a host, a path or a user name is what makes the message
/ actionable, and is not a secret.
/ @param text a diagnostic
/ @param cred the credential, "" when none was read
/ @return the text, safe to print or log
/ @eg .qetl.livecheck.redact["login failed for DRIVER=x;PWD=hunter2";"DRIVER=x;PWD=hunter2"]  ->  "login failed for DRIVER=x;PWD=<redacted>"
redact:{[text;cred]
    if[0=count cred; :text];
    s:settings cred;
    secrets:value[s] where key[s] in secret_keys;
    / an ipc credential host:port:user:password keeps its password last
    if[3<=sum cred=":"; secrets,:enlist last ":" vs cred];
    hide:secrets where 0<count each secrets;
    / longest first, so one secret inside another is masked whole
    hide:hide idesc count each hide;
    {ssr[x;y;"<redacted>"]}/[text;hide]}

/ Private: the first setting in `cred` that turns TLS verification off, or "".
/ @private
tls_disabled:{[cred]
    s:settings cred;
    / $[], not `and`: q's `and` evaluates both sides, and `s k` of a key the
    / credential lacks is not a string
    bad:{[s;k] $[k in key s; (`$lower s k) in tls_off k; 0b]}[s] each key tls_off;
    $[any bad; (string k),"=",s k:first key[tls_off] where bad; ""]}

/ Check one source, live.
/ @param source a registered source
/ @param window how far back the bounded read reaches, a timespan
/ @return dict source, transport, status (`ok`empty`failed), stage, rows, elapsed_ms, diagnostic
check:{[source;window]
    t0:.z.p;
    r:`source`transport`status`stage`rows`elapsed_ms`diagnostic!(source;`;`failed;`credential;0N;0N;"");
    finish:{[t0;r] r[`elapsed_ms]:`long$(.z.p-t0)%1000000; r}[t0];
    decl:@[.qetl.source.def;source;{(`error;x)}];
    if[failed decl; :finish r,`stage`diagnostic!(`declaration;last decl)];
    r[`transport]:decl`transport;
    if[not .qetl.source.has_credentials source;
        :finish r,enlist[`diagnostic]!enlist
            string[source]," has no credential - set ",.qetl.source.credential_var[source],
            ", or give it a row in sources.csv. The live check never reads a fixture"];
    cred:@[.qetl.source.require_credentials;source;{(`error;x)}];
    if[failed cred; :finish r,enlist[`diagnostic]!enlist last cred];
    cred:$[10h=type cred; cred; string cred];
    off:tls_disabled cred;
    if[count off;
        :finish r,`stage`diagnostic!(`tls;"the credential turns certificate verification off (",
            redact[off;cred],") - the live check refuses it; verify the server's certificate instead")];
    tr:.qetl.source.transport_def decl`transport;
    h:@[tr`open;cred;{(`error;x)}];
    if[failed h;
        :finish r,`stage`diagnostic!(`connect;redact[last h;cred])];
    / From here the handle is open: every path below closes it.
    outcome:@[{[source;window;h]
        .qetl.source.validate_live[source;h];
        now:.z.p;
        got:.qetl.source.fetch_window[source;h;now-window;now];
        rows:.qetl.source.primary[source;got 1];
        .qetl.source.validate[source;rows];
        (`ok;count rows)}[source;window];h;{(`error;x)}];
    @[tr`close;h;{[e] (::)}];
    if[failed outcome;
        schema:last[outcome] like "validate_live*";
        why:$[schema; last outcome; "the bounded read of the last ",string[window]," failed: ",last outcome];
        :finish r,`stage`diagnostic!($[schema; `schema; `read];redact[why;cred])];
    finish r,`status`stage`rows!($[0=last outcome; `empty; `ok];`done;last outcome)}

\d .
