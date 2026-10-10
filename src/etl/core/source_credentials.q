/ source_credentials.q - credentials, and the sources.csv settings that say what a source connects to.
/ .
/ Part of the external-source contract (.qetl.source): one namespace spread over
/ several files, loaded in order by src/etl/init.q after source_contract.q, which
/ carries the design record. Public names are unchanged by the split (#970).

\d .qetl.source

/ ---------------------------------------------------------- CREDENTIALS

/ The environment variable holding a source's credential.
/ .
/ Mechanical from the source name, so an operator can guess it. Deliberately
/ a separate prefix from .qetl.cfg's UQF_: a credential is not configuration,
/ and keeping the namespaces apart means a credential can never arrive
/ through the YAML or overrides layer by accident.
credential_var:{[source] "UQF_SOURCE_CRED_",upper string source}

/ --------------------------------------------------- CONFIGURED SETTINGS
/ .
/ What a source connects to, from a CSV rather than a per-source variable
/ (#718). One row per source: its transport, the setting that transport's
/ `open` takes - an HDB path, a host:port, an ODBC connection template - and,
/ when that setting needs a secret, the NAME of the environment variable
/ holding it. The secret itself never enters the file: a `{secret}` in the
/ setting is replaced by that variable's value when the source connects, and
/ an inline password is refused when the file is read.
/ .
/ WHICH FILE is not this file's business. The core only reads the one it is
/ handed: inside TorQ, .qtorq.load_source_settings picks it from TorQ's own
/ config layers (scripts/processes/torq_pipeline.q), and a plain q process
/ that loads none simply has no settings - every source then runs exactly as
/ before, on UQF_SOURCE_CRED_<SOURCE> or its fixture.
/ .
/ PRECEDENCE. UQF_SOURCE_CRED_<SOURCE>, when set, wins over a row: it is the
/ explicit per-run override for CI, Airflow and containers. A row that is
/ there but wrong - a stub, a transport the source does not declare, a
/ secret variable that is unset - FAILS the run. It never falls back to the
/ fixture: a configured source that cannot connect is an outage, and the
/ fixture would turn it into synthetic data that coverage records as real.

/ The columns of a settings file, in order. Exactly these: an extra column is
/ refused, because a `password` column is the obvious way to get it wrong.
settings_cols:`source`transport`setting`secret_env

/ The loaded settings, one row per source, with the file each came from.
settings:([source:`symbol$()] transport:`symbol$(); setting:(); secret_env:`symbol$(); origin:())

/ The environment variables a setting may name as ${VAR}: runtime-derived
/ directories, set by whoever loads the file. Empty until then.
settings_path_vars:`symbol$()

/ What `uqs config sources stub` writes for a source nobody has configured
/ yet. Refused when the source connects, so a stub cannot pass for a setting.
/ Built rather than spelled: test_no_scaffold_left.py fails on the marker
/ anywhere under src/, and this is the one place that must hold it.
settings_stub:upper "scaffolded"

/ Where a setting takes its secret.
secret_placeholder:"{secret}"

/ Keys of a key=value setting whose value is a secret, lowercased.
secret_keys:("pwd";"password";"passwd";"secret";"token";"apikey";"api_key";"key")

/ Does a setting carry a secret inline? A key=value pair (an ODBC string)
/ whose key is one of secret_keys and whose value is not {secret}, or an IPC
/ host:port:user:password whose password is not.
/ @param tr the row's transport
/ @param setting the row's setting
/ @return 1b when the setting holds a secret it should be referencing
/ @eg .qetl.source.inline_secret[`odbc;"DRIVER=x;PWD=hunter2"]  ->  1b
/ @eg .qetl.source.inline_secret[`odbc;"DRIVER=x;PWD={secret}"]  ->  0b
/ @eg .qetl.source.inline_secret[`ipc;"db1:5010:svc:hunter2"]  ->  1b
inline_secret:{[tr;setting]
    pairs:{x where "="in/:x} ";" vs setting;
    ks:{lower trim (x?"=")#x} each pairs;
    vals:{trim (1+x?"=")_x} each pairs;
    kv:any ({any secret_keys~\:x} each ks) and not vals~\:secret_placeholder;
    parts:":" vs setting;
    ipc:(`ipc~tr) and (4<=count parts) and not secret_placeholder~last parts;
    kv or ipc}

/ Read a settings file into the shape of `settings`, or refuse naming the
/ file, the line and what is wrong with it. Reads only: nothing is loaded.
/ @param path the file, as a symbol (hsym or not) or a string
/ @return a table keyed on source, one row per line of the file
/ @throws error on a missing header column, an extra one, a row missing its
/   source, transport or setting, an unknown transport, a duplicated source,
/   or a secret written inline
read_settings:{[path]
    p:hsym $[10h=type path; `$path; path];
    src_file:1_string p;
    who:"source settings ",src_file;
    if[()~key p; 'who,": no such file"];
    raw:read0 p;
    / Blank lines are skipped, but every message names the row's line IN THE
    / FILE: numbering what was kept put each row after a blank line one line
    / early, so an error pointed at the blank line itself (#803).
    keep:where 0<count each trim each raw;
    lines:raw keep;
    if[0=count lines; 'who,": empty - it needs a header, ",", " sv string settings_cols];
    hdr:`$trim each "," vs first lines;
    if[count missing:settings_cols except hdr; 'who,": its header lacks ",", " sv string missing];
    if[count extra:hdr except settings_cols;
        'who,": ",(", " sv string extra)," is not a column. A secret goes in an environment variable named by secret_env"];
    if[not hdr~settings_cols; 'who,": its columns must be in the order ",", " sv string settings_cols];
    / Every field as text, then trimmed, so " ipc" is ipc - as uqs reads it.
    t:$[1=count lines; flip settings_cols!(`symbol$();`symbol$();();`symbol$());
        [cells:("****";",") 0: 1 _ lines;
         flip settings_cols!(`$trim each cells 0;`$trim each cells 1;trim each cells 2;`$trim each cells 3)]];
    t:update origin:(count t)#enlist src_file from t;
    line_no:1+1_keep;
    if[count bad:where (null t`source) or (null t`transport) or 0=count each t`setting;
        'who,": line ",(string line_no first bad)," needs a source, a transport and a setting"];
    if[count bad:where not (t`transport) in transports[];
        'who,": line ",(string line_no first bad),"'s transport ",(string t[`transport] first bad),
         " is not one of ",", " sv string transports[]];
    if[count dup:where 1<count each group t`source;
        'who,": ",(string first dup)," has more than one row - lines ",", " sv string line_no (group t`source) first dup];
    if[count bad:where inline_secret'[t`transport;t`setting];
        'who,": line ",(string line_no first bad)," holds a secret inline - write {secret} and name its variable in secret_env"];
    `source xkey t}

/ Load a settings file, replacing whatever was loaded before.
/ @param path the file, as read_settings takes it
/ @param path_vars the environment variables a setting may use as ${VAR}
/ @return how many sources it configures
/ @throws whatever read_settings throws
load_settings:{[path;path_vars]
    t:read_settings[path];
    `.qetl.source.settings set t;
    `.qetl.source.settings_path_vars set (),path_vars;
    .[{.qetl.log.dbg[x;y;z]};(`source_settings;"settings loaded";`file`sources!(first exec origin from 0!t;count t));::];
    count t}

/ Forget every loaded setting, so sources fall back to the environment and
/ their fixtures. For tests and for a process that reloads.
clear_settings:{[]
    `.qetl.source.settings set 0#settings;
    `.qetl.source.settings_path_vars set `symbol$();}

/ The sources the loaded settings configure.
configured:{[] exec source from 0!settings}

/ Where a source's credential would come from now: `env when its variable is
/ set, `settings when a loaded row configures it, `none otherwise. Never the
/ value.
/ @param source a source name
/ @return `env, `settings or `none
credential_origin:{[source]
    $[0<count getenv `$credential_var[source]; `env;
      source in configured[]; `settings;
      `none]}

/ Replace each ${VAR} in a setting with that variable's value, when VAR is
/ one of settings_path_vars and is set; refuse any other.
/ @param who the prefix for an error
/ @param txt the setting
/ @return the setting with every ${VAR} expanded
expand_path_vars:{[who;txt]
    while[0<count hits:txt ss "${";
        b:first hits;
        e:b+(b _ txt)?"}";
        if[e>=count txt; 'who,": an unclosed ${ in its setting"];
        nm:`$(b+2)_e#txt;
        if[not nm in settings_path_vars;
            'who,": ${",(string nm),"} is not a path it may use - only ",", " sv string settings_path_vars];
        v:getenv nm;
        if[0=count v; 'who,": ${",(string nm),"} is not set in this process"];
        txt:(b#txt),v,(e+1)_txt];
    txt}

/ A configured source's setting, ready for its transport's `open`: ${VAR}s
/ expanded and {secret} replaced from secret_env. Refuses a stub, a
/ transport the source does not declare, and a secret that is not there.
/ @param source a source the loaded settings configure
/ @return the setting, as a string - which may now hold a secret, so never log it
/ @throws error naming the source, what is wrong, and the file it came from
resolve_setting:{[source]
    r:settings source;
    who:"source ",(string source)," in ",r`origin;
    if[settings_stub~r`setting; 'who," is still the scaffold's stub - write its setting, or delete the row to run on the fixture"];
    tr:(def[source])`transport;
    if[not tr~r`transport; 'who," says transport ",(string r`transport),", but the source declares ",string tr];
    txt:expand_path_vars[who;r`setting];
    uses:0<count txt ss secret_placeholder;
    if[uses and null r`secret_env; 'who,": its setting has {secret}, but secret_env names no variable"];
    if[(not uses) and not null r`secret_env; 'who,": secret_env is set, but its setting has no {secret} to put it in"];
    if[not uses; :txt];
    v:getenv r`secret_env;
    if[0=count v; 'who,": its secret_env, ",(string r`secret_env),", is not set"];
    ssr[txt;secret_placeholder;v]}

/ Read a source's credential, or refuse.
/ .
/ UQF_SOURCE_CRED_<SOURCE> first, then a loaded settings row (see
/ CONFIGURED SETTINGS above). Nothing secret is ever read from a file: a row
/ only names the environment variable a secret is in. The error names both
/ ways to configure the source, since that is all the operator needs.
/ @throws error when neither configures the source, or its row is wrong
/ `env_var`, not `var`: var is a q BUILTIN (variance), so assigning it as a
/ lambda LOCAL throws `assign at load time and aborts the rest of the file.
/ Sixth reserved-name collision here, and the first as a local rather than a
/ parameter - check_q_traps.py now covers both.
require_credentials:{[source]
    def source;
    env_var:credential_var[source];
    v:getenv `$env_var;
    origin:credential_origin[source];
    / Where it came from - never the value, which may be a credential.
    .[{.qetl.log.dbg[x;y;z]};(source;"credential lookup";`var`present`origin!(env_var;0<count v;origin));::];
    if[`env~origin; :v];
    if[`settings~origin; :resolve_setting[source]];
    '"require_credentials: ",string[source]," has no credential - set ",env_var,
     ", or give it a row in sources.csv. Nothing secret lives in this repository"}

/ Is a credential available? For deciding between the live and fixture paths
/ without throwing. A configured row counts even if it is wrong: that run
/ must fail on it, not quietly read the fixture.
has_credentials:{[source] not `none~credential_origin[source]}

/ Must every source be live? UQS_REQUIRE_LIVE_SOURCES=1 - what a deployment's
/ --live writes into its deploy.env (#800) - turns a missing credential from
/ the declared way to run on the fixture into a refusal: a server meant to
/ read real data must never publish synthetic rows, or record their windows
/ as covered, because one credential was not provisioned.
/ Compared as a symbol: getenv returns a string, and "1" is a char atom.
/ @return 1b when UQS_REQUIRE_LIVE_SOURCES is 1, else 0b
/ @eg .qetl.source.live_required[]  ->  0b
live_required:{[] `1~`$getenv `UQS_REQUIRE_LIVE_SOURCES}

/ Refuse to read `source`'s fixture when every source must be live; return
/ quietly otherwise. Called where a missing credential would select the
/ fixture: a bounded worker's init, and a polling feed's tick.
/ @param who the caller, leading the message
/ @param source the source that has no credential
/ @throws error naming the source and its variable, when live_required[]
refuse_fixture:{[who;source]
    if[live_required[];
        'who,": ",string[source]," has no credential and UQS_REQUIRE_LIVE_SOURCES=1 - refusing its fixture. Set ",
         credential_var[source],", or give it a row in sources.csv"]}

/ May a bounded worker WRITE its source's fixture (#1082)? The fixture_writes
/ flag - UQF_FIXTURE_WRITES, or `uqs backfill --fixture` - and off by
/ default. A run with no credential used to publish synthetic rows into the
/ dataset and record their windows as covered whenever a credential was
/ simply missing, so the first live run after one was provisioned found the
/ range done and fetched nothing. Writing a fixture is now something asked
/ for. validate, plan and dry_run read it freely: they write nothing.
/ Protected like the run mode: a loader without .qetl.cfg has no flag set.
/ @return 1b when fixture writes were asked for, else 0b
/ @eg .qetl.source.fixture_writes_allowed[]
fixture_writes_allowed:{[] @[{.qetl.cfg.get_flag `fixture_writes};::;{0b}]}

/ Refuse a run that would write `source`'s fixture unless that was asked for.
/ @param who the caller, leading the message
/ @param source the source that has no credential
/ @throws error naming the source, its variable and the opt-in
refuse_fixture_writes:{[who;source]
    if[not fixture_writes_allowed[];
        'who,": ",string[source]," has no credential - refusing to write its fixture. Set ",
         credential_var[source],", or UQF_FIXTURE_WRITES=1 (uqs backfill --fixture) for a demo"]}

/ The source_version a run on the fixture records coverage under: the
/ release asked for, tagged. A fixture's coverage is a claim about the
/ fixture, not about the source, so it must never answer "is this window
/ done?" for a live run of the same release (#1082). Already-tagged is left
/ as it is.
/ @param v the release asked for
/ @return v with ~fixture appended
/ @eg .qetl.source.fixture_version `v1  ->  `$"v1~fixture"
fixture_version:{[v]
    s:string v;
    $[s like "*~fixture"; v; `$s,"~fixture"]}

/ What this source's credential looks like, for an operator who has not set
/ one. The source's own `credential_example` when it declared one, and
/ otherwise the most that can be said from its transport alone.
/ @param source a registered source
/ @return the example, as a string
/ @eg .qetl.source.credential_example `crypto_market_data  ->  "DRIVER=DuckDB;Database=/path/live.duckdb;access_mode=READ_ONLY"
/ @eg .qetl.source.credential_example `demo_deals  ->  "localhost:5010"
/ .
/ Tests the VALUE, not `in key d`, and that is not defensive coding. The
/ declarations share one stored value list - see `store row_key NORMALISED`
/ in define - so the moment ONE source declares a twelfth key, q pads every
/ other declaration with a null of the matching type. `credential_example in
/ key d` is therefore 1b for every source in the tree, including the ones
/ that never declared it, and an empty string is what "did not declare" looks
/ like from here.
credential_example:{[source]
    d:def[source];
    ex:$[`credential_example in key d; d`credential_example; ""];
    if[0<count ex; :ex];
    (transport_def[d`transport])`example}

\d .
