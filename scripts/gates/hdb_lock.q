/ hdb_lock.q - take or give up an HDB root's write lock for a process that is
/ not q (#1168).
/ .
/ Run as:  q scripts/gates/hdb_lock.q acquire|release <hdb root> <holder pid>
/ from the repository root (init.q's \l lines are root-relative).
/ .
/ Called by uqs.stack.hdb_migrate, which holds the root's lock across its q
/ stage and its own atomic exchange. Python used to take the lock itself, with
/ its own path formula, owner record and no staleness rule: a migration killed
/ mid-way left a lock no q writer would break (its host was gethostname()'s,
/ which on macOS is not .z.h's case), and Python never broke a dead q writer's.
/ Here the lock is q's in every respect - the path is .qetl.io.hdb_lock's, the
/ owner record write_owner's, the wait and the breaking of a dead holder
/ take_lock_at's.
/ .
/ THE HOLDER IS THE CALLER, NOT THIS q. This process exits as soon as it has
/ the lock, and a lock owned by a dead pid is stale by definition - the next
/ writer would break it at once. So the owner recorded is <holder pid>, the
/ migration's own: while it runs, writers wait for it; once it is gone, by a
/ clean release or a kill, the lock is released or broken like any other.
/ .
/ acquire does not wait for a live holder: a migration is run with the stack
/ stopped, so a held lock means something is still writing and the operator
/ is told so. A dead holder's lock is broken and taken.
/ Prints LOCKED|<lock path> or RELEASED|<lock path> and exits 0; any refusal
/ goes to stderr with exit status 1.

args:.z.x;
if[3<>count args; -2 "hdb_lock: needs acquire|release <hdb root> <holder pid>"; exit 2];
if[not (args 0) in ("acquire";"release"); -2 "hdb_lock: the action is acquire or release"; exit 2];
if[null holder:"J"$args 2; -2 "hdb_lock: the holder pid is not a number"; exit 2];

\l src/init.q
\l src/etl/init.q

path:.qetl.io.hdb_lock hsym `$args 1;
refuse:{[e] -2 "hdb_lock: ",e; exit 1};
if["acquire"~args 0;
    .[.qetl.job.bounded.state.take_lock_at;(path;0D;holder);refuse];
    -1 "LOCKED|",path];
if["release"~args 0;
    .[.qetl.job.bounded.state.release_lock_at;(path;holder);refuse];
    -1 "RELEASED|",path];
exit 0
