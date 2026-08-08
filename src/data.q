// data.q - optional Databento parquet data access, keyed by symbol and
// date. Not loaded by src/init.q: it needs a real kdb+/KDB-X interpreter
// (PeachQ has no `2:`, so it can't load KX's native pq module) and a local
// .env pointing at the data. Load explicitly with `\l src/data.q`.
//
// Expects DATABENTO_DATA_DIR (from a repo-root .env file, or the OS
// environment) to point at a folder laid out as:
//   <DATABENTO_DATA_DIR>/<SYMBOL>/<SYMBOL>_<YYYY-MM-DD>_raw_mbp-10_us_hours.parquet
// .

\d .uqfdata

/ Parse a .env-style file (KEY=VALUE lines; blank lines and lines starting
/ with `#` are ignored) into a dict. Missing file -> empty dict, so this is
/ safe to call unconditionally at load time.
/ @param file the .env file to parse, e.g. `:.env`
/ @return a symbol!string dict of the parsed key/value pairs
/ @eg .uqfdata.parseEnv[`:.env]
parseEnv:{[file]
    if[()~key file; :()!()];
    lines:read0 file;
    lines:lines where not lines like "#*";
    lines:lines where 0<count each lines;
    lines:lines where lines like "*=*";
    kvs:{[l] i:first l ss "="; (`$i#l;(i+1)_l)} each lines;
    (first each kvs)!(last each kvs)};

/ Parsed contents of the repo-root .env file, if present - see parseEnv.
env:parseEnv[`:.env];

/ Look up a config value: the OS environment first, then the parsed .env
/ dict, then the given default.
/ @param k the variable name, e.g. `DATABENTO_DATA_DIR`
/ @param dflt fallback value, or generic null (::) to error if unset
/ @return the string value
/ @throws configMissing if k isn't set anywhere and no default was given
/ @eg .uqfdata.cfg[`DATABENTO_DATA_DIR;::]
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
/ @eg .uqfdata.databentoDir[]
databentoDir:{[] cfg[`DATABENTO_DATA_DIR;::]};

/ Expected path to one symbol/date's daily Databento MBP-10 parquet file.
/ @param sym ticker symbol, e.g. `AAPL`
/ @param date a q date, e.g. 2026.02.25
/ @return an hsym (file symbol) - the file need not exist yet
/ @eg .uqfdata.databentoFile[`AAPL;2026.02.25]
databentoFile:{[sym;date]
    dateStr:ssr[string date;".";"-"];
    symStr:upper string sym;
    `$":",databentoDir[],"/",symStr,"/",symStr,"_",dateStr,"_raw_mbp-10_us_hours.parquet"};

/ KX's official pq module (github kx.com KDB-X distribution), loaded and
/ memoized on first use rather than at file-load time, so simply loading
/ data.q doesn't require a module-capable interpreter.
/ @return the pq module namespace, exposing `.pq` (open file), `.op`/`.rd`
/         (low-level access)
/ @throws if `use` (KDB-X's module loader) is unavailable - real kdb+/KDB-X
/         is required here, not PeachQ
/ @eg .uqfdata.pqModule[][`pq]
pqm:(::);
pqModule:{[]
    if[pqm~(::); pqm::use`kx.pq];
    pqm};

/ Open one symbol/date's Databento parquet file as a lazy, row-group-pruned
/ virtual table (via pqModule) - select from it like a normal table, e.g.
/   select avg price by 1 xbar ts_event.minute from
/     .uqfdata.getBySymbolDate[`AAPL;2026.02.25]
/ @param sym ticker symbol, e.g. `AAPL`
/ @param date a q date, e.g. 2026.02.25
/ @return a virtual table handle over the matching parquet file
/ @throws fileNotFound if no file exists for that symbol/date
/ @eg .uqfdata.getBySymbolDate[`AAPL;2026.02.25]
getBySymbolDate:{[sym;date]
    f:databentoFile[sym;date];
    if[()~key f; '"getBySymbolDate: no file for ",string[sym]," on ",string[date],": ",1_string f];
    pqModule[][`pq] f};

\d .
