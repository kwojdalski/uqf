/ test_namespaces.q - the namespace enumeration four tools now share (.qt.qf.util.namespaces).
/ .
/ Worth its own suite because the failure this file exists to prevent is
/ SILENT. A root-level `(key `) where like "q*"` scan - what the contract
/ surface export, the coverage driver and the documentation ratchet each
/ carried privately - reports `.qpipe.job` as one namespace and never looks
/ inside it, so every worker's public surface disappears from all three at
/ once while each keeps printing a passing line over a smaller set.
/ .
/ The nested case is therefore asserted directly rather than through the
/ tools, and so is the char-keyed dictionary (a type-char lookup, as a source once held) that made the
/ first version of is_namespace throw a bare 'type from four frames down.


/ Framework and declarations must both remain visible to exporters and coverage.
.qt.qf.util.namespaces.test_nested_framework_and_shared_transform_are_enumerated:{[t]
    want:`.qetl.cfg.audit`.qetl.job.stream.normalizer`.qetl.job.bounded.runtime`.qpipe.transform.fx_markout;
    .qunit.assertTrue[all want in .qf.util.functional[];
        "deep framework modules and shared transforms are functional namespaces"]};

.qt.qf.util.namespaces.test_a_stream_cannot_reuse_a_bounded_job_name:{[t]
    .qunit.assertThrows[.qetl.job.stream.define[`demo_deals_backfill;];
        .qetl.job.stream.def `fx_feed;
        "*already a bounded job*";"execution modes cannot share an instance namespace"]};

.qt.qf.util.namespaces.test_a_bounded_job_cannot_reuse_a_stream_name:{[t]
    .qunit.assertThrows[.qetl.job.bounded.define[`fx_feed;];
        .qetl.job.bounded.def `demo_deals_backfill;
        "*already a streaming job*";"execution modes cannot share an instance namespace"]};

/ --- is_namespace ---------------------------------------------------------

.qt.qf.util.namespaces.test_a_namespace_is_recognised:{[t]
    .qunit.assertEquals[.qf.util.is_namespace value `.qetl.job.bounded;1b;
        "a namespace created by \\d is one"]};

.qt.qf.util.namespaces.test_the_root_namespace_is_not_recognised:{[t]
    / Asserted rather than left undefined. The root is the ONE namespace
    / whose keys do not include the empty symbol - a child namespace carries
    / it as a back-reference and root has nothing to point back to - so
    / is_namespace answers 0b for it. Nothing here starts a walk at root, so
    / the answer does not matter to any caller; it mattering later without
    / anyone noticing is what this test is for.
    .qunit.assertEquals[.qf.util.is_namespace value `.;0b;
        "the root has no empty-symbol back-reference, so it answers 0b"]};

.qt.qf.util.namespaces.test_an_ordinary_dictionary_is_not_a_namespace:{[t]
    / .qetl.job.stream.procnames is a symbol-keyed dictionary that is NOT a
    / namespace, which is the case the empty-symbol test exists to separate.
    .qunit.assertEquals[.qf.util.is_namespace .qetl.job.stream.procnames;0b;
        "a symbol-keyed dictionary with no empty key is not a namespace"]};

.qt.qf.util.namespaces.test_a_char_keyed_dictionary_is_not_a_namespace:{[t]
    / The trap: a lookup keyed by type CHARS (the shape .qetl.source.coercers had), so `` ` in key v ``
    / compares a symbol against a char vector and throws 'type. A scan that
    / walks every global meets it, and the error names nothing useful.
    .qunit.assertEquals[.qf.util.is_namespace "fjps"!`float`long`timestamp`symbol;0b;
        "a char-keyed dictionary answers 0b rather than throwing 'type"]};

.qt.qf.util.namespaces.test_a_table_is_not_a_namespace:{[t]
    .qunit.assertEquals[.qf.util.is_namespace ([] a:1 2);0b;"a table is not a namespace"]};

.qt.qf.util.namespaces.test_a_keyed_table_is_not_a_namespace:{[t]
    / 99h like a dictionary, but `key` returns a table rather than symbols.
    .qunit.assertEquals[.qf.util.is_namespace ([a:1 2] b:3 4);0b;"a keyed table is not a namespace"]};

.qt.qf.util.namespaces.test_a_function_is_not_a_namespace:{[t]
    .qunit.assertEquals[.qf.util.is_namespace {[x] x};0b;"a lambda is not a namespace"]};

/ --- owned ----------------------------------------------------------------

.qt.qf.util.namespaces.test_a_flat_library_namespace_is_owned:{[t]
    .qunit.assertEquals[`.qetl.job.bounded in .qf.util.owned[];1b;"the framework's own namespace is listed"]};

.qt.qf.util.namespaces.test_the_worker_root_is_owned:{[t]
    .qunit.assertEquals[`.qpipe.job in .qf.util.owned[];1b;
        "the container is listed too - a caller asking what exists gets it"]};

.qt.qf.util.namespaces.test_a_nested_worker_namespace_is_owned:{[t]
    / THE test. A root-level scan finds `.qpipe.job and stops.
    .qunit.assertEquals[`.qpipe.job.demo_deals_backfill in .qf.util.owned[];1b;
        "a worker nested under .qpipe.job is reached, not hidden behind its parent"]};

