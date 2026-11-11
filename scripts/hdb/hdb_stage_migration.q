/ Stage one HDB table partition using the current database.q declaration.
/ .
/ Called by uqs.stack.hdb_migrate. The caller checks STAGED before exchanging
/ directories; q exits with status zero even after some script errors.
/ Arguments: root schema date table stage old_names new_names cast_names.
/ Names are comma-separated or "-". A rename maps old_names[i] to new_names[i].
/ A type change must name its target column in cast_names and round-trip
/ exactly through the original type. Existing columns are never guessed away.
/ .
/ An optional ninth argument selects the mode:
/   migrate      the default: check, then stage the renamed and cast table
/   check        every check migrate makes, casts round-tripped, and stage
/                nothing; print CHECKED|rows - the dry run's plan (#2463)
/   reset-check  verify every stored column is empty; print PLAN|old|new; write nothing
/   reset        the same check, then stage the declaration's empty table (#2251)
/ Both ignore the rename/cast lists (the caller passes "-"). A reset never
/ guesses: one populated, unreadable or unaccounted-for file refuses it.

args:.z.x;
if[8>count args; -2 "hdb_stage_migration: needs 8 arguments"; exit 2];
root:hsym `$args 0;
schema_file:hsym `$args 1;
part:"D"$args 2;
table_name:`$args 3;
stage_dir:args 4;
names:{[s] $[s~(),"-";`symbol$();`$"," vs s]};
old_names:names args 5;
new_names:names args 6;
cast_names:names args 7;
mode:$[8>count args;"migrate";args 8];
if[not mode in ("migrate";"check";"reset-check";"reset"); -2 "hdb_stage_migration: unknown mode ",mode; exit 2];

fail:{[msg] -2 "hdb_stage_migration: ",msg; exit 1};
if[()~key root; fail "no HDB at ",string root];
if[()~key schema_file; fail "no schema at ",string schema_file];
if[(count old_names)<>(count new_names); fail "rename lists have different lengths"];
if[(count distinct old_names)<>(count old_names); fail "a source column is renamed twice"];
if[(count distinct new_names)<>(count new_names); fail "two columns rename to the same target"];

before_load:tables[];
system"l ",1_string schema_file;
if[not table_name in tables[] except before_load;
    fail "schema does not declare ",string table_name];
decl:value table_name;
want:flip decl;
target_cols:cols decl;
if[count new_names except target_cols;
    fail "rename target is not declared: ","," sv string new_names except target_cols];
if[count cast_names except target_cols;
    fail "cast target is not declared: ","," sv string cast_names except target_cols];

source_dir:.Q.par[root;part;table_name];
dfile:` sv source_dir,`.d;
if[()~key dfile; fail "no table at ",string source_dir];
source_cols:get dfile;

/ Reset: every stored column file must be readable and hold no rows. A file
/ in .d that is missing or unreadable, or a file .d does not account for, is
/ an inconsistent table and is refused rather than discarded.
spec:{[names;t] "," sv {string[x],":",string y}'[names;t]};
if[mode like "reset*";
    if[count (names args 5),(names args 6),(names args 7); fail "--reset-empty takes no rename or cast"];
    companions:raze {(x;`$string[x],"#";`$string[x],"##")} each source_cols;
    stray:(key source_dir) except `.d,companions;
    if[count stray; fail "table holds files .d does not list: ","," sv string stray];
    rows_in:{[dir;c] @[{count get x};` sv dir,c;{-1}]}[source_dir] each source_cols;
    if[any unreadable:rows_in<0; fail "unreadable column(s): ","," sv string source_cols where unreadable];
    if[any populated:rows_in>0; fail "populated column(s): ","," sv string source_cols where populated];
    old_types:{[dir;c] @[{type get x};` sv dir,c;{0Nh}]}[source_dir] each source_cols;
    new_types:{type x} each want target_cols;
    if[mode~"reset-check";
        -1 "PLAN|",spec[source_cols;old_types],"|",spec[target_cols;new_types];
        exit 0];
    rebuilt:flip target_cols!{[w;c] a:attr w c; $[null a;0#w c;a#0#w c]}[want] each target_cols;
    (hsym `$stage_dir,"/") set .Q.en[root;rebuilt];
    staged:select from get hsym `$stage_dir,"/";
    if[not target_cols~cols staged; fail "staged columns differ from the declaration"];
    if[0<>count staged; fail "staged table is not empty"];
    -1 "STAGED|0|",1_string source_dir;
    exit 0];
source_for:{[to;sources;c] $[c in to;sources to?c;c]}[new_names;old_names] each target_cols;
if[(count distinct source_for)<>count source_for;
    fail "two target columns read the same source column"];
if[count missing:source_for except source_cols;
    fail "source is missing ","," sv string missing];
if[count extra:source_cols except source_for;
    fail "source holds unmapped column(s) ","," sv string extra];

/ An enumerated column's indices have meaning only against root/sym.
decoded:{[root;v]
    if[not type[v] within 20 76h; :v];
    domain:key v;
    domain set get ` sv root,domain;
    value v};

/ A cast is explicit and lossless; a changed declaration alone never
/ authorises a conversion or a truncated value.
converted:{[want;casts;c;v]
    target_type:type want c;
    original_type:type v;
    if[target_type=0h; :v];
    if[target_type=original_type; :v];
    if[not c in casts; fail "type change on ",string[c]," needs --cast ",string c];
    if[target_type=11h;
        fail "cast of ",string[c]," to symbol is unsupported: it could change root/sym before the swap"];
    out:target_type$v;
    if[not v~original_type$out;
        fail "cast of ",string[c]," loses values; leave this partition untouched"];
    out};

/ Reapply the target declaration's attributes after conversion. A sorted
/ attribute that the rows no longer satisfy must fail before staging.
with_attr:{[want;c;v]
    a:attr want c;
    $[null a;v;a#v]};

raw:{[dir;c] get ` sv dir,c}[source_dir] each source_for;
values:decoded[root] each raw;
values:converted[want;cast_names]'[target_cols;values];
values:with_attr[want]'[target_cols;values];
rows:count first values;
rebuilt:flip target_cols!values;
if[rows<>count rebuilt; fail "rebuilt table changed its row count"];
if[mode~"check"; -1 "CHECKED|",string rows; exit 0];

/ Stage outside the mapped HDB and enumerate symbols against its own sym
/ file. The caller keeps the original table until an atomic directory swap.
(hsym `$stage_dir,"/") set .Q.en[root;rebuilt];
staged:select from get hsym `$stage_dir,"/";
if[not target_cols~cols staged; fail "staged columns differ from the declaration"];
if[rows<>count staged; fail "staged row count differs from the source"];
check:decoded[root] each value flip staged;
same:{x~y}'[values;check];
if[not all same;
    fail "staged values differ on ","," sv string target_cols where not same];
-1 "STAGED|",string[rows],"|",1_string source_dir;
exit 0
