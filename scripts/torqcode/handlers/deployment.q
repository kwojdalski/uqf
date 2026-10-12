/ scripts/torqcode/handlers/deployment.q - TorQ discovers only the processes
/ this deployment started (#1207).
/ .
/ THE NOISE, UPSTREAM. process.csv lists every process uqs knows, whether or
/ not this start runs it, because torq.sh starts from it and `uqs start
/ <name>` must be able to name any of them. discovery1 registers a row for
/ every process.csv entry at startup, connected or not, and getservices hands
/ every row back - so a process that asks discovery for sortworkers (sort1,
/ via wdb.q's .wdb.sortworkertypes) is told about sortworker2 even under the
/ `essential` profile, which starts sortworker1 only. Its .servers.retry timer
/ then tries sortworker2's port every RETRY period, forever, logging
/ "connection to :<host>:6067 failed ... Connection refused" for a process
/ nobody started.
/ .
/ THE FIX, HERE. uqs writes the deployment set - the profile's resolved
/ members, plus every process started since, with what each needs - to the
/ file UQS_DEPLOYMENT_FILE names (python/uqs/src/uqs/stack/deployment.py).
/ .servers.register, which every row of a connection table goes through on
/ its way into .servers.SERVERS - from process.csv at startup, from
/ discovery's getservices on a refresh, from discovery's procupdate when a
/ process registers - is wrapped to drop the process.csv rows that are not
/ in it. The file is read on every call, so a process `uqs start` adds later
/ is discoverable from then on, and a refresh cannot bring back one it left
/ out. Nothing else changes: a row that is registered is retried and
/ reconnected exactly as TorQ does it, and its failures are logged as before.
/ .
/ What is NOT filtered: rows with no procname (DISCOVERY host:ports), and
/ rows for processes process.csv does not declare (non-TorQ processes,
/ anything started outside uqs's list). No file, or an empty one, filters
/ nothing - a stack started without a profile discovers as TorQ always has.
/ .
/ HOW IT LOADS. torq.q loads KDBCODE's handlers, then KDBSERVCODE's, so this
/ runs after trackservers.q has defined .servers.register. A process's own
/ code (gateway.q, monitor.q) loads later still, through `-load`, and sets
/ .servers.addprocscustom/.servers.connectcustom - hooks this leaves alone,
/ so they compose with it rather than being replaced by it.

\d .servers

/ The process.csv rows of connectiontab this deployment includes.
/ @param allowed symbol list: the deployment set; empty means no filter
/ @param declared symbol list: the procnames process.csv declares
/ @param connectiontab table with a procname column
/ @return connectiontab less the declared rows whose procname is not allowed
/ @eg .servers.indeployment[`sort1`sortworker1;`sort1`sortworker1`sortworker2;([]procname:`sort1`sortworker1`sortworker2)]  ->  ([]procname:`sort1`sortworker1)
indeployment:{[allowed;declared;connectiontab]
    if[0=count allowed;:connectiontab];
    select from connectiontab where (null procname) or (procname in allowed) or not procname in declared}

/ The deployment set uqs wrote, or an empty list when there is none to read.
/ Read fresh each call: `uqs start <name>` extends it while processes run.
/ @param path string: the file's path, "" for none
/ @return symbol list of procnames
/ @eg .servers.deploymentset ""  ->  `symbol$()
deploymentset:{[path]
    if[0=count path;:`symbol$()];
    lines:@[read0;hsym`$path;{[e] ()}];
    (`$lines where 0<count each lines) except `}

/ The procnames process.csv declares: startup's parsed copy once it has run.
declaredprocs:{[] exec procname from @[value;`.servers.procstab;{[e] ([]procname:`symbol$())}]}

/ register's filter: connectiontab as the deployment set lets it through,
/ logging what it leaves out so an excluded process is named, once per call.
/ @param connectiontab table with a procname column
/ @return the rows to register
deploymentfilter:{[connectiontab]
    kept:indeployment[deploymentset[getenv`UQS_DEPLOYMENT_FILE];declaredprocs[];connectiontab];
    if[count dropped:distinct (exec procname from connectiontab) except exec procname from kept;
        .lg.o[`conn;"not registering ",(", " sv string dropped)," - not in this deployment's process set (",getenv[`UQS_DEPLOYMENT_FILE],")"]];
    kept}

/ Wrap register once, and only where trackservers.q defined it: a test that
/ loads this file for the functions above has no .servers to wrap.
if[(100h<=type @[value;`.servers.register;::]) and not `registerall in key `.servers;
    registerall:register;
    register:{[connectiontab;proc;connect] registerall[deploymentfilter[connectiontab];proc;connect]}];

\d .
