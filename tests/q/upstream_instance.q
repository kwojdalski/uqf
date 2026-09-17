/ upstream_instance.q - the minimal kdb+ instance: the starter pack's HDB,
/ loaded into a plain q process, listening on a port.
/ .
/ Nothing of this repository's runs here, and that is the design. It stands
/ in for the OTHER system - a kdb+ someone else operates, holding a table we
/ did not shape - so that a worker in a second process can move data out of
/ it through the data-engineering framework, over a real handle. It is what
/ tests/q/run_two_instances.q starts, and what a reader can start by hand:
/ .
/   q tests/q/upstream_instance.q -p 5010
/ .
/ The HDB is date-partitioned, which is the shape that makes a naive query
/ scan every partition - the source's query constrains `date` first for
/ that reason, and this process is where that matters.

system"l lib/torq-finance-starter-pack/hdb";
-1 "upstream: serving ",(", " sv string tables[])," from the starter pack on port ",string system"p";
