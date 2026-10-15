/ worker_config.q - typed, validated worker configuration with a stated
/ precedence (.qetl.cfg).
/ .
/ Implements the configuration checks, and the precedence
/ decision recorded on issue #71:
/ .
/   env  >  process_overrides.csv  >  config/backfill.yaml  >  code default
/ .
/ TWO LAYERS ARE WIRED TODAY, and saying which matters more than the order
/ does. `env` supplies almost everything. The overrides layer has one live
/ writer, `set_override` below, which the frontend reaches at runtime through
/ `PUT /control/worker-config` (python/uqf_frontend control.py), so a running
/ worker's override_values CAN hold keys an operator set from the UI - held
/ in memory only, and still outranked by `env`. The two file-backed layers
/ are not wired: `set_layers`, the only thing that populates yaml_values and
/ default_values, is called by tests alone, and `config/` does not exist in
/ this tree at all. So when a value is unexpected, `explain` says which
/ source answered - and "overrides" is a real answer (#548).
/ .
/ The precedence is therefore a decided contract with two suppliers, not a
/ description of four live layers. It is left standing rather than deleted
/ because the ORDER is settled and that answer does not expire - but a
/ reader who concluded they could drop a key into a YAML file and have a
/ worker pick it up would be wrong, and the previous version of this comment
/ invited exactly that. Wiring the rest needs a YAML hand-off from the Python
/ orchestrator (which already owns that parsing) plus a decision about where
/ `config/` lives, which is the question bank.
/ .
/ Most specific and most immediate wins, so an operator can override anything
/ from the environment without editing tracked config - which is what you
/ want when debugging a running process.
/ .
/ Two obligations came with that answer and both are met here rather than
/ left as intent: the order is documented at the point of use, and
/ test_worker_config.q asserts it by setting the same key in two sources and
/ checking which one wins. An unstated precedence's failure mode is "works on
/ my machine", which this repository already hit once with the vendored
/ tree's three
/ process-definition files.
/ .
/ Errors accumulate rather than failing on the first problem (the
/ shape canonical's own worker_config.q uses): a worker started with three
/ bad settings should be told about three, not discover them over three
/ restarts.

\d .

/ ------------------------------------------------------------- PRECEDENCE

/ The sources, most significant first. Named rather than positional so a
/ diagnostic can say WHERE a value came from, which is the question an
/ operator actually asks.
.qetl.cfg.sources:`env`overrides`yaml`default
/ The two layers a caller must tell apart by name: the environment outranks an
/ override, so setting one under the other is no change. Read, not retyped, by
/ the control surface (#1368); `sources` above is where they sit.
.qetl.cfg.env_layer:.qetl.cfg.sources 0
.qetl.cfg.override_layer:.qetl.cfg.sources 1

/ Private: per-source lookup tables, populated by `set_layers`. Kept separate
/ rather than merged eagerly so `explain` below can report provenance.
/ @private
.qetl.cfg.env_values:(`symbol$())!();
.qetl.cfg.override_values:(`symbol$())!();
.qetl.cfg.yaml_values:(`symbol$())!();
.qetl.cfg.default_values:(`symbol$())!();

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
/ aborts the rest of the file - leaving .qetl.cfg half-populated while the
/ enclosing script carries on. Fourth reserved-name collision in this
/ repository, after desc, tables and sv; scripts/../python's
/ test_q_programs.py checks new files against the full list.
.qetl.cfg.set_layers:{[overrides;yaml;defaults]
    .qetl.cfg.override_values::overrides;
    .qetl.cfg.yaml_values::yaml;
    .qetl.cfg.default_values::defaults;
    .qetl.cfg.sources}

/ What every config key'"'"'s environment variable starts with. A value, not just
/ inside `env_name`, because the browser tells an operator which variable to
/ unset and reads this prefix from the generated python facts (#1368).
.qetl.cfg.env_prefix:"UQF_"

/ Private: the environment variable name for a config key. UQF_BACKFILL_FROM
/ for `backfill_from, so the mapping is mechanical and an operator can guess
/ it without reading this file.
/ @private
.qetl.cfg.env_name:{[k] .qetl.cfg.env_prefix,upper string k}

/ Private: raw string value for a key from one named source, or "" when the
/ source does not carry it.
/ @private
.qetl.cfg.raw_from:{[source;k]
    $[source=.qetl.cfg.env_layer;      getenv `$.qetl.cfg.env_name[k];
      source=.qetl.cfg.override_layer; $[k in key .qetl.cfg.override_values; .qetl.cfg.override_values k; ""];
      source=`yaml;      $[k in key .qetl.cfg.yaml_values; .qetl.cfg.yaml_values k; ""];
      source=`default;   $[k in key .qetl.cfg.default_values; .qetl.cfg.default_values k; ""];
      ""]}

/ Which source supplies a key, and its raw value. Returns (`none;"") when no
/ source has it.
/ .
/ Exported rather than private because "where did this value come from" is
/ the question that actually gets asked when a worker misbehaves, and
/ answering it should not require reading the precedence order off a comment.
/ @eg .qetl.cfg.explain[`dry_run]  ->  (`none;"")
/ .
/ That is the honest UNSET answer, and it is what the example asserts. Once
/ UQF_DRY_RUN=true is exported the same call returns (`env;"true").
/ .
/ The previous example claimed (`env;"2026.09.01") for `backfill_from, which
/ was wrong twice over: nothing in this repository sets UQF_BACKFILL_FROM (a
/ worker's window comes from read_state, not from config), and an
/ example whose documented value depends on the caller's ambient environment
/ cannot be verified by anything. `dry_run` is the one key production really
/ reads, via .qetl.job.bounded.runtime.is_dry_run.
.qetl.cfg.explain:{[k]
    hits:.qetl.cfg.sources where 0<count each .qetl.cfg.raw_from[;k] each .qetl.cfg.sources;
    $[0=count hits; (`none;""); (first hits; .qetl.cfg.raw_from[first hits;k])]}

/ Set one override in a running process, and say which layer now answers.
/ .
/ For a caller outside this namespace - the frontend's PUT
/ /control/worker-config sends it over IPC - so the layer variables stay
/ private. That caller used to build the call to set_layers itself and read
/ the layers by names that do not exist (.qetl.cfg.overrides, .yaml,
/ .defaults), because nothing checked a q program held in a Python string.
/ .
/ The other overrides and the yaml and default layers are left as they are.
/ The value is stored as a string, like every layer: kola sends a Python str
/ as a symbol, and a symbol in a layer would make raw hand back a symbol
/ list that get_flag compares against strings and never matches.
/ .
/ The change lives in memory only: a restart reloads the layers from their
/ files.
/ @param k (symbol|string) the key, e.g. `dry_run
/ @param v (string|symbol|char) the value
/ @return (list) explain k: the source that now answers, and its raw value.
/ An environment variable still outranks the override, and this says so.
/ @throws qetl.cfg.set_override a key that is not a symbol or string, or a
/ value that is not a string, symbol or char
/ @eg .qetl.cfg.set_override[`example_only;"x"]
.qetl.cfg.set_override:{[k;v]
    if[10h=type k; k:`$k];
    if[not -11h=type k; '"qetl.cfg.set_override: the key must be a symbol, e.g. `dry_run"];
    v:$[-11h=type v; string v;
        10h=type v; v;
        -10h=type v; enlist v;
        '"qetl.cfg.set_override: the value must be a string or symbol"];
    .qetl.cfg.override_values::.qetl.cfg.override_values,(enlist k)!enlist v;
    .qetl.cfg.explain[k]}

/ Raw string value for a key, honouring the precedence order.
/ .
/ `(),` is load-bearing, not decoration. A single character is an ATOM in q,
/ so a layer holding a one-character value ("1", "7", "y") yields an atom
/ rather than a string, and every comparison against a list of string
/ literals then fails silently - get_flag returned FALSE for a flag
/ configured as "1". Normalising once here means no consumer has to remember.
.qetl.cfg.raw:{[k] (),last .qetl.cfg.explain k}

/ ----------------------------------------------------------------- TYPED

/ Errors accumulated by the typed getters below, so one startup reports
/ every bad setting rather than the first.
.qetl.cfg.errors:();

/ Private: record a problem and return a null of the right type.
/ @private
.qetl.cfg.note:{[msg;null_value] .qetl.cfg.errors,:enlist msg; null_value}

/ A required timestamp, e.g. a backfill window bound.
/ .
/ Read by .qetl.coerce.to_timestamp, so a zone suffix (Z, +02:00) is applied
/ and the value is UTC - "P"$ alone ignored "+02:00", two hours off. A
/ date-only value is midnight here, unlike in to_timestamp: a window bound
/ legitimately is a day, where a row's event time never is.
.qetl.cfg.get_timestamp:{[k]
    v:.qetl.cfg.raw[k];
    $[0=count v; .qetl.cfg.note["missing required setting ",string[k]," (",.qetl.cfg.env_name[k]," or config)";0Np];
      null p:$[.qetl.coerce.is_date_only v; .qetl.coerce.to_date_as_midnight v; .qetl.coerce.to_timestamp v];
        .qetl.cfg.note["setting ",string[k]," is not a timestamp: ",v;0Np];
      p]}

/ A required positive long, e.g. a row cap.
.qetl.cfg.get_positive:{[k]
    v:.qetl.cfg.raw[k];
    $[0=count v; .qetl.cfg.note["missing required setting ",string[k];0Nj];
      null j:"J"$v; .qetl.cfg.note["setting ",string[k]," is not an integer: ",v;0Nj];
      j<=0; .qetl.cfg.note["setting ",string[k]," must be positive, got ",v;0Nj];
      j]}

/ A required symbol, e.g. a source_version.
.qetl.cfg.get_symbol:{[k]
    v:.qetl.cfg.raw[k];
    $[0=count v; .qetl.cfg.note["missing required setting ",string[k];`];
      `$v]}

/ The accepted spellings. Note `enlist "1"`, not `"1"`: a single character is
/ an ATOM in q, so the plain literal would make these mixed atom-and-vector
/ general lists - and `"true" in ("1";"true")` is then a TYPE error, not a
/ false. Match with ~/: rather than `in` for the same reason: comparing a
/ string with `in` against a list of strings is a trap, and `in` on two
/ strings is per-character, which is the related trap one line further on.
.qetl.cfg.truthy:(enlist "1";"true";"yes";"on")
.qetl.cfg.falsy:(enlist "0";"false";"no";"off")

/ A boolean flag. Absent means false, so a flag is opt-in - which matters
/ for dry_run, where defaulting to true would make a worker silently do
/ nothing.
.qetl.cfg.get_flag:{[k]
    v:lower .qetl.cfg.raw k;
    $[0=count v; 0b;
      any v ~/: .qetl.cfg.truthy; 1b;
      any v ~/: .qetl.cfg.falsy;  0b;
      .qetl.cfg.note["setting ",string[k]," is not a boolean: ",v;0b]]}

/ ------------------------------------------------------------- VALIDATION

/ Reset the accumulated errors, before a fresh resolution.
.qetl.cfg.reset:{[] .qetl.cfg.errors::(); ()}

/ Throw if anything went wrong, naming every problem at once.
/ .
/ Refusing to start beats starting with defaults silently substituted: a
/ worker that begins with a wrong window backfills the wrong data and
/ reports success, which is worse than not starting.
/ @throws error listing every accumulated problem
.qetl.cfg.require_valid:{[]
    if[count .qetl.cfg.errors;
        '"worker config invalid (",string[count .qetl.cfg.errors]," problem(s)): ",
         "; " sv .qetl.cfg.errors];
    1b}

\d .
