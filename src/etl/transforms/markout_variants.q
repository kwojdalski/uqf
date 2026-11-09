/ markout_variants.q - a markout scored against a LIST of reference variants (.qpipe.transform.markout_variants, #2289).
/ .
/ markout_core.calc scores against ONE variant, the spec's `reference`. This is the same core run once per variant of a
/ list, over books gathered once (.qetl.variant.read): the result is the core's long layout with a `variant` key in
/ front, and every row carries the `config_id` of ITS variant (config_markout.q), so any row traces back to the
/ configuration snapshot that records the variant it was scored against.
/ .
/ VARIANT #1 IS UNCHANGED. Its rows are the core's own, to the bit, and its config_id is the id the same spec has
/ without a `reference` key: scoring a list that includes it re-labels nothing already published.
/ .
/ The core's input is read as it is: books that read depth are the gathered slice (rid their own index, ladders
/ carried), books that do not are any arranged top of book. Nothing here names a ladder or market_data.

\d .

/ The core's result with the variant that scored each row, first.
.qpipe.transform.markout_variants.result:`variant xcols update variant:`symbol$() from 0#.qpipe.transform.markout_core.result

/ The spec a variant scores under: the spec, naming it.
/ @param spec the markout spec
/ @param name the variant
/ @return the spec with reference set
/ @eg (.qpipe.transform.markout_variants.under[enlist[`max_age]!enlist 0D00:00:05;`best_top])`reference  ->  `best_top
.qpipe.transform.markout_variants.under:{[spec;name] spec,enlist[`reference]!enlist name}

/ The configuration id of each variant under a spec: the id of the snapshot its results belong to.
/ @param domain the domain dictionary
/ @param spec the markout spec
/ @param names the variants
/ @return a dictionary variant -> config_id; variant #1's is the spec's own without a reference
/ @throws error when the core refuses the spec or a variant is not registered
/ @eg ((.qpipe.transform.markout_variants.ids[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;enlist `best_top])`best_top) like "markout_*"  ->  1b
.qpipe.transform.markout_variants.ids:{[domain;spec;names]
    names:distinct (),names;
    names!{[domain;spec;n] .qpipe.transform.config_markout.id[domain;.qpipe.transform.markout_core.validate[domain;.qpipe.transform.markout_variants.under[spec;n]]]}[domain;spec] each names}

/ Record the snapshot of every variant's configuration, durably, BEFORE their rows are published.
/ @param domain the domain dictionary
/ @param spec the markout spec
/ @param names the variants
/ @param reason why, a non-empty string
/ @return a dictionary variant -> snapshot id
/ @throws error when a spec or variant is refused or a snapshot cannot be recorded
/ @eg count .qpipe.transform.markout_variants.pin[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;`symbol$();"example"]  ->  0
.qpipe.transform.markout_variants.pin:{[domain;spec;names;reason]
    names:distinct (),names;
    names!{[domain;spec;reason;n] .qpipe.transform.config_markout.pin[domain;.qpipe.transform.markout_variants.under[spec;n];reason]}[domain;spec;reason] each names}

/ Score executions against a list of reference variants.
/ @param domain the domain dictionary
/ @param spec the markout spec; its own `reference` is ignored in favour of each variant of the list
/ @param exe executions
/ @param md the books: arranged; the gathered slice (.qetl.variant.slice) where any variant reads depth
/ @param names the variants
/ @return the core's result for each variant, stacked, with `variant` in front; each row's config_id is its variant's
/ @throws error as markout_core.calc, or when a variant reads depth and md is not a slice
/ @eg count .qpipe.transform.markout_variants.calc[.qpipe.transform.markout_core.fixture_domain;.qpipe.transform.markout_core.fixture_spec;0#.qpipe.transform.markout_core.executions_in;0#.qpipe.transform.markout_core.market_data_in;enlist `best_top]  ->  0
.qpipe.transform.markout_variants.calc:{[domain;spec;exe;md;names]
    names:distinct (),names;
    one:{[domain;spec;exe;md;n]
        r:.qpipe.transform.markout_core.calc[domain;.qpipe.transform.markout_variants.under[spec;n];exe;md];
        `variant xcols update variant:(count r)#n from r}[domain;spec;exe;md];
    raze one each names}

\d .
