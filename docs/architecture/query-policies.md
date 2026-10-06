# Query policies on the gateway

An ordinary user reads the stack's tables only through TorQ's data-access API on
the gateway, `.dataaccess.getdata`. Each table they can reach has a policy: how
much time one request may cover, which filters it must carry, whether it may
aggregate, and how large a result it may return. A request that breaks the
policy is refused before it reaches a backend, with a message that says what to
change. Raw q, `.gw.syncexec` and direct connections to `rdb1` and `hdb1` stay
with the logins that already had them.

Nothing here is a new process or a new namespace. It is TorQ's own data-access,
validation, permissions and gateway code, configured, with two of its steps
wrapped.

## The path of one request

```text
client -> gateway1: .dataaccess.getdata[request]
            .pm          is this login allowed to call getdata at all?
            .checkinputs TorQ's parameter checks, then the table's policy
            routing      which rdb/hdb holds the time range (.dataaccess.attributesrouting)
          -> rdb1 / hdb1: getdata, under the policy's timeout (.gw)
          <- merge       .dataaccess.autojoin
            size check   rows and bytes against the policy
client <- result, or the refusal
```

  | Step                 | TorQ component                       | What this tree adds                                     |
  | ---                  | ---                                  | ---                                                     |
  | entry point          | `.dataaccess.getdata` on the gateway | turned on: `-dataaccess` on every gateway, rdb and hdb  |
  | who may call it      | `.pm`, TorQ's permissions handler    | turned on for gateways; the `analyst` and `quant` roles |
  | validation           | `.checkinputs.checkinputs`           | wrapped: after TorQ's checks, the table's policy        |
  | routing and timeouts | `.dataaccess` and `.gw`              | the policy's timeout replaces an absent or longer one   |
  | merge                | `.dataaccess.autojoin`               | wrapped: the merged result's rows and bytes are checked |

The wrapping is in
[`scripts/torqcode/gateway/querypolicy.q`](../../scripts/torqcode/gateway/querypolicy.q).
getdata calls both wrapped functions by name, so redefining them is enough; the
vendored tree is not edited.

## What a policy says

[`scripts/torqconfig/dataaccess/querypolicy.csv`](../../scripts/torqconfig/dataaccess/querypolicy.csv)
has one row per table, plus a row for each role that needs an exception:

  | Column                | Meaning                                                                                                    |
  | ---                   | ---                                                                                                        |
  | `tablename`, `role`   | the table; blank `role` is the row ordinary callers get                                                    |
  | `maxrange`            | the most time one request may cover, from `starttime` to `endtime` (a date `endtime` means that whole day) |
  | `requiredfilters`     | columns a request must filter on, `\|`-separated; the instrument column is satisfied by `instruments`      |
  | `operations`          | `raw`, `aggregate` or both; aggregating means `aggregations`, `timebar` or `grouping`                      |
  | `functions`           | if set, the only aggregation functions allowed                                                             |
  | `maxrows`, `maxbytes` | the largest merged result returned                                                                         |
  | `timeout`             | the longest the gateway waits on the backends                                                              |
  | `basis`               | why the numbers are what they are                                                                          |

A separate file rather than more columns on TorQ's `tableproperties.csv`:
`.checkinputs.readtableproperties` reads that file with a fixed type string, so
a column it does not know is a column it drops.

A caller with a role that has an exception for the table gets the most
permissive of their exceptions, field by field. Every policy, exception or not,
is capped by `.checkinputs.policyceiling` in
[`scripts/torqconfig/settings/gateway.q`](../../scripts/torqconfig/settings/gateway.q):
31 days, 5,000,000 rows, 128,000,000 bytes, five minutes. The byte ceiling sits
under `.pm.maxsize` (200MB), the limit TorQ applies to any one reply.

Some parameters are refused for every ordinary caller, whatever the table:
`sqlquery`, `freeformwhere`, `freeformby`, `freeformcolumn`, `postprocessing`,
`postback` and `join`. Each runs q, or a query, that the policy cannot see into.
`sqlquery` especially: TorQ's checks return early when they see it.

A table with no row is refused. So is a policy file that would allow more than
it says: an unknown operation, or a blank or zero limit. If the file cannot be
read, the gateway loads no policies and refuses every ordinary request.

## The tables, and why these limits

