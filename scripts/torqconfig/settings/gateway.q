/ scripts/torqconfig/settings/gateway.q - the query policy's settings, on
/ every gateway.
/ .
/ torq.q loads <KDBSERVCONFIG>/settings/<proctype>.q after TorQ's own
/ settings and before the starter pack's, which sets nothing below.
/ scripts/torqcode/gateway/querypolicy.q enforces what this file configures
/ (see it), and scripts/torqcode/handlers/pmusers.q sets up the users.

/ Permissions on: .z.pg and .z.ps run every call through .pm.req, so a role
/ can run only what it has been granted. Who has which role is
/ scripts/torqconfig/permissions/gateway.q.
\d .pm
enabled:1b

\d .checkinputs

/ The per-table policies.
querypolicypath:hsym`$getenv[`KDBSERVCONFIG],"/dataaccess/querypolicy.csv"

/ No policy row or role exception can exceed these. Under .pm.maxsize
/ (200MB), the limit TorQ applies to any one reply.
policyceiling:`maxrange`maxrows`maxbytes`timeout!(31D;5000000;128000000;0D00:05)

/ Roles whose getdata requests are not held to a policy: they can run raw q
/ on the gateway anyway. admin is the operator login (uqs, the frontend);
/ administrator is every process login (see handlers/pmusers.q).
trustedroles:`admin`administrator

\d .