.qt.qf.util.namespaces.test_every_registered_worker_has_its_namespace_listed:{[t]
    / Stated against the worker FILES rather than a hard-coded list, so a
    / worker added later is covered without editing this file.
    / .
    / Files, and not .qetl.job.bounded.state.registered[], because tests register their own
    / fixture workers into that registry at RUN time - `reference`, `partial`,
    / three `fixture_*` - and they have no namespace under .qpipe.job because they
    / have no declaration file. Reading the registry made this test pass or
    / fail on which suites happened to run first, which is ordering luck
    / rather than a property of the tree.
    / The registry, INTERSECTED with the workers this tree declares in a file.
    / Reading the registry alone made the test order-dependent; reading the
    / files alone stopped exercising .qetl.job.bounded.state.registered at all, which the
    / coverage lane noticed. Both halves matter: the registry is the thing
    / under test, the files are what separates a real worker from a fixture.
    registered:.qetl.job.bounded.state.registered[] inter .qt.support.etl_declaration_names["src/etl/workers"];
    missing:registered where not {[w] (.qetl.job.bounded.namespace w) in .qf.util.owned[]} each registered;
    .qunit.assertEquals[missing;`symbol$();
        "every registered bounded worker's namespace is enumerated"]};

.qt.qf.util.namespaces.test_kdbs_own_q_namespace_is_not_owned:{[t]
    / `.q is KX's, and its 180-odd names would swamp every report built on
    / this list.
    .qunit.assertEquals[`.q in .qf.util.owned[];0b;"q's own namespace is not this tree's"]};

.qt.qf.util.namespaces.test_the_names_are_fully_qualified:{[t]
    / The root scan produced undotted names (`qetl.job.bounded), and a caller that passed
    / one to `value` got the ROOT GLOBAL of that name, or a null - not the
    / namespace. Fully qualified here so there is nothing to reassemble.
    .qunit.assertEquals[all (string .qf.util.owned[]) like ".*";1b;
        "every name carries its leading dot"]};

.qt.qf.util.namespaces.test_the_listing_is_not_empty:{[t]
    / A guard against the whole enumeration silently answering nothing, which
    / would make every test above vacuous and every tool built on it report
    / a clean sweep over no code at all.
    .qunit.assertTrue[20<count .qf.util.owned[];
        "the enumeration finds this tree's namespaces, not a handful"]};

/ --- functional -----------------------------------------------------------

.qt.qf.util.namespaces.test_the_worker_root_holds_no_functions:{[t]
    / .qpipe.job holds only namespaces, so a tool that measures coverage or
    / exports a surface has nothing to do with it.
    .qunit.assertEquals[`.qpipe.job in .qf.util.functional[];0b;
        "a container of namespaces is not a functional namespace"]};

.qt.qf.util.namespaces.test_a_nested_worker_namespace_is_functional:{[t]
    .qunit.assertEquals[`.qpipe.job.demo_deals_backfill in .qf.util.functional[];1b;
        "the worker itself holds functions and is reported"]};

.qt.qf.util.namespaces.test_functional_is_a_subset_of_owned:{[t]
    .qunit.assertEquals[all .qf.util.functional[] in .qf.util.owned[];1b;
        "functional narrows owned rather than finding something else"]};

/ --- the seven .qf domains (#1411) -----------------------------------------

/ The quant library loads into seven domains under one root. The root holds
/ only those namespaces; a tool that scanned `.q*` at the top level would see
/ `.qf` as a container with nothing in it, which is exactly the .qpipe.job
/ shape, so the enumeration has to reach below it.
.qt.qf.util.namespaces.domains:`.qf.px`.qf.mkt`.qf.exec`.qf.port`.qf.math`.qf.util`.qf.demo;

/ Every namespace the seven domains replaced. None may exist: the migration
/ kept no aliases, wrappers or facades, so a retired name that still resolves
/ is a shim somebody left behind.
.qt.qf.util.namespaces.retired:`qccy`qcal`qdcf`qrates`qfwd`qopt`qbook`qcross`qmicro`qdqc`qasof`qexec`qalloc`qpos`qdesk`qrisk`qlimit`qstats`qmeta`qns`qrender`qschema`qexdef`qsynth;

.qt.qf.util.namespaces.test_every_domain_is_owned:{[t]
    .qunit.assertEquals[all .qt.qf.util.namespaces.domains in .qf.util.owned[];1b;
        "discovery reaches below .qf to every domain"]};

.qt.qf.util.namespaces.test_the_domain_root_is_a_container:{[t]
    .qunit.assertEquals[(`.qf in .qf.util.owned[]; `.qf in .qf.util.functional[]);(1b;0b);
        ".qf is listed, but holds namespaces and no functions"]};

.qt.qf.util.namespaces.test_the_domains_that_hold_functions_are_functional:{[t]
    / .qf.demo is excluded: the ETL adds its functions (.qf.demo.drift_one),
    / and a process that loads only src/init.q has its constants alone.
    .qunit.assertEquals[all (.qt.qf.util.namespaces.domains except `.qf.demo) in .qf.util.functional[];1b;
        "a coverage or surface tool reaches every domain's functions"]};

