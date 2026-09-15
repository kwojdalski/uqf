/ worker_config.q - typed, validated worker configuration with a stated
/ precedence (.qwcfg).
/ .
/ Implements the configuration half of requirement E-16, and the precedence
/ decision recorded on issue #71 (question-bank C-02):
/ .
/   env  >  process_overrides.csv  >  config/backfill.yaml  >  code default
/ .
/ Most specific and most immediate wins, so an operator can override anything
/ from the environment without editing tracked config - which is what you
/ want when debugging a running process.
/ .
/ Two obligations came with that answer and both are met here rather than
/ left as intent: the order is documented at the point of use, and
/ test_worker_config.q asserts it by setting the same key in two sources and
/ checking which one wins. An unstated precedence's failure mode is "works on
/ my machine", which this repository already hit once with H-01's three
/ process-definition files.
/ .
/ Errors accumulate rather than failing on the first problem (E-16, and the
/ shape canonical's own worker_config.q uses): a worker started with three
/ bad settings should be told about three, not discover them over three
/ restarts.

\d .qwcfg

/ ------------------------------------------------------------- PRECEDENCE

/ The sources, most significant first. Named rather than positional so a
/ diagnostic can say WHERE a value came from, which is the question an
/ operator actually asks.
sources:`env`overrides`yaml`default

/ Private: per-source lookup tables, populated by `set_layers`. Kept separate
/ rather than merged eagerly so `explain` below can report provenance.
env_values:(`symbol$())!();
override_values:(`symbol$())!();
yaml_values:(`symbol$())!();
default_values:(`symbol$())!();

/ Register the non-environment layers. Called by a worker's init, typically
/ from whatever parsed the YAML and the overrides CSV - this file does not
/ parse either format, because the Python orchestrator already owns those
/ and duplicating a parser is how the two drift.
/ @param overrides dict of key -> string value from process_overrides.csv
/ @param yaml dict of key -> string value from config/backfill.yaml
/ @param defaults dict of key -> string value, the code's own fallbacks
/ .
/ Named set_layers, not `load`: `load` is a q BUILTIN (the counterpart of
/ `save`), so defining it in a namespace throws `assign at load time and
/ aborts the rest of the file - leaving .qwcfg half-populated while the
/ enclosing script carries on. Fourth reserved-name collision in this
/ repository, after desc, tables and sv; scripts/../python's
/ test_q_programs.py checks new files against the full list.
set_layers:{[overrides;yaml;defaults]
    override_values::overrides;
    yaml_values::yaml;
    default_values::defaults;
    sources}

/ Private: the environment variable name for a config key. UQF_BACKFILL_FROM
/ for `backfill_from, so the mapping is mechanical and an operator can guess
/ it without reading this file.
env_name:{[k] "UQF_",upper[ssr[string k;"_";"_"]]}

/ Private: raw string value for a key from one named source, or "" when the
/ source does not carry it.
raw_from:{[source;k]
    $[source=`env;      getenv `$env_name k;
      source=`overrides; $[k in key override_values; override_values k; ""];
      source=`yaml;      $[k in key yaml_values; yaml_values k; ""];
      source=`default;   $[k in key default_values; default_values k; ""];
      ""]}

/ Which source supplies a key, and its raw value. Returns (`none;"") when no
/ source has it.
/ .
/ Exported rather than private because "where did this value come from" is
/ the question that actually gets asked when a worker misbehaves, and
/ answering it should not require reading the precedence order off a comment.
/ @eg .qwcfg.explain[`backfill_from]  ->  (`env;"2026.09.01")
explain:{[k]
    hits:sources where 0<count each raw_from[;k] each sources;
    $[0=count hits; (`none;""); (first hits; raw_from[first hits;k])]}

/ Raw string value for a key, honouring the precedence order.
/ .
/ `(),` is load-bearing, not decoration. A single character is an ATOM in q,
/ so a layer holding a one-character value ("1", "7", "y") yields an atom
/ rather than a string, and every comparison against a list of string
/ literals then fails silently - get_flag returned FALSE for a flag
/ configured as "1". Normalising once here means no consumer has to remember.
raw:{[k] (),last explain k}

/ ----------------------------------------------------------------- TYPED

/ Errors accumulated by the typed getters below, so one startup reports
/ every bad setting rather than the first.
errors:();

/ Private: record a problem and return a null of the right type.
note:{[msg;null_value] errors,:enlist msg; null_value}

/ A required timestamp, e.g. a backfill window bound.
get_timestamp:{[k]
    v:raw k;
    $[0=count v; note["missing required setting ",string[k]," (",env_name[k]," or config)";0Np];
      null p:"P"$v; note["setting ",string[k]," is not a timestamp: ",v;0Np];
      p]}

/ A required positive long, e.g. a row cap.
get_positive:{[k]
    v:raw k;
    $[0=count v; note["missing required setting ",string[k];0Nj];
      null j:"J"$v; note["setting ",string[k]," is not an integer: ",v;0Nj];
      j<=0; note["setting ",string[k]," must be positive, got ",v;0Nj];
      j]}

/ A required symbol, e.g. a source_version.
get_symbol:{[k]
    v:raw k;
    $[0=count v; note["missing required setting ",string[k];`];
      `$v]}

/ The accepted spellings. Note `enlist "1"`, not `"1"`: a single character is
/ an ATOM in q, so the plain literal would make these mixed atom-and-vector
/ general lists - and `"true" in ("1";"true")` is then a TYPE error, not a
/ false. Match with ~/: rather than `in` for the same reason: comparing a
/ string with `in` against a list of strings is a trap, and `in` on two
/ strings is per-character, which is the related trap one line further on.
truthy:(enlist "1";"true";"yes";"on")
falsy:(enlist "0";"false";"no";"off")

/ A boolean flag. Absent means false, so a flag is opt-in - which matters
/ for dry_run, where defaulting to true would make a worker silently do
/ nothing.
get_flag:{[k]
    v:lower raw k;
    $[0=count v; 0b;
      any v ~/: truthy; 1b;
      any v ~/: falsy;  0b;
      note["setting ",string[k]," is not a boolean: ",v;0b]]}

/ ------------------------------------------------------------- VALIDATION

/ Reset the accumulated errors, before a fresh resolution.
reset:{[] errors::(); ()}

/ Throw if anything went wrong, naming every problem at once (E-16).
/ .
/ Refusing to start beats starting with defaults silently substituted: a
/ worker that begins with a wrong window backfills the wrong data and
/ reports success, which is worse than not starting.
/ @throws error listing every accumulated problem
require_valid:{[]
    if[count errors;
        '"worker config invalid (",string[count errors]," problem(s)): ",
         "; " sv errors];
    1b}

\d .
