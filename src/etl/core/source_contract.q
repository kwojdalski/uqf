/ source_contract.q - the centralised external-source contract (.qetl.source).
/ .
/ THE CONTRACT IS SPLIT ACROSS FILES, ONE NAMESPACE (#970). This file is the design
/ record and the declaration schema; src/etl/init.q loads the rest in this order,
/ and every public name is still .qetl.source.<name>:
/ .
/   source_transports.q   the transport registry and the ipc/odbc/local/mock rows
/   source_registry.q     supporting and raw inputs, define, def
/   source_validation.q   validate, validate_fixture, validate_live
/   source_credentials.q  credential_var and the sources.csv settings
/   source_zones.q        the zone table, utc_to_local, local_to_utc
/   source_local_hdb.q    the local transport: an HDB read from its files
/   source_fetch.q        ipc and mock, fetch_window, zone bounds, fixture windowing
/ .
/ There is no shared row-coercion step here: an adapter converts its own rows to the
/ declared types (see .qetl.coerce for the helpers it can call), because a
/ per-source policy (case, decimal comma, date-only) is the adapter's to own (#1093).
/ .
/ The source contract: "register every external source table, target
/ mapping, required field and required type in the centralised source
/ contract. Validate both generated fixtures and live external metadata
/ against that same contract."
/ .
/ The phrase doing the work is "that SAME contract". One declaration validates
/ both a local fixture and a live source, so a fixture cannot drift from the
/ thing it stands in for. Two separate declarations would pass a suite while
/ the real source had changed - and a fixture that no longer resembles its
/ source is worse than no fixture, because it manufactures confidence.
/ .
/ Four ETL decisions are recorded here rather than left implicit, and the
/ time-and-timezone block after them likewise. They follow from answers
/ already given, and are stated so nobody has to re-derive them:
/ .
/   the question bank (answered by the maintainer) - the external driver is NOT a hard
/     dependency. A public single-host demo cannot require a licensed ODBC
/     driver, or the whole backfill path is undemonstrable. Every source
/     declares a fixture, so the path is exercisable with no driver at all.
/ .
/   the question bank (answered by the maintainer) - SECRETS come from the
/     ENVIRONMENT only. Nothing secret lives in this tree, and the YAML layer
/     must never carry one. What a source connects to may come from a
/     sources.csv row (#718), which names the variable a secret is in and is
/     refused if it holds one. `require_credentials` enforces that rather than
/     documenting it.
/ .
/   the question bank - source queries are PARAMETERISED q lambdas,
/     never built by string concatenation. The frontend's guarantee is that no
/     caller input reaches query text; uqf_frontend/queries.py already honours it,
/     and a source adapter is the same problem with a less friendly input.
/ .
/   the question bank - bank-internal business logic is not
/     reimplementable here. What gets built is a generic ANALOGUE with the
/     same shape and none of the logic. So this contract describes shapes, and
/     deliberately carries no business semantics.
/ .
/ TIME AND TIMEZONE (issue #80)
/ .
/   Do external sources return local times, and who converts?"
/     WHAT THE BANK'S SOURCES ACTUALLY RETURN IS NOT KNOWABLE FROM HERE. The
/     canonical tree is unreachable and this repository being public forbids
/     its schemas appearing
/     here, so nobody in this repository can answer that half. What IS
/     decidable is the policy, and the policy is what changes the code:
/ .
/       - internally, everything is UTC. That is not new: python/uqf_frontend
/         already enforces it at the HTTP edge, where queries.coerce rejects
/         a naive datetime outright. Same rule; this file's timezone handling
/         is the q-side half of it.
/       - the zone is therefore a PER-SOURCE property, because only the
/         source knows it, and it is DECLARED rather than defaulted. An
/         omitted zone is the exact shape of the bug: it reads as "UTC" to
/         every later reader while the source was handing over wall-clock
/         local time all along, which is silent and off by one offset.
/       - the FRAMEWORK converts, once, at fetch, in fetch_window below.
/         Not the worker (each would do it slightly differently, and a
/         worker that forgot would be indistinguishable from a UTC source),
/         and not the consumer (by then the zone is gone).
/ .
/   The `z->p` cast bug class. THE CANONICAL BUG ITSELF IS NOT
/     RECOVERABLE from this tree; what follows is the class, measured here
/     under KDB-X, and the guards that stop it recurring. q's `datetime`
/     (type 15h, `z`) is a FLOAT count of days; `timestamp` (12h, `p`) is a
/     long count of nanoseconds. Going z->p is therefore a float-to-long
/     rounding, and it is quiet:
/ .
/       - measured: of 1000 timestamps one nanosecond apart, 999 do not
/         survive a p->z->p round trip; the largest error seen was 629ns,
/         and up to 447ns of it BACKWARDS, i.e. to an earlier instant.
/       - whole seconds DO survive (0 error across a full day of them), so
/         the bug passes every hand-check built from round numbers and only
/         shows up on real trade timestamps.
/       - `=` says a z and a p at the same instant are equal (1b) while `~`
/         says they do not match (0b), and `distinct` keeps a value and its
/         own round trip as TWO values - so a dedupe on (key;time) silently
/         stops recognising rows it has already published.
/       - filtering a z column with p window bounds does not even warn: q
/         promotes, the window comes back plausible, and the wrongness is
/         carried in the data rather than raised.
/ .
/     Three things prevent recurrence, all of them enforcement rather than
/     documentation: (1) register below requires the time_column to be one of
/     the declared columns AND to be declared `p`, so a z time column is a
/     registration failure; (2) validate/validate_live compare declared type
/     characters against `meta`, so a source that silently changes a column
/     from p to z fails on both the fixture and the live path; (3)
/     scripts/gates/check_q_traps.py forbids the q datetime type in src/ outright -
/     the cast direction is not statically decidable, but the type's
/     PRESENCE is, and this tree has no legitimate use for it.
/ .
/   DST in windowed backfills. Windows are cut in UTC by
/     .qetl.job.bounded.runtime.windows, so a "daily" window is always exactly 24h of elapsed
/     time: never short, never long, and the coverage ledger keeps tiling
/     exactly across a transition. The variable thing is the LOCAL span, and
/     that is handled here rather than by warping window widths - see
/     source_bounds and local_to_utc for the two traps that produces.

\d .qetl.source

/ ---------------------------------------------------------------- SCHEMA

/ What every registered source must declare. Named as data so a test can
/ assert the set rather than trusting a code review.
/ .
/ `tz` is REQUIRED, with no default. A defaulted zone is the
/ bug: it reads as a decision downstream while nobody ever made one. Stating
/ `UTC` costs one symbol and makes "this source hands over UTC" a claim
/ somebody wrote, which validate_live can then be run against.
required_declarations:`source`table_name`target`time_column`row_key`columns`types`query`fixture`tz

\d .
