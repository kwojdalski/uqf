/ scripts/torqcode/handlers/loadpassword.q - TorQ's .servers.loadpassword,
/ fixed for a KDBSERVCONFIG layer.
/ .
/ THE BUG, UPSTREAM. lib/torq/code/handlers/trackservers.q reads the
/ outgoing username:password from passwords/<name>.txt, looked up for
/ default, the parent proctype, the proctype and the procname, through
/ .proc.getconfig[path;2]. That returns every layer's path, most specific
/ first: (app; serv; base) with KDBSERVCONFIG set, (app; base) without. The
/ loader picks columns `1 0`, which is base-then-app only while there are
/ exactly two. With three it reads serv and app and never the base - and the
/ base, lib/torq/config/passwords, is where every process's own credential
/ lives. uqs sets KDBSERVCONFIG (scripts/torqconfig, the log-level names),
/ so from #698 on no process loaded one: rdb1 and wdb1 sat in startup while
/ listening on their ports, and everything that called them timed out.
/ .
/ THE FIX, HERE. Reversing each path list puts base first however many
/ layers there are, then serv, then app, so later files still override
/ earlier ones exactly as before. It is the fix proposed upstream; until TorQ
/ carries it, this file applies it without editing the vendored tree.
/ .
/ HOW IT LOADS. uqs points KDBSERVCODE at scripts/torqcode
/ (uqs.stack.env.build_env), and torq.q loads <dir>/handlers for KDBCODE,
/ then KDBSERVCODE, then KDBAPPCODE - so this runs straight after
/ trackservers.q has defined and run the broken loader, and before any
/ process opens a connection. It redefines the loader and runs it again.

\d .servers

/ The password files to read, in the order to read them: every name's base
/ file, then every name's service file, then every name's application file,
/ so a more specific layer overrides a less specific one.
/ @param paths one .proc.getconfig[;2] result per name, each most specific first
/ @return the file paths to try, in load order, without duplicates or nulls
/ @eg .servers.passwordfiles (`a1`s1`b1;`a2`s2`b2)  ->  `b1`b2`s1`s2`a1`a2
passwordfiles:{[paths] distinct[raze flip reverse each paths] except `}

/ TorQ's loader, with the file order from passwordfiles. Otherwise as
/ trackservers.q has it: each file found sets USERPASS, so the last wins.
loadpassword:{
    .lg.o[`conn;"attempting to load external connection username:password from file"];
    loadpassfile:{[file]
         $[()~key hsym file;
           .lg.o[`conn;"password file ",(string file)," not found"];
           [.lg.o[`conn;"password file ",(string file)," found"];
            .servers.USERPASS:first`$read0 hsym file]]};
    files:{.proc.getconfig["passwords/",(string x),".txt";2]} each `default,.proc.parentproctype,.proc.proctype,.proc.procname;
    loadpassfile each passwordfiles files;
    }

\d .

/ Only inside TorQ, where trackservers.q has run: a test loads this file to
/ check passwordfiles, with no .proc to read paths from.
if[100h<=type @[value;`.proc.getconfig;::]; .servers.loadpassword[]];
