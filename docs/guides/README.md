# Guides

How to do a thing, start to finish. A guide is written to be *followed* ---
every command in one has been run against this tree and its output is what was
printed --- where an [architecture](../architecture/) page is written to be
understood and a [reference](../reference/) page to be consulted.

  | Page                                     | Answers                                                                                                        |
  | ---                                      | ---                                                                                                            |
  | [`uqs.md`](uqs.md)                       | running the stack: start, stop, profiles, config, logs, and what each process is                               |
  | [`new-pipeline.md`](new-pipeline.md)     | adding a pipeline end to end — a streaming job, or a source and a bounded worker, including the implementation |
  | [`when-it-breaks.md`](when-it-breaks.md) | a backfill that failed partway, or a streaming job that died: finding the cause and recovering                 |
  | [`metatables.md`](metatables.md)         | partition profiling over an HDB                                                                                |
  | [`config-audit.md`](config-audit.md)     | recording runtime configuration changes, and joining them to who made them                                     |
  | [`deploy.md`](deploy.md)                 | deploying to a server with an existing TorQ, verifying it, and rolling back                                    |

Choosing the *shape* of a new job, rather than walking one end to end, is
[`scaffolding/`](../scaffolding/). What one running service does is
[`services/`](../services/).
