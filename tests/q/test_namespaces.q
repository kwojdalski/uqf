/ test_namespaces.q - the namespace enumeration four tools now share (.nstest).
/ .
/ Worth its own suite because the failure this file exists to prevent is
/ SILENT. A root-level `(key `) where like "q*"` scan - what the contract
/ surface export, the coverage driver and the documentation ratchet each
/ carried privately - reports `.qwrk` as one namespace and never looks
/ inside it, so every worker's public surface disappears from all three at
/ once while each keeps printing a passing line over a smaller set.
/ .
/ The nested case is therefore asserted directly rather than through the
/ tools, and so is the char-keyed dictionary (.qsrc.coercers) that made the
/ first version of is_namespace throw a bare 'type from four frames down.

\d .nstest

/ --- is_namespace ---------------------------------------------------------

test_a_namespace_is_recognised:{[t]
    .qunit.assertEquals[.qns.is_namespace value `.qbw;1b;
        "a namespace created by \\d is one"]};

test_the_root_namespace_is_not_recognised:{[t]
    / Asserted rather than left undefined. The root is the ONE namespace
    / whose keys do not include the empty symbol - a child namespace carries
    / it as a back-reference and root has nothing to point back to - so
    / is_namespace answers 0b for it. Nothing here starts a walk at root, so
    / the answer does not matter to any caller; it mattering later without
    / anyone noticing is what this test is for.
    .qunit.assertEquals[.qns.is_namespace value `.;0b;
        "the root has no empty-symbol back-reference, so it answers 0b"]};

test_an_ordinary_dictionary_is_not_a_namespace:{[t]
    / .qbw.worker_cfg is a symbol-keyed dictionary that is NOT a namespace,
    / which is the case the empty-symbol test exists to separate.
    .qunit.assertEquals[.qns.is_namespace .qbw.worker_cfg;0b;
        "a symbol-keyed dictionary with no empty key is not a namespace"]};

test_a_char_keyed_dictionary_is_not_a_namespace:{[t]
    / The live trap: .qsrc.coercers is keyed by type CHARS, so `` ` in key v ``
    / compares a symbol against a char vector and throws 'type. A scan that
    / walks every global meets it, and the error names nothing useful.
    .qunit.assertEquals[.qns.is_namespace .qsrc.coercers;0b;
        "a char-keyed dictionary answers 0b rather than throwing 'type"]};

test_a_table_is_not_a_namespace:{[t]
    .qunit.assertEquals[.qns.is_namespace ([] a:1 2);0b;"a table is not a namespace"]};

test_a_keyed_table_is_not_a_namespace:{[t]
    / 99h like a dictionary, but `key` returns a table rather than symbols.
    .qunit.assertEquals[.qns.is_namespace ([a:1 2] b:3 4);0b;"a keyed table is not a namespace"]};

test_a_function_is_not_a_namespace:{[t]
    .qunit.assertEquals[.qns.is_namespace {[x] x};0b;"a lambda is not a namespace"]};

/ --- owned ----------------------------------------------------------------

test_a_flat_library_namespace_is_owned:{[t]
    .qunit.assertEquals[`.qbw in .qns.owned[];1b;"the framework's own namespace is listed"]};

test_the_worker_root_is_owned:{[t]
    .qunit.assertEquals[`.qwrk in .qns.owned[];1b;
        "the container is listed too - a caller asking what exists gets it"]};

test_a_nested_worker_namespace_is_owned:{[t]
    / THE test. A root-level scan finds `.qwrk and stops.
    .qunit.assertEquals[`.qwrk.demo_deals_backfill in .qns.owned[];1b;
        "a worker nested under .qwrk is reached, not hidden behind its parent"]};

