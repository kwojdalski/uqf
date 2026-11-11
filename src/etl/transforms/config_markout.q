/ config_markout.q - the configuration snapshot of a markout, and the lineage from a
/ result row to it (.qpipe.transform.config_markout, #1999).
/ .
/ A markout result used to repeat three of its configuration's fields on every row
/ (contract_version, rate_timing, report_ccy), which did not identify the rest: the
/ horizons, the reference-age limit, the declared rates, the rules that price an
/ instrument. This file is where the WHOLE configuration is named. A row carries one
/ compact reference, `config_id`, and the snapshot it names (src/etl/core/
/ config_snapshot.q) holds, once:
/ .
/   contract_version   the contract the rows were written under
/   code_revision      the core's calculation (config_markout.code_revision)
/   domain, domain_revision, datasets
/                      which domain priced the instrument, the revision of its rules, and
/                      the datasets that domain's spec produces (what `uqs data snapshot
/                      list --dataset` filters on)
/   horizons, max_age, report_ccy, rate_timing, rates
/                      the core's validated parameters, the rates as declared
/   reference          the reference variant, when it is not variant #1 (reference_variant.q)
/   domain_spec        the domain's own spec keys (an FX convention, a tolerance)
/ .
/ THE CALCULATION STAYS PURE. `id` is a function of the domain and the spec, so
/ markout_core.calc stamps it on a row without reading a store: the id is the
/ content's digest. Only three things here touch storage, and none is on the
/ calculation's path: `pin` (a job writes its snapshot before it calculates),
/ `check_pinned` (the publish path refuses a row whose snapshot is not durable) and
/ the readers `params`, `enrich` and `require_version` (which resolve a row's
/ snapshot and refuse an unknown one - they never fall back to a latest snapshot).
/ .
/ WHY THE FILE IS NAMED config_markout. The transforms directory loads alphabetically
/ and the shipped jobs' hand-worked examples carry their config_id, which they get from
/ `id` at load: this file has to load before fx_markout.q and crypto_markout_outputs.q,
/ and the file name is the namespace's last part (the load plan maps one to the other).
/ .
/ WHAT A SNAPSHOT IS NOT. It records the conditions of a calculation, not a way to
/ redo it: reproduction also needs the code at code_revision and the source data.

\d .

/ The snapshot kind this file records.
.qpipe.transform.config_markout.kind:`markout

/ The contract version (open question 5 of the markout contract: a long starting at 1,
/ apart from FX's own `version`). A change to anything the contract fixes bumps it.
/ A row does not carry it: the snapshot its config_id names does.
.qpipe.transform.config_markout.contract_version:1

/ The revision of the core's calculation: bumped by a change to how the core scores
/ that the contract's columns do not show - a formula, a status rule, the order of
/ precedence - so two runs of different code never share a configuration snapshot.
.qpipe.transform.config_markout.code_revision:1

/ The domain name and revision a domain that declares none is recorded under.
.qpipe.transform.config_markout.default_name:`unnamed
.qpipe.transform.config_markout.default_revision:`unversioned

/ The revision of a domain declared as data (markout_core.table_domain): the rules ARE
/ its two tables, so their digest is their revision and cannot be forgotten to bump.
/ @param inst the instruments table
/ @param conv the conversions table
/ @return a symbol, `table_` and 12 hex digits
/ @eg .qpipe.transform.config_markout.table_revision[([] sym:enlist `ACME);([] quote_ccy:enlist `USD)] like "table_*"  ->  1b
.qpipe.transform.config_markout.table_revision:{[inst;conv]
    `$"table_",12#string .qetl.snapshot.digest[`domain;`instruments`conversions!(inst;conv)]}

/ Private: the snapshot content of a spec the core has already validated.
/ @private
.qpipe.transform.config_markout.content_of:{[domain;spec]
    dk:domain`keys;
    nm:$[`name in key domain; domain`name; .qpipe.transform.config_markout.default_name];
    rv:$[`revision in key domain; domain`revision; .qpipe.transform.config_markout.default_revision];
    ds:$[`datasets in key domain; `symbol$domain`datasets; `symbol$()];
    base:`contract_version`code_revision`domain`domain_revision`datasets`horizons`max_age`report_ccy`rate_timing`rates`domain_spec!(
        .qpipe.transform.config_markout.contract_version;.qpipe.transform.config_markout.code_revision;nm;rv;ds;
        asc spec`horizons;spec`max_age;spec`report_ccy;spec`rate_timing;spec`rates;dk!spec dk);
    / The reference variant (#2288) is part of the configuration. Variant #1 is the contract's own rule, so it is
    / implied by contract_version and adds nothing: the ids of every result scored before variants existed, and of
    / every result scored under it since, are the same. Any other variant is recorded IN FULL, so a result
    / scored against another reference can never share a config_id with one scored against the default.
    ref:$[`reference in key spec; spec`reference; .qetl.reference.default];
    $[ref~.qetl.reference.default; base; base,enlist[`reference]!enlist .qetl.reference.describe ref]}