.qt.qf.util.namespaces.test_the_framework_and_the_jobs_are_still_found_beside_the_domains:{[t]
    .qunit.assertEquals[all `.qetl.job.bounded`.qpipe.job.demo_deals_backfill`.qpipe.source.demo_deals in .qf.util.owned[];1b;
        "the domains are one more container, not a replacement for the others"]};

.qt.qf.util.namespaces.test_no_retired_namespace_exists:{[t]
    .qunit.assertEquals[.qt.qf.util.namespaces.retired inter key `;`symbol$();
        "no old module namespace survives, as an alias or otherwise"]};

.qt.qf.util.namespaces.test_a_domain_function_resolves_by_symbol_by_get_and_by_string:{[t]
    / The ways a name reaches a function that a direct call does not: a symbol
    / handed to value or get, a string evaluated (what an IPC query is), and
    / a function placed in a functional select.
    by_sym:(value `.qf.exec.markout)[1;1.1000;1.1010;10000];
    by_get:(get `.qf.exec.markout)[1;1.1000;1.1010;10000];
    by_str:value ".qf.exec.markout[1;1.1000;1.1010;10000]";
    r:?[([] p0:1.1000 1.1000; p1:1.1010 1.1020);();0b;(enlist `m)!enlist (`.qf.exec.markout;1;`p0;`p1;10000)];
    .qunit.assertEquals[(by_sym;by_get;by_str);3#10f;"symbol, get and string agree with the documented example"];
    .qunit.assertEquals[r`m;10 20f;"a functional select calls the function by its symbol"]};

.qt.qf.util.namespaces.test_a_retired_name_is_not_callable_by_string:{[t]
    .qunit.assertThrows[value;".qexec.markout[1;1.1000;1.1010;10000]";".qexec.markout";
        "the old spelling of a function is an error, not a forward"]};

