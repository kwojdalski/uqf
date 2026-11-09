// test_kdb_fix_adaptor.q - which of the vendored FIX adaptor's q-side
// conversions are reversible, checked against the FIX dictionaries it ships.
//
// lib/kdb-fix-adaptor is vendored and never edited here (NOTICE.md). Its q
// layer is two lookup tables:
//   - .fix.tags   tag name -> tag number   (src/config/fix.q, 1527 lines)
//   - .fix.tables message name -> MsgType   (src/config/fixtabletags.q)
// and upstream's manual (docs/src/CompilingAndInstalling.tex) documents the
// reverse of both as `?`, "as each integer maps to a single tag". The
// generator it names (src/python/parseheaders.py) is not in the vendored
// tree, so the bundled spec XMLs (src/config/spec/FIX*.xml) are the only
// authority these tables can be held against.
//
// The tables are read as TEXT, never loaded: fix.q is a CMake template whose
// first line loads the shared library through an @BINARY_NAME@ placeholder,
// and defining .fix in this process would add a namespace outside the .q
// prefix. The q dictionary <-> FIX wire conversion lives in C++ (main.cxx)
// and is exercised by tests/q/smoke_kdb_fix_adaptor.q against a built
// library, not here.

.qt.vendor.kdb_fix_adaptor.root:"lib/kdb-fix-adaptor/src/config/"

/ Private: the text after `attr='` up to the closing quote on one XML line.
.qt.vendor.kdb_fix_adaptor.attr:{[a;line]
    i:first line ss a,"='";
    s:(i+2+count a)_line;
    (s?"'")#s}

/ Private: name!number of every `.fix.tags.<Name>: <n>` line, in file order.
/ A duplicate name would be silently collapsed by a dictionary, so the result
/ is a two-column table.
.qt.vendor.kdb_fix_adaptor.tags:{[]
    lines:read0 hsym `$.qt.vendor.kdb_fix_adaptor.root,"fix.q";
    lines:lines where lines like ".fix.tags.*";
    parts:{":" vs x} each lines;
    names:`$10_/:first each parts;
    nums:"J"$trim last each parts;
    ([] name:names; num:nums)}

/ Private: name and msgtype of every `.fix.tables.<Name> : `<code>` line.
.qt.vendor.kdb_fix_adaptor.tables:{[]
    lines:read0 hsym `$.qt.vendor.kdb_fix_adaptor.root,"fixtabletags.q";
    lines:lines where lines like ".fix.tables.*";
    parts:{":" vs x} each lines;
    names:`$trim 12_/:first each parts;
    codes:`$1_/:trim last each parts;
    ([] name:names; code:codes)}

/ Private: the bundled FIX dictionary files, by version.
.qt.vendor.kdb_fix_adaptor.spec_files:{[]
    f:key hsym `$.qt.vendor.kdb_fix_adaptor.root,"spec";
    f where f like "FIX*.xml"}

/ Private: every field DEFINITION (the lines carrying a number= attribute;
/ the per-message references carry only name=) across all bundled specs.
.qt.vendor.kdb_fix_adaptor.spec_fields:{[]
    one:{[f]
        lines:read0 hsym `$.qt.vendor.kdb_fix_adaptor.root,"spec/",string f;
        lines:lines where lines like "*<field number='*";
        ([] spec:f; name:`$.qt.vendor.kdb_fix_adaptor.attr["name"] each lines;
            num:"J"$.qt.vendor.kdb_fix_adaptor.attr["number"] each lines)};
    raze one each .qt.vendor.kdb_fix_adaptor.spec_files[]}

/ Private: every message definition across all bundled specs. Attribute order
/ differs between files (FIXT11 writes msgtype before name), hence attr.
.qt.vendor.kdb_fix_adaptor.spec_messages:{[]
    one:{[f]
        lines:read0 hsym `$.qt.vendor.kdb_fix_adaptor.root,"spec/",string f;
        lines:lines where lines like "*<message *";
        ([] spec:f; name:`$.qt.vendor.kdb_fix_adaptor.attr["name"] each lines;
            code:`$.qt.vendor.kdb_fix_adaptor.attr["msgtype"] each lines)};
    raze one each .qt.vendor.kdb_fix_adaptor.spec_files[]}

/ The reader sees every tag line: a line it failed to split would show up as
/ a null number, and a short count would hide lost rows behind the checks
/ below.
.qt.vendor.kdb_fix_adaptor.test_every_tag_line_parses:{[t]
    tg:.qt.vendor.kdb_fix_adaptor.tags[];
    .qunit.assertEquals[count tg;1527;"fix.q defines 1527 .fix.tags entries"];
    .qunit.assertEquals[any null tg`num;0b;"every tag line has a numeric value"];
    .qunit.assertEquals[count distinct tg`name;count tg;"no tag name is defined twice"]};