/ The configuration a markout runs under, as a snapshot records it.
/ .
/ The spec is validated first and completed with its defaults, so a spec that leaves
/ max_age out and one that states the default produce the same content - and the
/ same snapshot.
/ @param domain the domain dictionary
/ @param spec the markout spec
/ @return a dictionary of symbol to value
/ @throws error when the core or the domain refuses the spec
/ @eg (.qpipe.transform.config_markout.content[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec])`report_ccy  ->  `USD
.qpipe.transform.config_markout.content:{[domain;spec]
    .qpipe.transform.config_markout.content_of[domain;.qpipe.transform.markout_core.validate[domain;spec]]}

/ The id of the snapshot a spec's results belong to: a pure function of the domain
/ and the VALIDATED spec, which is what the core stamps on each row.
/ @param domain the domain dictionary
/ @param spec a spec the core has validated
/ @return the snapshot id
/ @eg .qpipe.transform.config_markout.id[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.validate[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec]] like "markout_*"  ->  1b
.qpipe.transform.config_markout.id:{[domain;spec]
    .qetl.snapshot.id_of[.qpipe.transform.config_markout.kind;.qpipe.transform.config_markout.content_of[domain;spec]]}

/ Pin a configuration: record its snapshot, durably, BEFORE the calculation that
/ will stamp its id on rows runs.
/ .
/ Idempotent: pinning a configuration already recorded changes nothing, so a live
/ job and its bounded twin, which share a spec, share one snapshot and the first
/ reason stands.
/ @param domain the domain dictionary
/ @param spec the markout spec
/ @param reason why it is being recorded, a non-empty string
/ @return the snapshot id
/ @throws error when the spec is refused or the snapshot cannot be recorded
/ @eg .qpipe.transform.config_markout.pin[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;"example"] like "markout_*"  ->  1b
.qpipe.transform.config_markout.pin:{[domain;spec;reason]
    .qetl.snapshot.record[.qpipe.transform.config_markout.kind;.qpipe.transform.config_markout.content[domain;spec];reason]}

/ The publish guard, in the shape a job's `check` takes: no failures when every
/ snapshot the rows name is durably recorded, else one failure naming why.
/ .
/ A result must not become visible before the configuration that explains it is on
/ disk, and a row naming no configuration cannot be explained at all. A dry run
/ (which pins and publishes nothing) is not held to it.
/ @param batch the rows about to be published
/ @return an empty failure table, or one row: check, status, detail
/ @eg count .qpipe.transform.config_markout.check_pinned[0#.qpipe.transform.markout_core.result]  ->  0
.qpipe.transform.config_markout.check_pinned:{[batch]
    if[0=count batch; :.qetl.job.bounded.no_failures[]];
    / a dry run pins nothing and publishes nothing, so there is no result to explain
    if[@[.qetl.job.bounded.runtime.is_dry_run;::;{[e] 0b}]; :.qetl.job.bounded.no_failures[]];
    r:@[{(1b;.qetl.snapshot.require_pinned x)};batch`config_id;{[e] (0b;e)}];
    $[first r; .qetl.job.bounded.no_failures[]; ([] check:enlist `config_unpinned; status:enlist `fail; detail:enlist last r)]}

/ The snapshot for rows written before snapshots existed (#1999): all that was ever recorded
/ about them is the three fields they repeated, so those are its parameters and everything
/ else is null or empty, and `legacy` says so. scripts/hdb/fill.q records one
/ per distinct triple in an HDB partition when `uqs data check --apply` folds the old columns.
/ @param dataset the dataset the rows are in, a symbol
/ @param cv the contract version the rows carried
/ @param rt the rate timing they carried
/ @param rc the reporting currency they carried
/ @return the snapshot content
/ @eg (.qpipe.transform.config_markout.legacy_content[`fx_markout;1;`execution;`USD])`legacy  ->  1b
.qpipe.transform.config_markout.legacy_content:{[dataset;cv;rt;rc]
    `contract_version`code_revision`domain`domain_revision`datasets`horizons`max_age`report_ccy`rate_timing`rates`domain_spec`legacy!(
        cv;0N;`legacy;`legacy;enlist dataset;`timespan$();0Nn;rc;rt;(`symbol$())!();(`symbol$())!();1b)}

