/ order_outcome.q - the vocabulary of the order-outcome contract (#1396): what an
/ `order_outcomes` row may say, shared by every source adapter and by the
/ statistic that reads them.
/ .
/ The contract itself (the columns) is the plant table `order_outcomes`
/ (src/etl/plant_tables.q). This file is the half a table cannot say: which
/ `outcome` values exist, which of them end a request, and which columns a
/ statistic may group by. It is one place, so an adapter cannot invent a sixth
/ outcome that the quality transform then silently counts nowhere.
/ .
/ The counting policy built on it is in src/etl/transforms/order_quality.q; the
/ adapters are the normalizer's mappings (src/etl/streaming/order_outcomes.q).

\d .qpipe.transform.order_outcome

/ The outcomes that END a request: the only ones that are counted.
terminal:`filled`rejected`cancelled

/ Every outcome a row may carry. `unresolved is a status update on a request that
/ has not ended (new, pending, partially filled, amended); `unmapped is a status
/ code the adapter does not know, or a row it could not identify. Neither is
/ counted, and both are kept in the plant table so the exclusion can be seen.
outcomes:terminal,`unresolved`unmapped

/ The columns a statistic may group by. A source that has none of one carries
/ null there; a null is a group of its own and is never filled in.
dimensions:`book`product`venue

/ The adapter's status table for a source: the source's own codes mapped to
/ outcomes, refusing a map that names an outcome outside the vocabulary.
/ @param codes the source's status codes, a symbol vector
/ @param outs the outcome of each, a symbol vector of outcomes
/ @return a dictionary from code to outcome
/ @throws error naming an outcome that is not in the vocabulary
/ @eg .qpipe.transform.order_outcome.status_map[`F`R;`filled`rejected]`F  ->  `filled
status_map:{[codes;outs]
    bad:distinct outs where not outs in outcomes;
    if[count bad; '"status_map: ",(", " sv string bad)," is not an order outcome - the vocabulary is ",", " sv string outcomes];
    if[count[codes]<>count outs; '"status_map: ",string[count codes]," codes but ",string[count outs]," outcomes"];
    codes!outs}

/ A source's codes translated: a code the map does not know is `unmapped, never
/ dropped and never guessed.
/ @param map a status_map
/ @param codes the source's codes, one per row
/ @return an outcome per row
/ @eg .qpipe.transform.order_outcome.translate[.qpipe.transform.order_outcome.status_map[enlist `F;enlist `filled];`F`Z]  ->  `filled`unmapped
translate:{[map;codes]
    o:map codes;
    ?[null o;`unmapped;o]}

\d .
