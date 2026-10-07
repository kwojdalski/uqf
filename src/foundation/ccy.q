/ ccy.q - currency pair symbol conventions: canonical CURCUR validation and
/ normalization from looser input formats (lowercase, with separators).
/ .
/ Canonical form used throughout this library is CURCUR - six uppercase
/ letters, no separator, e.g. `EURUSD (not `eurusd, not "EUR/USD").

\d .qccy

/ Private: string-coerce x without exploding an already-string input into
/ a list of 1-char strings - `string` on a char vector maps over each
/ char instead of acting as the identity, which is a real q gotcha.
/ @param x a symbol or string
/ @return x as a plain string
/ @eg .qccy.ccy_to_str `EURUSD  -> "EURUSD"
/ @private
ccy_to_str:{[x] $[10h=type x; x; string x]};

/ True if x is already in canonical CURCUR form: exactly 6 uppercase
/ letters, no separator.
/ @param x a symbol or string
/ @return 1b if x is a valid CURCUR pair, else 0b
/ @eg .qccy.is_ccy_pair `EURUSD  -> 1b
/ @eg .qccy.is_ccy_pair "eur/usd"  -> 0b
is_ccy_pair:{[x]
    s:ccy_to_str x;
    length_ok:(count s)=6;
    all_letters:all s in .Q.A;
    length_ok and all_letters};

/ Normalize a currency pair into canonical CURCUR form: uppercases and
/ strips common separators (/, -, _, space).
/ @param x a symbol or string, e.g. `eurusd, "EUR/USD", "eur-usd"
/ @return the canonical CURCUR symbol, e.g. `EURUSD
/ @throws error if x cannot be normalized to a 6-letter CURCUR pair
/ @eg .qccy.normalize_ccy_pair "eur/usd"  -> `EURUSD
normalize_ccy_pair:{[x]
    s:ccy_to_str x;
    no_slash:ssr[s;"/";""];
    no_dash:ssr[no_slash;"-";""];
    no_underscore:ssr[no_dash;"_";""];
    no_space:ssr[no_underscore;" ";""];
    canonical:upper no_space;
    if[not is_ccy_pair canonical; '"normalize_ccy_pair: cannot normalize '",s,"' to a 6-letter CURCUR pair"];
    `$canonical};

/ Build a canonical CURCUR pair symbol from a 3-letter base and quote
/ currency code.
/ @param base 3-letter base currency code, e.g. `EUR or "eur"
/ @param quote 3-letter quote currency code, e.g. `USD or "usd"
/ @return the canonical CURCUR symbol, e.g. `EURUSD
/ @throws error if base or quote is not a 3-letter alphabetic code
/ @eg .qccy.ccy_pair_symbol[`EUR;`USD]  -> `EURUSD
ccy_pair_symbol:{[base;quote]
    b:upper ccy_to_str base;
    q:upper ccy_to_str quote;
    if[(count b)<>3; '"ccy_pair_symbol: base currency code must be 3 letters, got '",b,"'"];
    if[(count q)<>3; '"ccy_pair_symbol: quote currency code must be 3 letters, got '",q,"'"];
    if[not all b in .Q.A; '"ccy_pair_symbol: base currency code must be letters, got '",b,"'"];
    if[not all q in .Q.A; '"ccy_pair_symbol: quote currency code must be letters, got '",q,"'"];
    `$b,q};

/ Split a currency pair (any reasonable format - normalized first) into
/ its base and quote 3-letter currency codes.
/ @param pair a symbol or string, e.g. `EURUSD, "eur/usd"
/ @return dict `base`quote!(baseSym;quoteSym)
/ @throws error if pair cannot be normalized to a 6-letter CURCUR pair
/ @eg .qccy.ccy_pair_legs `EURUSD  -> `base`quote!(`EUR;`USD)
ccy_pair_legs:{[pair]
    canonical:normalize_ccy_pair pair;
    s:string canonical;
    `base`quote!(`$3#s;`$-3#s)};


/ ------------------------------------------------------------------ PIPS

/ Pip size by QUOTE currency, where it is not the 0.0001 most pairs use.
/ One entry per exception, so a new one is a line here, not a literal at
/ every producer: a JPY slip moves a markout 100x and passes every check
/ (#623).
pip_size_by_quote:(enlist `JPY)!enlist 0.01

/ The pip size of a pair: 0.01 for a JPY quote, 0.0001 otherwise.
/ @param pair a pair, or a list of pairs, in any format normalize_ccy_pair accepts
/ @return the pip size, as a float - one per pair for a list
/ @eg .qccy.pip_size `EURUSD`USDJPY  -> 0.0001 0.01
pip_size:{[pair]
    / One pair is a symbol atom OR a string ("usd/jpy"): (),pair would have
    / taken a string's characters for pairs.
    one:(0>type pair) or 10h=type pair;
    quotes:{(ccy_pair_legs x)`quote} each $[one; enlist pair; pair];
    sz:0.0001^pip_size_by_quote quotes;
    $[one; first sz; sz]}

/ How many pips one unit of price is: 10000 for EURUSD, 100 for USDJPY.
/ What .qexec.markout and every `pip_factor` column multiply by.
/ @param pair a pair, or a list of pairs
/ @return the factor, as a long - one per pair for a list
/ @eg .qccy.pip_factor `EURUSD`USDJPY`EURJPY  -> 10000 100 100
pip_factor:{[pair] "j"$1%pip_size pair}

\d .
