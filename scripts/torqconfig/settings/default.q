/ scripts/torqconfig/settings/default.q - one log-level vocabulary for the
/ whole fleet.
/ .
/ uqs points KDBSERVCONFIG at scripts/torqconfig (uqs.stack.env.build_env),
/ and torq.q loads <that>/settings/default.q into EVERY process it starts,
/ vendored ones included, after its logging functions are defined. Beside
/ it, gateway.q holds the query policy's settings for every gateway. TorQ
/ looks a config FILE up in KDBAPPCONFIG first, so these add settings
/ without shadowing any of the starter pack's files.
/ .
/ WHAT IT CHANGES. TorQ's logger writes INF, WARN and ERR; .qetl.log writes
/ INFO, WARNING, ERROR, DEBUG and TRACE. A log read across both - and every
/ uqf process logs through both - carried two names for the same level.
/ This renames TorQ's three as each line is written, so a file, a published
/ logmsg row and `uqs logs` all read TRACE, DEBUG, INFO, WARNING or ERROR.
/ .
/ WHY .lg.format AND .lg.publish, and not .lg.o, .lg.w and .lg.e. Those
/ three are projections of .lg.l made when torq.q loads, so they hold the
/ level as an argument already bound; redefining them would miss every
/ caller that uses .lg.l directly. .lg.l itself looks .lg.format and
/ .lg.publish up by name on every call, so wrapping those two catches every
/ line. outmap and pubmap are still consulted with TorQ's own names, before
/ the rename, so routing is unchanged; the new names are added beside them
/ for .qetl.log's lines.

\d .lg

/ TorQ's level name -> the one written. ERROR is TorQ's too and unchanged.
uqf_names:`INF`WARN`ERR!`INFO`WARNING`ERROR

/ Private: a level as it is written. A name not in the map passes through.
uqf_level:{[level] level^uqf_names level}

format:{[f;loglevel;proctype;proc;id;message] f[uqf_level[loglevel];proctype;proc;id;message]}[format]
publish:{[f;loglevel;proctype;proc;id;message] f[uqf_level[loglevel];proctype;proc;id;message]}[publish]

/ publish checks pubmap with the name it is given, now the renamed one, so the
/ renamed names route exactly as TorQ routes the originals.
pubmap,:(value[uqf_names] except key pubmap)#(value uqf_names)!pubmap key uqf_names
outmap,:(value[uqf_names] except key outmap)#(value uqf_names)!outmap key uqf_names

\d .
