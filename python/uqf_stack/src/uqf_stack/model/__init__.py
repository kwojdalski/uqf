"""What the stack IS, declared rather than run.

The pipeline registry, the edges between pipelines, the plant's table schemas
and the process dependency graph. Nothing here starts a process or opens a
connection: these modules answer questions about the declared shape of the
stack, and `stack/` is what acts on the answers.
"""
