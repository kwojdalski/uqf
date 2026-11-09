// smoke_kdb_fix_adaptor.q - the vendored FIX adaptor's q dictionary <-> FIX
// wire conversion, through a built library and a real QuickFIX session.
//
// lib/kdb-fix-adaptor (vendored, never edited here) converts in C++:
//   .fix.send[dict]   q dict (long tag -> value) -> FIX tag=value (typedtostring)
//   .fix.onrecv[dict] FIX tag=value -> q dict, typed by FIX42.xml (convertmsgtype)
// This drives the cycle dict0 -> wire1 -> dict1 -> wire2 -> dict2 -> ... between
// an acceptor and an initiator, each its own q process on 127.0.0.1, and checks
// what survives. Every check states the CORRECT contract:
//   PASS / FAIL    a guarantee the adaptor keeps (FAIL fails the run);
//   XFAIL / XPASS  a known defect, reproduced (XFAIL) or no longer reproduced
//                  (XPASS fails the run, so the check gets moved to a guarantee).
// Several defects kill the receiving q process, so every case starts a fresh
// acceptor/initiator pair.
//
// Not a qUnit suite and not in the default lane: it needs the native library,
// which needs QuickFIX, which nothing here installs. It SKIPs, exit 0, without
// one. To build one (macOS arm64, KDB-X; nothing outside the scratch dir):
//   git clone --depth 1 https://github.com/quickfix/quickfix $W/qf-src
//   cmake -S $W/qf-src -B $W/qf-build -DCMAKE_INSTALL_PREFIX=$W/qf \
//         -DHAVE_SSL=OFF -DHAVE_PYTHON=OFF -DQUICKFIX_EXAMPLES=OFF -DQUICKFIX_TESTS=OFF
//   make -C $W/qf-build -j4 quickfix install
//   (QuickFIX v1.15.1 does not build on arm64: x86 asm in AtomicCount.h.)
// The vendored CMakeLists.txt cannot be used as is: it configure_file()s a
// LICENSE.md the vendored tree names LICENSE-kdb-fix-adaptor, and forces
// -std=c++11 where QuickFIX master needs C++17. Compile directly instead,
// through a one-line shim, because main.cxx calls to_time_t on
// high_resolution_clock, which is system_clock under libstdc++ (upstream's
// Linux target) but steady_clock under libc++:
//   V=lib/kdb-fix-adaptor
//   sed 's/@[A-Z_]*@/x/' $V/config.h > $W/inc/config.h
//   printf '#include <chrono>\n#define high_resolution_clock system_clock\n#include "%s"\n' \
//          "$PWD/$V/src/main.cxx" > $W/shim.cxx
//   clang++ -std=c++17 -Wno-dynamic-exception-spec -Wno-deprecated -Os -DKXVER=3 \
//     -fpic -shared -undefined dynamic_lookup -I$W/qf/include -I$V/third_party/kx/include \
//     -I$V/third_party/pugixml-1.7/src -I$V/src -I$W/inc $W/shim.cxx \
//     $V/third_party/pugixml-1.7/src/pugixml.cpp -L$W/qf/lib -lquickfix \
//     -Wl,-rpath,$W/qf/lib -o $W/kdbfix.so
//
// Run from the repository root:
//   q tests/q/smoke_kdb_fix_adaptor.q -lib $W/kdbfix.so [-port 17391]
// or set UQF_KDBFIX_LIB. Uses ports port..port+2 and build/kdbfix-smoke.
// The raw-client cases (inbound repeating groups, a timestamp without
// milliseconds) need python3 on the PATH and are skipped without it.

\c 400 1000
\P 17

opts:.Q.opt .z.x;
lib:$[`lib in key opts; first opts`lib; getenv`UQF_KDBFIX_LIB];
if[0=count lib;
    -1 "SKIP  no adaptor library - pass -lib path/to/kdbfix.so or set UQF_KDBFIX_LIB (build steps in this file's header)";
    exit 0];
if[()~key hsym `$lib;
    -1 "SKIP  ",lib," does not exist";
    exit 0];

fix_port:$[`port in key opts; "J"$first opts`port; 17391];
acc_port:fix_port+1;
ini_port:fix_port+2;
qbin:first .z.X;
vendored:"lib/kdb-fix-adaptor/src/config/";
dir:(first system "pwd"),"/build/kdbfix-smoke";
has_python:0<count @[system;"command -v python3";{()}];

