/ pmusers.q - who .pm lets do what, on a process that turns .pm on.
/ .
/ Only the gateway does (scripts/torqconfig/settings/gateway.q). There,
/ TorQ's handlers/permissions.q has already replaced .z.pw, .z.pg and .z.ps
/ and loaded TorQ's permissions/default.q - and this runs after it, because
/ torq.q loads KDBSERVCODE's handlers after KDBCODE's.
/ .
/ TWO KINDS OF LOGIN.
/ .
/ Everyone in the starter pack's access list - every process user, and
/ admin, which uqs and the frontend log in as - keeps exactly the access
/ they had before .pm was on: the administrator role, which grants every
/ function. TorQ's default.q gives most of them the narrow systemuser role
/ and knows nothing of several (metrics, sctp's segmentedtickerplant,
/ torquser), so leaving them to it would refuse calls the fleet makes today.
/ .
/ The users in <KDBSERVCONFIG>/permissions/<proctype>_users.csv get only the
/ role that file names - roles permissions/<proctype>.q defines, granted
/ the validated entry points and nothing else. That is the only place an
/ ordinary login comes from, and uqs adds the same users to this gateway's
/ access list (stack/runtime.py), so they can log in here and nowhere else.
/ .
/ TorQ reads permission files only from KDBCONFIG and KDBAPPCONFIG, so this
/ also loads KDBSERVCONFIG's.

\d .pm

/ Private: the user:password lines of an access list, as (user;password).
/ @eg .pm.accesslogins hsym`$"lib/torq-finance-starter-pack/appconfig/passwords/accesslist.txt"
accesslogins:{[path]
    entries:entries where 0<count each entries:trim read0 path;
    {i:x?":";(`$i#x;(i+1)_x)} each entries}

/ Private: the ordinary users and their roles.
policylogins:{[path] ("S*S";enlist",")0:path}

/ Give each process and operator login the administrator role, and each
/ ordinary user its own role. An ordinary user is never also made an
/ administrator, even if the access list names them too.
/ @param logins (user;password) pairs from the access list
/ @param policy user, password and role, from <proctype>_users.csv
setupusers:{[logins;policy]
    logins:logins where not logins[;0] in policy`user;
    {[u;p] .pm.adduser[u;`local;`md5;md5 p];.pm.assignrole[u;`administrator]}.' logins;
    {[u;p;r] .pm.adduser[u;`local;`md5;md5 p];.pm.assignrole[u;r]}'[policy`user;policy`password;policy`role];
    / Messages arriving on handles this process opened - a backend's
    / .gw.addserverresult - never pass .z.pw, and arrive as the blank user or
    / this process's own. Neither can log in: neither is in the access list.
    .pm.assignrole[`;`administrator];
    if[not .z.u in key .pm.user;.pm.adduser[.z.u;`local;`md5;md5 string first -1?0Ng]];
    .pm.assignrole[.z.u;`administrator];
    }

if[@[value;`.pm.enabled;0b];
    .proc.loadconfig[getenv[`KDBSERVCONFIG],"/permissions/";] each `default,.proc.proctype,.proc.procname;
    userspath:hsym`$getenv[`KDBSERVCONFIG],"/permissions/",string[.proc.proctype],"_users.csv";
    setupusers[
        accesslogins hsym`$getenv[`KDBAPPCONFIG],"/passwords/accesslist.txt";
        $[()~key userspath;flip`user`password`role!(`symbol$();();`symbol$());policylogins userspath]];
    .lg.o[`pmusers;"permissions on: ",string[count .pm.user]," users, roles ",", "sv string exec distinct role from .pm.userrole]]

\d .
