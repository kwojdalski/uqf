// test_worker_config.q - tests for src/etl/core/worker_config.q (.qwcfg),
// the typed configuration layer and its precedence. Load tests/lib/qunit.q,
// tests/lib/testutil.q and src/etl/core/worker_config.q before this file.

\d .wcfgtest

/ Every test starts from a known set of layers. The env layer is NOT set
/ here - it is read live from the process environment, so each test that
/ cares about it sets and unsets its own variable.
setUp_layers:{[]
    .qwcfg.reset[];
    .qwcfg.set_layers[
        `shared`only_override!("from-override";"ov"); / overrides
        `shared`only_yaml!("from-yaml";"ya");         / yaml
        `shared`only_default!("from-default";"de")];  / defaults
    setenv[`UQF_SHARED;""];
    }

/ --- precedence -------------------------------------

/ The whole reason this file exists. An unstated precedence's failure mode is
/ "works on my machine", so it is asserted rather than documented - and
/ asserted by setting the SAME key in two layers, which is the only way the
/ order is observable at all.
test_overrides_beat_yaml:{[t]
    .qunit.assertEquals[.qwcfg.raw `shared;"from-override";"process_overrides.csv outranks backfill.yaml"]};

test_yaml_beats_the_code_default:{[t]
    .qwcfg.set_layers[()!();(enlist `shared)!enlist "from-yaml";(enlist `shared)!enlist "from-default"];
    .qunit.assertEquals[.qwcfg.raw `shared;"from-yaml";"tracked config outranks a code fallback"]};

test_the_environment_beats_every_file_layer:{[t]
    setenv[`UQF_SHARED;"from-env"];
    got:.qwcfg.raw `shared;
    setenv[`UQF_SHARED;""];
    .qunit.assertEquals[got;"from-env";"an operator can override tracked config without editing it"]};

/ Provenance, not just the value: "where did this come from" is the question
/ actually asked when a worker misbehaves.
test_explain_names_the_winning_layer:{[t]
    .qunit.assertEquals[first .qwcfg.explain `only_yaml;`yaml;"explain reports the source, not only the value"]};

test_an_absent_key_explains_as_none:{[t]
    .qunit.assertEquals[first .qwcfg.explain `nothing_sets_this;`none;"an unset key is reported as unset rather than as an empty value from some layer"]};

test_each_layer_is_reachable_on_its_own:{[t]
    .qunit.assertEquals[
        (.qwcfg.raw `only_override;.qwcfg.raw `only_yaml;.qwcfg.raw `only_default);
        ("ov";"ya";"de");
        "a key present in exactly one layer resolves from that layer"]};

/ --- the environment variable mapping ------------------------------------

test_the_env_name_is_mechanical:{[t]
    .qunit.assertEquals[.qwcfg.env_name `backfill_from;"UQF_BACKFILL_FROM";"an operator can guess the variable name without reading the source"]};

/ --- typed getters -------------------------------------------------------

test_a_timestamp_parses:{[t]
    .qwcfg.set_layers[()!();()!();(enlist `w)!enlist "2026.09.13D00:00:00.000000000"];
    .qunit.assertEquals[.qwcfg.get_timestamp `w;2026.09.13D00:00:00.000000000;"a configured timestamp round-trips"]};

test_a_positive_long_parses:{[t]
    .qwcfg.set_layers[()!();()!();(enlist `n)!enlist "500"];
    .qunit.assertEquals[.qwcfg.get_positive `n;500j;"a configured count round-trips"]};

test_a_flag_accepts_the_usual_spellings:{[t]
    .qwcfg.set_layers[()!();()!();`a`b`c`d!("true";"1";"YES";"off")];
    .qunit.assertEquals[.qwcfg.get_flag each `a`b`c`d;1110b;"true/1/yes are on, off is off, case-insensitively"]};

/ Absent means false, so a flag is opt-in. This matters most for dry_run:
/ defaulting it to true would make a misconfigured worker silently do
/ nothing while reporting success.
test_an_absent_flag_is_false_not_an_error:{[t]
    .qunit.assertEquals[.qwcfg.get_flag `never_set;0b;"an unset flag is off, so a misconfigured worker does real work rather than none"]};

/ --- accumulated validation (ETL-16) ---------------------------------------

/ Reporting one error at a time means finding out over N restarts that a
/ worker was never configured. require_contract in backfill_state.q makes
/ the same choice for the same reason.
test_every_bad_setting_is_reported_at_once:{[t]
    .qwcfg.set_layers[()!();()!();`x`y!("not-a-timestamp";"-5")];
    .qwcfg.get_timestamp `x;
    .qwcfg.get_positive `y;
    .qwcfg.get_symbol `absent_symbol;
    .qunit.assertEquals[count .qwcfg.errors;3;"three bad settings produce three messages, not one"]};

test_a_valid_configuration_passes:{[t]
    .qwcfg.set_layers[()!();()!();(enlist `n)!enlist "7"];
    .qwcfg.get_positive `n;
    .qunit.assertEquals[.qwcfg.require_valid[];1b;"nothing wrong, nothing thrown"]};

test_an_invalid_configuration_refuses_to_start:{[t]
    .qwcfg.set_layers[()!();()!();()!()];
    .qwcfg.get_timestamp `missing_window_start;
    .qunit.assertError[{.qwcfg.require_valid[]};::;"refusing to start beats backfilling the wrong range and reporting success"]};

test_a_non_positive_count_is_rejected:{[t]
    .qwcfg.set_layers[()!();()!();(enlist `n)!enlist "0"];
    .qunit.assertEquals[null .qwcfg.get_positive `n;1b;"zero is not a positive count"]};

\d .