/ Record the legacy snapshot of a triple, or find it: the id is the content's digest, so the
/ same triple is the same snapshot wherever and however often it is pinned.
/ @param dataset the dataset the rows are in
/ @param cv the contract version
/ @param rt the rate timing
/ @param rc the reporting currency
/ @return the snapshot id
/ @eg .qpipe.transform.config_markout.legacy_pin[`fx_markout;1;`execution;`USD] like "markout_legacy_*"  ->  1b
.qpipe.transform.config_markout.legacy_pin:{[dataset;cv;rt;rc]
    c:.qpipe.transform.config_markout.legacy_content[dataset;cv;rt;rc];
    id:`$"markout_legacy_",12#string .qetl.snapshot.digest[.qpipe.transform.config_markout.kind;c];
    .qetl.snapshot.record_as[id;.qpipe.transform.config_markout.kind;c;`fill_hdb_partitions;
        "legacy: rows written before configuration snapshots (#1999); only contract_version, rate_timing and report_ccy were ever recorded for them"]}

/ What the snapshots the rows name say, one row per distinct config_id.
/ .
/ Resolves each id to the snapshot that PRODUCED it, from the file, digest checked.
/ An id nobody recorded is an error - there is no latest-configuration fallback.
/ @param rows a markout dataset's rows, with config_id
/ @return config_id, contract_version, code_revision, domain, domain_revision, report_ccy, rate_timing, max_age, legacy, digest
/ @throws error when a row names no snapshot, an unrecorded one, or one whose digest fails
/ @eg count .qpipe.transform.config_markout.params[0#.qpipe.transform.markout_core.result]  ->  0
.qpipe.transform.config_markout.params:{[rows]
    s:.qetl.snapshot.require_many distinct rows`config_id;
    c:.qetl.snapshot.decode each s`content;
    field:{[c;f;default] {[f;default;d] $[f in key d; d f; default]}[f;default] each c}[c];
    ([] config_id:s`snapshot_id; contract_version:`long$field[`contract_version;0N]; code_revision:`long$field[`code_revision;0N];
        domain:`symbol$field[`domain;`]; domain_revision:`symbol$field[`domain_revision;`]; report_ccy:`symbol$field[`report_ccy;`];
        rate_timing:`symbol$field[`rate_timing;`]; max_age:`timespan$field[`max_age;0Nn]; legacy:`boolean$field[`legacy;0b]; digest:s`digest)}

/ The enriched read projection: the rows with the configuration that produced each
/ one beside them, the fields a row used to repeat.
/ .
/ A reader that wants contract_version, report_ccy or rate_timing next to the
/ results reads them here, from the producing snapshot - never from whatever is
/ current.
/ @param rows a markout dataset's rows, with config_id
/ @return the rows, then contract_version, code_revision, domain, domain_revision, report_ccy, rate_timing, max_age, legacy, digest
/ @throws error as params does
/ @eg count .qpipe.transform.config_markout.enrich[0#.qpipe.transform.markout_core.result]  ->  0
.qpipe.transform.config_markout.enrich:{[rows]
    rows lj `config_id xkey .qpipe.transform.config_markout.params rows}

/ Refuse rows written under another contract version (a consumer pins one).
/ .
/ The version is the snapshot's, so the check resolves every distinct config_id the
/ rows name; a row that names no recorded snapshot is refused with the id, not read
/ as the pinned version.
/ @param rows a markout dataset's rows, with config_id
/ @param version the contract version the consumer reads
/ @return the rows
/ @throws error when any row's snapshot carries another version, or cannot be resolved
/ @eg count .qpipe.transform.config_markout.require_version[0#.qpipe.transform.markout_core.result;1]  ->  0
.qpipe.transform.config_markout.require_version:{[rows;version]
    other:distinct (.qpipe.transform.config_markout.params[rows]`contract_version) except version;
    if[count other; '"config_markout: contract version ",(", " sv string other)," is not the pinned ",string version];
    rows}

\d .