/ --- every shipped namespace is inside the prefix --------------------------

/ The `.q` prefix is not decoration: it is the whole of `owned`'s definition,
/ so a namespace declared without it cannot be seen by the contract surface,
/ the coverage report or the documentation ratchet even in a process that
/ HAS loaded it - and not seen in the way that reports success, because each
/ of those tools keeps printing a passing line over the smaller set.
/ .
/ Three namespaces in scripts/ were outside it until this test existed:
/ .cross, .markout and .posbook, the tickerplant subscriber processes, now
/ .qpipe.job.cross and friends. The prefix is a necessary condition, not a
/ sufficient one - those three also subscribe to a tickerplant as they load,
/ so no plain q process can load them and man.q's generator scans src/ only.
/ What the prefix buys is that the moment a tool DOES have them in
/ process - a coverage run inside the live process, `.qf.util.owned` there -
/ they are part of this tree rather than indistinguishable from q's own
/ globals.

/ Namespaces that are deliberately outside the prefix, each with the reason
/ it cannot simply be renamed. Written as a dictionary rather than a list so
/ the reason lives beside the name and a future reader does not have to
/ guess whether an entry is a decision or an oversight.
.qt.qf.util.namespaces.outside_the_prefix:(`symbol$())!();
.qt.qf.util.namespaces.outside_the_prefix[`.dqe]:"TorQ's OWN namespace - torq_metatables.q adds uqf_metatable INTO it so DQE's runquery can transport it. Renaming would break the integration, not tidy it.";
.qt.qf.util.namespaces.outside_the_prefix[`.rp]:"TorQ's OWN reporter namespace - uqs_reporter_config.q sets .rp.inputcsv there, where reporter.q reads it, and the result handler its generated rows name sits beside it (#1209).";
.qt.qf.util.namespaces.outside_the_prefix[`.cov]:"the coverage tool, in KX's published .cov API shape. .qetl.coverage is already the ETL coverage LEDGER, so the obvious rename collides with an unrelated namespace.";
.qt.qf.util.namespaces.outside_the_prefix[`.surface]:"the contract-surface exporter, which enumerates this tree's namespaces. Inside the prefix it would export itself - a tool appearing in the artifact it produces.";

/ Namespaces a file WRITES INTO without declaring them: names it assigns in q's or
/ TorQ's own namespace (.z.ts, .servers.x) or in a check script's private one.
/ Only the qualified-definition form can see these, which is why they are listed
/ apart from outside_the_prefix: that list is for namespaces uqf declares.
.qt.qf.util.namespaces.writes_into:(`symbol$())!();
.qt.qf.util.namespaces.writes_into[`.z]:"q's own handlers (.z.ts, .z.po ...), assigned by the process scripts that need one.";
.qt.qf.util.namespaces.writes_into[`.servers]:"TorQ's connection settings, set by scripts/processes/torq_tap.q where TorQ reads them.";
.qt.qf.util.namespaces.writes_into[`.pc]:"scripts/portable/checks/status_intervals.q's own helpers - a check that runs on a 4.0 build and so cannot use the prefix convention's tooling.";

/ Private: the top segment of a namespace, `.qpipe.job.cross -> `.qpipe.
.qt.qf.util.namespaces.top:{[n] `$"." sv 2#"." vs string n}

/ Private: the namespaces a file's lines declare. A file declares a namespace
/ either by opening it (`\d .qtorq`, one level at most) or, as the tree writes
/ nearly all of its own q since #1411, by defining a name in it fully qualified
/ from the root (`.qpipe.job.cross.upd:{...}` declares `.qpipe.job.cross`).
/ @param lines the file's lines
/ @return the symbols, leading dot kept, with repeats
.qt.qf.util.namespaces.namespaces_in:{[lines]
    opened:lines where lines like "\\d .*";
    opened:`$3_/:opened where not opened like "\\d .";
    defs:lines where lines like ".[a-zA-Z]*";
    / the name a line defines: the text up to its first ":", when nothing else comes first
    names:{[l] i:first where l in ":[ ("; $[(null i) or not ":"=l i; ""; i#l]} each defs;
    names:names where 0<count each names;
    owned:{[n] p:"." vs n; $[3>count p; `symbol$(); `$"." sv -1_p]} each names;
    opened,raze owned}

/ Private: every namespace declared by a file under src/ or scripts/.
.qt.qf.util.namespaces.declared_namespaces:{[]
    / scripts/torqcode/ and scripts/torqconfig/ are TorQ's OWN code (#711, #712):
    / they override TorQ's handlers and settings in TorQ's namespaces (.servers,
    / .lg, .checkinputs ...) where TorQ reads them, so they cannot carry uqf's
    / prefix and are not this rule's to judge.
    files:system"find src scripts -name '*.q' -not -path 'scripts/torqcode/*' -not -path 'scripts/torqconfig/*'";
    raze {[f] .qt.qf.util.namespaces.namespaces_in read0 hsym `$f} each files}

