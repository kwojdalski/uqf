/ webhook.q - the one outbound HTTP transport under src/etl/ (#987, #988).
/ .
/ WHY IT EXISTS. .Q.hp returns the BODY of a non-2xx answer instead of
/ signalling, so a webhook answering 500 "webhook down" looked delivered and
/ the breach was dropped. This file sends the request over a handle and reads
/ the status line, so only a 2xx is a delivery. A job declares what goes out
/ and to whom; it never calls .Q.hp/.Q.hg/.Q.hmb itself, and
/ scripts/gates/check_etl_layering.py refuses it (this file is the exception).
/ .
/ WHAT THIS RELIES ON (checked against the issue's run on KDB-X, not against
/ q.k, which is not shipped here): hopen on a `:http://host[:port]` or
/ `:https://host` address gives a handle on which a request string returns the
/ whole raw response, status line and headers included; .Q.hap splits a URL
/ into (scheme; user:password; host[:port]; path). The status parsing below
/ is pure and tested without a network.
/ .
/ Retry and dead-letter policy stays with the caller (alert_sink): this is
/ the transport only, and a second sink reuses it rather than copying it.

\d .qetl.webhook

/ The HTTP status of a raw response.
/ @param raw the response text, from the status line on
/ @return the status code as a long
/ @throws error when the text does not begin with an HTTP status line
/ @eg .qetl.webhook.status_of "HTTP/1.1 500 Internal Server Error\r\n\r\nwebhook down"
status_of:{[raw]
    if[not 10h=type raw; '"webhook: no HTTP status line in the response"];
    line:first "\r\n" vs raw;
    parts:" " vs line;
    s:$[(2<=count parts) and (first parts) like "HTTP/*"; "J"$parts 1; 0N];
    if[null s; '"webhook: no HTTP status line in the response"];
    s}

/ Pass a response through only if it is a 2xx.
/ @param raw the response text
/ @return the response body, after the blank line
/ @throws error naming the status when it is not 2xx, or when there is none
/ @eg .qetl.webhook.check "HTTP/1.1 204 No Content\r\n\r\n"
check:{[raw]
    s:status_of[raw];
    if[not s within 200 299; '"webhook: answered ",string[s]];
    i:raw ss "\r\n\r\n";
    $[count i; 4_(first i)_raw; ""]}

/ The HTTP request text for a JSON POST. Connection: close, so one request
/ is one handle.
/ @param host host[:port]
/ @param path the request path, from the slash
/ @param body the JSON text
/ @return the request as a string
/ @eg .qetl.webhook.request["hook.invalid";"/t";"{}"]
request:{[host;path;body]
    "POST ",path," HTTP/1.1\r\nHost: ",host,"\r\nContent-Type: application/json\r\nContent-Length: ",
    string[count body],"\r\nConnection: close\r\n\r\n",body}

/ POST JSON and require a 2xx answer. The seam alert_sink's `post` calls.
/ @param target the URL, as a string or a `:http://... symbol
/ @param body the JSON text
/ @return the response body
/ @throws error on a connection failure, or when the status is not 2xx
/ @eg .qetl.webhook.post["http://localhost:9/hook";"{}"]
post:{[target;body]
    url:$[-11h=type target; 1_string target; target];
    u:.Q.hap url;
    path:$[count u 3; u 3; "/"];
    addr:`$":",u[0],"://",$[count u 1; u[1],"@"; ""],u 2;
    h:hopen addr;
    res:.[{[h;req] (1b;h req)}; (h;request[u 2;path;body]); {[e] (0b;e)}];
    hclose h;
    if[not first res; 'last res];
    check[last res]}

\d .