The three tables are the in-tree analogues of the desk's deals, order book and
markout tables (see [`demo_deals.q`](../../src/etl/sources/demo_deals.q) for why
the desk's own names are not in this repository). Each limit is set from that
table's own data density, not copied between rows:

  | Table               | Density                                                                                                     | Default policy                                                                                                                               |
  | ---                 | ---                                                                                                         | ---                                                                                                                                          |
  | `mkt_orderbook`     | 10 rows/s: `widefeed1` ticks twice a second for five pairs; two 11-level vectors make a row about 220 bytes | one hour, a `sym` filter required, raw only, 50,000 rows, 16MB. One pair for an hour is 7,200 rows                                           |
  | `execution_quality` | 2 rows/s: one fill a second, scored at two horizons; about 56 bytes a row                                   | seven days, raw or aggregate, 250,000 rows, 32MB. A raw week (1.2M rows) stops at the row limit, which is intended: a week is for aggregates |
  | `duckdb_deals`      | 200 to 1,000 deals a day                                                                                    | 31 days, raw or aggregate, 100,000 rows, 16MB                                                                                                |

`quant` has one exception: a whole day of `mkt_orderbook` (172,800 rows for one
pair, about 38MB), still filtered by `sym`.

These numbers come from the synthetic feeds' rates. Against real data they are a
starting point.

## Who is held to a policy

On the gateway `.pm` is on
([`settings/gateway.q`](../../scripts/torqconfig/settings/gateway.q)), so every
call goes through `.pm.req`, and a login can run only what its roles grant.

- **Process and operator logins.** Every user in the starter pack's access list
  keeps the access they had before `.pm` was turned on: the `administrator`
  role, which grants every function. That covers the fleet's process users, and
  `admin`, which `uqs` and the frontend log in as. These roles, and `admin`, are
  in `.checkinputs.trustedroles`. Their getdata calls are not held to a policy,
  because they can run raw q anyway.
- **Ordinary users.** Each user in
  [`permissions/gateway_users.csv`](../../scripts/torqconfig/permissions/gateway_users.csv)
  gets only the role named there. The roles are defined in
  [`permissions/gateway.q`](../../scripts/torqconfig/permissions/gateway.q), and
  they grant `.dataaccess.getdata` and `.checkinputs.querypolicyfor` (which
  shows the caller's policy for a table), and nothing else: no `.gw.syncexec`,
  no `select`, no lambda.

[`scripts/torqcode/handlers/pmusers.q`](../../scripts/torqcode/handlers/pmusers.q)
sets this up. TorQ reads permission files only from its own config and the
starter pack's, so this handler loads the tree's ones.

**Going round the gateway.** An ordinary user is on `gateway1`'s access list and
on no other process's. `uqs` writes `gateway1` its own list: the starter pack's,
plus the users in `gateway_users.csv`. A direct connection to `rdb1` or `hdb1`
is therefore refused at login. The backends keep the starter pack's list and run
without `.pm`, so ingestion and every process-to-process call behave as before.

The users shipped, `analyst:analyst` and `quant:quant`, are demonstration logins
in the same sense as `admin:admin`.

## Seen from a client

From q, as `analyst`:

```q
h:hopen `::6057:analyst:analyst   / gateway1 on the default base port
h(`.dataaccess.getdata;`tablename`starttime`endtime`instruments!(`mkt_orderbook;.z.p-0D00:30;.z.p;`EURUSD))
h(`.checkinputs.querypolicyfor;`mkt_orderbook)
```

Refusals name the limit and the fix:

```text
querypolicy: mkt_orderbook allows at most 0D01:00:00.000000000 per request and this one covers 0D02:00:00.000000000 - narrow starttime/endtime or split the request
querypolicy: mkt_orderbook needs a filter on sym - pass instruments (e.g. `EURUSD) or a filters condition on it
querypolicy: mkt_orderbook allows raw and this request is aggregate - drop aggregations, timebar and grouping
querypolicy: freeformwhere not allowed for your role - a free-form where clause is q the policy cannot read - use filters
pm: user role does not permit running function [.gw.syncexec]
```

A result that is too large is refused after the merge. It arrives as the
gateway's own `failed to apply join function to result sets` error, carrying the
policy's message.

## Tested, and not yet

[`tests/q/test_torq_querypolicy.q`](../../tests/q/test_torq_querypolicy.q)
checks every refusal, the role exceptions and the ceiling, the response-size
check, and both wrapped steps. It runs on PeachQ as well as KDB-X, with TorQ's
two functions stubbed. The Python tests check the `-dataaccess` flags and
`gateway1`'s generated access list.

Running the stack with all of this switched on (`.pm` on the gateway, getdata on
the tiers, the generated access list) has not been done yet. The checks in the
table below need a KDB-X licence:

  | Check                                                                   | Expected                                         |
  | ---                                                                     | ---                                              |
  | `uqs start --profile essential`, then `uqs query 'count mkt_orderbook'` | works as before: `admin` is trusted              |
  | the frontend's pages                                                    | work as before: they log in as `admin`           |
  | the getdata call above, as `analyst`                                    | rows                                             |
  | `.gw.syncexec` as `analyst`                                             | `pm: user role does not permit running function` |
  | `hopen` to `rdb1` as `analyst`                                          | refused at login                                 |