.qt.qf.util.namespaces.test_every_shipped_namespace_is_inside_the_prefix_or_listed:{[t]
    / The ratchet. A new `\d .something` in src/ or scripts/ either carries
    / the prefix or is added to outside_the_prefix WITH its reason, which
    / makes the exception a decision someone wrote down rather than a file
    / nobody noticed.
    all_ns:distinct .qt.qf.util.namespaces.declared_namespaces[];
    tops:distinct .qt.qf.util.namespaces.top each all_ns;
    stray:tops where not (tops like ".q*") or (tops in key .qt.qf.util.namespaces.outside_the_prefix) or tops in key .qt.qf.util.namespaces.writes_into;
    .qunit.assertEquals[stray;`symbol$();
        "every namespace in src/ and scripts/ is .q-prefixed, or listed in outside_the_prefix with its reason"]};

.qt.qf.util.namespaces.test_the_scan_actually_found_the_declarations:{[t]
    / Without this the test above passes over an empty list the day the
    / directory layout changes - reporting success for having looked nowhere.
    / 10, not the 30 it was: the PeachQ lane runs a copy with every nested
    / `\d .a.b` flattened away, which leaves only the 14 single-level
    / namespaces declared (.qdata, .qtorq, .cov, ...) where the full tree has
    / over a hundred - the floor guards an empty scan, not the count.
    .qunit.assertTrue[10<count distinct .qt.qf.util.namespaces.declared_namespaces[];
        "the file scan found this tree's namespace declarations"]};

.qt.qf.util.namespaces.test_the_subscriber_processes_are_inside_the_prefix:{[t]
    / Named specifically, because these three are what the general rule above
    / was written for and a regression here would otherwise read as a count.
    declared:distinct .qt.qf.util.namespaces.declared_namespaces[];
    .qunit.assertEquals[all `.qpipe.job.cross`.qpipe.job.demo_markout`.qpipe.job.posbook in declared;1b;
        "the tickerplant subscriber processes declare their namespaces under .qpipe.job"]};

/ Private: the process scripts - the files TorQ starts as a process, as
/ opposed to the tools and worked examples that also live under scripts/.
/ .
/ scripts/processes/ since #241 foldered scripts/ by role. The directory is
/ named here rather than searched for, and the companion test below asserts
/ the scan found something: pointed at the old flat scripts/ this returned
/ an empty list and both tests passed over nothing, which is how the move
/ was caught.
.qt.qf.util.namespaces.process_scripts:{[] system"ls scripts/processes | grep '^torq_.*\\.q$'"}

