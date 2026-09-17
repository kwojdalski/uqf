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

\d .
