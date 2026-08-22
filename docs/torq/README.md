# TorQ demo architecture

Diagrams for the running state of the TorQ Finance Starter Pack demo (see
[docs/torq-demo.md](../torq-demo.md) for how to actually start/stop/query
it). Reflects what `torq-demo list processes` shows today: the vendored
14-process stack plus uqf's own additions (`fxfeed1`, `quotesfeed1`,
`widefeed1`, `cross1`, `vectorize1`).

## Process topology

Who connects to whom over IPC. Solid arrows are `.u.upd` publishes or
`.sub.subscribe` subscriptions (real data flow); dashed arrows are
discovery/registration only.

```mermaid
flowchart LR
    disc["discovery1<br/>:6051"]

    subgraph tp["Tickerplant"]
        stp["stp1<br/>segmentedtickerplant<br/>:6050"]
        sctp["sctp1<br/>chained TP<br/>:6065"]
    end

    subgraph feeds["Feeds (publish only, self-managed handle)"]
        feed1["feed1<br/>vendored equity<br/>:6064"]
        fxfeed1["fxfeed1<br/>uqf FX<br/>:6069"]
        quotesfeed1["quotesfeed1<br/>uqf depth-aware FX<br/>:6074"]
        widefeed1["widefeed1<br/>uqf wide book<br/>:6076"]
    end

    subgraph etl["uqf ETL (subscribe + republish, credentialed)"]
        cross1["cross1<br/>cross-rate reprice<br/>:6075"]
        vectorize1["vectorize1<br/>wide->vector fold<br/>:6077"]
    end

    subgraph storage["Storage"]
        rdb1["rdb1<br/>:6052"]
        wdb1["wdb1<br/>:6055"]
        sort1["sort1<br/>:6056"]
        sw["sortworker1/2<br/>:6066/6067"]
        hdb1["hdb1<br/>:6053"]
        hdb2["hdb2<br/>:6054"]
    end

    gw["gateway1<br/>:6057"]
    ops["housekeeping1 :6061<br/>metrics1 :6068"]

    feed1 -->|"upd quote/trade"| stp
    fxfeed1 -->|"upd quote"| stp
    quotesfeed1 -->|"upd quotes"| stp
    widefeed1 -->|"upd wide_book"| stp
    vectorize1 -->|"upd mkt_orderbook<br/>(2nd, unauth handle)"| stp

    stp -->|"subscribeto: all tables<br/>(default)"| rdb1
    stp -->|"sub.subscribe quotes"| cross1
    stp -->|"sub.subscribe wide_book"| vectorize1
    sctp -.->|"chained from"| stp

    rdb1 -->|"EOD writedown"| wdb1
    wdb1 --> sort1
    sort1 --> sw
    wdb1 -->|"reload"| hdb1
    wdb1 -->|"reload"| hdb2

    gw --> rdb1
    gw --> hdb1
    gw --> hdb2

    disc -.->|register| stp
    disc -.->|register| rdb1
    disc -.->|register| gw
    disc -.->|register| etl
```

Two different connection patterns coexist, deliberately:

- **Feeds** (`feed1`/`fxfeed1`/`quotesfeed1`/`widefeed1`) only ever
  *publish*. They find the tickerplant via
  `.servers.gethandlebytype[\`segmentedtickerplant;\`any]` - a
  self-managed handle, no credentials, no `.servers.startup[]`.
- **ETL processes** (`cross1`/`vectorize1`) *subscribe*, which needs a
  real `.servers`-managed, access-listed handle -
  `.servers.startup[]` against `accesslist.txt`. Both borrow the
  already-credentialed `metrics` proctype rather than adding a new
  password file to the vendored tree (see `core.py`'s `add_extra_process`
  comments). `vectorize1` additionally opens a *second*, self-managed
  publish handle (same kind feeds use) to republish its output - one
  process, two connection styles, for two different jobs.