/ --- the runtime directory ----------------------------------------------------
/ fix.q is a CMake template: its first line loads the library through
/ @BINARY_NAME@. The library reads ./spec/FIX42.xml relative to the process's
/ working directory when it loads, so every node runs from dir.

system "rm -rf ",dir;
system "mkdir -p ",dir,"/sessions";
system "cp ",lib," ",dir,"/kdbfix.so";
system "cp -R ",vendored,"spec ",dir,"/spec";
(hsym `$dir,"/fix.q") 0: ssr[;"@BINARY_NAME@";"kdbfix"] each read0 hsym `$vendored,"fix.q";

/ A node: loads the adaptor, records every inbound message in `got`.
(hsym `$dir,"/kdbfix_smoke_node.q") 0: (
    "\\l fix.q";
    "o:.Q.opt .z.x";
    "got:()";
    ".fix.onrecv:{[x] got,:enlist x}";
    "last_reject:{[] g:got where {(enlist \"3\")~x 35} each got; $[count g; (last g) 58; \"\"]}";
    ".fix.create[`$first o`role;`$\":sessions/\",(first o`ini),\".ini\"]");

/ Session settings. version is "4.2" or "4.4"; extra is more key=value lines.
ini_lines:{[version;role;extra]
    v:ssr[version;".";""];
    common:("[DEFAULT]";"BeginString=FIX.",version;"SocketNodelay=Y";
        "FileStorePath=store_",role;"FileLogPath=log_",role;
        "StartTime=00:00:00";"EndTime=00:00:00";"HeartBtInt=30";"ReconnectInterval=1";
        "DataDictionary=spec/FIX",v,".xml"),extra;
    sess:$[role~"acc";
        ("";"[SESSION]";"ConnectionType=acceptor";"SocketAcceptPort=",string fix_port;
            "SocketReuseAddress=Y";"SenderCompID=BROKER";"TargetCompID=UQF");
        ("";"[SESSION]";"ConnectionType=initiator";"SocketConnectHost=127.0.0.1";
            "SocketConnectPort=",string fix_port;"SenderCompID=UQF";"TargetCompID=BROKER")];
    common,sess}
write_ini:{[name;lines] (hsym `$dir,"/sessions/",name,".ini") 0: lines}
write_ini["acc42";ini_lines["4.2";"acc";()]];
write_ini["ini42";ini_lines["4.2";"ini";()]];
write_ini["acc42udf";ini_lines["4.2";"acc";enlist "ValidateUserDefinedFields=N"]];
write_ini["acc44";ini_lines["4.4";"acc";()]];
write_ini["ini44";ini_lines["4.4";"ini";()]];

/ A raw FIX 4.2 initiator, for what the adaptor cannot send: it logs on, then
/ sends each argument ("35=D|11=..."), filling in 8, 9, 34, 49, 52, 56 and 10.
(hsym `$dir,"/raw_fix_client.py") 0: (
    "import socket, sys, time, datetime";
    "SOH = '\\x01'";
    "def build(fields):";
    "    body = ''.join(f'{t}={v}{SOH}' for t, v in fields)";
    "    msg = f'8=FIX.4.2{SOH}9={len(body.encode())}{SOH}' + body";
    "    return (msg + f'10={sum(msg.encode()) % 256:03d}{SOH}').encode()";
    "now = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H:%M:%S.000')";
    "hdr = lambda seq, mt: [('35', mt), ('34', seq), ('49', 'UQF'), ('52', now), ('56', 'BROKER')]";
    "s = socket.create_connection(('127.0.0.1', int(sys.argv[1])))";
    "s.settimeout(2)";
    "s.sendall(build(hdr(1, 'A') + [('98', '0'), ('108', '30')]))";
    "s.recv(4096)";
    "for i, m in enumerate(sys.argv[2:]):";
    "    f = [tuple(x.split('=', 1)) for x in m.split('|')]";
    "    s.sendall(build(hdr(i + 2, f[0][1]) + f[1:]))";
    "time.sleep(0.5)");

/ --- driving a pair -----------------------------------------------------------

sleep:{[s] system "sleep ",string s}
connect:{[p] k:0; h:0Ni; while[null[h] and k<100; h:@[hopen;p;{[e] 0Ni}]; if[null h; sleep 0.05]; k+:1]; h}
alive:{[h] 1~@[h;"1";{[e] 0}]}

stop:{[]
    {@[hclose;x;{[e] ::}]} each @[get;;{[e] 0Ni}] each `A`I;
    @[system;"pkill -9 -f kdbfix_smoke_node.q";{[e] ::}];
    / Until they are gone: a node still holding its port would answer the
    / next case in place of the fresh one.
    k:0; while[(0<count @[system;"pgrep -f kdbfix_smoke_node.q";{[e] ()}]) and k<100; sleep 0.05; k+:1];
    }

/ A fresh acceptor (and initiator, unless ini is "") with clean stores, the
/ acceptor under time zone tz. Returns once the initiator is logged on.
start:{[acc;ini;tz]
    stop[];
    system "rm -rf ",dir,"/store_* ",dir,"/log_*";
    launch:{[tz;role;ini;port]
        / In a subshell: q reads a system command starting "cd" as its own \cd.
        system "(cd ",dir," && TZ=",tz," ",qbin," kdbfix_smoke_node.q -role ",role," -ini ",ini,
            " -p ",string[port]," -q > ",role,".out 2>&1 &)"};
    / A listener left by something else would answer in place of the fresh node.
    ports:acc_port,ini_port;
    hs:{@[hopen;(hsym `$"::",string x;500);{[e] 0Ni}]} each ports;
    {@[hclose;x;{[e] ::}]} each hs where not null hs;
    if[any not null hs; '"port ",(string first ports where not null hs)," is in use - pass -port to move all three"];
    launch[tz;"acceptor";acc;acc_port];
    `A set connect acc_port;
    if[count ini;
        launch["UTC";"initiator";ini;ini_port];
        `I set connect ini_port;
        k:0; while[(1>@[I;"count got";{[e] 0}]) and k<100; sleep 0.05; k+:1]];
    }

cnt:{[] @[A;"count got";{[e] -1}]}
await:{[n] k:0; while[(n=cnt[]) and k<100; sleep 0.02; k+:1];
    c:cnt[]; $[c<0; `acceptor_died; n<c; A"last got"; `timeout]}
/ Send from the initiator; what the acceptor's .fix.onrecv got, or why not.
rt:{[msg] n:cnt[]; r:@[I;(`.fix.send;msg);{[e] `$"send: ",e}]; $[-11h=type r; r; await n]}
/ Send from the raw client.
raw:{[msgs] n:cnt[];
    system "python3 -I ",dir,"/raw_fix_client.py ",string[fix_port]," ",
        " " sv {"'",x,"'"} each msgs;
    await n}

ord:(8 49 56 35 11 21 55 54 40 60)!("FIX.4.2";"UQF";"BROKER";enlist "D";"ORD1";enlist "1";"EURUSD";enlist "1";enlist "2";2016.03.04D14:21:36.567000000);
with:{[d;t;v] d,(enlist t)!enlist v}
describe:{[r] $[99h=type r; -3!r; string r]}
/ One field through the cycle: (received = expected; what happened).
field:{[t;v;want] r:rt with[ord;t;v];
    $[99h=type r; (want~r t; "sent ",(-3!v),", got ",-3!r t); (0b; "sent ",(-3!v),", ",string r)]}
same_field:{[t;v] field[t;v;v]}
/ The acceptor's inbound NewOrderSingles from the session log, minus the
/ fields the session layer rewrites on every send (34, 52, 10).
wire_nos:{[]
    f:dir,"/log_acc/FIX.4.2-BROKER-UQF.messages.current.log";
    lines:read0 hsym `$f;
    lines:lines where (lines like "*\00135=D\001*") and lines like "*\00149=UQF\001*";
    msgs:{(3+first x ss " : ")_x} each lines;
    {"\001" sv f where not any (f:"\001" vs x) like/: ("34=*";"52=*";"10=*")} each msgs}

/ --- checks -------------------------------------------------------------------

results:([] name:(); kind:`symbol$(); verdict:`symbol$(); detail:());
check:{[name;kind;outcome]
    ok:first outcome; detail:last outcome;
    verdict:$[kind=`guarantee; `FAIL`PASS ok; `XFAIL`XPASS ok];
    -1 (6$string verdict),name,$[count detail; " - ",detail; ""];
    `results upsert enlist `name`kind`verdict`detail!(name;kind;verdict;detail);
    }
/ A case on a fresh pair: setup is (acceptor ini; initiator ini; acceptor TZ).
case:{[name;kind;setup;f]
    start . setup;
    outcome:@[f;::;{[e] (0b;"case threw: ",e)}];
    check[name;kind;outcome];
    }
std:("acc42";"ini42";"UTC");
raw_only:("acc42";"";"UTC");

/ Guarantees.

case["dict0 -> dict1 keeps every field's meaning; re-sending dict1 is idempotent, typed and on the wire";`guarantee;std;{[]
    sess:9 10 34 52;
    d0:ord,(38 44 114)!(1000000f;1.25;1b);
    d1:rt d0;
    if[not 99h=type d1; :(0b;"no delivery: ",describe d1)];
    d2:rt sess _ d1;
    d3:rt sess _ d2;
    / The documented normalization: a symbol comes back as its string (55 is
    / the exception, a symbol), a 1-char string as a char atom, and the body
    / in tag order.
    norm:{[d] k:asc key d; k!{$[-11h=type x; string x; -10h=type x; enlist x; x]} each d k};
    sem:(norm d0)~norm sess _ d1;
    idem:((sess _ d1)~sess _ d2) and (sess _ d2)~sess _ d3;
    w:-3#wire_nos[];
    bytes:(3=count w) and 1=count distinct w;
    (sem and idem and bytes; "semantic d0~d1: ",string[sem],", d1~d2~d3: ",string[idem],
        ", wire1~wire2~wire3 (minus 34/52/10): ",string bytes)}];

case["booleans survive as Y/N";`guarantee;std;{[]
    a:same_field[114;1b]; b:same_field[114;0b]; (a[0] and b[0]; a[1],"; ",b[1])}];

case["'=' and spaces inside a value survive";`guarantee;std;{[] same_field[58;"a=b c"]}];

case["sub-millisecond time is truncated to the millisecond FIX 4.2 carries";`guarantee;std;{[]
    field[60;2016.03.04D14:21:36.567891000;2016.03.04D14:21:36.567000000]}];

case[".fix.send refuses a dict that is not long -> general list, with 'type";`guarantee;std;{[]
    a:@[I;(`.fix.send;(8 49 56 35)!`a`b`c`d);{[e] e}];
    b:@[I;(`.fix.send;(8 49i)!("FIX.4.2";"UQF"));{[e] e}];
    ((a~"type") and b~"type"; "symbol values: ",(-3!a),", int keys: ",-3!b)}];

/ QuickFIX refuses these at the RECEIVING engine, and the reason reaches the
/ sender's .fix.onrecv as a Reject (35=3) - asynchronously; .fix.send itself
/ returns as if it had succeeded (see the defects).
rejected:{[msg;reason]
    r:rt msg;
    k:0; why:""; while[(0=count why) and k<50; why:@[I;"last_reject[]";{[e] ""}]; if[0=count why; sleep 0.02]; k+:1];
    ((r~`timeout) and why~reason; "acceptor got: ",describe[r],", sender's Reject: ",-3!why)}
case["an empty value is refused by the counterparty with its reason";`guarantee;std;{[]
    rejected[with[ord;58;""];"Tag specified without a value"]}];
case["a header tag the adaptor writes into the body is refused by the counterparty";`guarantee;std;{[]
    rejected[with[ord;115;"ONBEHALF"];"Tag specified out of required order"]}];
case["an INT beyond 32 bits is refused by the counterparty";`guarantee;std;{[]
    rejected[with[ord;68;3000000000];"Incorrect data format for value"]}];
case["a repeating group cannot be sent: the counterparty refuses its count";`guarantee;std;{[]
    rejected[ord,(78 79 80)!(2;"A1";100f);"Incorrect NumInGroup count for repeating group"]}];

/ Known defects, each stated as the contract it breaks.

case[".fix.send signals when no session matches the message (the manual promises 'session)";`defect;std;{[]
    r:@[I;(`.fix.send;with[ord;56;"NOBODY"]);{[e] `$e}];
    (-11h=type r; "returned ",-3!r)}];
case[".fix.send refuses a value whose type it cannot render (a long vector)";`defect;std;{[]
    r:@[I;(`.fix.send;with[ord;58;1 2 3]);{[e] `$e}];
    (-11h=type r; "returned ",(-3!r),"; it sent the vector's raw bytes")}];
case[".fix.send refuses a dict with a repeated tag";`defect;std;{[]
    r:@[I;(`.fix.send;(key[ord],58 58)!value[ord],("first";"second"));{[e] `$e}];
    (-11h=type r; "returned ",(-3!r),"; the last value is sent, the first dropped")}];
case["an SOH inside a value cannot inject a field";`defect;std;{[]
    r:rt with[ord;58;"x\00144=9.99"];
    $[99h=type r; (not 44 in key r; "acceptor got 44=",-3!r 44); (1b; describe r)]}];
case["a price keeps its double precision (inbound parses with stof)";`defect;std;{[] same_field[44;1.23456]}];
case["a quantity keeps its digits (outbound renders with q's 7-digit string)";`defect;std;{[] same_field[38;1234567.89]}];
case["a large quantity is sent as a FIX number, not 1.234568e+08";`defect;std;{[] same_field[38;123456789f]}];
case["an exact-second timestamp is sent as that second";`defect;std;{[] same_field[60;2016.03.04D00:00:02.000000000]}];
case["milliseconds survive outside 2003.03.03-2031.09.09 (read from fixed string offsets)";`defect;std;{[]
    a:same_field[60;2035.03.04D14:21:36.567000000]; b:same_field[60;1999.03.04D14:21:36.567000000];
    (a[0] and b[0]; a[1],"; ",b[1])}];
case["a received timestamp is exact (inbound adds milliseconds as a double)";`defect;std;{[] same_field[60;2026.11.09D09:14:35.475000000]}];
case["a received UTCTimestamp does not depend on the receiver's TZ";`defect;("acc42";"ini42";"America/New_York");{[] same_field[60;2016.03.04D14:21:36.567000000]}];
case["an EXCHANGE field arrives as the symbol the type map declares";`defect;std;{[] same_field[207;`XLON]}];
case["a date can be sent twice (the first send frees the caller's atom)";`defect;std;{[]
    a:same_field[432;2016.07.04]; b:same_field[432;2016.03.04];
    (a[0] and b[0] and alive I; a[1],"; ",b[1],"; initiator alive: ",string alive I)}];
case["a Currency field does not abort the receiver (stof on CURRENCY)";`defect;std;{[] same_field[15;"USD"]}];
case["a 9000-byte message does not overflow the receiver's 4096/8192-byte buffers";`defect;std;{[]
    r:rt with[ord;58;9000#"x"];
    $[99h=type r; (9000=count r 58; "58 has ",string[count r 58]," chars"); (0b; describe r)]}];
case["a user-defined tag does not crash the receiver (no type-map entry)";`defect;("acc42udf";"ini42";"UTC");{[] same_field[5001;"custom"]}];
case["a FIX 4.4 session receives a FIX 4.4 field (the type map is FIX42.xml only)";`defect;("acc44";"ini44";"UTC");{[]
    r:rt with[@[ord;8;:;"FIX.4.4"];528;enlist "A"];
    $[99h=type r; ("A"~r 528; "got 528=",-3!r 528); (0b; describe r)]}];

$[has_python;
    [case["an inbound repeating group reaches q";`defect;raw_only;{[]
        r:raw enlist "35=D|11=ORD1|21=1|55=EURUSD|54=1|60=20160304-14:21:36.567|40=2|78=2|79=A1|80=100|79=A2|80=200";
        $[99h=type r; (79 in key r; "received tags ",-3!key r); (0b; describe r)]}];
     case["an inbound timestamp without milliseconds reads as .000";`defect;raw_only;{[]
        r:raw enlist "35=D|11=ORD1|21=1|55=EURUSD|54=1|60=20160304-14:21:36|40=2";
        $[99h=type r; ((r 60)~2016.03.04D14:21:36.000000000; "got ",-3!r 60); (0b; describe r)]}]];
    -1 "SKIP  raw-client cases: no python3 on the PATH"];

stop[];
bad:select from results where verdict in `FAIL`XPASS;
-1 "";
-1 string[count results]," checks: ",", " sv {string[x],"=",string y}'[key c;value c:count each group results`verdict];
if[count bad; -1 "FAILED: ",", " sv bad`name; exit 1];
exit 0
