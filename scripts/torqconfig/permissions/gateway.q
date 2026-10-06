/ scripts/torqconfig/permissions/gateway.q - the roles the query policy
/ applies to, on every gateway.
/ .
/ Loaded by scripts/torqcode/handlers/pmusers.q after TorQ's own
/ permissions/default.q, which defines admin, administrator and systemuser.
/ TorQ itself reads permission files only from KDBCONFIG and KDBAPPCONFIG,
/ which is why a handler loads this one.
/ .
/ An ordinary role is granted the validated entry points and nothing else:
/ no .gw.syncexec, no select, no lambda. The gateway connects to the
/ backends as its own process user, and an ordinary login is in neither
/ rdb1's nor hdb1's access list, so going round the gateway is refused at
/ login.
/ .
/ The users holding these roles, and their passwords, are
/ gateway_users.csv beside this file.

/ Reads the exposed tables through getdata, under their table policy.
.pm.addrole[`analyst;"reads exposed tables through .dataaccess.getdata, under querypolicy.csv"]
.pm.grantfunction[`.dataaccess.getdata;`analyst;{1b}]
.pm.grantfunction[`.checkinputs.querypolicyfor;`analyst;{1b}]

/ The same entry points; querypolicy.csv gives this role longer ranges and
/ larger results on some tables.
.pm.addrole[`quant;"as analyst, with the role exceptions in querypolicy.csv"]
.pm.grantfunction[`.dataaccess.getdata;`quant;{1b}]
.pm.grantfunction[`.checkinputs.querypolicyfor;`quant;{1b}]