/ name -> number agrees with the dictionaries QuickFIX validates against:
/ every pair is in some bundled spec, and every spec field has its pair. So
/ the forward direction neither invents nor drops a field.
.qt.vendor.kdb_fix_adaptor.test_tag_names_match_the_bundled_specs_both_ways:{[t]
    tg:.qt.vendor.kdb_fix_adaptor.tags[];
    sp:distinct select name, num from .qt.vendor.kdb_fix_adaptor.spec_fields[];
    extra:tg except sp;
    missing:sp except tg;
    .qunit.assertEquals[extra;0#tg;"every .fix.tags pair is defined by a bundled spec"];
    .qunit.assertEquals[missing;0#tg;"every bundled spec field has its .fix.tags pair"]};

/ The documented reverse lookup (`.fix.tags?n`, "each integer maps to a single
/ tag") is NOT injective: FIX renamed 38 fields between versions, and fix.q
/ keeps both names. `?` returns whichever is defined first, so name -> number
/ -> name loses exactly the later name of each renamed field - no more, no
/ fewer. The lossy set is derived from the specs, so a regenerated table that
/ adds or resolves an alias moves both sides together.
.qt.vendor.kdb_fix_adaptor.test_number_to_name_is_lossy_exactly_on_renamed_fields:{[t]
    tg:.qt.vendor.kdb_fix_adaptor.tags[];
    d:(exec name from tg)!exec num from tg;
    back:d?value d;
    lost:asc (key d) where not back=key d;
    sp:distinct select name, num from .qt.vendor.kdb_fix_adaptor.spec_fields[];
    renamed:exec num from (select n:count i by num from sp) where n>1;
    expected:asc exec name from tg where num in renamed, i>(first;i) fby num;
    .qunit.assertEquals[count renamed;38;"38 tag numbers carry two names across FIX 4.0-5.0SP2"];
    .qunit.assertEquals[lost;expected;"the reverse lookup loses the second name of each renamed field, and nothing else"];
    .qunit.assertEquals[d?22;`IDSource;"counterexample: tag 22 reads back as the FIX 4.0-4.2 name"];
    .qunit.assertEquals[d?d`SecurityIDSource;`IDSource;"so SecurityIDSource (FIX 4.3+) does not survive name -> number -> name"]};

/ .fix.tables is injective, so its documented `?` reverse lookup round-trips
/ every name it holds.
.qt.vendor.kdb_fix_adaptor.test_message_name_code_round_trips:{[t]
    tb:.qt.vendor.kdb_fix_adaptor.tables[];
    d:(exec name from tb)!exec code from tb;
    .qunit.assertEquals[count d;93;"fixtabletags.q defines 93 message types"];
    .qunit.assertEquals[count distinct value d;count d;"no MsgType code is listed twice"];
    .qunit.assertEquals[d?value d;key d;"name -> code -> name is the identity"]};

/ Against the specs the message table is neither exact nor complete: two
/ names are spelled as in no bundled spec, and the 23 message types FIX 5.0,
/ 5.0SP1 and 5.0SP2 added (BI..CE) have no entry, so looking one up gives a
/ null rather than a refusal. Every FIX 4.0-4.4 / FIXT 1.1 code is covered.
.qt.vendor.kdb_fix_adaptor.test_message_table_against_the_specs:{[t]
    tb:.qt.vendor.kdb_fix_adaptor.tables[];
    sm:.qt.vendor.kdb_fix_adaptor.spec_messages[];
    unknown:asc exec name from tb where not ([] name; code) in select name, code from sm;
    .qunit.assertEquals[unknown;`RequestforPositionsAck`XMLMessage;
        "two names match no spec: a lower-case f (spec: RequestForPositionsAck) and XMLMessage (spec: XMLnonFIX)"];
    old:exec distinct code from sm where not spec like "FIX5*";
    .qunit.assertEquals[old except tb`code;`symbol$();"every FIX 4.0-4.4 and FIXT 1.1 MsgType has an entry"];
    gap:asc (exec distinct code from sm) except tb`code;
    .qunit.assertEquals[count gap;23;"23 MsgTypes added in FIX 5.0/5.0SP1/5.0SP2 have no entry"];
    .qunit.assertEquals[(first gap;last gap);`BI`CE;"the gap is the BI..CE range"];
    .qunit.assertEquals[null (exec code!name from tb)`BN;1b;"so ExecutionAcknowledgement (BN) reads back as a null name"]};