## Data pipeline: table by table

What each process actually reads and writes, and where the two uqf ETL
processes diverge - one publishes its output back onto the tickerplant
(a real, persisted table), the other keeps it private to the process.

```mermaid
flowchart TD
    fxfeed1["fxfeed1"] -->|writes| quote[("quote<br/>(vendored schema)")]
    feed1["feed1"] -->|writes| quote
    feed1 -->|writes| trade[("trade<br/>(vendored schema)")]

    quotesfeed1["quotesfeed1"] -->|writes| quotes[("quotes<br/>time,sym,bid/ask_prices,bid/ask_sizes")]
    widefeed1["widefeed1"] -->|writes| wide_book[("wide_book<br/>time,sym,bids0..10,asks0..10")]

    quotes -->|"sub.subscribe"| cross1["cross1<br/>.qfwd.cross_book_at"]
    cross1 -->|"private, in-process only<br/>(never written to stp1)"| cross_quotes["cross_quotes<br/>(cross1's own memory)"]

    wide_book -->|"sub.subscribe"| vectorize1["vectorize1<br/>.qbook.book_from_wide_levels"]
    vectorize1 -->|"republished via upd"| mkt_orderbook[("mkt_orderbook<br/>time,sym,bid/ask_prices")]

    quote --> rdb1[("rdb1<br/>(today's ticks, in memory)")]
    trade --> rdb1
    quotes --> rdb1
    wide_book --> rdb1
    mkt_orderbook --> rdb1

    rdb1 -->|EOD writedown| hdb[("hdb1/hdb2<br/>(on-disk history)")]

    style cross_quotes stroke-dasharray: 5 5
```

`cross_quotes` is drawn dashed because it never becomes a real database
table - it's `.cross.cross_quotes`, a plain in-memory table inside
`cross1`'s own process, queryable only by connecting to `cross1` directly
(`torq-demo query "select from cross_quotes" --port 6075`). `mkt_orderbook`
is a full round trip instead: `vectorize1` folds `wide_book` and republishes
onto `stp1`, so it flows through `rdb1`/`wdb1`/`hdb` exactly like any
vendored table and survives past `vectorize1` restarting.

## Config generation: never edit the vendored tree

Every process/table addition here (`fxfeed1` through `vectorize1`, and
anything the `new-process` wizard adds) follows the same rule: read the
vendored file fresh, generate an extended copy, point the real process at
the copy - the vendored `lib/torq-finance-starter-pack/appconfig/process.csv`
and `database.q` are never written to.

```mermaid
flowchart LR
    vendored_csv["vendored<br/>appconfig/process.csv"]
    vendored_schema["vendored<br/>database.q"]
    overrides["process_overrides.csv<br/>(config-set)"]
    extra_procs["extra_processes.csv<br/>(new-process wizard)"]
    extra_schema["extra_schema.q<br/>(new-process wizard)"]

    vendored_csv --> base["_base_process_rows()<br/>+ fxfeed1/quotesfeed1/<br/>cross1/widefeed1/vectorize1"]
    extra_procs --> base
    overrides -->|"field overrides<br/>applied on top"| gen_csv
    base --> gen_csv["generated<br/>scripts/output/torq-demo/<br/>process.csv"]

    vendored_schema --> gen_schema["generated<br/>scripts/output/torq-demo/<br/>database.q"]
    extra_schema --> gen_schema

    gen_csv -->|"stp1's -schemafile<br/>repointed at"| gen_schema
    gen_csv -->|"read by"| torqsh["lib/torq/torq.sh<br/>(every start/stop/summary)"]
```

`bootstrap()` (`python/torq_orchestrator/src/torq_orchestrator/core.py`)
regenerates both files on every command - `start`, `stop`, `summary`,
everything - so nothing here is a one-time setup step; the generated
files are always a fresh function of the vendored tree plus whatever's
in the three small, git-tracked extension files.