.qt.qf.util.namespaces.test_every_process_script_declares_a_namespace:{[t]
    / The prefix rule above only sees files that declare a namespace at all.
    / Four feed processes and the tap declared NOTHING - every name they
    / owned sat at the root of their own process, which is both invisible to
    / .qf.util and indistinguishable from q's own globals. A process script now
    / has to say which namespace is its own.
    / .
    / Only torq_*.q: a worked example or a one-shot tool legitimately works
    / at the root, and demanding a namespace of them would be a rule about
    / the wrong files.
    bare:.qt.qf.util.namespaces.process_scripts[] where not {[f]
        0<count .qt.qf.util.namespaces.namespaces_in read0 hsym `$"scripts/processes/",f} each .qt.qf.util.namespaces.process_scripts[];
    .qunit.assertEquals[bare;();
        "every scripts/processes/torq_*.q declares the namespace it owns, rather than working at the root"]};

.qt.qf.util.namespaces.test_the_process_script_scan_found_the_scripts:{[t]
    .qunit.assertTrue[3<count .qt.qf.util.namespaces.process_scripts[];
        "the scan finds this tree's process scripts rather than nothing"]};

.qt.qf.util.namespaces.test_every_listed_exception_carries_a_reason:{[t]
    / An exception list whose entries may be empty strings is a list of
    / names, which is the thing this deliberately is not.
    empty:(key .qt.qf.util.namespaces.outside_the_prefix) where 0=count each value .qt.qf.util.namespaces.outside_the_prefix;
    .qunit.assertEquals[empty;`symbol$();"each exception says why it is one"]};

.qt.qf.util.namespaces.test_the_exception_list_has_not_become_the_rule:{[t]
    / A floor on the prefix's meaning: if half the tree ends up excepted,
    / the tools built on the prefix are measuring a minority of the code.
    .qunit.assertTrue[5>count key .qt.qf.util.namespaces.outside_the_prefix;
        "the exceptions stay a handful - each one is code no tool can see"]};

/ ---------------------------------------------------------------------------
/ The declaration globs in src/etl/init.q
/ ---------------------------------------------------------------------------
/ init.q loads sources/, workers/ and streaming/ by GLOB rather than by a
/ hand-kept list. That removes the failure it replaced - a file nobody
/ loaded - but only if the glob really reaches every file, and nothing else
/ in the tree would notice if it stopped: the Python edge verifier reads
/ those directories itself, straight from disk, so it agrees with the files
/ whether or not q ever loaded them.

/ Every file registered, checked as a SUBSET rather than by count. .qpipe.job and
/ .qpipe.source are mutable: tests register their own workers and sources into them
/ (reference_worker.q does it at load time), so a count comparison passes or
/ fails depending on which suites ran first. The property that actually
/ matters is that no file was MISSED.
.qt.qf.util.namespaces.missing_declarations:{[names;registered] names except registered}

/ `.qpipe.source.demo_deals -> `demo_deals. The prefix length is derived from the
/ root rather than counted by hand: `.qpipe.source` and `.qpipe.job` differ by one, and
/ a hardcoded drop silently yields `feed.demo_deals`, which matches nothing
/ and fails as though the file were unregistered.
.qt.qf.util.namespaces.leaf_names:{[root;names] `$(1+count string root)_/:string names}

.qt.qf.util.namespaces.test_every_streaming_file_is_a_registered_job:{[t]
    missed:.qt.qf.util.namespaces.missing_declarations[.qt.support.etl_declaration_names["src/etl/streaming"];
        key .qetl.job.stream.jobs];
    .qunit.assertEquals[missed; `symbol$();
        "every src/etl/streaming/*.q registers a job - one the glob missed would load nothing"]};

.qt.qf.util.namespaces.test_every_source_file_is_a_registered_source:{[t]
    missed:.qt.qf.util.namespaces.missing_declarations[.qt.support.etl_declaration_names["src/etl/sources"];
        .qt.qf.util.namespaces.leaf_names[`.qpipe.source; .qf.util.children `.qpipe.source]];
    .qunit.assertEquals[missed; `symbol$();
        "every src/etl/sources/*.q registers under .qpipe.source"]};

.qt.qf.util.namespaces.test_every_worker_file_is_a_registered_worker:{[t]
    missed:.qt.qf.util.namespaces.missing_declarations[.qt.support.etl_declaration_names["src/etl/workers"];
        .qt.qf.util.namespaces.leaf_names[`.qpipe.job; .qf.util.children `.qpipe.job]];
    .qunit.assertEquals[missed; `symbol$();
        "every src/etl/workers/*.q registers under .qpipe.job"]};
