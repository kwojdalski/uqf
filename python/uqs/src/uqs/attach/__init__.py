"""Attaching a uqf job to a TorQ stack that already exists (#1424).

plan -> validate -> apply -> verify -> detach, from an operation manifest, and
never a restart of a process this workflow did not start. The design, the
TorQ internals each step touches and the reversal rules are in
docs/guides/attach-existing-torq.md.
"""
