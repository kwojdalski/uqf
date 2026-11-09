// data.q - optional Databento parquet data access, keyed by symbol and
// date. Not loaded by src/init.q: it needs KDB-X's module loader for KX's
// native pq module, and a local
// .env pointing at the data. Load explicitly with `\l src/integrations/data.q`.
//
// Expects DATABENTO_DATA_DIR (from a repo-root .env file, or the OS
// environment) to point at a folder laid out as:
//   <DATABENTO_DATA_DIR>/<SYMBOL>/<SYMBOL>_<YYYY-MM-DD>_raw_mbp-10_us_hours.parquet
// .

\d .qdata

/ Parse a .env-style file into a dict, by the rules uqs's own reader
/ (uqs.stack.envfiles.parse_dotenv, #2395) applies, so a q started directly
/ and one started under uqs read the same values (#2544): blank lines and `#`
/ comments are skipped, an `export` prefix is allowed, the key and value are
/ stripped, a value in matching quotes is what is between them, an unquoted
/ value ends at ` #`, and a line that is not an assignment is skipped. A key
/ written twice keeps its last value. Missing file -> empty dict, so this is
/ safe to call unconditionally at load time.
/ @param file the .env file to parse, e.g. `:.env`
/ @return a symbol!string dict of the parsed key/value pairs
/ @eg .qdata.parseEnv[`:.env]
parseEnv:{[file]
    if[()~key file; :()!()];
    kvs:{x where 0<count each x} parseEnvLine each read0 file;
    if[0=count kvs; :()!()];
    k:first each kvs;
    (distinct k)#(reverse k)!reverse last each kvs};

/ Whitespace stripped from both ends; q's trim strips spaces only.
/ @param s a string
/ @return s without leading or trailing spaces, tabs, CRs or newlines
/ @eg .qdata.envStrip["\t a \r"]
envStrip:{[s]
    keep:not s in " \t\r\n";
    $[any keep; (first where keep)_(1+last where keep)#s; ""]};

/ One .env line as (key;value), or () when it is not an assignment.
/ @param line a line of a .env file
/ @return a (symbol;string) pair, or () for a blank, comment or non-assignment
/ @eg .qdata.parseEnvLine["export KEY = 'v' # note"]
parseEnvLine:{[line]
    line:envStrip[line];
    if[(0=count line) or "#"=first line; :()];
    if[("export"~6#line) and (6<count line) and line[6] in " \t"; line:envStrip[6_line]];
    i:line?"=";
    if[i=count line; :()];
    k:envStrip[i#line];
    if[(0=count k) or (first[k] in .Q.n) or not all k in .Q.an; :()];
    (`$k;parseEnvValue[envStrip[(i+1)_line]])};

/ A stripped .env value: the text between matching quotes when only
/ whitespace or a ` #` comment follows the closing one, else the text
/ before ` #`.
/ @param v the value as written, already stripped
/ @return the value as uqs reads it
/ @eg .qdata.parseEnvValue["\"/data/x\" # note"]
parseEnvValue:{[v]
    if[(1<count v) and first[v] in "\"'";
        rests:{[v;e] (e+1)_v}[v] each ends:1+where first[v]=1_v;
        ok:{[r] s:envStrip[r]; (0=count s) or ("#"=first s) and r[0] in " \t\r\n"} each rests;
        if[any ok; :1_(ends first where ok)#v]];
    comment:v ss " #";
    envStrip[$[count comment; first[comment]#v; v]]};

/ Parsed contents of the repo-root .env file, if present - see parseEnv.
env:parseEnv[`:.env];

/ Look up a config value: the OS environment first, then the parsed .env
/ dict, then the given default.
/ @param k the variable name, e.g. `DATABENTO_DATA_DIR`
/ @param dflt fallback value, or generic null (::) to error if unset
/ @return the string value
/ @throws configMissing if k isn't set anywhere and no default was given
/ @eg .qdata.cfg[`DATABENTO_DATA_DIR;::]
cfg:{[k;dflt]
    osVal:getenv k;
    if[not osVal~""; :osVal];
    if[k in key env; :env k];
    if[dflt~(::); '"cfg: missing ",string[k]," (set it in .env or the OS environment)"];
    dflt};

/ Root folder for locally-available Databento parquet dumps
/ (DATABENTO_DATA_DIR), one subfolder per symbol.
/ @return the configured folder path, as a string
/ @throws configMissing if DATABENTO_DATA_DIR isn't set anywhere
/ @eg .qdata.databentoDir[]
databentoDir:{[] cfg[`DATABENTO_DATA_DIR;::]};

/ Expected path to one symbol/date's daily Databento MBP-10 parquet file.
/ @param sym ticker symbol, e.g. `AAPL`
/ @param date a q date, e.g. 2026.02.25
/ @return an hsym (file symbol) - the file need not exist yet
/ @eg .qdata.databentoFile[`AAPL;2026.02.25]
databentoFile:{[sym;date]
    dateStr:ssr[string date;".";"-"];
    symStr:upper string sym;
    `$":",databentoDir[],"/",symStr,"/",symStr,"_",dateStr,"_raw_mbp-10_us_hours.parquet"};

/ Private: the memo cell for pqModule below. Declared BEFORE that function's
/ doc block rather than between it and the definition: a qDoc block belongs to
/ whatever follows it, so sitting in the middle made the docs describe `pqm`
/ while their own @eg named `pqModule`.
/ @private
pqm:(::);

/ KX's official pq module (github kx.com KDB-X distribution), loaded and
/ memoized on first use rather than at file-load time, so simply loading
/ data.q doesn't require a module-capable interpreter.
/ @return the pq module namespace, exposing `.pq` (open file), `.op`/`.rd`
/         (low-level access)
/ @throws if `use` (KDB-X's module loader) is unavailable
/ @eg .qdata.pqModule[][`pq]
pqModule:{[]
    if[pqm~(::); pqm::use`kx.pq];
    pqm};

/ Open one symbol/date's Databento parquet file as a lazy, row-group-pruned
/ virtual table (via pqModule) - select from it like a normal table, e.g.
/   select avg price by 1 xbar ts_event.minute from
/     .qdata.getBySymbolDate[`AAPL;2026.02.25]
/ @param sym ticker symbol, e.g. `AAPL`
/ @param date a q date, e.g. 2026.02.25
/ @return a virtual table handle over the matching parquet file
/ @throws fileNotFound if no file exists for that symbol/date
/ @eg .qdata.getBySymbolDate[`AAPL;2026.02.25]
getBySymbolDate:{[sym;date]
    f:databentoFile[sym;date];
    if[()~key f; '"getBySymbolDate: no file for ",string[sym]," on ",string[date],": ",1_string f];
    pqModule[][`pq] f};

\d .
