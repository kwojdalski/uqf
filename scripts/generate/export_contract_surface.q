/ export_contract_surface.q - dump this tree's q contract surface as JSON.
/ .
/ The half of bank question WP2 (#139) that does NOT need the authority
/ checkout. Locking contracts means comparing two surfaces; this produces one
/ of them mechanically, so when the other tree is readable the comparison is a
/ diff rather than a manual reading of 30 namespaces.
/ .
/ WHAT IS A CONTRACT HERE. A public function's NAME, RANK and PARAMETER NAMES,
/ and a table's COLUMN NAMES AND TYPES. Not the bodies: an implementation may
/ differ freely between two trees, and diffing bodies would bury the four
/ differences that matter under four hundred that do not.
/ .
/ Parameter names are included deliberately, even though q does not enforce
/ them at call sites. They are the closest thing q has to a signature, and a
/ silently reordered pair of same-typed arguments is exactly the defect that
/ compiles, passes shape checks and returns a wrong number - which this
/ repository has already been bitten by.
/ .
/ Run: q scripts/generate/export_contract_surface.q -q < /dev/null > surface.json

\l src/init.q
\l src/integrations/data.q
\l src/etl/init.q

\d .surface

/ Namespaces this tree owns, from the one enumeration in src/namespaces.q.
/ .
/ It used to scan the root for a `q` prefix here. That is right only while
/ every namespace is single-level: worker instances nest under .qwrk, and a
/ root scan reports `qwrk` - a namespace holding no functions - while every
/ worker's surface silently disappears from the export the gates diff
/ against. .qns.functional drops the containers and keeps the leaves.
/ .
/ The names come back fully qualified (`.qbw`); the rest of this file works
/ in the undotted form the root scan produced, so the dot is trimmed here.
own_namespaces:{[]
    ns:.qns.functional[];
    ns:ns except `.qunit;
    asc {[n] `$1_string n} each ns}

/ Private: is this value a lambda? 100h is a q lambda; anything else (a
/ projection, a primitive, a table, a constant) has no parameter list to read.
is_lambda:{[v] 100h=type v}

/ Private: a lambda's declared parameter names, in order.
/ .
/ `value` on a lambda returns a list whose second element is the parameter
/ symbols. A niladic lambda reports `x` in some q versions, so an explicit
/ [] is distinguished by the source text rather than trusted from here.
param_names:{[v] $[is_lambda v; (value v)1; `$()]}

/ Every exported name in one namespace, with what can be said about it.
/ .
/ Leading-underscore and empty names are dropped: the empty symbol appears in
/ `key` for a namespace and is not a function.
namespace_surface:{[ns]
    full:` sv `,ns;
    names:key full;
    names:names where not names in `;
    names:names where not (string names) like "_*";
    names:asc names;
    {[full;nm]
        v:@[{value ` sv x,y}[full];nm;{(::)}];
        ps:param_names v;
        `name`kind`rank`params!(
            string nm;
            $[is_lambda v; "function"; 98h=type v; "table"; 99h=type v; "dict"; "value"];
            $[is_lambda v; count ps; 0N];
            string ps)
      }[full] each names}

/ Every namespace, as a dictionary of name -> surface.
functions:{[]
    ns:own_namespaces[];
    ns!namespace_surface each ns}

/ Private: materialise the lazily-created tables.
/ .
/ Without this the export reported ZERO tables, which was the most misleading
/ possible answer: the ETL tables a reconciliation most needs to compare -
/ etl_coverage above all - do not exist until a worker init calls `attach`,
/ and a surface claiming no tables would have read as "this tree defines
/ none" rather than "none had been created yet".
/ .
/ Discovered by convention rather than listed: a niladic `attach` in an owned
/ namespace is this tree's create-if-absent-and-verify function (.qmatz.attach,
/ .qhb.attach), and it is idempotent by design - that is what makes calling it
/ from an export script safe rather than a side effect.
/ .
/ Errors are swallowed per namespace: an attach that needs an environment this
/ export does not have should cost that one table, not the whole surface.
materialise_tables:{[]
    ns:own_namespaces[];
    fns:raze {[n]
        full:` sv `,n;
        ks:key full;
        ` sv/: full,/:ks where ks=`attach
      } each ns;
    {@[{value[x][]};x;{[e] (::)}]} each fns;
    count fns}

/ Private: define the tickerplant tables, so the surface carries them.
/ .
/ They are top-level table declarations in scripts/processes/uqf_stack_tables.q rather
/ than the lazily-created, namespace-owned kind materialise_tables above
/ reaches - nothing calls an `attach` for them, because the process that
/ creates them is stp1 loading a generated database.q.
/ .
/ Without this the surface listed four tables while the system had thirteen,
/ and a reconciliation would have compared the ETL ledgers while silently
/ ignoring every table the demo actually publishes into.
/ .
/ Loading is idempotent and safe here for the same reason calling `attach` is:
/ these are empty typed declarations with no side effect beyond existing.
load_tickerplant_tables:{[]
    f:"scripts/processes/uqf_stack_tables.q";
    @[{system"l ",x};f;{[e] -2 "could not load ",f,": ",e;}];
    f}

/ Table schemas: column names and type characters, for every table this tree
/ defines at the root. The type CHARACTER rather than the number, because
/ "p" is readable in a diff and 12h is not.
table_schemas:{[]
    materialise_tables[];
    load_tickerplant_tables[];
    ts:tables `;
    if[0=count ts; :()!()];
    ts!{[t]
        m:0!meta t;
        `columns`types!(string m`c; string m`t)
      } each ts}

/ The whole surface, as a dictionary ready for .j.j.
/ .
/ `generated_by` is recorded so a stale export is identifiable; there is
/ deliberately no timestamp, because a timestamp makes every export differ
/ from every other and destroys the diff this file exists to enable.
surface:{[]
    `generated_by`namespaces`functions`tables!(
        "scripts/generate/export_contract_surface.q";
        string own_namespaces[];
        functions[];
        table_schemas[])}

\d .

-1 .j.j .surface.surface[];
