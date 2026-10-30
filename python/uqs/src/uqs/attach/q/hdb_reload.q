/ hdb_reload.q - ask one HDB reader to reload its database, as a native end of
/ day does, after `uqs data alter --attach` changed it (#1755).
/ .
/ Sent over IPC as one lambda by uqs.attach.hdb_where to each HDB process that
/ serves the attachment's HDB directory (hdb_reader.q decides which). TorQ's
/ own `reload` (hdbstandard.q) when the process has one, else `\l .` - the
/ directory a loaded database made the process's working directory.
/ .
/ @param req JSON, ignored (kept so every operation takes one string)
/ @return JSON string: status "reloaded" or "failed", and the error
{[req]
 r:@[{[d] $[`reload in key `.; reload d; system "l ."]; ""};.z.d;{[e] e}];
 .j.j `status`detail!($[0=count r;"reloaded";"failed"];r)}
