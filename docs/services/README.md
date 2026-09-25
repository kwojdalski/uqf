# Services

One page per running service: what it computes, what it reads and publishes, how
it is built and why, and how to run it. Operating the stack as a whole -
starting, stopping, config, logs - is [running the uqf stack](../guides/uqs.md);
adding a new service is [adding a pipeline](../guides/new-pipeline.md).

  | Page                                       | Processes                                 | In one line                                                                  |
  | ---                                        | ---                                       | ---                                                                          |
  | [`synthetic-feeds.md`](synthetic-feeds.md) | `fxfeed1`, `quotesfeed1`                  | the invented FX quotes the demo runs on                                      |
  | [`superbook.md`](superbook.md)             | `marketdata1`, `superbook1`, `arbitrage1` | direct liquidity merged across sources, and the crossed prices that fall out |
  | [`cross-arbitrage.md`](cross-arbitrage.md) | `crossarb1`                               | the direct book against a synthetic route through other pairs                |
  | [`fx-positions.md`](fx-positions.md)       | `fxordersfeed1`, `fxpositions1`           | a running book with limits and breach alerts                                 |
  | [`databento.md`](databento.md)             | `databento1`                              | live Databento MBP-10 folded into the book shape                             |
  | [`crypto-recorder.md`](crypto-recorder.md) | `cryptomock1`, cryptorust's recorders     | venue books and fills from cryptorust, or a mock of them                     |
  | [`tap.md`](tap.md)                         | `tap1`                                    | a diagnostic subscriber that logs every batch                                |

Every process, with its port, what it subscribes to and publishes, is in the
generated [process table](../reference/processes.md).
