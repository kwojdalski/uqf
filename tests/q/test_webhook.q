/ test_webhook.q - the outbound HTTP transport's status handling (.webhooktest, #987).
/ .
/ The network edge (hopen and the request) is not exercised: the point of the
/ file is the rule .Q.hp lacks - a response is a delivery only when its status
/ line says 2xx. Responses are written out as raw text, as a server sends them.

\d .webhooktest

raw:{[code;reason;body] "HTTP/1.1 ",string[code]," ",reason,"\r\nContent-Length: ",string[count body],"\r\n\r\n",body}

test_the_status_is_read_from_the_status_line:{[t]
    .qunit.assertEquals[.qetl.webhook.status_of raw[500;"Internal Server Error";"webhook down"];500j;"a 500"];
    .qunit.assertEquals[.qetl.webhook.status_of raw[204;"No Content";""];204j;"a 204 with no body"];
    .qunit.assertEquals[.qetl.webhook.status_of "HTTP/1.0 404 Not Found\r\n\r\n";404j;"a bare response"]};

test_a_2xx_passes_and_returns_the_body:{[t]
    .qunit.assertEquals[.qetl.webhook.check raw[200;"OK";"ok"];"ok";"the body, headers stripped"];
    .qunit.assertEquals[.qetl.webhook.check raw[299;"Edge";""];"";"the top of the range, empty body"]};

test_anything_else_is_refused_naming_the_status:{[t]
    .qunit.assertThrows[.qetl.webhook.check;raw[500;"Internal Server Error";"webhook down"];"webhook: answered 500";"a 500 is not a delivery, though .Q.hp would return its body"];
    .qunit.assertThrows[.qetl.webhook.check;raw[401;"Unauthorized";"no"];"webhook: answered 401";"a 4xx"];
    .qunit.assertThrows[.qetl.webhook.check;raw[199;"Odd";""];"webhook: answered 199";"just below the range"];
    .qunit.assertThrows[.qetl.webhook.check;raw[300;"Moved";""];"webhook: answered 300";"a redirect is not followed, so not delivered"]};

test_a_response_with_no_status_line_is_refused:{[t]
    .qunit.assertThrows[.qetl.webhook.check;"webhook down";"webhook: no HTTP status line*";"a body alone"];
    .qunit.assertThrows[.qetl.webhook.check;"";"webhook: no HTTP status line*";"nothing"];
    .qunit.assertThrows[.qetl.webhook.check;4i;"webhook: no HTTP status line*";"not text at all"]};

test_the_request_carries_the_length_of_the_body:{[t]
    r:.qetl.webhook.request["hook.invalid:8080";"/t";"{\"a\":1}"];
    .qunit.assertEquals[first "\r\n" vs r;"POST /t HTTP/1.1";"the request line"];
    .qunit.assertEquals[0<count r ss "Content-Length: 7\r\n";1b;"Content-Length is the body's byte count"];
    .qunit.assertEquals[r like "*Host: hook.invalid:8080\r\n*";1b;"and the Host the URL named"];
    .qunit.assertEquals[(-7#r);"{\"a\":1}";"the body ends it"]};

\d .