test_every_registered_worker_has_its_namespace_listed:{[t]
    / Stated against the worker registry rather than a hard-coded list, so a
    / worker added later is covered without editing this file.
    missing:.qbfstate.registered[] where not
        {[w] (.qbw.namespace w) in .qns.owned[]} each .qbfstate.registered[];
    .qunit.assertEquals[missing;`symbol$();
        "every registered bounded worker's namespace is enumerated"]};

test_kdbs_own_q_namespace_is_not_owned:{[t]
    / `.q is KX's, and its 180-odd names would swamp every report built on
    / this list.
    .qunit.assertEquals[`.q in .qns.owned[];0b;"q's own namespace is not this tree's"]};

test_the_names_are_fully_qualified:{[t]
    / The root scan produced undotted names (`qbw), and a caller that passed
    / one to `value` got the ROOT GLOBAL of that name, or a null - not the
    / namespace. Fully qualified here so there is nothing to reassemble.
    .qunit.assertEquals[all (string .qns.owned[]) like ".*";1b;
        "every name carries its leading dot"]};

test_the_listing_is_not_empty:{[t]
    / A guard against the whole enumeration silently answering nothing, which
    / would make every test above vacuous and every tool built on it report
    / a clean sweep over no code at all.
    .qunit.assertTrue[20<count .qns.owned[];
        "the enumeration finds this tree's namespaces, not a handful"]};

/ --- functional -----------------------------------------------------------

test_the_worker_root_holds_no_functions:{[t]
    / .qwrk holds only namespaces, so a tool that measures coverage or
    / exports a surface has nothing to do with it.
    .qunit.assertEquals[`.qwrk in .qns.functional[];0b;
        "a container of namespaces is not a functional namespace"]};

test_a_nested_worker_namespace_is_functional:{[t]
    .qunit.assertEquals[`.qwrk.demo_deals_backfill in .qns.functional[];1b;
        "the worker itself holds functions and is reported"]};

test_functional_is_a_subset_of_owned:{[t]
    .qunit.assertEquals[all .qns.functional[] in .qns.owned[];1b;
        "functional narrows owned rather than finding something else"]};

/ --- every shipped namespace is inside the prefix --------------------------

/ The `.q` prefix is not decoration: it is the whole of `owned`'s definition,
/ so a namespace declared without it cannot be seen by the contract surface,
/ the coverage report or the documentation ratchet even in a process that
/ HAS loaded it - and not seen in the way that reports success, because each
/ of those tools keeps printing a passing line over the smaller set.
/ .
/ Three namespaces in scripts/ were outside it until this test existed:
/ .cross, .markout and .posbook, the tickerplant subscriber processes, now
/ .qsub.cross and friends. The prefix is a necessary condition, not a
/ sufficient one - those three also subscribe to a tickerplant as they load,
/ so no plain q process can load them and man.q's generator scans src/ only.
/ What the prefix buys is that the moment a tool DOES have them in
/ process - a coverage run inside the live process, `.qns.owned` there -
/ they are part of this tree rather than indistinguishable from q's own
/ globals.

/ Namespaces that are deliberately outside the prefix, each with the reason
/ it cannot simply be renamed. Written as a dictionary rather than a list so
/ the reason lives beside the name and a future reader does not have to
/ guess whether an entry is a decision or an oversight.
outside_the_prefix:(`symbol$())!();
outside_the_prefix[`.dqe]:"TorQ's OWN namespace - torq_metatables.q adds uqf_metatable INTO it so DQE's runquery can transport it. Renaming would break the integration, not tidy it.";
outside_the_prefix[`.cov]:"the coverage tool, in KX's published .cov API shape. .qmatz is already the ETL coverage LEDGER, so the obvious rename collides with an unrelated namespace.";
outside_the_prefix[`.surface]:"the contract-surface exporter, which enumerates this tree's namespaces. Inside the prefix it would export itself - a tool appearing in the artifact it produces.";

/ Private: every namespace declared by a file under src/ or scripts/.
declared_namespaces:{[]
    files:system"find src scripts -name '*.q'";
    raze {[f]
        lines:read0 hsym `$f;
        decls:lines where lines like "\\d .*";
        decls:decls where not decls like "\\d .";
        `$3_/:decls} each files}

test_every_shipped_namespace_is_inside_the_prefix_or_listed:{[t]
    / The ratchet. A new `\d .something` in src/ or scripts/ either carries
    / the prefix or is added to outside_the_prefix WITH its reason, which
    / makes the exception a decision someone wrote down rather than a file
    / nobody noticed.
    all_ns:distinct declared_namespaces[];
    stray:all_ns where not (all_ns like ".q*") or all_ns in key outside_the_prefix;
    .qunit.assertEquals[stray;`symbol$();
        "every namespace in src/ and scripts/ is .q-prefixed, or listed in outside_the_prefix with its reason"]};

test_the_scan_actually_found_the_declarations:{[t]
    / Without this the test above passes over an empty list the day the
    / directory layout changes - reporting success for having looked nowhere.
    .qunit.assertTrue[30<count distinct declared_namespaces[];
        "the file scan found this tree's namespace declarations"]};

test_the_subscriber_processes_are_inside_the_prefix:{[t]
    / Named specifically, because these three are what the general rule above
    / was written for and a regression here would otherwise read as a count.
    declared:distinct declared_namespaces[];
    .qunit.assertEquals[all `.qsub.cross`.qsub.markout`.qsub.posbook in declared;1b;
        "the tickerplant subscriber processes declare their namespaces under .qsub"]};

/ Private: the process scripts - the files TorQ starts as a process, as
/ opposed to the tools and worked examples that also live under scripts/.
/ .
/ scripts/processes/ since #241 foldered scripts/ by role. The directory is
/ named here rather than searched for, and the companion test below asserts
/ the scan found something: pointed at the old flat scripts/ this returned
/ an empty list and both tests passed over nothing, which is how the move
/ was caught.
process_scripts:{[] system"ls scripts/processes | grep '^torq_.*\\.q$'"}

test_every_process_script_declares_a_namespace:{[t]
    / The prefix rule above only sees files that declare a namespace at all.
    / Four feed processes and the tap declared NOTHING - every name they
    / owned sat at the root of their own process, which is both invisible to
    / .qns and indistinguishable from q's own globals. A process script now
    / has to say which namespace is its own.
    / .
    / Only torq_*.q: a worked example or a one-shot tool legitimately works
    / at the root, and demanding a namespace of them would be a rule about
    / the wrong files.
    bare:.nstest.process_scripts[] where not {[f]
        any (read0 hsym `$"scripts/processes/",f) like "\\d .*"} each .nstest.process_scripts[];
    .qunit.assertEquals[bare;();
        "every scripts/processes/torq_*.q declares the namespace it owns, rather than working at the root"]};

test_the_process_script_scan_found_the_scripts:{[t]
    .qunit.assertTrue[3<count process_scripts[];
        "the scan finds this tree's process scripts rather than nothing"]};

test_every_listed_exception_carries_a_reason:{[t]
    / An exception list whose entries may be empty strings is a list of
    / names, which is the thing this deliberately is not.
    empty:(key outside_the_prefix) where 0=count each value outside_the_prefix;
    .qunit.assertEquals[empty;`symbol$();"each exception says why it is one"]};

test_the_exception_list_has_not_become_the_rule:{[t]
    / A floor on the prefix's meaning: if half the tree ends up excepted,
    / the tools built on the prefix are measuring a minority of the code.
    .qunit.assertTrue[5>count key outside_the_prefix;
        "the exceptions stay a handful - each one is code no tool can see"]};

\d .
